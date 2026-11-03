from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from bili_remote.collage import (
    CollageConflictError,
    CollageError,
    CollageService,
    output_name_for,
    sample_times,
)
from bili_remote.config import Settings
from bili_remote.database import Database
from bili_remote.file_service import FileService, OperationCancelled
from bili_remote.media_probe import video_details_from_payload


def make_video(path: Path, *, size: str = "320x180", duration: int = 2) -> None:
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
        pytest.skip(
            "无法生成测试视频："
            + completed.stderr.decode("utf-8", errors="replace")[-300:]
        )


def make_video_with_longer_audio(path: Path) -> None:
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
            "testsrc2=size=320x180:rate=10:duration=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=4",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(
            "无法生成音视频时长不一致的测试文件："
            + completed.stderr.decode("utf-8", errors="replace")[-300:]
        )


def service_for(settings: Settings) -> tuple[CollageService, FileService]:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    files = FileService(settings, database)
    return CollageService(settings, files), files


def request_data(
    sources: list[tuple[str, str]],
    *,
    columns: int = 1,
    rows: int = 2,
) -> dict:
    return {
        "sources": [
            {"root_key": root_key, "relative_path": relative_path}
            for root_key, relative_path in sources
        ],
        "layout_mode": "grid",
        "columns": columns,
        "rows": rows,
        "thumbnail_width": 240,
        "content_type": "custom",
        "skip_start_seconds": 0,
        "skip_end_seconds": 0,
        "output_relative_path": "",
        "overwrite": False,
    }


def test_sample_times_and_output_names() -> None:
    assert sample_times(100, 10, 10, 4) == [20.0, 40.0, 60.0, 80.0]
    with pytest.raises(CollageError, match="有效时长"):
        sample_times(10, 6, 5, 1)
    assert output_name_for("第01集:测试?.mkv") == "第01集_测试__collage.jpg"
    assert output_name_for("CON.mkv") == "_CON_collage.jpg"


def test_video_details_do_not_guess_missing_values() -> None:
    details = video_details_from_payload(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "av1",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "24000/1001",
                }
            ],
            "format": {"duration": "125.5"},
        }
    )
    assert details["width"] == 1920
    assert details["height"] == 1080
    assert details["aspect_ratio"] == "16:9"
    assert details["fps"] == pytest.approx(23.976, rel=1e-4)
    assert details["video_codec"] == "AV1"
    assert details["audio_codec"] == ""
    assert details["duration_seconds"] == 125.5

    unknown = video_details_from_payload(
        {
            "streams": [{"codec_type": "video"}],
            "format": {},
        }
    )
    assert unknown["width"] is None
    assert unknown["height"] is None
    assert unknown["fps"] is None
    assert unknown["duration_seconds"] is None


def test_video_details_prefer_video_stream_duration() -> None:
    details = video_details_from_payload(
        {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 3840,
                    "height": 2160,
                    "duration": "565.480000",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "duration": "1174.890667",
                },
            ],
            "format": {"duration": "1174.891000"},
        }
    )
    assert details["duration_seconds"] == pytest.approx(565.48)


