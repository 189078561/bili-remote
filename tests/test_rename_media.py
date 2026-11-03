from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bili_remote.media_probe import probe_media_file
from bili_remote.web import create_app


def login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]


def fake_ffprobe(tmp_path: Path, payload: dict, *, fail: bool = False) -> str:
    script = tmp_path / ("ffprobe-fail.py" if fail else "ffprobe-rename.py")
    if fail:
        script.write_text("import sys\nsys.stderr.write('probe failed')\nsys.exit(1)\n")
    else:
        script.write_text(
            "import json\nprint(json.dumps(" + repr(payload) + "))\n",
            encoding="utf-8",
        )
    return str(script)


def probe_payload(
    *,
    video_codec: str = "hevc",
    width: int = 3840,
    height: int = 2160,
    audio_codec: str = "eac3",
    audio_profile: str | None = None,
    color_transfer: str = "smpte2084",
    side_data: list[dict] | None = None,
) -> dict:
    audio_stream = {"codec_type": "audio", "codec_name": audio_codec}
    if audio_profile:
        audio_stream["profile"] = audio_profile
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": video_codec,
                "width": width,
                "height": height,
                "color_transfer": color_transfer,
                "side_data_list": side_data or [],
            },
            audio_stream,
        ],
        "format": {"format_name": "matroska", "duration": "120"},
    }


def fake_ffprobe_dispatch(tmp_path: Path, payloads: dict[str, dict]) -> str:
    script = tmp_path / "ffprobe-dispatch.py"
    script.write_text(
        "import json, os, sys\n"
        f"payloads = {payloads!r}\n"
        "name = os.path.basename(sys.argv[-1])\n"
        "print(json.dumps(payloads[name]))\n",
        encoding="utf-8",
    )
    return str(script)


def test_media_probe_normalizes_codecs_resolution_and_hdr(settings, tmp_path: Path) -> None:
    media_file = tmp_path / "sample.mkv"
    media_file.write_bytes(b"video")
    settings.ffprobe_command = (
        sys.executable,
        fake_ffprobe(tmp_path, probe_payload()),
    )
    result = probe_media_file(settings, media_file)
    assert result["status"] == "completed"
    assert result["media"] == {
        "resolution": "2160p",
        "video_codec": "H265",
        "audio_codec": "EAC3",
        "hdr": "HDR",
    }

    settings.ffprobe_command = (
        sys.executable,
        fake_ffprobe(
            tmp_path,
            probe_payload(
                video_codec="avc",
                width=1920,
                height=1080,
                audio_codec="pcm_s24le",
                color_transfer="bt709",
            ),
        ),
    )
    result = probe_media_file(settings, media_file)
    assert result["media"] == {
        "resolution": "1080p",
        "video_codec": "H264",
        "audio_codec": "Uncompressed",
        "hdr": "",
    }

    settings.ffprobe_command = (
        sys.executable,
        fake_ffprobe(
            tmp_path,
            probe_payload(
                color_transfer="",
                side_data=[{"side_data_type": "DOVI configuration record"}],
            ),
        ),
    )
    assert probe_media_file(settings, media_file)["media"]["hdr"] == "DV"

    settings.ffprobe_command = (
        sys.executable,
        fake_ffprobe(tmp_path, probe_payload(color_transfer="arib-std-b67")),
    )
    assert probe_media_file(settings, media_file)["media"]["hdr"] == "HLG"

    settings.ffprobe_command = (
        sys.executable,
        fake_ffprobe(
            tmp_path,
            probe_payload(audio_codec="dts", audio_profile="DTS-HD MA"),
        ),
    )
    assert probe_media_file(settings, media_file)["media"]["audio_codec"] == "DTS-HD"

    settings.ffprobe_command = (
        sys.executable,
        fake_ffprobe(
            tmp_path,
            probe_payload(
                video_codec="av1",
                width=7680,
                height=4320,
                audio_codec="flac",
                color_transfer="",
            ),
        ),
    )
    assert probe_media_file(settings, media_file)["media"] == {
        "resolution": "4320p",
        "video_codec": "AV1",
        "audio_codec": "FLAC",
        "hdr": "",
    }


