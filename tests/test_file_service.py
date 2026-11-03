from __future__ import annotations

import base64
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import bili_remote.file_service as file_service_module
from bili_remote.database import Database
from bili_remote.file_service import FileConflictError, FileService, FileServiceError
from bili_remote.web import create_app


def login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]


def wait_task(client: TestClient, task_id: int, timeout: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout
    task: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200, response.text
        task = response.json()
        if task["status"] in {"completed", "failed", "cancelled"}:
            return task
        time.sleep(0.04)
    raise AssertionError(f"task did not finish: {task}")


def preview_operation(
    client: TestClient,
    operation: str,
    sources: list[dict[str, str]],
    target: dict[str, str] | None = None,
    policy: str = "abort",
) -> dict:
    response = client.post(
        "/api/files/operations/preview",
        json={
            "operation": operation,
            "sources": sources,
            "target": target,
            "conflict_policy": policy,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_unified_browse_roots_sorting_focus_and_legacy_compatibility(
    settings, tmp_path: Path
) -> None:
    settings.ensure_directories()
    for name in ("10.mkv", "2.mkv", "1.mkv"):
        (settings.download_root / name).write_bytes(b"video")
    (settings.download_root / "a").mkdir()
    (settings.download_root / "B").mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    os.symlink(outside, settings.download_root / "escape.txt")

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        roots = client.get("/api/files/roots").json()
        assert {item["key"] for item in roots} == {
            "downloads",
            "torrents",
            "images",
            "release_info",
        }
        mounts = client.get("/api/files/roots", params={"scope": "mounts"}).json()
        assert {item["key"] for item in mounts} == {
            "data",
            "downloads",
            "imports",
            "staging",
            "releases",
            "torrents",
            "images",
            "release_info",
        }
        assert all("path" in item and "writable" in item for item in mounts)

        first_page = client.get(
            "/api/files/browse",
            params={"root_key": "downloads", "page": 1, "page_size": 2},
        )
        assert first_page.status_code == 200, first_page.text
        data = first_page.json()
        assert [item["name"] for item in data["items"]] == ["a", "B"]
        assert data["total"] == 6
        assert data["has_more"] is True

        focused = client.get(
            "/api/files/browse",
            params={
                "root_key": "downloads",
                "page": 1,
                "page_size": 2,
                "focus": "2.mkv",
            },
        ).json()
        assert focused["focus_page"] == 2
        assert focused["page"] == 2
        assert "2.mkv" in [item["name"] for item in focused["items"]]

        sorted_files = client.get(
            "/api/files/browse",
            params={
                "root_key": "downloads",
                "sort": "size",
                "direction": "desc",
                "page_size": 20,
            },
        ).json()
        assert sorted_files["items"][0]["is_dir"] is True

        symlink = next(
            item
            for item in sorted_files["items"]
            if item["name"] == "escape.txt"
        )
        assert symlink["is_symlink"] is True
        assert symlink["allowed"] is False
        assert symlink["capabilities"]["select"] is False
        escaped = client.get(
            "/api/files/text",
            params={"root_key": "downloads", "path": "escape.txt"},
        )
        assert escaped.status_code == 422

        traversal = client.get(
            "/api/files/browse",
            params={"root_key": "downloads", "path": "../data"},
        )
        assert traversal.status_code == 422

        legacy = client.get(
            "/api/workspace/browse",
            params={"root_key": "downloads", "path": ""},
        )
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["items"][0]["is_dir"] is True


def test_text_encoding_save_conflict_binary_and_image_security(
    settings, tmp_path: Path
) -> None:
    settings.ensure_directories()
    utf8 = settings.images_root.parent / "downloads" / "notes.txt"
    utf8.write_bytes(b"\xef\xbb\xbf\xe7\xac\xac\xe4\xb8\x80\xe8\xa1\x8c\r\nsecond\r\n")
    gb = settings.download_root / "legacy.nfo"
    gb.write_bytes("中文说明".encode("gb18030"))
    binary = settings.download_root / "binary.txt"
    binary.write_bytes(b"hello\x00world")
    large = settings.download_root / "large.log"
    large.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    image_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZJ8sAAAAASUVORK5CYII="
    )
    (settings.images_root / "pixel.png").write_bytes(image_bytes)
    (settings.download_root / "fake.png").write_text("<html>no</html>", encoding="utf-8")
    (settings.download_root / "vector.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8"
    )

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        document = client.get(
            "/api/files/text",
            params={"root_key": "downloads", "path": "notes.txt"},
        )
        assert document.status_code == 200, document.text
        payload = document.json()
        assert payload["encoding_label"] == "UTF-8 BOM"
        assert payload["newline"] == "CRLF"
        assert payload["content"].startswith("第一行")

        saved = client.put(
            "/api/files/text",
            json={
                "root_key": "downloads",
                "relative_path": "notes.txt",
                "content": "一年\n二行\n",
                "fingerprint": payload["fingerprint"],
            },
        )
        assert saved.status_code == 200, saved.text
        raw = utf8.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" in raw
        assert raw.decode("utf-8-sig").splitlines() == ["一年", "二行"]

        stale = client.put(
            "/api/files/text",
            json={
                "root_key": "downloads",
                "relative_path": "notes.txt",
                "content": "stale",
                "fingerprint": payload["fingerprint"],
            },
        )
        assert stale.status_code == 409
        assert "一年" in utf8.read_text(encoding="utf-8-sig")

        legacy = client.get(
            "/api/files/text",
            params={"root_key": "downloads", "path": "legacy.nfo"},
        ).json()
        assert legacy["encoding"] == "gb18030"
        assert legacy["content"] == "中文说明"

        for path in ("binary.txt", "large.log"):
            response = client.get(
                "/api/files/text",
                params={"root_key": "downloads", "path": path},
            )
            assert response.status_code == 422

        image = client.get(
            "/api/files/image",
            params={"root_key": "images", "path": "pixel.png"},
        )
        assert image.status_code == 200, image.text
        assert image.headers["content-type"] == "image/png"
        assert image.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in image.headers["content-security-policy"]

        for root_key, path in (
            ("downloads", "fake.png"),
            ("downloads", "vector.svg"),
        ):
            rejected = client.get(
                "/api/files/image",
                params={"root_key": root_key, "path": path},
            )
            assert rejected.status_code == 422


def test_ordinary_rename_conflicts_protected_roots_and_active_jobs(settings) -> None:
    settings.ensure_directories()
    (settings.download_root / "old.txt").write_text("one", encoding="utf-8")
    (settings.download_root / "existing.txt").write_text("two", encoding="utf-8")
    (settings.download_root / "folder").mkdir()
    (settings.download_root / "active").mkdir()

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        renamed = client.post(
            "/api/files/rename",
            json={
                "root_key": "downloads",
                "relative_path": "old.txt",
                "new_name": "new.txt",
            },
        )
        assert renamed.status_code == 200, renamed.text
        assert (settings.download_root / "new.txt").exists()
        assert not (settings.download_root / "old.txt").exists()

        conflict = client.post(
            "/api/files/rename",
            json={
                "root_key": "downloads",
                "relative_path": "new.txt",
                "new_name": "existing.txt",
            },
        )
        assert conflict.status_code == 409

        invalid = client.post(
            "/api/files/rename",
            json={
                "root_key": "downloads",
                "relative_path": "new.txt",
                "new_name": "bad/name.txt",
            },
        )
        assert invalid.status_code == 422

        folder = client.post(
            "/api/files/rename",
            json={
                "root_key": "downloads",
                "relative_path": "folder",
                "new_name": "folder2",
            },
        )
        assert folder.status_code == 200, folder.text
        assert (settings.download_root / "folder2").is_dir()

        app.state.services.database.create_job(
            source_url="https://www.bilibili.com/video/BV1ACTIVE",
            kind="manual",
            output_template="Bilibili/%(title)s.%(ext)s",
            output_dir=str(settings.download_root / "active"),
        )
        protected = client.post(
            "/api/files/rename",
            json={
                "root_key": "downloads",
                "relative_path": "active",
                "new_name": "renamed-active",
            },
        )
        assert protected.status_code == 409

        app.state.services.database.update_settings(
            {"downloads_root_mount": "data", "downloads_root_relative": ""}
        )
        roots = client.get("/api/files/roots").json()
        data_root = next(item for item in roots if item["key"] == "downloads")
        assert data_root["path"] == str(settings.data_dir)
        listing = client.get(
            "/api/files/browse", params={"root_key": "downloads", "path": ""}
        )
        assert listing.status_code == 200, listing.text
        reserved = next(
            (item for item in listing.json()["items"] if item["name"] == "runtime"),
            None,
        )
        if reserved:
            assert reserved["allowed"] is False
        with pytest.raises(FileServiceError):
            app.state.services.files.resolve(
                "downloads", "bili-remote.sqlite3", must_exist=True
            )


def test_active_download_protects_only_writing_files_and_legacy_ready_files_are_mutable(
    settings,
) -> None:
    settings.ensure_directories()
    active = settings.download_root / "active"
    active.mkdir()
    (active / "finished.mkv").write_bytes(b"finished")
    (active / "writing.mkv.part").write_bytes(b"partial")
    (active / "writing.mkv.part.aria2").write_text("control", encoding="utf-8")
    default_only = settings.download_root / "default-only"
    default_only.mkdir()
    (default_only / "legacy.mkv").write_bytes(b"legacy")

    database = Database(settings.database_path)
    database.initialize()
    service = FileService(settings, database)
    job_id = database.create_job(
        source_url="https://www.bilibili.com/video/BV1ACTIVE",
        kind="manual",
        output_template="active/%(title)s.%(ext)s",
        output_dir=str(active),
    )
    database.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    database.update_settings({"download_default_output_dir": "default-only"})
    project_id = database.create_release_project(
        title="Legacy",
        media_type="series",
        source_type="local",
    )
    legacy = default_only / "legacy.mkv"
    database.upsert_release_file(
        project_id=project_id,
        root_key="downloads",
        relative_path="default-only/legacy.mkv",
        file_role="video",
        size_bytes=legacy.stat().st_size,
        mtime_ns=legacy.stat().st_mtime_ns,
        season=1,
        episode=1,
        title="Legacy",
        language=None,
    )

    parent = service.browse(root_key="downloads", relative_path="")
    active_item = next(item for item in parent["items"] if item["name"] == "active")
    assert active_item["capabilities"]["rename"] is False
    assert active_item["protection_reason"]

    listing = service.browse(root_key="downloads", relative_path="active")
    items = {item["name"]: item for item in listing["items"]}
    assert items["finished.mkv"]["capabilities"]["rename"] is True
    assert items["finished.mkv"]["capabilities"]["delete"] is True
    assert items["writing.mkv.part"]["capabilities"]["rename"] is False
    assert items["writing.mkv.part"]["capabilities"]["delete"] is False
    assert "写入" in items["writing.mkv.part"]["protection_reason"]

    service.rename(
        root_key="downloads",
        relative_path="active/finished.mkv",
        new_name="finished-renamed.mkv",
    )
    assert (active / "finished-renamed.mkv").exists()

    with pytest.raises(FileConflictError):
        service.rename(
            root_key="downloads",
            relative_path="active/writing.mkv.part",
            new_name="renamed.part",
        )

    delete_preview = service.preview_operations(
        operation="delete",
        sources=[
            {"root_key": "downloads", "relative_path": "active/writing.mkv.part"}
        ],
        target=None,
    )
    assert delete_preview["summary"]["blocked"] == 1, delete_preview

    legacy_listing = service.browse(
        root_key="downloads", relative_path="default-only"
    )
    legacy_item = legacy_listing["items"][0]
    assert legacy_item["capabilities"]["rename"] is True
    assert legacy_item["capabilities"]["delete"] is True

    active_task_dir = settings.download_root / "active-task"
    active_task_dir.mkdir()
    (active_task_dir / "locked.mkv").write_bytes(b"locked")
    active_project = database.create_release_project(
        title="Active task",
        media_type="series",
        source_type="local",
    )
    database.create_processing_task(
        project_id=active_project,
        task_type="media_check",
        input_data={
            "root_key": "downloads",
            "relative_path": "active-task/locked.mkv",
        },
        log_path=str(settings.logs_dir / "active-task.log"),
        idempotency_key="test-active-media-check",
    )
    active_task_listing = service.browse(
        root_key="downloads", relative_path="active-task"
    )
    active_task_item = active_task_listing["items"][0]
    assert active_task_item["capabilities"]["rename"] is False
    assert active_task_item["protection_reason"]


def test_browse_dependency_queries_do_not_grow_with_file_count(
    settings, monkeypatch
) -> None:
    settings.ensure_directories()
    large = settings.download_root / "large"
    large.mkdir()
    for index in range(120):
        (large / f"{index:03d}.mkv").write_bytes(b"x")

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        database = app.state.services.files.database
        calls: list[str] = []
        original = database.fetch_all

        def counted(query: str, params: tuple = ()) -> list[dict]:
            calls.append(query)
            return original(query, params)

        monkeypatch.setattr(database, "fetch_all", counted)
        response = client.get(
            "/api/files/browse",
            params={
                "root_key": "downloads",
                "path": "large",
                "page_size": 100,
            },
        )
        assert response.status_code == 200, response.text
        assert len(response.json()["items"]) == 100
        assert len(calls) <= 8
        assert sum(" FROM jobs " in query for query in calls) == 1
        assert sum("processing_tasks" in query for query in calls) <= 2


def test_copy_move_delete_conflicts_symlinks_and_task_filter(settings) -> None:
    settings.ensure_directories()
    source = settings.download_root / "source.mkv"
    source.write_bytes(b"source-video")
    nested = settings.download_root / "series"
    nested.mkdir()
    (nested / "episode.mkv").write_bytes(b"episode")
    (settings.download_root / "dest").mkdir()
    (settings.download_root / "moved").mkdir()
    (settings.download_root / "dest" / "source.mkv").write_bytes(b"old-target")
    (settings.images_root / "source.mkv").write_bytes(b"old-image")

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        source_ref = {"root_key": "downloads", "relative_path": "source.mkv"}
        target = {"root_key": "downloads", "relative_path": "dest"}

        abort_plan = preview_operation(client, "copy", [source_ref], target)
        assert abort_plan["summary"]["conflict"] == 1
        rejected = client.post(
            "/api/files/operations/apply",
            json={"token": abort_plan["token"], "confirm_overwrite": False},
        )
        assert rejected.status_code == 409

        skip_plan = preview_operation(client, "copy", [source_ref], target, "skip")
        assert skip_plan["items"][0]["status"] == "skipped"
        skip_task = client.post(
            "/api/files/operations/apply",
            json={"token": skip_plan["token"], "confirm_overwrite": False},
        ).json()["task_id"]
        assert wait_task(client, skip_task)["status"] == "completed"
        assert (settings.download_root / "dest" / "source.mkv").read_bytes() == b"old-target"

        overwrite_plan = preview_operation(
            client, "copy", [source_ref], target, "overwrite"
        )
        assert overwrite_plan["requires_overwrite_confirmation"] is True
        missing_confirmation = client.post(
            "/api/files/operations/apply",
            json={"token": overwrite_plan["token"], "confirm_overwrite": False},
        )
        assert missing_confirmation.status_code == 409
        overwrite_task = client.post(
            "/api/files/operations/apply",
            json={"token": overwrite_plan["token"], "confirm_overwrite": True},
        ).json()["task_id"]
        reused = client.post(
            "/api/files/operations/apply",
            json={"token": overwrite_plan["token"], "confirm_overwrite": True},
        )
        assert reused.status_code == 409
        assert wait_task(client, overwrite_task)["status"] == "completed"
        assert (settings.download_root / "dest" / "source.mkv").read_bytes() == b"source-video"
        assert source.exists()

        inside_plan = preview_operation(
            client,
            "copy",
            [{"root_key": "downloads", "relative_path": "series"}],
            {"root_key": "downloads", "relative_path": "series"},
        )
        assert inside_plan["summary"]["blocked"] == 1

        (nested / "link").symlink_to(settings.download_root / "source.mkv")
        symlink_plan = preview_operation(
            client,
            "copy",
            [{"root_key": "downloads", "relative_path": "series"}],
            target,
        )
        assert symlink_plan["items"][0]["status"] == "blocked"
        (nested / "link").unlink()

        copy_series = preview_operation(
            client,
            "copy",
            [{"root_key": "downloads", "relative_path": "series"}],
            {"root_key": "images", "relative_path": ""},
        )
        copy_series_task = client.post(
            "/api/files/operations/apply",
            json={"token": copy_series["token"], "confirm_overwrite": False},
        ).json()["task_id"]
        assert wait_task(client, copy_series_task)["status"] == "completed"
        assert (settings.images_root / "series" / "episode.mkv").exists()

        move_series = preview_operation(
            client,
            "move",
            [{"root_key": "images", "relative_path": "series"}],
            {"root_key": "downloads", "relative_path": "moved"},
        )
        move_series_task = client.post(
            "/api/files/operations/apply",
            json={"token": move_series["token"], "confirm_overwrite": False},
        ).json()["task_id"]
        assert wait_task(client, move_series_task)["status"] == "completed"
        assert not (settings.images_root / "series").exists()
        assert (settings.download_root / "moved" / "series" / "episode.mkv").exists()

        move_plan = preview_operation(
            client,
            "move",
            [{"root_key": "downloads", "relative_path": "source.mkv"}],
            {"root_key": "images", "relative_path": ""},
            "overwrite",
        )
        assert move_plan["items"][0]["will_overwrite"] is True
        moved_task = client.post(
            "/api/files/operations/apply",
            json={"token": move_plan["token"], "confirm_overwrite": True},
        ).json()["task_id"]
        moved = wait_task(client, moved_task)
        assert moved["status"] == "completed"
        assert not source.exists()
        assert (settings.images_root / "source.mkv").read_bytes() == b"source-video"

        delete_plan = preview_operation(
            client,
            "delete",
            [{"root_key": "downloads", "relative_path": "series"}],
        )
        delete_task = client.post(
            "/api/files/operations/apply",
            json={"token": delete_plan["token"], "confirm_overwrite": False},
        ).json()["task_id"]
        assert wait_task(client, delete_task)["status"] == "completed"
        assert not nested.exists()

        tasks = client.get("/api/tasks", params={"task_type": "file_operation"}).json()
        assert len(tasks) >= 6
        assert all(task["task_type"] == "file_operation" for task in tasks)

        root_plan = preview_operation(
            client,
            "delete",
            [{"root_key": "downloads", "relative_path": ""}],
        )
        assert root_plan["summary"]["blocked"] == 1


def test_file_operation_rechecks_source_fingerprint(settings) -> None:
    settings.ensure_directories()
    source = settings.download_root / "changing.mkv"
    source.write_bytes(b"one")
    (settings.download_root / "dest").mkdir()
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        plan = preview_operation(
            client,
            "copy",
            [{"root_key": "downloads", "relative_path": "changing.mkv"}],
            {"root_key": "downloads", "relative_path": "dest"},
        )
        source.write_bytes(b"two-changed")
        task_id = client.post(
            "/api/files/operations/apply",
            json={"token": plan["token"], "confirm_overwrite": False},
        ).json()["task_id"]
        task = wait_task(client, task_id)
        assert task["status"] == "failed"
        assert task["result"]["items"][0]["status"] == "failed"
        assert not (settings.download_root / "dest" / "changing.mkv").exists()


def test_file_service_operation_conflict_is_specific(settings) -> None:
    settings.ensure_directories()
    (settings.download_root / "a.mkv").write_bytes(b"a")
    (settings.download_root / "dest").mkdir()
    (settings.download_root / "dest" / "a.mkv").mkdir()
    app = create_app(settings)
    with TestClient(app):
        service = app.state.services.files
        plan = service.preview_operations(
            operation="copy",
            sources=[{"root_key": "downloads", "relative_path": "a.mkv"}],
            target={"root_key": "downloads", "relative_path": "dest"},
            conflict_policy="overwrite",
        )
        assert plan["items"][0]["status"] == "blocked"
        assert any("类型" in reason for reason in plan["items"][0]["reasons"])
        with pytest.raises(FileConflictError):
            service.prepare_operation(token=plan["token"], confirm_overwrite=True)


def test_operation_blocks_casefold_collisions_before_and_during_execution(settings) -> None:
    settings.ensure_directories()
    source = settings.download_root / "Episode.MKV"
    source.write_bytes(b"source")
    target = settings.download_root / "dest"
    target.mkdir()
    (target / "episode.mkv").write_bytes(b"case-conflict")
    app = create_app(settings)
    with TestClient(app):
        service = app.state.services.files
        blocked = service.preview_operations(
            operation="copy",
            sources=[{"root_key": "downloads", "relative_path": "Episode.MKV"}],
            target={"root_key": "downloads", "relative_path": "dest"},
            conflict_policy="overwrite",
        )
        assert blocked["items"][0]["status"] == "blocked"
        assert any("大小写" in reason for reason in blocked["items"][0]["reasons"])

        (target / "episode.mkv").unlink()
        plan = service.preview_operations(
            operation="copy",
            sources=[{"root_key": "downloads", "relative_path": "Episode.MKV"}],
            target={"root_key": "downloads", "relative_path": "dest"},
            conflict_policy="abort",
        )
        assert plan["items"][0]["status"] == "ready"
        (target / "episode.mkv").write_bytes(b"appeared-after-preview")
        result = service.execute_operation(
            service.prepare_operation(token=plan["token"], confirm_overwrite=False),
            task_id=0,
            log=lambda _: None,
            should_cancel=lambda: False,
        )
        assert result["status"] == "failed"
        assert result["items"][0]["status"] == "failed"
        assert not (target / "Episode.MKV").exists()
        assert (target / "episode.mkv").read_bytes() == b"appeared-after-preview"


def test_cross_filesystem_move_and_partial_failure(settings, monkeypatch) -> None:
    settings.ensure_directories()
    source = settings.download_root / "cross.mkv"
    source.write_bytes(b"cross-filesystem")
    (settings.download_root / "dest").mkdir()
    app = create_app(settings)
    with TestClient(app):
        service = app.state.services.files
        plan = service.preview_operations(
            operation="move",
            sources=[{"root_key": "downloads", "relative_path": "cross.mkv"}],
            target={"root_key": "downloads", "relative_path": "dest"},
            conflict_policy="abort",
        )
        real_rename = file_service_module.rename_no_replace

        def simulated_exdev(current: Path, target: Path) -> None:
            if current == source and target == settings.download_root / "dest" / "cross.mkv":
                raise OSError(18, "Cross-device link")
            return real_rename(current, target)

        monkeypatch.setattr(file_service_module, "rename_no_replace", simulated_exdev)
        result = service.execute_operation(
            service.prepare_operation(token=plan["token"], confirm_overwrite=False),
            task_id=0,
            log=lambda _: None,
            should_cancel=lambda: False,
        )
        assert result["status"] == "completed"
        assert not source.exists()
        assert (settings.download_root / "dest" / "cross.mkv").read_bytes() == b"cross-filesystem"

        first = settings.download_root / "first.txt"
        second = settings.download_root / "second.txt"
        first.write_text("first", encoding="utf-8")
        second.write_text("second", encoding="utf-8")
        partial_plan = service.preview_operations(
            operation="copy",
            sources=[
                {"root_key": "downloads", "relative_path": "first.txt"},
                {"root_key": "downloads", "relative_path": "second.txt"},
            ],
            target={"root_key": "downloads", "relative_path": "dest"},
            conflict_policy="abort",
        )
        second.unlink()
        partial = service.execute_operation(
            service.prepare_operation(token=partial_plan["token"], confirm_overwrite=False),
            task_id=0,
            log=lambda _: None,
            should_cancel=lambda: False,
        )
        assert partial["status"] == "failed"
        assert partial["succeeded"] == 1
        assert partial["failed"] == 1
        assert partial["partial"] is True
        assert any(item["status"] == "failed" for item in partial["items"])
