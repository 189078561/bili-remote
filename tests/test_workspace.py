from __future__ import annotations

from pathlib import Path

import pytest

from bili_remote.database import Database
from bili_remote.workspace import (
    WorkspaceError,
    browse_directory,
    build_subtitle_links,
    discover_media,
    infer_subtitle_language,
    parse_episode_details,
    parse_episode_metadata,
    parse_media_name,
    resolve_workspace_path,
    scan_project,
)


def test_workspace_paths_reject_traversal_absolute_and_symlink_escape(
    settings, tmp_path: Path
) -> None:
    settings.ensure_directories()
    inside = settings.imports_root / "inside.mkv"
    inside.write_bytes(b"video")
    symlink_file = settings.imports_root / "inside-link.mkv"
    symlink_file.symlink_to(inside)
    resolve_workspace_path(settings, "imports", "inside.mkv", must_exist=True)
    resolved, _ = resolve_workspace_path(settings, "imports", "inside-link.mkv")
    assert resolved == inside.resolve()

    outside = tmp_path / "outside"
    outside.mkdir()
    escape = settings.imports_root / "escape"
    escape.symlink_to(outside, target_is_directory=True)
    with pytest.raises(WorkspaceError):
        resolve_workspace_path(settings, "imports", "escape/file.mkv")
    with pytest.raises(WorkspaceError):
        resolve_workspace_path(settings, "imports", "../outside")
    with pytest.raises(WorkspaceError):
        resolve_workspace_path(settings, "imports", str(outside))
    with pytest.raises(WorkspaceError):
        resolve_workspace_path(settings, "unknown", "file")


def test_browse_paginates_and_blocks_symlink_escape(settings, tmp_path: Path) -> None:
    settings.ensure_directories()
    for index in range(6):
        (settings.imports_root / f"file-{index}.mkv").write_bytes(b"x")
    (settings.imports_root / "sub").mkdir()
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (settings.imports_root / "blocked").symlink_to(outside, target_is_directory=True)

    first = browse_directory(settings, root_key="imports", page=1, page_size=4)
    second = browse_directory(settings, root_key="imports", page=2, page_size=4)
    assert first["total"] == 8
    assert len(first["items"]) == 4
    assert first["has_more"] is True
    assert len(second["items"]) == 4
    blocked = next(item for item in first["items"] if item["name"] == "blocked")
    assert blocked["allowed"] is False


def test_media_name_and_language_parsing() -> None:
    parsed = parse_media_name("Show.S02E07.1080p.mkv")
    assert parsed["season"] == 2
    assert parsed["episode"] == 7
    assert parsed["title"]
    assert parse_media_name("动画 第12集.mkv")["episode"] == 12
    assert parse_media_name("动画 第12集.mkv")["season"] is None
    assert parse_media_name("Show.E02.mkv")["episode"] == 2
    assert parse_media_name("Show.EP02.mkv")["episode"] == 2
    two_by_three = parse_media_name("Show.2x03.mkv")
    assert two_by_three["season"] == 2
    assert two_by_three["episode"] == 3
    bilibili_index = parse_media_name("S190 - 190 慕兰之战14 [3854814].mp4")
    assert bilibili_index["season"] is None
    assert bilibili_index["episode"] == 190
    assert parse_media_name("Movie.2024.1080p.mkv")["episode"] is None
    assert parse_media_name("Movie.1920x1080.mkv")["episode"] is None
    assert parse_media_name("Show.S190 - 190.mkv")["season"] is None
    assert infer_subtitle_language("Show.S01E01.chs.srt") == "zh-Hans"
    assert infer_subtitle_language("Show.S01E01.eng.ass") == "en"
    assert infer_subtitle_language("Show.S01E01.srt") is None