def test_media_probe_missing_failure_and_missing_stream_leave_fields_empty(
    settings, tmp_path: Path
) -> None:
    media_file = tmp_path / "sample.mkv"
    media_file.write_bytes(b"video")
    settings.ffprobe_command = ("definitely-not-installed-ffprobe",)
    unavailable = probe_media_file(settings, media_file)
    assert unavailable["status"] == "unavailable"
    assert unavailable["media"]["resolution"] == ""

    settings.ffprobe_command = (
        sys.executable,
        fake_ffprobe(tmp_path, {}, fail=True),
    )
    failed = probe_media_file(settings, media_file)
    assert failed["status"] == "failed"
    assert failed["media"]["video_codec"] == ""

    settings.ffprobe_command = (
        sys.executable,
        fake_ffprobe(
            tmp_path,
            {"streams": [{"codec_type": "audio", "codec_name": "aac"}]},
        ),
    )
    no_video = probe_media_file(settings, media_file)
    assert no_video["status"] == "failed"
    assert no_video["media"]["audio_codec"] == ""


def test_analyze_api_detects_media_episode_and_season(settings, tmp_path: Path) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    for name in ["Show.S02E03.mkv", "Show.2x04.mkv", "Show.第05集.mkv"]:
        (directory / name).write_bytes(name.encode())
    settings.ffprobe_command = (
        sys.executable,
        fake_ffprobe(tmp_path, probe_payload()),
    )

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/files/rename/analyze",
            json={
                "root_key": "downloads",
                "directory": "Show",
                "files": ["Show.S02E03.mkv", "Show.2x04.mkv", "Show.第05集.mkv"],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["season_suggestion"] == 2
        assert data["mixed_seasons"] is False
        by_name = {item["name"]: item for item in data["items"]}
        assert by_name["Show.S02E03.mkv"]["episode"] == 3
        assert by_name["Show.2x04.mkv"]["season"] == 2
        assert by_name["Show.2x04.mkv"]["episode"] == 4
        assert by_name["Show.第05集.mkv"]["episode"] == 5
        assert by_name["Show.S02E03.mkv"]["media"]["video_codec"] == "H265"
        assert by_name["Show.S02E03.mkv"]["status"] == "completed"


def test_analyze_probes_each_file_independently(settings, tmp_path: Path) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    names = ["Show.S01E01.mkv", "Show.S01E02.mkv"]
    for name in names:
        (directory / name).write_bytes(name.encode())
    settings.ffprobe_command = (
        sys.executable,
        fake_ffprobe_dispatch(
            tmp_path,
            {
                names[0]: probe_payload(
                    video_codec="avc",
                    width=1920,
                    height=1080,
                    audio_codec="aac",
                    color_transfer="bt709",
                ),
                names[1]: probe_payload(
                    video_codec="hevc",
                    width=3840,
                    height=2160,
                    audio_codec="dts",
                    audio_profile="DTS-HD MA",
                    side_data=[{"side_data_type": "SMPTE ST 2094 App 4"}],
                ),
            },
        ),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/files/rename/analyze",
            json={
                "root_key": "downloads",
                "directory": "Show",
                "files": names,
            },
        )
        assert response.status_code == 200, response.text
        by_name = {item["name"]: item for item in response.json()["items"]}
        assert by_name[names[0]]["media"] == {
            "resolution": "1080p",
            "video_codec": "H264",
            "audio_codec": "AAC",
            "hdr": "",
        }
        assert by_name[names[1]]["media"] == {
            "resolution": "2160p",
            "video_codec": "H265",
            "audio_codec": "DTS-HD",
            "hdr": "HDR10+",
        }
        assert by_name[names[0]]["extension"] == "mkv"
        assert by_name[names[1]]["original_name"] == names[1]


def test_analyze_reports_ambiguous_episode_without_guessing(settings, tmp_path: Path) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    name = "Show.S01E02.第03集.mkv"
    (directory / name).write_bytes(b"video")
    settings.ffprobe_command = (sys.executable, fake_ffprobe(tmp_path, probe_payload()))
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/files/rename/analyze",
            json={"root_key": "downloads", "directory": "Show", "files": [name]},
        )
        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["episode"] is None
        assert item["episode_ambiguous"] is True
        assert {candidate["episode"] for candidate in item["episode_candidates"]} == {2, 3}


