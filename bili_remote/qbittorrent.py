from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .database import Database
from .file_service import FileServiceError
from .security import SecretBox


class QbittorrentError(FileServiceError):
    pass


class QbittorrentService:
    def __init__(
        self,
        database: Database,
        secrets: SecretBox,
        *,
        client_factory: Callable[[str, float], httpx.Client] | None = None,
    ) -> None:
        self.database = database
        self.secrets = secrets
        self._client_factory = client_factory or self._build_client

    @staticmethod
    def _build_client(base_url: str, timeout: float) -> httpx.Client:
        return httpx.Client(
            base_url=base_url,
            timeout=timeout,
            follow_redirects=True,
            headers={"Referer": base_url, "User-Agent": "Bili-Remote/1.0"},
        )

    def config(self) -> dict[str, Any]:
        stored = self.database.get_settings()
        url = str(stored.get("qbittorrent_url") or "").rstrip("/")
        if url:
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                url = ""
        try:
            timeout_value = float(stored.get("qbittorrent_timeout_seconds", "15"))
        except ValueError:
            timeout_value = 15.0
        return {
            "url": url,
            "username": stored.get("qbittorrent_username", ""),
            "password_configured": bool(stored.get("qbittorrent_password")),
            "category": stored.get("qbittorrent_category", ""),
            "save_path": stored.get("qbittorrent_save_path", ""),
            "paused": stored.get("qbittorrent_paused", "false").lower() == "true",
            "timeout": min(max(timeout_value, 3.0), 120.0),
            "configured": bool(url and stored.get("qbittorrent_username")),
        }

    def status(self) -> dict[str, Any]:
        config = self.config()
        return {
            "configured": config["configured"],
            "url": config["url"],
            "category": config["category"],
            "password_configured": config["password_configured"],
            "connected": None,
        }

    def _password(self) -> str:
        raw = self.database.get_settings().get("qbittorrent_password", "")
        if not raw:
            return ""
        try:
            return self.secrets.decrypt_json(raw)
        except ValueError as exc:
            raise QbittorrentError("已保存的 qBittorrent 密码无法解密，请重新填写") from exc

    def _login(self, client: httpx.Client, config: dict[str, Any]) -> None:
        if not config["configured"]:
            raise QbittorrentError("qBittorrent 尚未配置 Web API 地址和用户名")
        try:
            response = client.post(
                "/api/v2/auth/login",
                data={
                    "username": config["username"],
                    "password": self._password(),
                },
            )
        except httpx.HTTPError as exc:
            raise QbittorrentError(f"无法连接 qBittorrent：{exc}") from exc
        body = response.text.strip()
        if response.status_code != 200 or body.lower().startswith("fails"):
            raise QbittorrentError("qBittorrent 登录失败，请检查用户名和密码")
        if not client.cookies.get("SID"):
            raise QbittorrentError("qBittorrent 登录响应缺少会话 Cookie")

    def test_connection(self) -> dict[str, Any]:
        config = self.config()
        if not config["configured"]:
            raise QbittorrentError("qBittorrent 尚未配置 Web API 地址和用户名")
        try:
            with self._client_factory(config["url"], config["timeout"]) as client:
                self._login(client, config)
                version_response = client.get("/api/v2/app/version")
                if version_response.status_code != 200:
                    raise QbittorrentError(
                        f"qBittorrent 版本接口返回 HTTP {version_response.status_code}"
                    )
                transfer_response = client.get("/api/v2/transfer/info")
                transfer: dict[str, Any] = {}
                if transfer_response.status_code == 200:
                    try:
                        value = transfer_response.json()
                        transfer = value if isinstance(value, dict) else {}
                    except json.JSONDecodeError:
                        transfer = {}
        except httpx.HTTPError as exc:
            raise QbittorrentError(f"无法连接 qBittorrent：{exc}") from exc
        return {
            "connected": True,
            "version": version_response.text.strip(),
            "download_speed": transfer.get("dl_info_speed"),
            "upload_speed": transfer.get("up_info_speed"),
        }

    def add_torrent(self, torrent_path: Path, *, category: str | None = None) -> dict[str, Any]:
        config = self.config()
        if torrent_path.is_symlink() or not torrent_path.is_file():
            raise QbittorrentError("种子文件不存在或不是普通文件")
        if torrent_path.suffix.lower() != ".torrent":
            raise QbittorrentError("只能添加 .torrent 文件")
        data: dict[str, str] = {}
        selected_category = category if category is not None else config["category"]
        if selected_category:
            data["category"] = str(selected_category)
        if config["save_path"]:
            data["savepath"] = str(config["save_path"])
        data["paused"] = "true" if config["paused"] else "false"
        data["tags"] = "bili-remote"
        try:
            with self._client_factory(config["url"], config["timeout"]) as client:
                self._login(client, config)
                with torrent_path.open("rb") as handle:
                    response = client.post(
                        "/api/v2/torrents/add",
                        data=data,
                        files={
                            "torrents": (
                                torrent_path.name,
                                handle,
                                "application/x-bittorrent",
                            )
                        },
                    )
        except (OSError, httpx.HTTPError) as exc:
            raise QbittorrentError(f"添加种子到 qBittorrent 失败：{exc}") from exc
        body = response.text.strip()
        if response.status_code != 200 or body.lower().startswith("fails"):
            detail = body[:200] if body else f"HTTP {response.status_code}"
            raise QbittorrentError(f"qBittorrent 拒绝添加种子：{detail}")
        return {
            "added": True,
            "torrent_path": str(torrent_path),
            "category": data.get("category", ""),
            "save_path": data.get("savepath", ""),
            "paused": data["paused"] == "true",
            "message": "种子已成功添加到 qBittorrent",
        }

    def list_torrents(self, *, limit: int = 100) -> list[dict[str, Any]]:
        config = self.config()
        if not config["configured"]:
            raise QbittorrentError("qBittorrent 尚未配置 Web API 地址和用户名")
        try:
            with self._client_factory(config["url"], config["timeout"]) as client:
                self._login(client, config)
                response = client.get("/api/v2/torrents/info")
        except httpx.HTTPError as exc:
            raise QbittorrentError(f"无法读取 qBittorrent 任务：{exc}") from exc
        if response.status_code != 200:
            raise QbittorrentError(
                f"qBittorrent 任务接口返回 HTTP {response.status_code}"
            )
        try:
            values = response.json()
        except json.JSONDecodeError as exc:
            raise QbittorrentError("qBittorrent 返回了无效任务列表") from exc
        if not isinstance(values, list):
            raise QbittorrentError("qBittorrent 返回了无效任务列表")
        return values[: min(max(limit, 1), 500)]
