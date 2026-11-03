from __future__ import annotations

import hashlib
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from bili_remote.config import Settings
from bili_remote.database import Database
from bili_remote.file_service import FileService
from bili_remote.screenshots import (
    ScreenshotError,
    ScreenshotService,
    parse_timestamp,
    parse_timestamps,
    sample_random_times,
)


def make_video(path: Path, *, size: str = "320x180", duration: int = 3) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("测试环境没有 ffmpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size={size}:rate=10:duration={duration}",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("无法生成测试视频")


def service_for(settings: Settings) -> tuple[ScreenshotService, FileService]:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    files = FileService(settings, database)
    return ScreenshotService(settings, files, rng=random.Random(1234)), files


def create_data(*paths: str, total: int = 3, width: int = 320) -> dict:
    return {
        "sources": [
            {"root_key": "downloads", "relative_path": path} for path in paths
        ],
        "format": "jpg", "style": "plain",
        "total": total,
        "width": width,
        "content_type": "custom",
        "skip_start_seconds": 0,
        "skip_end_seconds": 0,
        "output_relative_path": "",
    }


def run_session(service: ScreenshotService, data: dict, task_id: int = 1) -> tuple[dict, str]:
    prepared = service.prepare_create(data)
    service.attach_task(prepared["session_id"], task_id)
    service.run(
        prepared["task_input"],
        task_id=task_id,
        log=lambda _: None,
        should_cancel=lambda: False,
    )
    return service.get_session(prepared["session_id"]), prepared["session_id"]


def test_random_sampling_is_sorted_unique_and_respects_skip_range() -> None:
    times = sample_random_times(100, 10, 20, 6, random.Random(7))
    assert len(times) == 6
    assert times == sorted(times)
    assert len(times) == len(set(times))
    assert all(10 <= value <= 80 for value in times)
    with pytest.raises(ScreenshotError, match="有效时长"):
        sample_random_times(10, 6, 5, 1)
    assert parse_timestamp("01:02:03.450") == pytest.approx(3723.45)
    assert parse_timestamp("02:03") == pytest.approx(123)
    with pytest.raises(ScreenshotError, match="时间格式"):
        parse_timestamp("bad")


def test_explicit_timestamps_are_sorted_deduplicated_and_used_verbatim(settings) -> None:
    assert parse_timestamps(["00:00:03", "00:00:01"]) == [1.0, 3.0]
    with pytest.raises(ScreenshotError, match="重复"):
        parse_timestamps(["00:00:01", "00:00:01.000"])
    with pytest.raises(ScreenshotError, match="时间格式"):
        parse_timestamps(["not-a-time"])

    service, _ = service_for(settings)
    make_video(settings.download_root / "timed.mp4", duration=4)
    data = create_data("timed.mp4", total=20)
    data["timestamps"] = ["00:00:03", "00:00:01"]
    prepared = service.prepare_create(data)
    assert prepared["task_input"]["sampling_mode"] == "timestamps"
    assert prepared["task_input"]["timestamps"] == [1.0, 3.0]
    session, _ = run_session(service, data)
    assert session["status"] == "completed"
    assert [item["timestamp"] for item in session["videos"][0]["items"]] == [
        "00:00:01",
        "00:00:03",
    ]
    assert session["params"]["sampling_mode"] == "timestamps"


def test_explicit_timestamp_beyond_duration_fails_without_fake_frames(settings) -> None:
    service, _ = service_for(settings)
    make_video(settings.download_root / "short.mp4", duration=3)
    data = create_data("short.mp4")
    data["timestamps"] = ["00:00:20"]
    session, _ = run_session(service, data)
    assert session["status"] == "failed"
    video = session["videos"][0]
    assert video["status"] == "failed"
    assert "超出视频时长" in video["error"]
    assert video["items"] == []


def test_session_generates_files_and_persists_with_per_video_media(settings) -> None:
    service, _ = service_for(settings)
    make_video(settings.download_root / "wide.mp4", size="320x180")
    make_video(settings.download_root / "tall.mp4", size="320x240")
    session, session_id = run_session(
        service,
        create_data("wide.mp4", "tall.mp4", total=2, width=240),
    )
    assert session["status"] == "completed"
    assert session["counts"]["completed"] == 4
    assert session["videos"][0]["media"]["height"] == 180
    assert session["videos"][1]["media"]["height"] == 240
    for video in session["videos"]:
        assert video["status"] == "completed"
        assert len(video["items"]) == 2
        for item in video["items"]:
            assert item["available"] is True
            assert item["upload"]["status"] == "unavailable"
            path = settings.images_root / item["relative_path"]
            assert path.is_file()
            with Image.open(path) as image:
                assert image.format == "JPEG"
                assert image.width == 240
    assert (settings.data_dir / "screenshot-sessions" / f"{session_id}.json").is_file()

    restarted_database = Database(settings.database_path)
    restarted_files = FileService(settings, restarted_database)
    restarted = ScreenshotService(settings, restarted_files)
    assert restarted.get_session(session_id)["counts"]["completed"] == 4
    assert restarted.list_sessions()[0]["id"] == session_id


def test_recapture_reorder_delete_and_collage_without_source_video(settings) -> None:
    service, _ = service_for(settings)
    source = settings.download_root / "episode.mp4"
    make_video(source, duration=4)
    session, session_id = run_session(service, create_data("episode.mp4", total=3))
    video = session["videos"][0]
    items = video["items"]
    item = items[0]

    original_path = settings.images_root / item["relative_path"]
    original_hash = hashlib.sha256(original_path.read_bytes()).hexdigest()
    with pytest.raises(ScreenshotError, match="时间格式"):
        service.recapture(session_id, item["id"], timestamp="invalid")
    assert hashlib.sha256(original_path.read_bytes()).hexdigest() == original_hash

    recaptured = service.recapture(
        session_id, item["id"], timestamp="00:00:02.500"
    )
    first = recaptured["videos"][0]["items"][0]
    assert first["timestamp"] == "00:00:02"
    assert first["actual_timestamp_seconds"] == pytest.approx(2.5)

    reordered = service.reorder_items(
        session_id,
        {video["id"]: [entry["id"] for entry in reversed(items)]},
    )
    assert reordered["videos"][0]["items"][0]["id"] == items[-1]["id"]

    source.unlink()
    session = service.get_session(session_id)
    missing_item = session["videos"][0]["items"][-1]
    (settings.images_root / missing_item["relative_path"]).unlink()
    session = service.get_session(session_id)
    assert session["counts"]["missing"] == 1
    assert session["videos"][0]["items"][-1]["available"] is False
    selected = [entry["id"] for entry in session["videos"][0]["items"][:2]]
    prepared = service.prepare_collage(
        session_id,
        {
            "selections": [{"video_id": video["id"], "item_ids": selected}],
            "layout_mode": "auto",
            "columns": 2,
            "thumbnail_width": 240,
            "gap": 5,
            "output_relative_path": "",
            "overwrite": False,
        },
    )
    service.run_collage(
        {key: value for key, value in prepared.items() if key not in {"outputs", "conflicts"}},
        task_id=9,
        log=lambda _: None,
        should_cancel=lambda: False,
    )
    collage = settings.images_root / "episode_collage.jpg"
    assert collage.is_file()
    assert collage.stat().st_size > 0

    remaining = service.delete_item(session_id, selected[0])
    assert all(
        entry["id"] != selected[0]
        for entry in remaining["videos"][0]["items"]
    )


def test_create_rejects_traversal_and_symlink_sources(settings) -> None:
    service, _ = service_for(settings)
    with pytest.raises(Exception):
        service.prepare_create(create_data("../outside.mp4"))
    real = settings.download_root / "real.mp4"
    make_video(real)
    link = settings.download_root / "linked.mp4"
    link.symlink_to(real)
    with pytest.raises(Exception, match="符号链接"):
        service.prepare_create(create_data("linked.mp4"))


def test_zero_width_preserves_source_resolution(settings) -> None:
    service, _ = service_for(settings)
    make_video(settings.download_root / "original.mp4", size="320x180", duration=2)
    session, _ = run_session(
        service,
        create_data("original.mp4", total=1, width=0),
    )
    item = session["videos"][0]["items"][0]
    with Image.open(settings.images_root / item["relative_path"]) as image:
        assert (image.width, image.height) == (320, 180)


def test_recapture_failure_keeps_existing_image_and_records_error(
    settings, monkeypatch
) -> None:
    service, _ = service_for(settings)
    make_video(settings.download_root / "episode.mp4", duration=3)
    session, session_id = run_session(service, create_data("episode.mp4", total=1))
    item = session["videos"][0]["items"][0]
    target = settings.images_root / item["relative_path"]
    original_hash = hashlib.sha256(target.read_bytes()).hexdigest()

    def fail_extract(*args, **kwargs):
        raise ScreenshotError("模拟截图失败")

    monkeypatch.setattr(service, "_extract_frame_with_retries", fail_extract)
    with pytest.raises(ScreenshotError, match="模拟截图失败"):
        service.recapture(session_id, item["id"], timestamp="00:00:01")

    assert hashlib.sha256(target.read_bytes()).hexdigest() == original_hash
    persisted = service.get_session(session_id)
    assert persisted["videos"][0]["items"][0]["last_error"] == "模拟截图失败"


def test_rendered_collage_is_checked_against_real_pixel_limits(
    tmp_path, monkeypatch
) -> None:
    rendered = tmp_path / "collage.jpg"
    Image.new("RGB", (10, 10), "white").save(rendered, "JPEG")
    ScreenshotService._validate_rendered_collage(rendered)

    monkeypatch.setattr("bili_remote.screenshots.MAX_CANVAS_PIXELS", 50)
    with pytest.raises(ScreenshotError, match="画布尺寸过大"):
        ScreenshotService._validate_rendered_collage(rendered)