def test_analyze_api_mixed_seasons_do_not_suggest_a_season(
    settings, tmp_path: Path
) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    for name in ["Show.S01E01.mkv", "Show.S02E01.mkv"]:
        (directory / name).write_bytes(b"video")
    settings.ffprobe_command = (
        sys.executable,
        fake_ffprobe(tmp_path, probe_payload()),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/files/rename/analyze",
            json={
                "root_key": "downloads",
                "directory": "Show",
                "files": ["Show.S01E01.mkv", "Show.S02E01.mkv"],
            },
        )
        data = response.json()
        assert response.status_code == 200, response.text
        assert data["seasons"] == [1, 2]
        assert data["season_suggestion"] is None
        assert data["mixed_seasons"] is True


def rename_request(
    files: list[str],
    *,
    template: str,
    values: dict[str, str] | None = None,
    episode_mode: str | None = None,
    media: dict[str, dict[str, str]] | None = None,
    file_values: dict[str, dict[str, str]] | None = None,
    episode_overrides: dict[str, int | str] | None = None,
    start_episode: int = 1,
    episode_step: int = 1,
    episode_offset: int = 0,
    episode_direction: str = "increment",
    episode_scope: str = "all",
    file_sort: str | None = None,
) -> dict:
    payload = {
        "root_key": "downloads",
        "directory": "Show",
        "files": files,
        "template": template,
        "start_episode": start_episode,
        "episode_step": episode_step,
        "episode_offset": episode_offset,
        "episode_direction": episode_direction,
        "episode_scope": episode_scope,
        "episode_overrides": episode_overrides or {},
        "media": media or {},
        "file_values": file_values or {},
        "values": values or {"title": "Show", "season": "01"},
        "manual_names": {},
    }
    if episode_mode is not None:
        payload["episode_mode"] = episode_mode
    if file_sort is not None:
        payload["file_sort"] = file_sort
    return payload


def test_detected_mode_preserves_duplicates_gaps_and_unknown(settings) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    names = [
        "Show.S01E02.mkv",
        "Show.S01E02.alt.mkv",
        "Show.S01E05.mkv",
        "Show.extra.mkv",
    ]
    for name in names:
        (directory / name).write_bytes(name.encode())
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/files/rename/preview",
            json=rename_request(names, template="{original_name}.{ext}"),
        )
        assert response.status_code == 200, response.text
        plan = response.json()
        assert plan["valid"] is True
        assert [item["final_episode"] for item in plan["operations"]] == [2, 2, 5, None]
        assert [item["changed"] for item in plan["operations"]] == [False] * 4

        default_season = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                ["Show.S01E02.mkv"],
                template="{title}.S{season:02d}E{episode:02d}.{ext}",
                values={"title": "Show"},
            ),
        )
        assert default_season.status_code == 200, default_season.text
        assert default_season.json()["operations"][0]["target_name"] == (
            "Show.S01E02.mkv"
        )


def test_start_offset_and_per_file_episode_override(settings) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    names = ["Show.E05.mkv", "Show.E02.mkv", "Show.extra.mkv"]
    for name in names:
        (directory / name).write_bytes(name.encode())
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        started = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                names,
                template="{original_name}.E{episode:02d}.{ext}",
                episode_mode="start",
                start_episode=1,
                episode_step=2,
            ),
        )
        assert started.status_code == 200, started.text
        started_plan = started.json()
        assert [
            (item["source_name"], item["final_episode"])
            for item in started_plan["operations"]
        ] == [
            ("Show.E02.mkv", 1),
            ("Show.E05.mkv", 3),
            ("Show.extra.mkv", 5),
        ]

        offset = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                names,
                template="{original_name}.{ext}",
                episode_mode="offset",
                episode_offset=1,
            ),
        )
        assert offset.status_code == 200, offset.text
        assert [item["final_episode"] for item in offset.json()["operations"]] == [6, 3, None]

        overridden = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                names,
                template="{original_name}.{ext}",
                episode_mode="start",
                start_episode=10,
                episode_overrides={"Show.E02.mkv": 7},
            ),
        )
        assert overridden.status_code == 200, overridden.text
        overridden_plan = overridden.json()
        assert overridden_plan["operations"][0]["final_episode"] == 7

        negative = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                names,
                template="{original_name}.{ext}",
                episode_mode="offset",
                episode_overrides={"Show.E02.mkv": -1},
            ),
        )
        assert negative.status_code == 200, negative.text
        assert any(
            "最终集数不能为负数" in item["message"]
            for item in negative.json()["errors"]
        )


