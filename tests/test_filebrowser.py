from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bili_remote import filebrowser as filebrowser_module
from bili_remote.web import create_app


def login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]


def rename_payload(
    files: list[str],
    *,
    template: str = "{title}.S{season}E{episode:02d}.{ext}",
    episode_mode: str = "start",
    start_episode: int = 1,
    episode_step: int = 1,
    values: dict[str, str] | None = None,
    manual_names: dict[str, str] | None = None,
    directory: str = "Show",
) -> dict:
    return {
        "root_key": "downloads",
        "directory": directory,
        "files": files,
        "template": template,
        "episode_mode": episode_mode,
        "start_episode": start_episode,
        "episode_step": episode_step,
        "values": values
        if values is not None
        else {"title": "Show", "season": "01"},
        "manual_names": manual_names or {},
    }


def test_file_browser_roots_and_directory_navigation(settings) -> None:
    settings.ensure_directories()
    source = settings.download_root / "Show" / "Season 1"
    source.mkdir(parents=True)
    (source / "episode.mkv").write_bytes(b"video")
    (settings.torrents_root / "Show.torrent").write_bytes(b"torrent")

    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/files/roots").status_code == 401
        login(client)
        roots = client.get("/api/files/roots")
        assert roots.status_code == 200, roots.text
        assert [item["key"] for item in roots.json()] == [
            "downloads",
            "torrents",
            "images",
            "release_info",
        ]
        assert {item["label"] for item in roots.json()} == {
            "资源下载目录",
            "种子目录",
            "图片目录",
            "发布信息目录",
        }

        browsed = client.get(
            "/api/files/browse",
            params={"root_key": "downloads", "path": "Show/Season 1"},
        )
        assert browsed.status_code == 200, browsed.text
        data = browsed.json()
        assert data["root_label"] == "资源下载目录"
        assert data["relative_path"] == "Show/Season 1"
        assert data["items"][0]["name"] == "episode.mkv"
        assert data["items"][0]["kind"] == "video"

        escaped = client.get(
            "/api/files/browse",
            params={"root_key": "downloads", "path": "../"},
        )
        assert escaped.status_code == 422


def test_batch_rename_preview_apply_manual_name_and_conflicts(
    settings, tmp_path: Path
) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    for name in (
        "old1.mkv",
        "old2.mkv",
        "taken.mkv",
        "existing.S01E03.mkv",
        "Show.S01E03.mkv",
    ):
        (directory / name).write_bytes(name.encode())

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        preview = client.post(
            "/api/files/rename/preview",
            json=rename_payload(
                ["old1.mkv", "old2.mkv"],
                start_episode=1,
                episode_step=1,
                manual_names={"old2.mkv": "Show.S01E09.mkv"},
            ),
        )
        assert preview.status_code == 200, preview.text
        plan = preview.json()
        assert plan["valid"] is True
        assert plan["changed_count"] == 2
        assert [item["target_name"] for item in plan["operations"]] == [
            "Show.S01E01.mkv",
            "Show.S01E09.mkv",
        ]

        stale = client.post(
            "/api/files/rename/apply",
            json={**rename_payload(["old1.mkv", "old2.mkv"]), "fingerprint": "0" * 64},
        )
        assert stale.status_code == 409

        applied = client.post(
            "/api/files/rename/apply",
            json={
                **rename_payload(
                    ["old1.mkv", "old2.mkv"],
                    manual_names={"old2.mkv": "Show.S01E09.mkv"},
                ),
                "fingerprint": plan["fingerprint"],
            },
        )
        assert applied.status_code == 200, applied.text
        assert applied.json()["renamed_count"] == 2
        assert (directory / "Show.S01E01.mkv").read_bytes() == b"old1.mkv"
        assert (directory / "Show.S01E09.mkv").read_bytes() == b"old2.mkv"
        assert not (directory / "old1.mkv").exists()
        assert not (directory / "old2.mkv").exists()

        conflict = client.post(
            "/api/files/rename/preview",
            json=rename_payload(["taken.mkv"], start_episode=3),
        )
        assert conflict.status_code == 200, conflict.text
        conflict_plan = conflict.json()
        assert conflict_plan["valid"] is False
        assert "目标已存在" in conflict_plan["errors"][0]["message"]

        duplicate = client.post(
            "/api/files/rename/preview",
            json=rename_payload(
                ["taken.mkv", "existing.S01E03.mkv"],
                template="{original_name}.{ext}",
                manual_names={
                    "taken.mkv": "same.mkv",
                    "existing.S01E03.mkv": "same.mkv",
                },
            ),
        )
        assert duplicate.status_code == 200, duplicate.text
        duplicate_plan = duplicate.json()
        assert duplicate_plan["valid"] is False
        assert any("相同目标" in item["message"] for item in duplicate_plan["errors"])

        invalid = client.post(
            "/api/files/rename/preview",
            json=rename_payload(
                ["taken.mkv"],
                template="{title}.{ext}",
                values={"title": "bad?name"},
            ),
        )
        assert invalid.status_code == 200, invalid.text
        invalid_plan = invalid.json()
        assert invalid_plan["valid"] is False
        assert any("非法字符" in item["message"] for item in invalid_plan["errors"])
        assert (directory / "taken.mkv").exists()

        missing_value = client.post(
            "/api/files/rename/preview",
            json=rename_payload(
                ["taken.mkv"],
                template="{source}.{ext}",
                values={"source": ""},
            ),
        )
        assert missing_value.status_code == 200, missing_value.text
        assert any(
            "未填写模板变量 {source}" in item["message"]
            for item in missing_value.json()["errors"]
        )


