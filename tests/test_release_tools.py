from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from bili_remote import release_info as release_info_module
from bili_remote.database import Database
from bili_remote.file_service import FileConflictError, FileService, FileServiceError
from bili_remote.image_hosts import ImageHostError, ImageHostService
from bili_remote.release_info import ReleaseInfoService
from bili_remote.security import SecretBox
from bili_remote.torrents import TorrentService, bdecode
from bili_remote.web import create_app

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZJ8sAAAAASUVORK5CYII="
)


def login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]


class FakeImageClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def upload(self, path: Path, media_type: str, timeout: float) -> dict[str, str]:
        self.calls += 1
        assert path.is_file()
        assert media_type in {"image/png", "image/jpeg"}
        assert timeout > 0
        if self.fail:
            raise ImageHostError("模拟上传失败")
        return {
            "remote_url": f"https://images.example/{path.name}",
            "thumbnail_url": f"https://images.example/thumb/{path.name}",
            "remote_id": path.stem,
        }


def test_image_host_upload_retry_and_link_formats(settings) -> None:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    files = FileService(settings, database)
    client = FakeImageClient(fail=True)
    service = ImageHostService(
        files,
        database,
        SecretBox(settings.app_secret),
        client_factory=lambda _provider, _key, _timeout: client,
    )
    (settings.images_root / "pixel.png").write_bytes(PNG_BYTES)

    failed = service.upload_file(root_key="images", relative_path="pixel.png")
    assert failed["status"] == "failed"
    assert failed["attempts"] == 1
    assert "模拟上传失败" in failed["error"]

    client.fail = False
    completed = service.retry(failed["id"])
    assert completed["status"] == "completed"
    assert completed["attempts"] == 2
    assert completed["remote_url"] == "https://images.example/pixel.png"
    assert service.status()["configured"] is True

    formatted = service.format_links([completed["id"]], "bbcode")
    assert formatted["content"] == "[img]https://images.example/pixel.png[/img]"
    assert formatted["missing"] == []


def test_image_upload_api_records_real_provider_result(settings) -> None:
    settings.ensure_directories()
    (settings.images_root / "pixel.png").write_bytes(PNG_BYTES)
    app = create_app(settings)
    client = FakeImageClient()
    app.state.services.image_hosts._client_factory = (
        lambda _provider, _key, _timeout: client
    )
    with TestClient(app) as http:
        login(http)
        response = http.post(
            "/api/uploads",
            json={
                "items": [
                    {
                        "root_key": "images",
                        "relative_path": "pixel.png",
                    }
                ]
            },
        )
        assert response.status_code == 200, response.text
        record = response.json()["items"][0]
        assert record["status"] == "completed"
        assert record["remote_url"] == "https://images.example/pixel.png"
        assert client.calls == 1

        listing = http.get("/api/uploads").json()
        assert listing["items"][0]["id"] == record["id"]
        formatted = http.get(
            "/api/uploads/formats",
            params={"ids": [record["id"]], "format": "html"},
        )
        assert formatted.status_code == 200, formatted.text
        assert "images.example/pixel.png" in formatted.json()["content"]


def test_torrent_build_and_conflict_detection(settings) -> None:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    files = FileService(settings, database)
    service = TorrentService(files, database)
    source = settings.download_root / "Package"
    (source / "Sub").mkdir(parents=True)
    (source / "episode.mkv").write_bytes(b"video-data" * 1000)
    (source / "Sub" / "readme.txt").write_text("中文说明", encoding="utf-8")

    record = service.create(
        {
            "source_root_key": "downloads",
            "source_relative_path": "Package",
            "output_relative_path": "builds",
            "filename": "package.torrent",
            "trackers": ["https://tracker.example/announce"],
            "private": True,
            "piece_length": "16K",
            "source": "BiliRemote",
        }
    )
    assert record["file_count"] == 2
    assert record["total_size_bytes"] == (source / "episode.mkv").stat().st_size + len(
        "中文说明".encode()
    )
    assert len(record["info_hash"]) == 40
    torrent_path = service.file_path(record["id"])
    payload = bdecode(torrent_path.read_bytes())
    assert payload[b"announce"] == b"https://tracker.example/announce"
    assert payload[b"info"][b"private"] == 1
    assert payload[b"info"][b"source"] == b"BiliRemote"

    try:
        service.create(
            {
                "source_root_key": "downloads",
                "source_relative_path": "Package",
                "output_relative_path": "builds",
                "filename": "package.torrent",
            }
        )
    except FileConflictError as exc:
        assert "已存在" in str(exc)
    else:
        raise AssertionError("同名种子应被拒绝")


