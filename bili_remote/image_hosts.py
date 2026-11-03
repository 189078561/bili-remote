from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

from .database import Database
from .file_service import FileService, FileServiceError
from .security import SecretBox

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PROVIDERS = ("catbox", "imgbb", "pixhost")


class ImageHostError(FileServiceError):
    pass


class ImageHostClient(Protocol):
    def upload(self, path: Path, media_type: str, timeout: float) -> dict[str, str]:
        """Upload one image and return remote_url, thumbnail_url, remote_id."""


def _validated_remote_url(value: Any) -> str:
    text = str(value or "").strip()
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise ImageHostError("图床返回了无效链接") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ImageHostError("图床返回了无效链接")
    return text


class CatboxClient:
    endpoint = "https://catbox.moe/user/api.php"

    def upload(self, path: Path, media_type: str, timeout: float) -> dict[str, str]:
        try:
            with (
                path.open("rb") as handle,
                httpx.Client(timeout=timeout, follow_redirects=True) as client,
            ):
                response = client.post(
                    self.endpoint,
                    data={"reqtype": "fileupload"},
                    files={"fileToUpload": (path.name, handle, media_type)},
                )
        except (OSError, httpx.HTTPError) as exc:
            raise ImageHostError(f"Catbox 上传请求失败：{exc}") from exc
        body = response.text.strip()
        if response.status_code < 200 or response.status_code >= 300:
            raise ImageHostError(f"Catbox 上传失败（HTTP {response.status_code}）")
        if body.lower().startswith("error") or len(body) > 500:
            raise ImageHostError(f"Catbox 上传失败：{body[:200] or '未知错误'}")
        url = _validated_remote_url(body)
        return {"remote_url": url, "thumbnail_url": url, "remote_id": url.rsplit("/", 1)[-1]}


class ImgBBClient:
    endpoint = "https://api.imgbb.com/1/upload"

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ImageHostError("ImgBB API Key 尚未配置")
        self.api_key = api_key

    def upload(self, path: Path, media_type: str, timeout: float) -> dict[str, str]:
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                response = client.post(
                    self.endpoint,
                    data={"key": self.api_key, "image": encoded, "name": path.stem},
                )
        except (OSError, httpx.HTTPError) as exc:
            raise ImageHostError(f"ImgBB 上传请求失败：{exc}") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise ImageHostError(f"ImgBB 上传失败（HTTP {response.status_code}）")
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise ImageHostError("ImgBB 返回了无效响应") from exc
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            message = body.get("error", {}).get("message") if isinstance(body, dict) else None
            raise ImageHostError(f"ImgBB 上传失败：{message or '响应缺少 data'}")
        url = _validated_remote_url(data.get("display_url") or data.get("url"))
        thumbnail = (
            data.get("thumb", {}).get("url") if isinstance(data.get("thumb"), dict) else None
        )
        return {
            "remote_url": url,
            "thumbnail_url": _validated_remote_url(thumbnail) if thumbnail else url,
            "remote_id": str(data.get("id") or ""),
        }