def test_prepare_rejects_non_video_symlink_and_batch_name_collisions(settings) -> None:
    service, _ = service_for(settings)
    (settings.download_root / "notes.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(CollageError, match="不支持的文件类型"):
        service.prepare(request_data([("downloads", "notes.txt")]))

    target = settings.download_root / "real.mp4"
    make_video(target)
    link = settings.download_root / "linked.mp4"
    link.symlink_to(target)
    with pytest.raises(Exception, match="符号链接"):
        service.prepare(request_data([("downloads", "linked.mp4")]))

    first = settings.download_root / "A" / "same.mp4"
    second = settings.download_root / "B" / "same.mp4"
    make_video(first)
    make_video(second)
    with pytest.raises(CollageConflictError, match="批量输出"):
        service.prepare(
            request_data(
                [("downloads", "A/same.mp4"), ("downloads", "B/same.mp4")]
            )
        )


def test_prepare_detects_existing_output_and_requires_explicit_overwrite(settings) -> None:
    service, _ = service_for(settings)
    make_video(settings.download_root / "episode.mp4")
    target = settings.images_root / "episode_collage.jpg"
    target.write_bytes(b"old")
    with pytest.raises(CollageConflictError) as conflict:
        service.prepare(request_data([("downloads", "episode.mp4")]))
    assert conflict.value.conflicts[0]["reason"] == "目标文件已存在"

    data = request_data([("downloads", "episode.mp4")])
    data["overwrite"] = True
    prepared = service.prepare(data)
    assert prepared["outputs"][0]["output_name"] == "episode_collage.jpg"


def test_prepare_rejects_hard_conflicts_even_with_overwrite(settings) -> None:
    service, _ = service_for(settings)
    make_video(settings.download_root / "episode.mp4")
    target = settings.images_root / "episode_collage.jpg"
    target.mkdir()
    data = request_data([("downloads", "episode.mp4")])
    data["overwrite"] = True
    with pytest.raises(CollageConflictError) as conflict:
        service.prepare(data)
    assert conflict.value.conflicts[0]["hard"] is True
    assert "目录" in conflict.value.conflicts[0]["reason"]

    target.rmdir()
    colliding = settings.images_root / "EPISODE_COLLAGE.JPG"
    colliding.write_bytes(b"old")
    with pytest.raises(CollageConflictError) as case_conflict:
        service.prepare(data)
    assert case_conflict.value.conflicts[0]["hard"] is True
    assert "大小写" in case_conflict.value.conflicts[0]["reason"]


def test_count_layout_cannot_exceed_twelve_rows(settings) -> None:
    service, _ = service_for(settings)
    make_video(settings.download_root / "episode.mp4")
    data = request_data([("downloads", "episode.mp4")])
    data.update({"layout_mode": "count", "columns": 1, "total": 120})
    data.pop("rows", None)
    with pytest.raises(CollageError, match="行数不能超过"):
        service.prepare(data)


def test_run_generates_each_video_with_its_own_media_info(settings) -> None:
    service, _ = service_for(settings)
    wide = settings.download_root / "wide.mp4"
    tall = settings.download_root / "tall.mp4"
    make_video(wide, size="320x180")
    make_video(tall, size="320x240")
    before = hashlib.sha256(wide.read_bytes()).hexdigest()
    data = request_data(
        [("downloads", "wide.mp4"), ("downloads", "tall.mp4")],
        rows=2,
    )
    service.prepare(data)
    logs: list[str] = []
    result = service.run(
        data,
        task_id=999,
        log=logs.append,
        should_cancel=lambda: False,
    )
    assert result["status"] == "completed"
    assert result["succeeded"] == 2
    assert result["failed"] == 0
    assert result["items"][0]["media"]["height"] == 180
    assert result["items"][1]["media"]["height"] == 240
    assert result["items"][0]["sample_timestamps"] == ["00:00:00", "00:00:01"]
    assert (settings.images_root / "wide_collage.jpg").is_file()
    assert (settings.images_root / "tall_collage.jpg").is_file()
    assert hashlib.sha256(wide.read_bytes()).hexdigest() == before
    assert not list(settings.runtime_dir.glob("collage-*"))
    assert any("截图 1/2" in line for line in logs)


def test_run_samples_with_video_duration_when_audio_is_longer(settings) -> None:
    service, _ = service_for(settings)
    source = settings.download_root / "long-audio.mp4"
    make_video_with_longer_audio(source)
    data = request_data([("downloads", "long-audio.mp4")], rows=2)
    service.prepare(data)
    result = service.run(
        data,
        task_id=1002,
        log=lambda _: None,
        should_cancel=lambda: False,
    )
    assert result["status"] == "completed"
    assert result["succeeded"] == 1
    assert result["items"][0]["media"]["duration_seconds"] == pytest.approx(2.0)
    assert result["items"][0]["sample_timestamps"] == ["00:00:00", "00:00:01"]
    assert (settings.images_root / "long-audio_collage.jpg").is_file()
    assert not list(settings.runtime_dir.glob("collage-*"))


def test_run_continues_after_one_video_fails(settings) -> None:
    service, _ = service_for(settings)
    make_video(settings.download_root / "good.mp4")
    (settings.download_root / "broken.mp4").write_bytes(b"not a real video")
    data = request_data(
        [("downloads", "broken.mp4"), ("downloads", "good.mp4")],
        rows=2,
    )
    result = service.run(
        data,
        task_id=1000,
        log=lambda _: None,
        should_cancel=lambda: False,
    )
    assert result["status"] == "partial"
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["items"][0]["status"] == "failed"
    assert result["items"][1]["status"] == "completed"
    assert (settings.images_root / "good_collage.jpg").is_file()
    assert not list(settings.runtime_dir.glob("collage-*"))


def test_cancel_cleans_temporary_directory(settings) -> None:
    service, _ = service_for(settings)
    make_video(settings.download_root / "episode.mp4")
    data = request_data([("downloads", "episode.mp4")])
    with pytest.raises(OperationCancelled):
        service.run(
            data,
            task_id=1001,
            log=lambda _: None,
            should_cancel=lambda: True,
        )
    assert not list(settings.runtime_dir.glob("collage-*"))
    assert not (settings.images_root / "episode_collage.jpg").exists()