def test_torrent_download_rejects_build_after_source_rename(settings) -> None:
    settings.ensure_directories()
    source = settings.download_root / "Old.Package"
    (source / "Sub").mkdir(parents=True)
    (source / "episode.mkv").write_bytes(b"video-data" * 1000)
    (source / "Sub" / "readme.txt").write_text("说明", encoding="utf-8")

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        created = client.post(
            "/api/torrents",
            json={
                "source_root_key": "downloads",
                "source_relative_path": "Old.Package",
                "output_relative_path": "builds",
                "filename": "old-package.torrent",
                "piece_length": "16K",
                "trackers": ["https://tracker.example/announce"],
            },
        )
        assert created.status_code == 200, created.text
        build = created.json()
        assert client.get(build["download_url"]).status_code == 200

        renamed = settings.download_root / "New.Package"
        source.rename(renamed)

        stale = client.get(build["download_url"])
        assert stale.status_code == 422, stale.text
        assert "源资源已变化，请重新制种" in stale.json()["detail"]
        assert (settings.torrents_root / "builds" / "old-package.torrent").is_file()

        rebuilt = client.post(
            "/api/torrents",
            json={
                "source_root_key": "downloads",
                "source_relative_path": "New.Package",
                "output_relative_path": "builds",
                "filename": "new-package.torrent",
                "piece_length": "16K",
                "trackers": ["https://tracker.example/announce"],
            },
        )
        assert rebuilt.status_code == 200, rebuilt.text
        assert client.get(rebuilt.json()["download_url"]).status_code == 200


def test_release_info_torrent_and_publish_api(settings) -> None:
    settings.ensure_directories()
    package = settings.download_root / "Show"
    package.mkdir()
    (package / "episode01.nfo").write_text("第一集\n作者：测试", encoding="utf-8")
    (package / "notes.txt").write_text("release notes", encoding="utf-8")

    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        analyzed = client.post(
            "/api/release-info/analyze",
            json={
                "root_key": "downloads",
                "relative_path": "Show",
                "title": "测试发布",
                "format": "markdown",
            },
        )
        assert analyzed.status_code == 200, analyzed.text
        analysis = analyzed.json()
        assert analysis["title"] == "测试发布"
        assert len(analysis["files"]) == 2
        assert "文件清单" in analysis["generated_text"]

        document_response = client.post(
            "/api/release-info",
            json={
                "root_key": "downloads",
                "relative_path": "Show",
                "title": "测试发布",
                "format": "markdown",
                "output_relative_path": "docs",
                "filename": "show.md",
                "description": "# 测试发布\n\n人工编辑内容",
            },
        )
        assert document_response.status_code == 200, document_response.text
        document = document_response.json()
        assert (settings.release_info_root / "docs" / "show.md").read_text(
            encoding="utf-8"
        ) == "# 测试发布\n\n人工编辑内容"

        torrent_response = client.post(
            "/api/torrents",
            json={
                "source_root_key": "downloads",
                "source_relative_path": "Show",
                "output_relative_path": "",
                "filename": "show.torrent",
                "trackers": ["https://tracker.example/announce"],
                "private": True,
                "piece_length": "auto",
                "source": "BiliRemote",
            },
        )
        assert torrent_response.status_code == 200, torrent_response.text
        torrent = torrent_response.json()
        assert torrent["file_count"] == 2

        publish_response = client.post(
            "/api/publish/sessions",
            json={
                "title": "测试发布",
                "release_document_id": document["id"],
                "torrent_build_id": torrent["id"],
                "image_upload_ids": [],
                "description": "人工确认后的发布说明",
            },
        )
        assert publish_response.status_code == 200, publish_response.text
        publish = publish_response.json()
        assert publish["status"] == "draft"
        assert publish["metadata"]["checks"]["errors"]
        assert "人工确认后的发布说明" in publish["markdown"]
        assert "[code]" not in publish["bbcode"]
        assert publish["torrent_build_id"] == torrent["id"]

        listed = client.get("/api/publish/sessions").json()["items"]
        assert any(item["id"] == publish["id"] for item in listed)


