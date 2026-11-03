from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

from bili_remote.web import create_app


def login(client: TestClient, *, set_header: bool = True) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200, response.text
    csrf = response.json()["csrf"]
    if set_header:
        client.headers["X-CSRF-Token"] = csrf
    return csrf


def wait_project_task(client: TestClient, project_id: int) -> list[dict]:
    tasks: list[dict] = []
    for _ in range(120):
        tasks = client.get(f"/api/tasks?project_id={project_id}").json()
        if tasks and all(
            task["status"] in {"completed", "failed", "cancelled"} for task in tasks
        ):
            return tasks
        time.sleep(0.05)
    raise AssertionError(f"tasks did not finish: {tasks}")


def test_workspace_project_scan_preview_apply_rollback_and_delete(
    settings, tmp_path: Path
) -> None:
    settings.ensure_directories()
    source = settings.imports_root / "Show" / "S01"
    source.mkdir(parents=True)
    (source / "old1.S01E01.mkv").write_bytes(b"video")
    (source / "old1.S01E01.chs.srt").write_text("subtitle", encoding="utf-8")
    script = tmp_path / "ffprobe.py"
    script.write_text(
        "import json\n"
        "print(json.dumps({'streams':[{'codec_type':'audio','codec_name':'aac'}],"
        "'format':{'format_name':'matroska','duration':'10'}}))\n",
        encoding="utf-8",
    )
    settings.ffprobe_command = (sys.executable, str(script))

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        roots = client.get("/api/workspace/roots")
        assert roots.status_code == 200
        assert {item["key"] for item in roots.json()} == {
            "downloads", "imports", "staging", "releases"
        }
        browse = client.get(
            "/api/workspace/browse", params={"root_key": "imports", "path": "Show"}
        )
        assert browse.status_code == 200

        created = client.post(
            "/api/releases",
            json={
                "title": "Show",
                "media_type": "series",
                "source_type": "local",
                "year": 2026,
                "season": 1,
                "root_key": "imports",
                "source_path": "Show",
                "final_dir": "TV/Show",
            },
        )
        assert created.status_code == 200, created.text
        project_id = created.json()["id"]
        job_id = app.state.services.database.create_job(
            source_url="https://www.bilibili.com/video/BV1",
            kind="manual",
            output_template="Bilibili/%(title)s.%(ext)s",
            output_dir="",
        )
        linked = client.post(
            f"/api/releases/{project_id}/jobs",
            json={"job_ids": [job_id], "mode": "replace"},
        )
        assert linked.status_code == 200, linked.text
        assert [job["id"] for job in linked.json()["jobs"]] == [job_id]
        project_after_link = client.get(f"/api/releases/{project_id}").json()
        assert project_after_link["metadata"]["source_path"] == "Show"

        scanned = client.post(
            "/api/workspace/scan",
            json={
                "project_id": project_id,
                "root_key": "imports",
                "relative_path": "Show",
                "season": 1,
            },
        )
        assert scanned.status_code == 200, scanned.text
        tasks = wait_project_task(client, project_id)
        assert tasks[-1]["task_type"] == "scan"
        assert tasks[-1]["status"] == "completed", tasks[-1]

        files = client.get(f"/api/releases/{project_id}/files").json()
        assert len(files["files"]) == 2
        assert len(files["links"]) == 1
        video_file = next(
            item for item in files["files"] if item["file_role"] == "video"
        )
        edited = client.patch(
            f"/api/releases/{project_id}/files/{video_file['id']}",
            json={"season": 2, "episode": 3, "language": "ja"},
        )
        assert edited.status_code == 200
        assert edited.json()["episode"] == 3
        client.patch(
            f"/api/releases/{project_id}/files/{video_file['id']}",
            json={"season": 1, "episode": 1, "language": None},
        )

        preview = client.post(
            f"/api/releases/{project_id}/rename/preview",
            json={
                "template": "{title}.S{season:02d}E{episode:02d}",
                "target_mode": "in_place",
            },
        )
        assert preview.status_code == 200, preview.text
        preview_data = preview.json()
        assert len(preview_data["operations"]) == 2
        applied = client.post(
            f"/api/releases/{project_id}/rename/apply",
            json={"preview_token": preview_data["token"]},
        )
        assert applied.status_code == 200, applied.text
        tasks = wait_project_task(client, project_id)
        assert any(
            task["task_type"] == "rename_apply" and task["status"] == "completed"
            for task in tasks
        )
        assert (source / "Show.S01E01.mkv").exists()

        rolled = client.post(f"/api/releases/{project_id}/rename/rollback")
        assert rolled.status_code == 200, rolled.text
        tasks = wait_project_task(client, project_id)
        assert any(
            task["task_type"] == "rename_rollback" and task["status"] == "completed"
            for task in tasks
        )
        assert (source / "old1.S01E01.mkv").exists()

        checked = client.post(f"/api/releases/{project_id}/checks")
        assert checked.status_code == 200, checked.text
        tasks = wait_project_task(client, project_id)
        assert any(
            task["task_type"] == "media_check" and task["status"] == "completed"
            for task in tasks
        )
        checks = client.get(f"/api/releases/{project_id}/checks").json()
        assert checks[0]["status"] == "completed"

        events = client.get(f"/api/releases/{project_id}/events").json()
        assert events
        integrations = client.get("/api/integrations/status").json()
        assert integrations["services"]["minfo"]["status"] == "未配置"

        deleted = client.delete(f"/api/releases/{project_id}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["retained_file_count"] == 2
        assert (source / "old1.S01E01.mkv").exists()
        assert client.get(f"/api/releases/{project_id}").status_code == 404


def test_workspace_mutations_require_csrf(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        login(client, set_header=False)
        response = client.post("/api/releases", json={"title": "No CSRF"})
        assert response.status_code == 403