def test_natural_sort_direction_scope_and_selection_order(settings) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    names = [
        "Show.E1.mkv",
        "Show.E10.mkv",
        "Show.E2.mkv",
        "Show.extra.mkv",
        "Show.another.mkv",
    ]
    for name in names:
        (directory / name).write_bytes(name.encode())

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        natural = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                names[:4],
                template="{original_name}.R{episode:02d}.{ext}",
                episode_mode="start",
                start_episode=1,
                file_sort="natural",
            ),
        )
        assert natural.status_code == 200, natural.text
        natural_plan = natural.json()
        assert [
            (item["source_name"], item["final_episode"])
            for item in natural_plan["operations"]
        ] == [
            ("Show.E1.mkv", 1),
            ("Show.E2.mkv", 2),
            ("Show.E10.mkv", 3),
            ("Show.extra.mkv", 4),
        ]

        decrement = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                names[:3],
                template="{original_name}.R{episode:02d}.{ext}",
                episode_mode="start",
                start_episode=10,
                episode_direction="decrement",
                file_sort="natural",
            ),
        )
        assert decrement.status_code == 200, decrement.text
        assert [item["final_episode"] for item in decrement.json()["operations"]] == [10, 9, 8]

        negative = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                names[:3],
                template="{original_name}.{ext}",
                episode_mode="start",
                start_episode=1,
                episode_direction="decrement",
                file_sort="natural",
            ),
        )
        assert negative.status_code == 200, negative.text
        assert any("最终集数不能为负数" in item["message"] for item in negative.json()["errors"])

        detected_only = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                names[:4],
                template="{original_name}.{ext}",
                episode_mode="start",
                start_episode=10,
                episode_scope="detected",
                file_sort="natural",
            ),
        )
        assert detected_only.status_code == 200, detected_only.text
        assert [
            (item["source_name"], item["final_episode"])
            for item in detected_only.json()["operations"]
        ] == [
            ("Show.E1.mkv", 10),
            ("Show.E2.mkv", 11),
            ("Show.E10.mkv", 12),
            ("Show.extra.mkv", None),
        ]

        missing_only = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                ["Show.E2.mkv", "Show.extra.mkv", "Show.another.mkv"],
                template="{original_name}.{ext}",
                episode_mode="start",
                start_episode=5,
                episode_scope="missing",
                file_sort="natural",
            ),
        )
        assert missing_only.status_code == 200, missing_only.text
        assert [
            (item["source_name"], item["final_episode"])
            for item in missing_only.json()["operations"]
        ] == [
            ("Show.another.mkv", 5),
            ("Show.E2.mkv", 2),
            ("Show.extra.mkv", 6),
        ]

        selection = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                ["Show.E10.mkv", "Show.E2.mkv", "Show.E1.mkv"],
                template="{original_name}.R{episode:02d}.{ext}",
                episode_mode="start",
                start_episode=1,
                file_sort="selection",
            ),
        )
        assert selection.status_code == 200, selection.text
        assert [
            (item["source_name"], item["final_episode"])
            for item in selection.json()["operations"]
        ] == [
            ("Show.E10.mkv", 1),
            ("Show.E2.mkv", 2),
            ("Show.E1.mkv", 3),
        ]

        for name, modified in (
            ("Show.E1.mkv", 400),
            ("Show.E2.mkv", 200),
            ("Show.E10.mkv", 300),
            ("Show.extra.mkv", 100),
        ):
            os.utime(directory / name, (1_000_000 + modified, 1_000_000 + modified))

        expected_sources = {
            "name": [
                "Show.E1.mkv",
                "Show.E10.mkv",
                "Show.E2.mkv",
                "Show.extra.mkv",
            ],
            "name_desc": [
                "Show.extra.mkv",
                "Show.E2.mkv",
                "Show.E10.mkv",
                "Show.E1.mkv",
            ],
            "mtime": [
                "Show.extra.mkv",
                "Show.E2.mkv",
                "Show.E10.mkv",
                "Show.E1.mkv",
            ],
            "mtime_desc": [
                "Show.E1.mkv",
                "Show.E10.mkv",
                "Show.E2.mkv",
                "Show.extra.mkv",
            ],
        }
        for sort_mode, expected in expected_sources.items():
            sorted_plan = client.post(
                "/api/files/rename/preview",
                json=rename_request(
                    names[:4],
                    template="{original_name}.R{episode:02d}.{ext}",
                    episode_mode="start",
                    start_episode=1,
                    file_sort=sort_mode,
                ),
            )
            assert sorted_plan.status_code == 200, sorted_plan.text
            assert [
                item["source_name"] for item in sorted_plan.json()["operations"]
            ] == expected


