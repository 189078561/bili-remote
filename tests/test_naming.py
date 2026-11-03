from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bili_remote.database import Database
from bili_remote.naming import (
    NamingError,
    apply_preview,
    create_preview,
    parse_naming_template,
    render_naming_template,
    rollback_latest,
    sanitize_filename_component,
)
from bili_remote.workspace import path_state


def make_project(settings, *, final_dir: str = "TV/Show") -> tuple[Database, int]:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    project_id = database.create_release_project(
        title="Show",
        media_type="series",
        source_type="local",
        year=2026,
        season=1,
        metadata={"root_key": "imports", "source_path": "Show", "source": "WEB-DL"},
        download_dir="Show",
        final_dir=final_dir,
    )
    return database, project_id


def add_file(
    settings,
    database: Database,
    project_id: int,
    relative_path: str,
    *,
    role: str = "video",
    season: int | None = 1,
    episode: int | None = 1,
    language: str | None = None,
) -> int:
    path = settings.imports_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if role == "bdmv":
        path.mkdir(exist_ok=True)
        (path / "STREAM").mkdir(exist_ok=True)
        (path / "STREAM" / "00001.m2ts").write_bytes(b"stream")
    else:
        path.write_bytes(f"content-{relative_path}".encode())
    size_bytes, mtime_ns = path_state(path)
    return database.upsert_release_file(
        project_id=project_id,
        root_key="imports",
        relative_path=relative_path,
        file_role=role,
        size_bytes=size_bytes,
        mtime_ns=mtime_ns,
        season=season,
        episode=episode,
        title="Show",
        language=language,
        scan_result={},
    )


def add_task(database: Database, project_id: int) -> int:
    return database.create_processing_task(
        project_id=project_id,
        task_type="rename_apply",
        input_data={},
        log_path="/tmp/test-task.log",
        idempotency_key=f"test:{project_id}:{os.urandom(4).hex()}",
    )


def test_template_validation_and_sanitization() -> None:
    parse_naming_template("{title}.S{season:02d}E{episode:02d}.{resolution}")
    with pytest.raises(NamingError):
        parse_naming_template("{title}.{unknown}")
    with pytest.raises(NamingError):
        parse_naming_template("{title}.{resolution:02d}")
    with pytest.raises(NamingError):
        parse_naming_template("folder/{title}")
    rendered, warnings = render_naming_template(
        "{title}: S{season:02d}E{episode:02d}.{group}", {"title": "A/B", "season": 1, "episode": 2}
    )
    assert "/" not in rendered
    assert rendered.startswith("A_B")
    assert warnings == ["缺少可选值：group"]
    assert sanitize_filename_component("...") == "untitled"


def test_preview_in_place_links_subtitle_and_applies_then_rolls_back(settings) -> None:
    database, project_id = make_project(settings)
    video_id = add_file(settings, database, project_id, "Show/S01/old1.mkv")
    subtitle_id = add_file(
        settings,
        database,
        project_id,
        "Show/S01/old1.chs.srt",
        role="subtitle",
        language="zh-Hans",
    )
    database.replace_release_file_links(
        project_id, [(video_id, subtitle_id, "external", 0.99)]
    )
    preview = create_preview(
        settings,
        database,
        project_id,
        template="{title}.S{season:02d}E{episode:02d}",
        target_mode="in_place",
    )
    targets = {item["target_relative_path"] for item in preview.operations}
    assert targets == {
        "Show/S01/Show.S01E01.mkv",
        "Show/S01/Show.S01E01.chs.srt",
    }
    result = apply_preview(
        settings, database, preview, task_id=add_task(database, project_id)
    )
    assert result["applied"] == 2
    assert (settings.imports_root / "Show/S01/Show.S01E01.mkv").exists()
    assert (settings.imports_root / "Show/S01/Show.S01E01.chs.srt").exists()
    assert not (settings.imports_root / "Show/S01/old1.mkv").exists()
    assert database.get_release_file(video_id)["relative_path"].endswith(
        "Show.S01E01.mkv"
    )

    rolled = rollback_latest(settings, database, project_id)
    assert rolled["rolled_back"] == 2
    assert (settings.imports_root / "Show/S01/old1.mkv").exists()
    assert database.get_release_file(video_id)["relative_path"].endswith("old1.mkv")


