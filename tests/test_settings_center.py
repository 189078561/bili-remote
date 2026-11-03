from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bili_remote.settings_registry import (
    DEFINITIONS_BY_KEY,
    GROUPS,
    RUNTIME_DEFINITIONS,
    SOURCE_DEFAULTS,
    all_definitions,
)
from bili_remote.web import create_app


def login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]


def test_settings_registry_has_unique_complete_definitions(settings) -> None:
    definitions = all_definitions()
    keys = [definition.key for definition in definitions]
    assert len(keys) == len(set(keys))
    assert [group.key for group in GROUPS] == [
        "general",
        "files",
        "downloads",
        "rename",
        "collage",
        "image_host",
        "torrents",
        "release",
        "maintenance",
    ]
    assert set(SOURCE_DEFAULTS) == {"WEB-DL", "WEBRip", "BluRay", "HDTV", "DVD"}
    allowed_controls = {
        "text",
        "template",
        "number",
        "switch",
        "select",
        "list",
        "directory",
        "relative-directory",
        "readonly",
        "password",
    }
    for definition in definitions:
        assert definition.label
        assert definition.control in allowed_controls
        if definition.control == "select":
            assert definition.choices
        if definition.control == "number":
            assert definition.minimum is not None
            assert definition.maximum is not None
            assert definition.minimum <= definition.maximum
        if definition.runtime:
            assert definition.runtime_attr
            assert hasattr(settings, definition.runtime_attr)
        if definition.requires_idle:
            assert definition.runtime

    assert "log_retention_days" in RUNTIME_DEFINITIONS
    assert RUNTIME_DEFINITIONS["log_retention_days"].requires_idle is False
    assert "app_secret" not in DEFINITIONS_BY_KEY
    assert "admin_password" not in DEFINITIONS_BY_KEY