def test_per_file_season_source_and_media_do_not_leak(settings) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    names = ["Show.S01E01.mkv", "Show.S01E02.mkv"]
    for name in names:
        (directory / name).write_bytes(name.encode())
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                names,
                template="{title}.S{season:02d}E{episode:02d}.{resolution}.{source}.{video_codec}.{audio_codec}.{hdr}.{ext}",
                values={"title": "Show", "season": "01", "source": "WEB-DL"},
                file_values={
                    names[0]: {"season": "02", "source": "BluRay"},
                    names[1]: {"season": "03", "source": "WEBRip"},
                },
                media={
                    names[0]: {
                        "resolution": "1080p",
                        "video_codec": "H264",
                        "audio_codec": "AAC",
                        "hdr": "",
                    },
                    names[1]: {
                        "resolution": "2160p",
                        "video_codec": "H265",
                        "audio_codec": "TrueHD",
                        "hdr": "DV",
                    },
                },
            ),
        )
        assert response.status_code == 200, response.text
        plan = response.json()
        assert [item["target_name"] for item in plan["operations"]] == [
            "Show.S02E01.1080p.BluRay.H264.AAC.mkv",
            "Show.S03E02.2160p.WEBRip.H265.TrueHD.DV.mkv",
        ]
        assert [item["final_season"] for item in plan["operations"]] == ["02", "03"]
        assert [item["source"] for item in plan["operations"]] == ["BluRay", "WEBRip"]

        cleared_source = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                [names[0]],
                template="{title}.{source}.{ext}",
                values={"title": "Show", "source": "WEB-DL"},
                file_values={names[0]: {"source": ""}},
            ),
        )
        assert cleared_source.status_code == 200, cleared_source.text
        assert cleared_source.json()["valid"] is False
        assert any(
            "未填写模板变量 {source}" in item["message"]
            for item in cleared_source.json()["errors"]
        )


def test_detected_mode_allows_manual_episode_without_enabling_reindex(settings) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    names = ["Show.E05.mkv", "Show.extra.mkv"]
    for name in names:
        (directory / name).write_bytes(name.encode())
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                names[:4],
                template="{original_name}.E{episode:02d}.{ext}",
                episode_mode="detected",
                episode_overrides={"Show.extra.mkv": 8},
            ),
        )
        assert response.status_code == 200, response.text
        plan = response.json()
        assert [
            (item["source_name"], item["final_episode"], item["episode_origin"])
            for item in plan["operations"]
        ] == [("Show.E05.mkv", 5, "detected"), ("Show.extra.mkv", 8, "manual")]

        cleared = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                ["Show.extra.mkv"],
                template="{original_name}.{ext}",
                episode_mode="detected",
                episode_overrides={"Show.extra.mkv": ""},
            ),
        )
        assert cleared.status_code == 200, cleared.text
        cleared_operation = cleared.json()["operations"][0]
        assert cleared_operation["final_episode"] is None
        assert cleared_operation["episode_origin"] == "manual"