def test_preview_to_releases_preserves_relative_structure(settings) -> None:
    database, project_id = make_project(settings)
    add_file(settings, database, project_id, "Show/S01/old1.mkv")
    preview = create_preview(
        settings,
        database,
        project_id,
        template="{title}.S{season:02d}E{episode:02d}",
        target_mode="releases",
    )
    operation = preview.operations[0]
    assert operation["target_root_key"] == "releases"
    assert operation["target_relative_path"] == "TV/Show/S01/Show.S01E01.mkv"
    apply_preview(settings, database, preview, task_id=add_task(database, project_id))
    assert (settings.releases_root / "TV/Show/S01/Show.S01E01.mkv").exists()
    assert not (settings.imports_root / "Show/S01/old1.mkv").exists()


def test_preview_and_apply_reject_collisions_and_state_changes(settings) -> None:
    database, project_id = make_project(settings)
    add_file(settings, database, project_id, "Show/S01/one.mkv", episode=1)
    add_file(settings, database, project_id, "Show/S01/two.mkv", episode=1)
    with pytest.raises(NamingError, match="相同目标"):
        create_preview(
            settings,
            database,
            project_id,
            template="{title}.S{season:02d}E{episode:02d}",
            target_mode="in_place",
        )

    project2 = database.create_release_project(
        title="Show",
        media_type="series",
        source_type="local",
        year=2026,
        season=1,
        metadata={"root_key": "imports", "source_path": "Show"},
        download_dir="Show",
    )
    add_file(settings, database, project2, "Show/S01/old.mkv")
    preview = create_preview(
        settings,
        database,
        project2,
        template="{title}.S{season:02d}E{episode:02d}",
    )
    source = settings.imports_root / "Show/S01/old.mkv"
    os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 1))
    with pytest.raises(NamingError, match="发生变化"):
        apply_preview(settings, database, preview, task_id=add_task(database, project2))
    assert source.exists()


def test_bdmv_renames_only_the_directory_and_rollback_checks_target(settings) -> None:
    database, project_id = make_project(settings)
    add_file(
        settings,
        database,
        project_id,
        "Show/BDMV",
        role="bdmv",
        season=1,
        episode=None,
    )
    preview = create_preview(
        settings,
        database,
        project_id,
        template="{title}.{year}",
        target_mode="in_place",
    )
    assert preview.operations[0]["target_relative_path"] == "Show/Show.2026"
    apply_preview(settings, database, preview, task_id=add_task(database, project_id))
    assert (settings.imports_root / "Show/Show.2026/STREAM/00001.m2ts").exists()
    target = settings.imports_root / "Show/Show.2026/STREAM/00001.m2ts"
    target.write_bytes(b"changed-after-rename")
    with pytest.raises(NamingError, match="发生变化"):
        rollback_latest(settings, database, project_id)


def test_internal_symlink_file_is_renamed_without_moving_its_target(settings) -> None:
    database, project_id = make_project(settings)
    directory = settings.imports_root / "Show" / "S01"
    directory.mkdir(parents=True)
    real = directory / "real.mkv"
    real.write_bytes(b"real-video")
    link = directory / "link.mkv"
    link.symlink_to(real)
    size, mtime_ns = path_state(link)
    database.upsert_release_file(
        project_id=project_id,
        root_key="imports",
        relative_path="Show/S01/link.mkv",
        file_role="video",
        size_bytes=size,
        mtime_ns=mtime_ns,
        season=1,
        episode=1,
        title="Show",
        language=None,
    )
    preview = create_preview(
        settings,
        database,
        project_id,
        template="{title}.S{season:02d}E{episode:02d}",
    )
    apply_preview(settings, database, preview, task_id=add_task(database, project_id))
    moved_link = directory / "Show.S01E01.mkv"
    assert real.exists() and real.read_bytes() == b"real-video"
    assert moved_link.is_symlink()
    assert moved_link.read_bytes() == b"real-video"


def test_single_file_project_updates_and_restores_source_path(settings) -> None:
    database, project_id = make_project(settings)
    database.update_release_project(
        project_id,
        download_dir="Show/single.iso",
        metadata_json=json.dumps(
            {"root_key": "imports", "source_path": "Show/single.iso"}
        ),
    )
    add_file(
        settings,
        database,
        project_id,
        "Show/single.iso",
        role="iso",
        season=None,
        episode=None,
    )
    preview = create_preview(settings, database, project_id, template="{title}")
    apply_preview(settings, database, preview, task_id=add_task(database, project_id))
    project = database.get_release_project(project_id)
    assert project["download_dir"] == "Show/Show.iso"
    assert project["metadata"]["source_path"] == "Show/Show.iso"
    rollback_latest(settings, database, project_id)
    project = database.get_release_project(project_id)
    assert project["download_dir"] == "Show/single.iso"
    assert project["metadata"]["source_path"] == "Show/single.iso"