def test_release_info_runs_bdinfo_for_bluray_and_persists_report(
    settings, monkeypatch
) -> None:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    disc = settings.download_root / "Bluray" / "Disc"
    (disc / "BDMV").mkdir(parents=True)
    (disc / "BDMV" / "index.bdmv").write_bytes(b"bluray-index")
    (disc / "notes.txt").write_text("release notes", encoding="utf-8")
    service = ReleaseInfoService(settings, FileService(settings, database), database)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        release_info_module.shutil,
        "which",
        lambda name: "/fake/BDInfo" if name in {"bdinfo", "BDInfo"} else None,
    )

    def fake_run(command, **_kwargs):
        calls.append(command)
        Path(command[-1], "BDINFO.txt").write_text(
            "Disc Title: Test Disc\nPlaylist: 00001.MPLS",
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(release_info_module.subprocess, "run", fake_run)
    analysis = service.analyze(
        {
            "root_key": "downloads",
            "relative_path": "Bluray/Disc",
            "title": "蓝光测试",
            "format": "markdown",
        }
    )
    assert analysis["disc_info_tool"] == "bdinfo"
    assert "Disc Title: Test Disc" in analysis["disc_info"]
    assert "## 蓝光信息" in analysis["generated_text"]
    assert calls and calls[0][1] == "-w"
    assert calls[0][2] == str(disc)

    record = service.create(
        {
            "root_key": "downloads",
            "relative_path": "Bluray/Disc",
            "title": "蓝光测试",
            "format": "markdown",
            "output_relative_path": "docs",
            "filename": "bluray.md",
        }
    )
    persisted = database.get_release_document(record["id"])
    assert persisted is not None
    assert "Disc Title: Test Disc" in persisted["media"]["disc_info"]
    assert persisted["media"]["disc_info_tool"] == "bdinfo"


def test_release_info_does_not_fabricate_bdinfo_failure(settings, monkeypatch) -> None:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    disc = settings.download_root / "Bluray" / "Broken"
    (disc / "BDMV").mkdir(parents=True)
    (disc / "BDMV" / "index.bdmv").write_bytes(b"bluray-index")
    service = ReleaseInfoService(settings, FileService(settings, database), database)

    monkeypatch.setattr(
        release_info_module.shutil,
        "which",
        lambda name: "/fake/BDInfo" if name in {"bdinfo", "BDInfo"} else None,
    )
    monkeypatch.setattr(
        release_info_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="scan failed",
        ),
    )

    analysis = service.analyze(
        {
            "root_key": "downloads",
            "relative_path": "Bluray/Broken",
            "title": "蓝光失败测试",
            "format": "text",
        }
    )
    assert analysis["disc_info"] is None
    assert any("BDInfo 报告生成失败" in warning for warning in analysis["warnings"])
    assert "Disc Title:" not in analysis["generated_text"]
    assert "蓝光信息" not in analysis["generated_text"]


def test_release_info_reports_redact_absolute_server_paths(settings, monkeypatch) -> None:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    source = settings.download_root / "Show" / "episode01.mkv"
    source.parent.mkdir()
    source.write_bytes(b"synthetic-video")
    service = ReleaseInfoService(settings, FileService(settings, database), database)

    monkeypatch.setattr(
        release_info_module.shutil,
        "which",
        lambda name: "/fake/mediainfo" if name == "mediainfo" else None,
    )
    monkeypatch.setattr(
        release_info_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                f"General\nComplete name : {source}\n"
                f"Folder name : {source.parent}\n\nVideo\nFormat : AVC\n"
            ),
            stderr="",
        ),
    )
    mediainfo = service._mediainfo_text(source)
    assert mediainfo is not None
    assert str(settings.download_root) not in mediainfo
    assert "Complete name : episode01.mkv" in mediainfo
    assert "Folder name : ." in mediainfo

    ffprobe = service._ffprobe_text(
        {"path": str(source), "duration_seconds": 1, "size_bytes": source.stat().st_size},
        {"format": {"format_name": "matroska"}, "streams": []},
    )
    assert str(settings.download_root) not in ffprobe
    assert f"Complete name: {source.name}" in ffprobe


def test_settings_encrypt_image_secret_and_preserve_stored_qbittorrent_values(settings) -> None:
    settings.ensure_directories()
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        database = app.state.services.database
        database.update_settings(
            {
                "qbittorrent_url": "http://qbittorrent:8080",
                "qbittorrent_username": "admin",
                "qbittorrent_password": app.state.services.secrets.encrypt_json(
                    "super-secret-qbit-password"
                ),
            }
        )
        response = client.put(
            "/api/settings",
            json={
                "values": {
                    "image_host_provider": "imgbb",
                    "image_host_imgbb_api_key": "super-secret-image-key",
                }
            },
        )
        assert response.status_code == 200, response.text
        assert "super-secret" not in response.text
        payload = response.json()
        assert payload["secrets_configured"]["image_host_imgbb_api_key"] is True
        assert "qbittorrent_credentials" not in payload["secrets_configured"]
        assert all("qbittorrent" not in key for key in payload["values"])

        stored = database.get_settings()
        assert stored["image_host_imgbb_api_key"] != "super-secret-image-key"
        assert stored["qbittorrent_password"] != "super-secret-qbit-password"
        assert app.state.services.secrets.decrypt_json(
            stored["qbittorrent_password"]
        ) == "super-secret-qbit-password"

        rejected = client.put("/api/settings", json={"values": {"qbittorrent_password": ""}})
        assert rejected.status_code == 422, rejected.text
        assert "qbittorrent_password" in rejected.text
        assert app.state.services.secrets.decrypt_json(
            database.get_settings()["qbittorrent_password"]
        ) == "super-secret-qbit-password"


def test_torrent_rejects_symlink_source(settings) -> None:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    service = TorrentService(FileService(settings, database), database)
    target = settings.download_root / "target.txt"
    target.write_text("target", encoding="utf-8")
    link = settings.download_root / "link.txt"
    link.symlink_to(target)
    try:
        service.create(
            {
                "source_root_key": "downloads",
                "source_relative_path": "link.txt",
                "output_relative_path": "",
            }
        )
    except FileServiceError as exc:
        assert "符号链接" in str(exc)
    else:
        raise AssertionError("符号链接源应被拒绝")