def test_settings_page_is_independent_authenticated_route(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        anonymous = client.get("/settings")
        assert anonymous.status_code == 200
        assert "登录" in anonymous.text
        assert 'id="settings-view"' not in anonymous.text

        login(client)
        authenticated = client.get("/settings")
        assert authenticated.status_code == 200, authenticated.text
        assert 'id="settings-view"' in authenticated.text
        assert 'data-view="files"' in authenticated.text
        assert 'data-view="download"' in authenticated.text
        assert 'data-view="settings"' not in authenticated.text
        assert 'id="settings-entry"' in authenticated.text


def test_settings_schema_and_responses_do_not_leak_credentials(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        schema_response = client.get("/api/settings/schema")
        settings_response = client.get("/api/settings")
        assert schema_response.status_code == 200, schema_response.text
        assert settings_response.status_code == 200, settings_response.text

        schema = schema_response.json()
        fields = {
            field["key"]: field
            for group in schema["groups"]
            for section in group["sections"]
            for field in section["fields"]
        }
        assert fields["file_browser_page_size"]["control"] == "number"
        assert fields["directories.downloads"]["control"] == "directory"
        assert fields["collage_output_relative_path"]["control"] == "relative-directory"
        assert fields["collage_output_relative_path"]["directory_root"] == "images"
        assert fields["aria2_connections"]["requires_idle"] is True
        assert fields["log_retention_days"]["requires_idle"] is False
        assert fields["mount_paths"]["read_only"] is True
        assert schema["source_defaults"] == list(SOURCE_DEFAULTS)

        payload = settings_response.json()
        assert set(payload["values"]) >= {
            "app_default_view",
            "file_browser_page_size",
            "aria2_connections",
            "rename_default_season",
            "collage_default_layout_mode",
            "collage_default_columns",
            "collage_default_total",
            "collage_default_width",
            "collage_output_relative_path",
            "log_retention_days",
            "mount_paths",
        }
        for response in (schema_response, settings_response):
            lowered = response.text.lower()
            assert "app_secret" not in lowered
            assert "admin_password" not in lowered
            assert "cookie" not in lowered
            assert "csrf" not in lowered
            assert "bearer" not in lowered


def test_settings_reject_unknown_or_invalid_values_without_partial_write(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        before = client.get("/api/settings").json()["values"]

        unknown = client.put(
            "/api/settings",
            json={
                "values": {
                    "app_default_view": "download",
                    "not_a_real_setting": True,
                }
            },
        )
        assert unknown.status_code == 422

        invalid_number = client.put(
            "/api/settings",
            json={
                "values": {
                    "app_default_view": "download",
                    "file_browser_page_size": 1,
                }
            },
        )
        assert invalid_number.status_code == 422

        invalid_runtime = client.put(
            "/api/settings",
            json={
                "values": {
                    "app_default_view": "download",
                    "aria2_connections": 99,
                }
            },
        )
        assert invalid_runtime.status_code == 422

        after = client.get("/api/settings").json()["values"]
        assert after["app_default_view"] == before["app_default_view"] == "files"
        assert after["file_browser_page_size"] == before["file_browser_page_size"]
        assert after["aria2_connections"] == before["aria2_connections"]


def test_runtime_and_browser_defaults_persist_and_apply(settings) -> None:
    settings.aria2c_command = "sh"
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        services = app.state.services
        assert services.runner.aria2_executable is not None

        saved = client.put(
            "/api/settings",
            json={
                "values": {
                    "app_default_view": "download",
                    "file_browser_page_size": 20,
                    "file_browser_default_sort": "mtime",
                    "file_browser_default_direction": "desc",
                    "aria2_enabled": False,
                    "aria2_connections": 4,
                    "resolve_timeout_seconds": 45,
                    "tv_cdn_probe_enabled": True,
                    "cdn_probe_timeout_seconds": 2.5,
                    "cdn_probe_sample_size_kib": 128,
                    "cdn_probe_connections": 3,
                    "line_compare_enabled": False,
                    "log_retention_days": 7,
                }
            },
        )
        assert saved.status_code == 200, saved.text
        payload = saved.json()
        assert payload["app_default_view"] == "download"
        assert payload["file_browser_page_size"] == 20
        assert payload["file_browser_default_sort"] == "mtime"
        assert payload["file_browser_default_direction"] == "desc"
        assert services.settings.aria2_enabled is False
        assert services.settings.aria2_connections == 4
        assert services.settings.resolve_timeout_seconds == 45
        assert services.settings.tv_cdn_probe_enabled is True
        assert services.settings.cdn_probe_timeout_seconds == 2.5
        assert services.settings.cdn_probe_sample_bytes == 128 * 1024
        assert services.settings.cdn_probe_connections == 3
        assert services.settings.line_compare_enabled is False
        assert services.settings.log_retention_days == 7
        assert services.runner.aria2_executable is None

        enabled = client.put(
            "/api/settings", json={"values": {"aria2_enabled": True}}
        )
        assert enabled.status_code == 200, enabled.text
        assert services.settings.aria2_enabled is True
        assert services.runner.aria2_executable is not None

        listing = services.files.browse(root_key="downloads")
        assert listing["page_size"] == 20

    restarted = create_app(settings)
    with TestClient(restarted) as client:
        login(client)
        persisted = client.get("/api/settings").json()
        assert persisted["app_default_view"] == "download"
        assert persisted["file_browser_page_size"] == 20
        assert persisted["file_browser_default_sort"] == "mtime"
        assert persisted["file_browser_default_direction"] == "desc"
        assert persisted["values"]["aria2_connections"] == 4
        assert persisted["values"]["resolve_timeout_seconds"] == 45
        assert persisted["values"]["line_compare_enabled"] is False
        services = restarted.state.services
        assert services.settings.aria2_connections == 4
        assert services.settings.cdn_probe_sample_bytes == 128 * 1024
        assert services.settings.log_retention_days == 7

        schema = client.get("/api/settings/schema").json()
        schema_fields = {
            field["key"]: field
            for group in schema["groups"]
            for section in group["sections"]
            for field in section["fields"]
        }
        assert schema_fields["aria2_connections"]["default"] == settings.aria2_connections
        assert schema_fields["log_retention_days"]["default"] == settings.log_retention_days


def test_active_download_blocks_only_idle_runtime_settings(settings, monkeypatch) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        services = app.state.services
        monkeypatch.setattr(services.runner, "has_active_operation", lambda: True)

        blocked = client.put(
            "/api/settings",
            json={
                "values": {
                    "app_default_view": "download",
                    "line_compare_enabled": False,
                }
            },
        )
        assert blocked.status_code == 409
        assert services.settings.line_compare_enabled is True
        assert client.get("/api/settings").json()["app_default_view"] == "files"

        maintenance = client.put(
            "/api/settings", json={"values": {"log_retention_days": 14}}
        )
        assert maintenance.status_code == 200, maintenance.text
        assert services.settings.log_retention_days == 14


def test_busy_pointer_and_hover_regressions() -> None:
    root = Path(__file__).resolve().parents[1]
    static = root / "bili_remote" / "static"
    css = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (static / "styles.css", static / "workspace.css")
    )
    scripts = "\n".join(
        path.read_text(encoding="utf-8") for path in static.glob("*.js")
    )
    assert "cursor: wait" not in css
    assert ".button:disabled { cursor: not-allowed;" in css
    assert ".button.is-busy:disabled { cursor: progress; }" in css
    for event_name in ("mouseenter", "mouseover", "pointerenter"):
        assert f'addEventListener("{event_name}"' not in scripts
    assert scripts.count("setBusy(button, false)") >= 2
