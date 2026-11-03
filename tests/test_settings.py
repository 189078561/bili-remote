from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bili_remote.web import create_app


def login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]


def test_settings_persist_and_apply_to_new_jobs_only(settings) -> None:
    settings.ensure_directories()
    media_dir = settings.imports_root / "Media"
    media_dir.mkdir()

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        initial = client.get("/api/settings")
        assert initial.status_code == 200, initial.text
        initial_payload = initial.json()
        assert {item["key"] for item in initial_payload["directories"]} == {
            "downloads",
            "torrents",
            "images",
            "release_info",
        }
        assert len(initial_payload["mount_roots"]) == 8
        text = initial.text.lower()
        assert "cookie" not in text
        assert "token" not in text
        assert "password" not in text

        first = client.post(
            "/api/jobs",
            json={
                "url": "https://www.bilibili.com/video/BV1SETTINGS1",
                "output_template": "Original/%(title)s.%(ext)s",
                "output_dir": "Original",
            },
        )
        assert first.status_code == 200, first.text
        first_id = first.json()["id"]

        saved = client.put(
            "/api/settings",
            json={
                "single_output_template": "Changed/%(title)s.%(ext)s",
                "playlist_output_template": "Changed/S%(playlist_index)02d.%(ext)s",
                "download_default_output_dir": "Default",
                "directories": {
                    "downloads": {
                        "mount_key": "imports",
                        "relative_path": "Media",
                    }
                },
                "rename_default_season": "1",
                "rename_default_source": "WEBRip",
                "rename_custom_sources": ["Custom", "custom", " WEBRip ", ""],
            },
        )
        assert saved.status_code == 200, saved.text
        payload = saved.json()
        download_setting = next(
            item for item in payload["directories"] if item["key"] == "downloads"
        )
        assert download_setting["mount_key"] == "imports"
        assert download_setting["relative_path"] == "Media"
        assert download_setting["path"] == str(media_dir)
        assert payload["rename_default_season"] == "01"
        assert payload["rename_custom_sources"] == ["Custom", "WEBRip"]

        second = client.post(
            "/api/jobs",
            json={"url": "https://www.bilibili.com/video/BV1SETTINGS2"},
        )
        assert second.status_code == 200, second.text
        second_id = second.json()["id"]

        first_job = app.state.services.database.get_job(first_id)
        second_job = app.state.services.database.get_job(second_id)
        assert first_job is not None and second_job is not None
        assert first_job["output_template"] == "Original/%(title)s.%(ext)s"
        assert Path(first_job["output_dir"]) == settings.download_root / "Original"
        assert second_job["output_template"] == "Changed/%(title)s.%(ext)s"
        assert Path(second_job["output_dir"]) == media_dir / "Default"

    restarted = create_app(settings)
    with TestClient(restarted) as client:
        login(client)
        persisted = client.get("/api/settings").json()
        assert persisted["single_output_template"] == "Changed/%(title)s.%(ext)s"
        assert persisted["download_default_output_dir"] == "Default"
        download_setting = next(
            item for item in persisted["directories"] if item["key"] == "downloads"
        )
        assert download_setting["mount_key"] == "imports"
        assert download_setting["relative_path"] == "Media"
        assert persisted["rename_default_source"] == "WEBRip"
        assert persisted["rename_custom_sources"] == ["Custom", "WEBRip"]


def test_settings_reject_invalid_directories_seasons_and_sources(settings) -> None:
    settings.ensure_directories()
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        missing = client.put(
            "/api/settings",
            json={
                "directories": {
                    "downloads": {"mount_key": "imports", "relative_path": "missing"}
                }
            },
        )
        assert missing.status_code == 422
        traversal = client.put(
            "/api/settings",
            json={
                "directories": {
                    "downloads": {"mount_key": "downloads", "relative_path": "../data"}
                }
            },
        )
        assert traversal.status_code == 422
        reserved = client.put(
            "/api/settings",
            json={
                "directories": {
                    "downloads": {"mount_key": "data", "relative_path": "runtime"}
                }
            },
        )
        assert reserved.status_code == 422
        bad_season = client.put(
            "/api/settings", json={"rename_default_season": "first"}
        )
        assert bad_season.status_code == 422
        too_many = client.put(
            "/api/settings",
            json={"rename_custom_sources": [f"custom-{index}" for index in range(51)]},
        )
        assert too_many.status_code == 422


def test_naming_profile_crud_and_default_constraints(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        initial = client.get("/api/naming/profiles").json()
        series = [item for item in initial if item["media_type"] == "series"]
        assert len(series) == 1
        original_id = series[0]["id"]

        created = client.post(
            "/api/naming/profiles",
            json={
                "name": "自定义剧集",
                "media_type": "series",
                "template": "{title}.S{season:02d}E{episode:02d}.{ext}",
                "is_default": False,
            },
        )
        assert created.status_code == 200, created.text
        created_id = created.json()["id"]

        duplicate = client.post(
            "/api/naming/profiles",
            json={
                "name": "自定义剧集",
                "media_type": "movie",
                "template": "{title}.{ext}",
            },
        )
        assert duplicate.status_code == 409

        updated = client.patch(
            f"/api/naming/profiles/{created_id}",
            json={
                "name": "自定义剧集 2",
                "template": "{title}.E{episode:02d}.{ext}",
                "is_default": True,
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["is_default"] == 1

        profiles = client.get("/api/naming/profiles").json()
        defaults = [
            item
            for item in profiles
            if item["media_type"] == "series" and item["is_default"]
        ]
        assert [item["id"] for item in defaults] == [created_id]

        cancelled_default = client.patch(
            f"/api/naming/profiles/{created_id}",
            json={
                "name": "自定义剧集 2",
                "template": "{title}.E{episode:02d}.{ext}",
                "is_default": False,
            },
        )
        assert cancelled_default.status_code == 200, cancelled_default.text
        assert cancelled_default.json()["is_default"] == 0
        profiles = client.get("/api/naming/profiles").json()
        series_defaults = [
            item
            for item in profiles
            if item["media_type"] == "series" and item["is_default"]
        ]
        assert [item["id"] for item in series_defaults] == [original_id]

        restored_default = client.patch(
            f"/api/naming/profiles/{created_id}",
            json={
                "name": "自定义剧集 2",
                "template": "{title}.E{episode:02d}.{ext}",
                "is_default": True,
            },
        )
        assert restored_default.status_code == 200, restored_default.text

        deleted_default = client.delete(f"/api/naming/profiles/{created_id}")
        assert deleted_default.status_code == 200, deleted_default.text
        profiles = client.get("/api/naming/profiles").json()
        remaining_series = [
            item for item in profiles if item["media_type"] == "series"
        ]
        assert len(remaining_series) == 1
        assert remaining_series[0]["id"] == original_id
        assert remaining_series[0]["is_default"] == 1

        last_delete = client.delete(f"/api/naming/profiles/{original_id}")
        assert last_delete.status_code == 409
        still_exists = client.get("/api/naming/profiles").json()
        assert any(item["id"] == original_id for item in still_exists)