def test_episode_formats_ambiguity_and_false_positives() -> None:
    assert parse_episode_metadata("Show.S01E02.mkv") == {"season": 1, "episode": 2}
    assert parse_episode_metadata("Show.S01 EP02.mkv") == {"season": 1, "episode": 2}
    assert parse_episode_metadata("Show.S01 - 02.mkv") == {"season": 1, "episode": 2}
    assert parse_episode_metadata("Show.第02集.mkv") == {"season": None, "episode": 2}
    assert parse_episode_metadata("Show.第2话.mkv") == {"season": None, "episode": 2}
    assert parse_episode_metadata("Show.2x03.mkv") == {"season": 2, "episode": 3}
    assert parse_episode_metadata("Show.E02.mkv") == {"season": None, "episode": 2}
    assert parse_episode_metadata("Show.EP02.mkv") == {"season": None, "episode": 2}
    assert parse_episode_metadata("S190 - 190 慕兰之战14 [3854814].mp4") == {
        "season": None,
        "episode": 190,
    }
    for name in (
        "Movie.2024.1080p.mkv",
        "Movie.1920x1080.mkv",
        "Show.2160p.mkv",
        "Show.BV1xx411c7mD.mkv",
    ):
        assert parse_episode_metadata(name)["episode"] is None

    ambiguous = parse_episode_details("Show.S01E02.第03集.mkv")
    assert ambiguous["episode"] is None
    assert ambiguous["episode_ambiguous"] is True
    assert {item["episode"] for item in ambiguous["episode_candidates"]} == {2, 3}


def test_scan_detects_bdmv_iso_subtitles_and_marks_missing(settings) -> None:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    project_id = database.create_release_project(
        title="Show",
        media_type="series",
        source_type="local",
        season=1,
    )
    source = settings.imports_root / "Show"
    (source / "S01").mkdir(parents=True)
    (source / "S01" / "Show.S01E01.1080p.mkv").write_bytes(b"video-1")
    (source / "S01" / "Show.S01E02.1080p.mkv").write_bytes(b"video-2")
    (source / "S01" / "Show.S01E01.chs.srt").write_text("subtitle", encoding="utf-8")
    (source / "S01" / "notes.txt").write_text("ignored", encoding="utf-8")
    (source / "Disc.iso").write_bytes(b"iso")
    (source / "BDMV" / "STREAM").mkdir(parents=True)
    (source / "BDMV" / "STREAM" / "00001.m2ts").write_bytes(b"stream")

    discovered = discover_media(settings, "imports", "Show", season_hint=1)
    roles = [item["file_role"] for item in discovered]
    assert roles.count("video") == 2
    assert roles.count("subtitle") == 1
    assert roles.count("iso") == 1
    assert roles.count("bdmv") == 1
    assert not any(item["relative_path"].endswith("00001.m2ts") for item in discovered)
    bdmv_only = discover_media(settings, "imports", "Show/BDMV/STREAM")
    assert len(bdmv_only) == 1
    assert bdmv_only[0]["file_role"] == "bdmv"
    assert bdmv_only[0]["relative_path"] == "Show/BDMV"
    iso_only = discover_media(settings, "imports", "Show/Disc.iso")
    assert iso_only[0]["file_role"] == "iso"
    assert iso_only[0]["relative_path"] == "Show/Disc.iso"

    summary = scan_project(
        settings,
        database,
        project_id,
        root_key="imports",
        relative_path="Show",
        season_hint=1,
    )
    assert summary.counts == {"video": 2, "subtitle": 1, "iso": 1, "bdmv": 1, "other": 0}
    assert summary.unsupported == 1
    files = database.list_release_files(project_id)
    links = database.list_release_file_links(project_id)
    assert len(links) == 1
    assert database.get_release_project(project_id)["download_dir"] == "Show"

    (source / "S01" / "Show.S01E02.1080p.mkv").unlink()
    scan_project(
        settings,
        database,
        project_id,
        root_key="imports",
        relative_path="Show",
        season_hint=1,
    )
    missing = [
        item for item in database.list_release_files(project_id) if item["missing_at"]
    ]
    assert len(missing) == 1
    assert missing[0]["relative_path"].endswith("Show.S01E02.1080p.mkv")


def test_subtitle_link_prefers_same_directory_and_episode() -> None:
    files = [
        {
            "id": 1,
            "relative_path": "S01/Show.E01.mkv",
            "file_role": "video",
            "season": 1,
            "episode": 1,
        },
        {
            "id": 2,
            "relative_path": "S01/Show.E02.mkv",
            "file_role": "video",
            "season": 1,
            "episode": 2,
        },
        {
            "id": 3,
            "relative_path": "S01/Show.E02.chs.srt",
            "file_role": "subtitle",
            "season": 1,
            "episode": 2,
        },
    ]
    links = build_subtitle_links(files)
    assert links == [(2, 3, "external", 0.92)]