def test_series_and_movie_templates_clean_empty_hdr_and_keep_extension(
    settings,
) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    for name in ["Show.S01E01.mkv", "Show.S01E02.mkv", "Movie.2025.mkv"]:
        (directory / name).write_bytes(name.encode())
    values = {
        "title": "Show",
        "year": "2026",
        "season": "01",
        "source": "WEB-DL",
        "group": "GRP",
    }
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        series = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                ["Show.S01E01.mkv", "Show.S01E02.mkv"],
                template=(
                    "{title}.S{season:02d}E{episode:02d}.{year}.{resolution}."
                    "{source}.{hdr}.{video_codec}.{audio_codec}-{group}.{ext}"
                ),
                values=values,
                media={
                    "Show.S01E01.mkv": {
                        "resolution": "2160p",
                        "video_codec": "H265",
                        "audio_codec": "TrueHD",
                        "hdr": "",
                    },
                    "Show.S01E02.mkv": {
                        "resolution": "1080p",
                        "video_codec": "H264",
                        "audio_codec": "AAC",
                        "hdr": "HDR",
                    },
                },
            ),
        )
        assert series.status_code == 200, series.text
        series_plan = series.json()
        assert series_plan["valid"] is True
        assert [item["target_name"] for item in series_plan["operations"]] == [
            "Show.S01E01.2026.2160p.WEB-DL.H265.TrueHD-GRP.mkv",
            "Show.S01E02.2026.1080p.WEB-DL.HDR.H264.AAC-GRP.mkv",
        ]

        movie = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                ["Movie.2025.mkv"],
                template=(
                    "{title}.{year}.{resolution}.{source}.{hdr}.{video_codec}."
                    "{audio_codec}-{group}.{ext}"
                ),
                values={**values, "title": "Movie", "year": "2025"},
                media={
                    "Movie.2025.mkv": {
                        "resolution": "1080p",
                        "video_codec": "H264",
                        "audio_codec": "AAC",
                        "hdr": "",
                    }
                },
            ),
        )
        assert movie.status_code == 200, movie.text
        assert movie.json()["operations"][0]["target_name"] == (
            "Movie.2025.1080p.WEB-DL.H264.AAC-GRP.mkv"
        )


def test_title_year_and_group_can_be_empty_and_omitted(settings) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    for name in ["Show.S01E01.mkv", "Show.S01E02.mkv", "Show.extra.mkv"]:
        (directory / name).write_bytes(name.encode())
    template = (
        "{title}.{year}.S{season:02d}E{episode:02d}.{resolution}.{source}."
        "{hdr}.{video_codec}.{audio_codec}-{group}.{ext}"
    )
    first_media = {
        "Show.S01E01.mkv": {
            "resolution": "1080p",
            "video_codec": "H264",
            "audio_codec": "AAC",
            "hdr": "",
        }
    }
    second_media = {
        "Show.S01E02.mkv": {
            "resolution": "1080p",
            "video_codec": "H264",
            "audio_codec": "AAC",
            "hdr": "",
        }
    }
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        all_blank = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                ["Show.S01E01.mkv"],
                template=template,
                values={"title": "", "year": "", "season": "01", "source": "WEB-DL"},
                media=first_media,
            ),
        )
        assert all_blank.status_code == 200, all_blank.text
        assert all_blank.json()["operations"][0]["target_name"] == (
            "S01E01.1080p.WEB-DL.H264.AAC.mkv"
        )

        group_blank = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                ["Show.S01E02.mkv"],
                template=template,
                values={
                    "title": "Show",
                    "year": "2026",
                    "season": "01",
                    "source": "WEB-DL",
                    "group": "",
                },
                media=second_media,
            ),
        )
        assert group_blank.status_code == 200, group_blank.text
        assert group_blank.json()["operations"][0]["target_name"] == (
            "Show.2026.S01E02.1080p.WEB-DL.H264.AAC.mkv"
        )

        extension_only = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                ["Show.S01E01.mkv"],
                template="{title}.{year}.{group}.{ext}",
                values={"title": "", "year": "", "group": ""},
            ),
        )
        assert extension_only.status_code == 200, extension_only.text
        assert any(
            "模板变量均为空" in item["message"]
            for item in extension_only.json()["errors"]
        )

        missing_episode = client.post(
            "/api/files/rename/preview",
            json=rename_request(
                ["Show.extra.mkv"],
                template=template,
                values={"title": "Show", "year": "2026", "season": "01", "source": "WEB-DL"},
                media={
                    "Show.extra.mkv": {
                        "resolution": "1080p",
                        "video_codec": "H264",
                        "audio_codec": "AAC",
                        "hdr": "",
                    }
                },
            ),
        )
        assert missing_episode.status_code == 200, missing_episode.text
        assert any(
            "未填写模板变量 {episode}" in item["message"]
            for item in missing_episode.json()["errors"]
        )