def test_rename_requires_csrf_and_rejects_directory_traversal(settings, tmp_path: Path) -> None:
    settings.ensure_directories()
    (settings.download_root / "inside.mkv").write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.mkv").write_bytes(b"outside")
    (settings.download_root / "escape.mkv").symlink_to(outside / "outside.mkv")
    (settings.download_root / "escape-dir").symlink_to(outside, target_is_directory=True)

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        client.headers.pop("X-CSRF-Token", None)
        response = client.post(
            "/api/files/rename/preview",
            json=rename_payload(["inside.mkv"], directory=""),
        )
        assert response.status_code == 403

        login(client)
        escaped = client.post(
            "/api/files/rename/preview",
            json=rename_payload(["outside.mkv"], directory="../outside"),
        )
        assert escaped.status_code == 422
        nested = client.post(
            "/api/files/rename/preview",
            json=rename_payload(["../outside/outside.mkv"], directory=""),
        )
        assert nested.status_code == 200
        assert nested.json()["valid"] is False
        symlinked = client.post(
            "/api/files/rename/preview",
            json=rename_payload(["escape.mkv"], directory=""),
        )
        assert symlinked.status_code == 200
        assert symlinked.json()["valid"] is False
        browsed = client.get(
            "/api/files/browse",
            params={"root_key": "downloads", "path": ""},
        )
        escape_directory = next(
            item for item in browsed.json()["items"] if item["name"] == "escape-dir"
        )
        assert escape_directory["allowed"] is False
        assert (outside / "outside.mkv").exists()
        assert (settings.download_root / "inside.mkv").exists()


def test_apply_uses_per_file_values_and_rejects_changed_source(settings) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    for name in ("one.mkv", "two.mkv", "changed.mkv"):
        (directory / name).write_bytes(name.encode())

    payload = {
        "root_key": "downloads",
        "directory": "Show",
        "files": ["one.mkv", "two.mkv"],
        "template": "{title}.S{season:02d}E{episode:02d}.{source}.{ext}",
        "episode_mode": "detected",
        "episode_overrides": {"one.mkv": 1, "two.mkv": 2},
        "file_values": {
            "one.mkv": {"season": "02", "source": "BluRay"},
            "two.mkv": {"season": "03", "source": "WEBRip"},
        },
        "values": {"title": "Show", "season": "01", "source": "WEB-DL"},
    }
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        preview = client.post("/api/files/rename/preview", json=payload)
        assert preview.status_code == 200, preview.text
        plan = preview.json()
        assert [item["target_name"] for item in plan["operations"]] == [
            "Show.S02E01.BluRay.mkv",
            "Show.S03E02.WEBRip.mkv",
        ]

        changed_preview = client.post(
            "/api/files/rename/preview",
            json={**payload, "files": ["changed.mkv"], "episode_overrides": {"changed.mkv": 3}, "file_values": {}},
        )
        assert changed_preview.status_code == 200, changed_preview.text
        changed_fingerprint = changed_preview.json()["fingerprint"]
        (directory / "changed.mkv").write_bytes(b"changed after preview")
        stale = client.post(
            "/api/files/rename/apply",
            json={
                **payload,
                "files": ["changed.mkv"],
                "episode_overrides": {"changed.mkv": 3},
                "file_values": {},
                "fingerprint": changed_fingerprint,
            },
        )
        assert stale.status_code == 409
        assert (directory / "changed.mkv").exists()

        applied = client.post(
            "/api/files/rename/apply",
            json={**payload, "fingerprint": plan["fingerprint"]},
        )
        assert applied.status_code == 200, applied.text
        assert (directory / "Show.S02E01.BluRay.mkv").read_bytes() == b"one.mkv"
        assert (directory / "Show.S03E02.WEBRip.mkv").read_bytes() == b"two.mkv"


def test_apply_rolls_back_when_a_later_rename_fails(settings, monkeypatch) -> None:
    settings.ensure_directories()
    directory = settings.download_root / "Show"
    directory.mkdir()
    for name in ("one.mkv", "two.mkv"):
        (directory / name).write_bytes(name.encode())

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        payload = rename_payload(["one.mkv", "two.mkv"])
        preview = client.post("/api/files/rename/preview", json=payload)
        assert preview.status_code == 200, preview.text
        plan = preview.json()
        assert plan["valid"] is True

        original_rename = filebrowser_module._rename_no_replace

        def fail_second(source: Path, target: Path) -> None:
            if source.name == "two.mkv":
                raise OSError("simulated rename failure")
            original_rename(source, target)

        monkeypatch.setattr(filebrowser_module, "_rename_no_replace", fail_second)
        applied = client.post(
            "/api/files/rename/apply",
            json={**payload, "fingerprint": plan["fingerprint"]},
        )
        assert applied.status_code == 409
        assert "已回滚本次操作" in applied.json()["detail"]
        assert (directory / "one.mkv").read_bytes() == b"one.mkv"
        assert (directory / "two.mkv").read_bytes() == b"two.mkv"
        assert not (directory / "Show.S01E01.mkv").exists()
        assert not (directory / "Show.S01E02.mkv").exists()