class PixhostClient:
    endpoint = "https://api.pixhost.to/images"

    def upload(self, path: Path, media_type: str, timeout: float) -> dict[str, str]:
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ImageHostError("图片超过 Pixhost 20 MiB 限制，请手动调整后重试，原图未修改")
        try:
            with path.open("rb") as handle, httpx.Client(timeout=timeout) as client:
                response = client.post(
                    self.endpoint,
                    data={"content_type": "0", "max_th_size": "350"},
                    files={"img": (path.name, handle, media_type)},
                    headers={"Accept": "application/json"},
                )
            response.raise_for_status()
            data = response.json()
            thumb = _validated_remote_url(data.get("th_url"))
            # Pixhost documents the paired tN/thumbs and imgN/images URL convention.
            direct = re.sub(r"//t(\d+)\.", r"//img\1.", thumb).replace("/thumbs/", "/images/")
            if direct == thumb:
                raise ImageHostError("Pixhost 返回了无法识别的原图链接")
            return {
                "remote_url": _validated_remote_url(direct),
                "thumbnail_url": thumb,
                "remote_id": str(data.get("show_url") or ""),
            }
        except httpx.HTTPStatusError as exc:
            raise ImageHostError(
                f"Pixhost 上传失败（HTTP {exc.response.status_code}），请稍后重试"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ImageHostError("Pixhost 上传超时，请检查网络后重试") from exc
        except httpx.HTTPError as exc:
            raise ImageHostError(f"Pixhost 连接失败：{exc}，请检查网络后重试") from exc
        except (OSError, ValueError) as exc:
            raise ImageHostError(f"Pixhost 图片读取或响应解析失败：{exc}") from exc


def _stat_fingerprint(path: Path) -> str:
    info = path.stat()
    return hashlib.sha256(
        f"{info.st_size}:{info.st_mtime_ns}:{info.st_ino}".encode("ascii")
    ).hexdigest()


def _format_link(record: Mapping[str, Any], output_format: str) -> str:
    url = str(record.get("remote_url") or "")
    if not url:
        return ""
    if output_format == "html":
        alt = str(record.get("source_name") or "image").replace('"', "&quot;")
        return f'<a href="{url}"><img src="{url}" alt="{alt}"></a>'
    if output_format == "markdown":
        return f"![{record.get('source_name') or 'image'}]({url})"
    return f"[img]{url}[/img]" if output_format == "bbcode" else url


class ImageHostService:
    def __init__(
        self,
        files: FileService,
        database: Database,
        secrets: SecretBox,
        *,
        client_factory: Callable[[str, str, float], ImageHostClient] | None = None,
    ) -> None:
        self.files = files
        self.database = database
        self.secrets = secrets
        self._client_factory = client_factory or self._build_client

    def _stored(self) -> dict[str, str]:
        return self.database.get_settings()

    def _secret(self, key: str) -> str:
        raw = self._stored().get(key, "")
        if not raw:
            return ""
        try:
            return self.secrets.decrypt_json(raw)
        except ValueError as exc:
            raise ImageHostError("已保存的图床密钥无法解密，请在设置中重新填写") from exc

    def status(self) -> dict[str, Any]:
        stored = self._stored()
        provider = stored.get("image_host_provider", "catbox")
        if provider not in PROVIDERS:
            provider = "catbox"
        configured = provider in {"catbox", "pixhost"} or bool(
            stored.get("image_host_imgbb_api_key")
        )
        return {
            "provider": provider,
            "configured": configured,
            "providers": list(PROVIDERS),
            "limits": {"max_bytes": MAX_UPLOAD_BYTES},
        }

    def _build_client(self, provider: str, api_key: str, _timeout: float) -> ImageHostClient:
        if provider == "catbox":
            return CatboxClient()
        if provider == "imgbb":
            return ImgBBClient(api_key)
        if provider == "pixhost":
            return PixhostClient()
        raise ImageHostError("不支持的图床服务")

    def _provider(self, requested: str | None) -> tuple[str, float]:
        stored = self._stored()
        provider = (requested or stored.get("image_host_provider") or "catbox").strip()
        if provider not in PROVIDERS:
            raise ImageHostError("不支持的图床服务")
        try:
            timeout = float(stored.get("image_host_timeout_seconds", "60"))
        except ValueError:
            timeout = 60.0
        timeout = min(max(timeout, 5.0), 300.0)
        return provider, timeout

    def _upload_path(
        self,
        path: Path,
        media_type: str,
        *,
        provider: str,
        timeout: float,
    ) -> dict[str, str]:
        if path.is_symlink() or not path.is_file():
            raise ImageHostError("只能上传普通图片文件")
        if path.stat().st_size <= 0:
            raise ImageHostError("图片文件为空")
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise ImageHostError("图片超过 25 MiB 上传限制")
        api_key = self._secret("image_host_imgbb_api_key") if provider == "imgbb" else ""
        client = self._client_factory(provider, api_key, timeout)
        result = client.upload(path, media_type, timeout)
        return {
            "remote_url": _validated_remote_url(result.get("remote_url")),
            "thumbnail_url": _validated_remote_url(
                result.get("thumbnail_url") or result.get("remote_url")
            ),
            "remote_id": str(result.get("remote_id") or ""),
        }

    def upload_file(
        self,
        *,
        root_key: str,
        relative_path: str,
        provider: str | None = None,
        session_id: str | None = None,
        session_item_id: str | None = None,
    ) -> dict[str, Any]:
        path, relative = self.files.resolve(
            root_key, relative_path, must_exist=True, allow_empty=False
        )
        image_path, media_type = self.files.image(root_key=root_key, relative_path=relative)
        if image_path.resolve() != path.resolve():
            raise ImageHostError("图片路径校验失败")
        provider_name, timeout = self._provider(provider)
        fingerprint = _stat_fingerprint(path)
        upload_id = self.database.create_image_upload(
            provider=provider_name,
            source_kind="screenshot" if session_id and session_item_id else "file",
            source_root_key=root_key,
            source_relative_path=relative,
            source_name=path.name,
            source_size_bytes=path.stat().st_size,
            source_fingerprint=fingerprint,
            session_id=session_id,
            session_item_id=session_item_id,
        )
        self.database.update_image_upload(
            upload_id,
            status="uploading",
            attempts=1,
            error=None,
        )
        try:
            result = self._upload_path(path, media_type, provider=provider_name, timeout=timeout)
            if _stat_fingerprint(path) != fingerprint:
                raise ImageHostError("上传期间本地图片发生变化，结果未保存")
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            self.database.update_image_upload(
                upload_id,
                status="failed",
                error=message[:1000],
            )
            record = self.database.get_image_upload(upload_id)
            if record is None:
                raise ImageHostError("图床记录创建失败") from exc
            return record
        self.database.update_image_upload(
            upload_id,
            status="completed",
            remote_url=result["remote_url"],
            thumbnail_url=result["thumbnail_url"],
            remote_id=result["remote_id"],
            error=None,
        )
        record = self.database.get_image_upload(upload_id)
        if record is None:
            raise ImageHostError("图床记录保存失败")
        return record

    def retry(self, upload_id: int) -> dict[str, Any]:
        record = self.database.get_image_upload(upload_id)
        if record is None:
            raise ImageHostError("图床记录不存在")
        path, relative = self.files.resolve(
            str(record["source_root_key"]),
            str(record["source_relative_path"]),
            must_exist=True,
            allow_empty=False,
        )
        _image_path, media_type = self.files.image(
            root_key=str(record["source_root_key"]), relative_path=relative
        )
        if (
            record.get("source_fingerprint")
            and _stat_fingerprint(path) != record["source_fingerprint"]
        ):
            self.database.update_image_upload(
                upload_id,
                status="failed",
                error="本地图片已变化，请重新选择后上传",
            )
            updated = self.database.get_image_upload(upload_id)
            if updated is None:
                raise ImageHostError("图床记录不存在")
            return updated
        provider, timeout = self._provider(str(record.get("provider") or ""))
        attempts = int(record.get("attempts") or 0) + 1
        self.database.update_image_upload(
            upload_id,
            status="uploading",
            attempts=attempts,
            error=None,
        )
        try:
            result = self._upload_path(path, media_type, provider=provider, timeout=timeout)
        except Exception as exc:
            self.database.update_image_upload(
                upload_id,
                status="failed",
                error=(str(exc) or exc.__class__.__name__)[:1000],
            )
        else:
            self.database.update_image_upload(
                upload_id,
                status="completed",
                remote_url=result["remote_url"],
                thumbnail_url=result["thumbnail_url"],
                remote_id=result["remote_id"],
                error=None,
            )
        updated = self.database.get_image_upload(upload_id)
        if updated is None:
            raise ImageHostError("图床记录不存在")
        return updated

    def attach_to_session(self, session: dict[str, Any]) -> dict[str, Any]:
        uploads = self.database.latest_uploads_for_session_items(str(session.get("id") or ""))
        for video in session.get("videos") or []:
            for item in video.get("items") or []:
                upload = uploads.get(str(item.get("id") or ""))
                if upload:
                    fresh = True
                    try:
                        path, _ = self.files.resolve(
                            upload["source_root_key"],
                            upload["source_relative_path"],
                            must_exist=True,
                        )
                        fresh = _stat_fingerprint(path) == upload.get("source_fingerprint")
                    except FileServiceError:
                        fresh = False
                    if not fresh and upload["status"] == "completed":
                        upload = {**upload, "status": "stale", "error": "截图已变化，请重新上传"}
                    item["upload"] = {
                        "id": upload["id"],
                        "status": upload["status"],
                        "url": upload.get("remote_url"),
                        "thumbnail_url": upload.get("thumbnail_url"),
                        "error": upload.get("error"),
                        "provider": upload.get("provider"),
                        "uploaded_at": (
                            upload.get("updated_at")
                            if upload.get("status") == "completed"
                            else None
                        ),
                    }
        return session

    def list_records(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.database.list_image_uploads(limit=limit, offset=offset)

    def format_links(
        self, upload_ids: list[int], output_format: str, variant="original"
    ) -> dict[str, Any]:
        if output_format not in {"raw", "bbcode", "html", "markdown"}:
            raise ImageHostError("不支持的引用格式")
        records: list[dict[str, Any]] = []
        missing: list[int] = []
        for upload_id in upload_ids:
            record = self.database.get_image_upload(upload_id)
            if record is None or record.get("status") != "completed":
                missing.append(upload_id)
                continue
            if variant == "thumbnail":
                record = {
                    **record,
                    "remote_url": record.get("thumbnail_url") or record.get("remote_url"),
                }
            records.append(record)
        links = [_format_link(record, output_format) for record in records]
        return {
            "format": output_format,
            "links": links,
            "content": "\n".join(link for link in links if link),
            "missing": missing,
        }
