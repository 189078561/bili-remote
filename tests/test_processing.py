from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from bili_remote.database import Database
from bili_remote.naming import PreviewStore
from bili_remote.processing import ProcessingTaskManager


def setup_project(settings) -> tuple[Database, int, int]:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    project_id = database.create_release_project(
        title="Show",
        media_type="series",
        source_type="local",
        metadata={"root_key": "imports", "source_path": "Show"},
        download_dir="Show",
    )
    video = settings.imports_root / "Show" / "Show.S01E01.mkv"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"fake-video")
    stat = video.stat()
    file_id = database.upsert_release_file(
        project_id=project_id,
        root_key="imports",
        relative_path="Show/Show.S01E01.mkv",
        file_role="video",
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        season=1,
        episode=1,
        title="Show",
        language=None,
    )
    return database, project_id, file_id


def fake_ffprobe(tmp_path: Path, *, fail: bool = False) -> tuple[str, str]:
    script = tmp_path / ("ffprobe-fail.py" if fail else "ffprobe-ok.py")
    if fail:
        script.write_text(
            "import sys\nsys.stderr.write('probe failed')\nsys.exit(1)\n",
            encoding="utf-8",
        )
    else:
        payload = {
            "streams": [
                {"index": 1, "codec_type": "audio", "codec_name": "aac"},
                {
                    "index": 2,
                    "codec_type": "subtitle",
                    "codec_name": "ass",
                    "tags": {"language": "chi"},
                },
            ],
            "format": {"format_name": "matroska", "duration": "120.5"},
        }
        script.write_text(
            "import json\nprint(json.dumps(" + repr(payload) + "))\n",
            encoding="utf-8",
        )
    return str(script), str(script)


async def wait_task(database: Database, task_id: int) -> dict:
    for _ in range(100):
        task = database.get_processing_task(task_id)
        if task and task["status"] in {"completed", "failed", "cancelled"}:
            return task
        await asyncio.sleep(0.03)
    raise AssertionError("task did not finish")


async def test_ffprobe_success_records_audio_and_internal_subtitles(settings, tmp_path) -> None:
    database, project_id, file_id = setup_project(settings)
    script, _ = fake_ffprobe(tmp_path)
    settings.ffprobe_command = (sys.executable, script)
    manager = ProcessingTaskManager(settings, database, PreviewStore())
    task, created = manager.enqueue(
        project_id=project_id,
        task_type="media_check",
        input_data={},
        idempotency_key="check-success",
    )
    assert created is True
    manager.start()
    try:
        finished = await wait_task(database, int(task["id"]))
    finally:
        await manager.stop()
    assert finished["status"] == "completed"
    assert finished["result"]["status"] == "completed"
    record = database.get_release_file(file_id)
    assert record["scan_result"]["audio_codec"] == "aac"
    assert record["scan_result"]["internal_subtitles"][0]["language"] == "chi"
    checks = database.list_release_checks(project_id)
    assert checks[0]["status"] == "completed"


async def test_ffprobe_missing_is_unavailable_not_success(settings) -> None:
    database, project_id, _ = setup_project(settings)
    settings.ffprobe_command = ("definitely-not-installed-ffprobe",)
    manager = ProcessingTaskManager(settings, database, PreviewStore())
    task, _ = manager.enqueue(
        project_id=project_id,
        task_type="media_check",
        input_data={},
        idempotency_key="check-unavailable",
    )
    manager.start()
    try:
        finished = await wait_task(database, int(task["id"]))
    finally:
        await manager.stop()
    assert finished["status"] == "completed"
    assert finished["result"]["status"] == "unavailable"
    assert database.list_release_checks(project_id)[0]["status"] == "unavailable"


async def test_ffprobe_failure_marks_task_and_check_failed(settings, tmp_path) -> None:
    database, project_id, _ = setup_project(settings)
    script, _ = fake_ffprobe(tmp_path, fail=True)
    settings.ffprobe_command = (sys.executable, script)
    manager = ProcessingTaskManager(settings, database, PreviewStore())
    task, _ = manager.enqueue(
        project_id=project_id,
        task_type="media_check",
        input_data={},
        idempotency_key="check-failed",
    )
    manager.start()
    try:
        finished = await wait_task(database, int(task["id"]))
    finally:
        await manager.stop()
    assert finished["status"] == "failed"
    assert "ffprobe" in finished["error"]
    checks = database.list_release_checks(project_id)
    assert checks[0]["status"] == "failed"
    assert checks[0]["result"]["files"][0]["status"] == "failed"


async def test_running_task_can_be_cancelled(settings, tmp_path) -> None:
    database, project_id, _ = setup_project(settings)
    second = settings.imports_root / "Show" / "Show.S01E02.mkv"
    second.write_bytes(b"fake-video-2")
    stat = second.stat()
    database.upsert_release_file(
        project_id=project_id,
        root_key="imports",
        relative_path="Show/Show.S01E02.mkv",
        file_role="video",
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        season=1,
        episode=2,
        title="Show",
        language=None,
    )
    slow = tmp_path / "ffprobe-slow.py"
    slow.write_text(
        "import json,time\ntime.sleep(0.4)\n"
        "print(json.dumps({'streams':[],'format':{'format_name':'matroska'}}))\n",
        encoding="utf-8",
    )
    settings.ffprobe_command = (sys.executable, str(slow))
    manager = ProcessingTaskManager(settings, database, PreviewStore())
    task, _ = manager.enqueue(
        project_id=project_id,
        task_type="media_check",
        input_data={},
        idempotency_key="cancel-me",
    )
    manager.start()
    try:
        for _ in range(50):
            current = database.get_processing_task(int(task["id"]))
            if current and current["status"] == "running":
                break
            await asyncio.sleep(0.02)
        assert manager.cancel(int(task["id"]))
        finished = await wait_task(database, int(task["id"]))
    finally:
        await manager.stop()
    assert finished["status"] == "cancelled"
    assert database.get_release_project(project_id)["status"] == "ready"


def test_task_startup_recovery_and_idempotency(settings) -> None:
    database, project_id, _ = setup_project(settings)
    task_id = database.create_processing_task(
        project_id=project_id,
        task_type="scan",
        input_data={},
        log_path=str(settings.logs_dir / "task-test.log"),
        idempotency_key="startup-task",
    )
    assert database.claim_processing_task(task_id)
    database.prepare_processing_tasks_on_startup()
    task = database.get_processing_task(task_id)
    assert task["status"] == "failed"
    assert "服务重启" in task["error"]
    assert database.get_release_project(project_id)["status"] == "failed"
    assert not database.retry_processing_task(999999)

    manager = ProcessingTaskManager(settings, database, PreviewStore())
    first, created = manager.enqueue(
        project_id=project_id,
        task_type="scan",
        input_data={"root_key": "imports", "relative_path": "Show"},
        idempotency_key="same-scan",
    )
    assert created is True
    second, created_again = manager.enqueue(
        project_id=project_id,
        task_type="scan",
        input_data={"root_key": "imports", "relative_path": "Show"},
        idempotency_key="same-scan",
    )
    assert created_again is False
    assert first["id"] == second["id"]
