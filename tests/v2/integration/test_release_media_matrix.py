"""Release-candidate media matrix using real FFmpeg, ffprobe and MediaInfo."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import shutil
import subprocess

import pytest

from bili_remote.database import Database
from bili_remote.file_service import FileService
from bili_remote.media_probe import probe_json_sync
from bili_remote.release_info import ReleaseInfoService
from bili_remote.v2.naming import NamingService
from bili_remote.v2.naming.probe import MediaProbeAdapter


class Audit:
    def __init__(self):
        self.records = {}

    def create(self, value):
        self.update(value)

    def update(self, value):
        self.records[value["id"]] = deepcopy(value)

    def get(self, batch_id):
        return deepcopy(self.records.get(batch_id))


class Tasks:
    """The real executor is driven directly by this test."""

    def __init__(self):
        self.calls = []

    def enqueue_naming(self, batch_id, idempotency_key, undo=False):
        self.calls.append(
            {
                "batch_id": batch_id,
                "idempotency_key": idempotency_key,
                "undo": undo,
            }
        )
        return {
            "kind": "processing",
            "id": len(self.calls),
            "task_type": "v2_rename_undo" if undo else "v2_rename_apply",
            "status": "queued",
        }


def require_tools():
    tools = {
        name: shutil.which(name)
        for name in ("ffmpeg", "ffprobe", "mediainfo")
    }
    if not all(tools.values()):
        pytest.skip("release media matrix requires ffmpeg, ffprobe and MediaInfo")
    return tools


def run_ffmpeg(executable: str, *arguments: str):
    subprocess.run(
        [executable, "-v", "error", *arguments],
        check=True,
        capture_output=True,
    )


def make_h264_aac(path: Path, ffmpeg: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x180:rate=24000/1001:duration=1.2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=1.2",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-shortest",
        "-movflags",
        "+faststart",
        "-y",
        str(path),
    )


def make_hevc_multitrack_mkv(
    path: Path,
    ffmpeg: str,
    *,
    embedded_srt: Path,
    embedded_ass: Path,
):
    embedded_srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nEnglish subtitle\n",
        encoding="utf-8",
    )
    embedded_ass.write_text(
        """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 180

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Chinese subtitle
""",
        encoding="utf-8",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        ffmpeg,
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x360:rate=25:duration=1.2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=1.2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=660:sample_rate=48000:duration=1.2",
        "-i",
        str(embedded_srt),
        "-i",
        str(embedded_ass),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-map",
        "2:a:0",
        "-map",
        "3:s:0",
        "-map",
        "4:s:0",
        "-c:v",
        "libx265",
        "-preset",
        "ultrafast",
        "-x265-params",
        "log-level=error",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-c:s",
        "srt",
        "-metadata:s:a:0",
        "language=eng",
        "-metadata:s:a:1",
        "language=jpn",
        "-metadata:s:s:0",
        "language=eng",
        "-metadata:s:s:1",
        "language=chi",
        "-shortest",
        "-y",
        str(path),
    )


def real_service(settings):
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    tasks = Tasks()
    service = NamingService(
        FileService(settings, database),
        MediaProbeAdapter(settings),
        owner_session="release-media-matrix",
        audit=Audit(),
        tasks=tasks,
    )
    return service, tasks, database


def test_real_h264_hevc_probe_and_mediainfo_reports(settings, tmp_path):
    tools = require_tools()
    h264 = tmp_path / "H264.AAC.Source.mp4"
    hevc = tmp_path / "HEVC.MultiTrack.Source.mkv"
    make_h264_aac(h264, tools["ffmpeg"])
    make_hevc_multitrack_mkv(
        hevc,
        tools["ffmpeg"],
        embedded_srt=tmp_path / "embedded.srt",
        embedded_ass=tmp_path / "embedded.ass",
    )

    adapter = MediaProbeAdapter(settings)
    h264_probe, hevc_probe = asyncio.run(
        _probe_both(adapter, h264, hevc)
    )
    assert h264_probe["video_codec"]["value"] == "H.264"
    assert h264_probe["audio_codec"]["value"] == "AAC"
    assert h264_probe["resolution"]["value"] == "180p"
    assert h264_probe["bit_depth"]["value"] == 8
    assert h264_probe["probe_tool"] == "ffprobe"
    assert h264_probe["probe_version"]
    assert hevc_probe["video_codec"]["value"] == "H.265"
    assert hevc_probe["resolution"]["value"] == "360p"
    assert hevc_probe["audio_codec"]["value"] == "AAC"

    payload = probe_json_sync(settings, hevc)
    streams = payload["streams"]
    assert len([item for item in streams if item["codec_type"] == "video"]) == 1
    assert len([item for item in streams if item["codec_type"] == "audio"]) == 2
    assert len([item for item in streams if item["codec_type"] == "subtitle"]) == 2
    assert {item.get("tags", {}).get("language") for item in streams if item["codec_type"] == "audio"} == {
        "eng",
        "jpn",
    }

    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    source = settings.download_root / hevc.name
    shutil.copyfile(hevc, source)
    report = ReleaseInfoService(
        settings,
        FileService(settings, database),
        database,
    ).analyze(
        {
            "root_key": "downloads",
            "relative_path": source.name,
            "include_mediainfo": True,
            "format": "markdown",
        }
    )
    assert report["videos"][0]["details"]["video_codec"] == "H265"
    assert report["videos"][0]["details"]["audio_codecs"] == ["aac", "aac"]
    assert report["reports"][0]["kind"] == "MediaInfo"
    assert report["reports"][0]["valid"] is True
    assert "HEVC" in report["reports"][0]["text"]
    assert "Audio #1" in report["reports"][0]["text"]
    assert "Text #2" in report["reports"][0]["text"]
    assert "媒体信息" in report["generated_text"]


async def _probe_both(adapter, first, second):
    return await asyncio.gather(adapter.probe(first), adapter.probe(second))


def test_real_naming_matrix_preview_conflict_apply_and_undo(settings, tmp_path):
    tools = require_tools()
    service, tasks, _ = real_service(settings)
    season_source = tmp_path / "Motion.Study.Source.Season"
    season_root = settings.download_root / season_source.name
    make_h264_aac(season_root / "Motion.Study.S01E01.mp4", tools["ffmpeg"])
    make_h264_aac(season_root / "Motion.Study.S01E02.mp4", tools["ffmpeg"])
    # The filename says episode 9; the explicit per-file mapping below
    # resolves the real episode number to 3.
    make_h264_aac(season_root / "Motion.Study.S01E09.mp4", tools["ffmpeg"])
    (season_root / "Motion.Study.S01E01.en.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nEnglish\n",
        encoding="utf-8",
    )
    (season_root / "Motion.Study.S01E01.chs.ass").write_text(
        """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 180

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,Chinese
""",
        encoding="utf-8",
    )

    directory_ref = service.files.reference("downloads", season_root.name)
    first_video_ref = service.files.reference(
        "downloads",
        f"{season_root.name}/Motion.Study.S01E01.mp4",
    )
    context = asyncio.run(
        service.analyze(
            [directory_ref, first_video_ref],
            manual_overrides={
                "selected_title": "Motion Study",
                "year": 2024,
            },
            rules={"source": "WEB-DL", "group": "RELEASE"},
        )
    )
    paths = [item["ref"]["relative_path"] for item in context["files"]]
    assert len(paths) == 3
    assert len(paths) == len(set(paths))
    assert context["episode"]["scope"] == "season"
    assert context["effective_rules"]["naming_rule"]["template_id"] == "series"
    assert (
        context["effective_rules"]["sources"]["naming_rule.template_id"]["reason"]
        == "filename_episode"
    )
    assert all(item["probe"]["video_codec"]["source"] == "ffprobe" for item in context["files"])

    preview = service.preview(
        context,
        {
            "episode_overrides": {
                "Motion.Study.Source.Season/Motion.Study.S01E09.mp4": 3
            }
        },
        rules={"source": "WEB-DL", "group": "RELEASE"},
    )
    assert preview["can_apply"]
    targets = [item["target_relative_path"] for item in preview["operations"]]
    assert len(targets) == 5
    assert any(".S01E01." in path for path in targets)
    assert any(".S01E02." in path for path in targets)
    assert any(".S01E03." in path for path in targets)
    assert any(path.endswith(".en.srt") for path in targets)
    assert any(path.endswith(".chs.ass") for path in targets)
    assert not any(".S01E09." in path for path in targets)
    assert (
        context["effective_rules"]["naming_rule"]["source"]["source"]
        == "manual"
    )

    before = {
        path.relative_to(settings.download_root).as_posix(): path.read_bytes()
        for path in season_root.rglob("*")
        if path.is_file()
    }
    collision = settings.download_root / targets[0]
    collision.write_bytes(b"existing")
    conflicted = service.preview(
        service.contexts[context["context_id"]],
        {
            "episode_overrides": {
                "Motion.Study.Source.Season/Motion.Study.S01E09.mp4": 3
            }
        },
        rules={"source": "WEB-DL", "group": "RELEASE"},
    )
    assert conflicted["can_apply"] is False
    assert any(item["code"] == "target_exists" for item in conflicted["conflicts"])
    collision.unlink()

    final_preview = service.preview(
        service.contexts[context["context_id"]],
        {
            "episode_overrides": {
                "Motion.Study.Source.Season/Motion.Study.S01E09.mp4": 3
            }
        },
        rules={"source": "WEB-DL", "group": "RELEASE"},
    )
    assert final_preview["can_apply"]
    assert before
    assert all(
        (settings.download_root / relative).read_bytes() == content
        for relative, content in before.items()
    )

    result = asyncio.run(
        service.apply(
            final_preview["preview_token"],
            final_preview["context_revision"],
            "real-media-apply",
        )
    )
    service.executor.execute(result["batch_id"])
    assert len(tasks.calls) == 1
    for operation in final_preview["operations"]:
        target = settings.download_root / operation["target_relative_path"]
        source = settings.download_root / operation["source"]["relative_path"]
        assert target.is_file()
        assert not source.exists()
    for relative, content in before.items():
        expected = next(
            (
                item["target_relative_path"]
                for item in final_preview["operations"]
                if item["source"]["relative_path"] == relative
            ),
            None,
        )
        assert expected is not None
        assert (settings.download_root / expected).read_bytes() == content

    reverse = asyncio.run(service.undo_preview(result["batch_id"]))
    assert reverse["can_apply"]
    undo = asyncio.run(
        service.apply(
            reverse["preview_token"],
            reverse["context_revision"],
            "real-media-undo",
            undo=True,
        )
    )
    service.executor.execute(undo["batch_id"])
    assert all(
        (settings.download_root / relative).read_bytes() == content
        for relative, content in before.items()
    )


def test_long_movie_and_unrecognized_episode_have_explicit_resolutions(
    settings,
    tmp_path,
):
    tools = require_tools()
    service, _, _ = real_service(settings)
    long_stem = "Motion.Study." + ("Very.Long.Release.Name." * 7) + "S01E04"
    long_path = settings.download_root / f"{long_stem}.mp4"
    make_h264_aac(long_path, tools["ffmpeg"])
    long_context = asyncio.run(
        service.analyze(
            [service.files.reference("downloads", long_path.name)],
            manual_overrides={
                "selected_title": "Motion Study",
                "year": 2024,
            },
        )
    )
    assert len(long_path.name) > 150
    assert long_context["effective_rules"]["naming_rule"]["template_id"] == "series"
    long_preview = service.preview(long_context)
    assert long_preview["can_apply"]
    assert ".S01E04." in long_preview["operations"][0]["target_relative_path"]

    movie_path = settings.download_root / "unrecognized_source_file.mp4"
    make_h264_aac(movie_path, tools["ffmpeg"])
    movie_context = asyncio.run(
        service.analyze(
            [service.files.reference("downloads", movie_path.name)],
            manual_overrides={
                "selected_title": "Unrecognized Source",
                "year": 2025,
            },
        )
    )
    assert any(
        item["code"] == "missing_episode"
        for item in movie_context["warnings"]
    )
    assert (
        movie_context["effective_rules"]["sources"]["naming_rule.template_id"][
            "reason"
        ]
        == "movie_fallback"
    )
    movie_preview = service.preview(movie_context)
    assert movie_preview["can_apply"]
    assert (
        "Unrecognized.Source.2025.180p.WEB-DL.H.264.AAC-HDVWEB.mp4"
        == Path(movie_preview["operations"][0]["target_relative_path"]).name
    )

    unrecognized_episode = settings.download_root / "series_unknown_episode.mp4"
    make_h264_aac(unrecognized_episode, tools["ffmpeg"])
    episode_context = asyncio.run(
        service.analyze(
            [service.files.reference("downloads", unrecognized_episode.name)],
            manual_overrides={
                "selected_title": "Unknown Episode Series",
                "year": 2025,
            },
        )
    )
    explicit = service.preview(
        episode_context,
        {"episode_overrides": {unrecognized_episode.name: 7}},
        rules={"template_id": "series"},
    )
    assert explicit["can_apply"]
    assert (
        ".S01E07."
        in explicit["operations"][0]["target_relative_path"]
    )
