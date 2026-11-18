from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import shutil
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from .bili import BiliAPIError
from .collage import CollageConflictError, CollageError
from .config import Settings
from .downloader import DownloadFailure, create_job_log_path
from .file_service import (
    FileConflictError,
    FileServiceError,
    OperationPreviewExpired,
)
from .filebrowser import (
    FileRenameError,
    analyze_file_rename,
    apply_file_rename,
    preview_file_rename,
)
from .image_hosts import ImageHostError
from .integrations import integration_status
from .naming import NamingError, create_preview
from .processing import task_log_content
from .publishing import PublishError
from .release_info import ReleaseInfoError
from .screenshots import ScreenshotConflictError, ScreenshotError
from .security import SessionData
from .services import Services
from .settings_registry import (
    DEFINITIONS_BY_KEY,
    RUNTIME_IDLE_KEYS,
    RUNTIME_SETTING_KEYS,
    SECRET_SETTING_KEYS,
    parse_setting_value,
    parse_stored_values,
    schema_payload,
    static_values,
)
from .torrents import TorrentError
from .validation import (
    ValidationError,
    canonicalize_bilibili_url,
    compile_format_profile,
    render_episode_template,
    validate_api_mode,
    validate_format_selector,
    validate_output_directory,
    validate_output_template,
)
from .v2.download import project_download_job
from .v2.download.quality import exact_tracks
from .v2.download.naming import NameOptions, NamePreviewRequest, download_plan, episode_facts, initial_filename, url_facts, without_video_part
from .workspace import (
    WorkspaceError,
    normalize_relative_path,
    resolve_workspace_path,
)

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=APP_DIR / "templates")


def asset_version() -> str:
    paths = (
        APP_DIR / "static" / "app.js",
        APP_DIR / "static" / "collage.js",
        APP_DIR / "static" / "file-browser.js",
        APP_DIR / "static" / "modules.js",
        APP_DIR / "static" / "media-tools.js",
        APP_DIR / "static" / "media-tools.css",
        APP_DIR / "static" / "ui.js",
        APP_DIR / "static" / "ui.css",
        APP_DIR / "static" / "settings.js",
        APP_DIR / "static" / "settings-fields.js",
        APP_DIR / "static" / "settings-profiles.js",
        APP_DIR / "static" / "styles.css",
        APP_DIR / "static" / "workspace.css",
    )
    paths += tuple((APP_DIR / "static" / "v2").rglob("*.js")) + tuple((APP_DIR / "static" / "v2").rglob("*.css"))
    try:
        return str(max(path.stat().st_mtime_ns for path in paths))
    except OSError:
        return "1"


class LoginRequest(BaseModel):
    username: str
    password: str


class ResolveRequest(BaseModel):
    url: str
    api_mode: str = "web"

    @field_validator("api_mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        return validate_api_mode(value)


class JobCreateRequest(BaseModel):
    url: str
    api_mode: str = "web"
    format_selector: str = "bv*+ba/b"
    all_parts: bool = False
    output_template: str | None = None
    output_dir: str | None = None
    title: str | None = None
    uploader: str | None = None
    naming: NameOptions | None = None

    @field_validator("output_template")
    @classmethod
    def validate_template(cls, value: str | None) -> str | None:
        return validate_output_template(value) if value is not None else None

    @field_validator("output_dir")
    @classmethod
    def validate_directory(cls, value: str | None) -> str | None:
        return validate_output_directory(value) if value is not None else None

    @field_validator("api_mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        return validate_api_mode(value)


class BatchFormatProfile(BaseModel):
    height: int = Field(ge=144, le=4320)
    fps_high: bool | None = None
    vcodec: Literal["avc", "hevc", "av1", "any"] = "any"
    dynamic_range: Literal["SDR", "HDR10", "DV", "HLG", "any"] = "any"


class BatchJobCreateRequest(BaseModel):
    url: str
    episode_ids: list[str] = Field(min_length=1, max_length=600)
    api_mode: str = "web"
    format_selector: str = "bv*+ba/b"
    format_profile: BatchFormatProfile | None = None
    episode_selectors: dict[str, str] | None = Field(default=None, max_length=600)
    output_template: str | None = None
    output_dir: str | None = None

    naming: NameOptions | None = None

    @field_validator("output_template")
    @classmethod
    def validate_template(cls, value: str | None) -> str | None:
        return validate_output_template(value) if value is not None else None

    @field_validator("output_dir")
    @classmethod
    def validate_directory(cls, value: str | None) -> str | None:
        return validate_output_directory(value) if value is not None else None

    @field_validator("api_mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        return validate_api_mode(value)


class EpisodeFormatsRequest(BaseModel):
    url: str
    api_mode: str = "web"

    @field_validator("api_mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        return validate_api_mode(value)


class BatchEpisodeFormatsRequest(BaseModel):
    url: str
    episode_ids: list[str] = Field(min_length=1, max_length=600)
    api_mode: str = "web"
    detailed: bool = False

    @field_validator("api_mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        return validate_api_mode(value)


class DirectoryLocatorRequest(BaseModel):
    mount_key: str = Field(min_length=1, max_length=40)
    relative_path: str = ""

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return normalize_relative_path(value)


class SettingsUpdateRequest(BaseModel):
    single_output_template: str | None = None
    playlist_output_template: str | None = None
    directories: dict[str, DirectoryLocatorRequest] | None = None
    download_default_output_dir: str | None = None
    rename_default_season: str | None = None
    rename_default_source: str | None = None
    rename_custom_sources: list[str] | None = None
    values: dict[str, Any] | None = None

    @field_validator("single_output_template", "playlist_output_template")
    @classmethod
    def validate_template(cls, value: str | None) -> str | None:
        return validate_output_template(value) if value is not None else None

    @field_validator("download_default_output_dir")
    @classmethod
    def validate_default_output(cls, value: str | None) -> str | None:
        return validate_output_directory(value) if value is not None else None

    @field_validator("rename_default_season")
    @classmethod
    def validate_default_season(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not re.fullmatch(r"\d{1,3}", value):
            raise ValueError("默认季号必须是 0 到 999 的数字")
        return value.zfill(2)

    @field_validator("rename_default_source")
    @classmethod
    def validate_default_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > 60 or any(ord(char) < 32 for char in value):
            raise ValueError("默认片源类型无效")
        return value


class ReleaseProjectCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    media_type: Literal["movie", "series", "anime", "single", "bluray"] = "series"
    source_type: Literal["bilibili", "local", "bluray", "other"] = "local"
    source_url: str | None = None
    year: int | None = Field(default=None, ge=1888, le=2200)
    season: int | None = Field(default=None, ge=0, le=999)
    expected_episodes: int | None = Field(default=None, ge=0, le=10000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    root_key: Literal["downloads", "imports", "staging", "releases"] = "imports"
    source_path: str = ""
    download_dir: str = ""
    work_dir: str = ""
    final_dir: str = ""
    naming_profile_id: int | None = None
    naming_template: str | None = None
    job_ids: list[int] = Field(default_factory=list, max_length=500)

    @field_validator("source_path", "download_dir", "work_dir", "final_dir")
    @classmethod
    def validate_paths(cls, value: str) -> str:
        return normalize_relative_path(value)

    @field_validator("naming_template")
    @classmethod
    def validate_naming_template(cls, value: str | None) -> str | None:
        if value:
            from .naming import parse_naming_template

            parse_naming_template(value)
        return value


class ReleaseProjectUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    media_type: Literal["movie", "series", "anime", "single", "bluray"] | None = None
    source_type: Literal["bilibili", "local", "bluray", "other"] | None = None
    source_url: str | None = None
    year: int | None = Field(default=None, ge=1888, le=2200)
    season: int | None = Field(default=None, ge=0, le=999)
    expected_episodes: int | None = Field(default=None, ge=0, le=10000)
    metadata: dict[str, Any] | None = None
    status: Literal["draft", "ready", "processing", "review", "completed", "failed"] | None = None
    download_dir: str | None = None
    work_dir: str | None = None
    final_dir: str | None = None
    last_rename_target: Literal["in_place", "releases"] | None = None
    naming_profile_id: int | None = None
    naming_template: str | None = None

    @field_validator("download_dir", "work_dir", "final_dir")
    @classmethod
    def validate_optional_paths(cls, value: str | None) -> str | None:
        return normalize_relative_path(value) if value is not None else None

    @field_validator("naming_template")
    @classmethod
    def validate_optional_naming_template(cls, value: str | None) -> str | None:
        if value:
            from .naming import parse_naming_template

            parse_naming_template(value)
        return value


class WorkspaceScanRequest(BaseModel):
    project_id: int
    root_key: Literal["downloads", "imports", "staging", "releases"]
    relative_path: str = ""
    season: int | None = Field(default=None, ge=0, le=999)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return normalize_relative_path(value)


class ReleaseJobsRequest(BaseModel):
    job_ids: list[int] = Field(default_factory=list, max_length=500)
    mode: Literal["append", "replace"] = "append"


class ReleaseFileUpdateRequest(BaseModel):
    season: int | None = Field(default=None, ge=0, le=999)
    episode: int | None = Field(default=None, ge=0, le=9999)
    title: str | None = Field(default=None, max_length=300)
    language: str | None = Field(default=None, max_length=40)


class NamingProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    media_type: Literal["movie", "series"]
    template: str
    is_default: bool = False

    @field_validator("template")
    @classmethod
    def validate_template(cls, value: str) -> str:
        from .naming import parse_naming_template

        parse_naming_template(value)
        return value


class NamingProfileUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    template: str
    is_default: bool = False

    @field_validator("template")
    @classmethod
    def validate_template(cls, value: str) -> str:
        from .naming import parse_naming_template

        parse_naming_template(value)
        return value


class FileOperationPreviewRequest(BaseModel):
    operation: Literal["copy", "move", "delete"]
    sources: list[dict[str, str]] = Field(min_length=1, max_length=200)
    target: dict[str, str] | None = None
    conflict_policy: Literal["abort", "skip", "overwrite"] = "abort"


class FileOperationApplyRequest(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    confirm_overwrite: bool = False


class CollageSourceRequest(BaseModel):
    disc_selection: str | None = Field(default=None, max_length=5, pattern=r"^\d{2,5}$")
    root_key: str = Field(min_length=1, max_length=40)
    relative_path: str = Field(min_length=1, max_length=4096)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return normalize_relative_path(value, allow_empty=False)


class CollageCreateRequest(BaseModel):
    sources: list[CollageSourceRequest] = Field(min_length=1, max_length=100)
    layout_mode: Literal["grid", "count"] = "grid"
    columns: int | None = Field(default=None, ge=1, le=10)
    rows: int | None = Field(default=None, ge=1, le=12)
    total: int | None = Field(default=None, ge=1, le=120)
    thumbnail_width: int = Field(default=480, ge=240, le=1920)
    content_type: Literal["movie", "series", "anime", "custom"] = "anime"
    skip_start_seconds: float | None = Field(default=None, ge=0, le=86400)
    skip_end_seconds: float | None = Field(default=None, ge=0, le=86400)
    output_relative_path: str = ""
    overwrite: bool = False

    @field_validator("output_relative_path")
    @classmethod
    def validate_output_path(cls, value: str) -> str:
        return normalize_relative_path(value)

    @field_validator("skip_start_seconds", "skip_end_seconds")
    @classmethod
    def validate_finite_seconds(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("跳过时长必须是有限数字")
        return value


class ScreenshotCreateRequest(BaseModel):
    columns: int | None = Field(default=None, ge=1, le=10)
    rows: int | None = Field(default=None, ge=1, le=12)
    thumbnail_width: int | None = Field(default=None, ge=240, le=1920)
    # Opt-in final output workflow. Omitted by existing screenshot API callers.
    output_mode: Literal["single", "collage"] | None = None
    format: Literal["png", "jpg"] | None = None
    style: Literal["script", "plain"] | None = None
    sampling_mode: Literal["random", "uniform"] | None = None
    hdr_processor: Literal["auto", "libplacebo", "zscale"] | None = None
    auto_upload: bool | None = None
    upload_variant: Literal["raw", "styled"] | None = None
    sources: list[CollageSourceRequest] = Field(min_length=1, max_length=100)
    total: int | None = Field(default=None, ge=1, le=120)
    timestamps: list[str] = Field(default_factory=list, max_length=120)
    width: int | None = Field(default=None, ge=0, le=3840)
    content_type: Literal["movie", "series", "anime", "custom"] | None = None
    start_percent: float | None = Field(default=None, ge=0, lt=100)
    end_percent: float | None = Field(default=None, gt=0, le=100)
    skip_start_seconds: float | None = Field(default=None, ge=0, le=86400)
    skip_end_seconds: float | None = Field(default=None, ge=0, le=86400)
    output_relative_path: str | None = None

    @field_validator("output_relative_path")
    @classmethod
    def validate_output_path(cls, value: str) -> str:
        return normalize_relative_path(value) if value is not None else None

    @field_validator("skip_start_seconds", "skip_end_seconds")
    @classmethod
    def validate_finite_seconds(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("跳过时长必须是有限数字")
        return value


class ScreenshotRecaptureRequest(BaseModel):
    timestamp: str | None = Field(default=None, max_length=20)


class ScreenshotOrderRequest(BaseModel):
    orders: dict[str, list[str]] = Field(min_length=1, max_length=100)


class ScreenshotSelectionRequest(BaseModel):
    video_id: str = Field(min_length=1, max_length=64)
    item_ids: list[str] = Field(min_length=1, max_length=120)


class ScreenshotCollageRequest(BaseModel):
    format: Literal["png", "jpg"] | None = None
    selections: list[ScreenshotSelectionRequest] = Field(min_length=1, max_length=100)
    layout_mode: Literal["auto", "custom"] | None = None
    columns: int | None = Field(default=None, ge=1, le=10)
    rows: int | None = Field(default=None, ge=1, le=12)
    thumbnail_width: int | None = Field(default=None, ge=240, le=1920)
    gap: int | None = Field(default=None, ge=0, le=40)
    output_relative_path: str | None = None
    overwrite: bool = False

    @field_validator("output_relative_path")
    @classmethod
    def validate_output_path(cls, value: str) -> str:
        return normalize_relative_path(value) if value is not None else None


class FileTextSaveRequest(BaseModel):
    root_key: str
    relative_path: str
    content: str
    fingerprint: str = Field(min_length=64, max_length=64)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return normalize_relative_path(value, allow_empty=False)


class FileOrdinaryRenameRequest(BaseModel):
    root_key: str
    relative_path: str
    new_name: str = Field(min_length=1, max_length=240)
    fingerprint: str | None = None

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return normalize_relative_path(value, allow_empty=False)


class RenamePreviewRequest(BaseModel):
    template: str | None = None
    target_mode: Literal["in_place", "releases"] | None = None
    file_ids: list[int] = Field(default_factory=list, max_length=2000)

    @field_validator("template")
    @classmethod
    def validate_template(cls, value: str | None) -> str | None:
        if value:
            from .naming import parse_naming_template

            parse_naming_template(value)
        return value


class RenameApplyRequest(BaseModel):
    preview_token: str = Field(min_length=10, max_length=200)


class FileRenameAnalyzeRequest(BaseModel):
    root_key: Literal["downloads", "torrents", "images", "release_info"]
    directory: str = ""
    files: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, value: str) -> str:
        return normalize_relative_path(value)


class FileRenameRequest(FileRenameAnalyzeRequest):
    template: str = Field(min_length=1, max_length=500)
    episode_mode: Literal["detected", "start", "offset"] = "detected"
    start_episode: int = Field(default=1, ge=0, le=100000)
    episode_step: int = Field(default=1, ge=1, le=1000)
    episode_offset: int = Field(default=0, ge=-100000, le=100000)
    episode_direction: Literal["increment", "decrement"] = "increment"
    episode_scope: Literal["all", "detected", "missing"] = "all"
    file_sort: (
        Literal["natural", "name", "name_desc", "mtime", "mtime_desc", "selection"] | None
    ) = None
    episode_overrides: dict[str, int | str] = Field(default_factory=dict)
    media: dict[str, dict[str, str]] = Field(default_factory=dict)
    file_values: dict[str, dict[str, str]] = Field(default_factory=dict)
    values: dict[str, str] = Field(default_factory=dict)
    manual_names: dict[str, str] = Field(default_factory=dict)


class FileRenameApplyRequest(FileRenameRequest):
    fingerprint: str = Field(min_length=64, max_length=64)


class UploadSourceRequest(BaseModel):
    root_key: Literal["downloads", "torrents", "images", "release_info"] = "images"
    relative_path: str
    session_item_id: str | None = None

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return normalize_relative_path(value, allow_empty=False)


class ImageUploadRequest(BaseModel):
    items: list[UploadSourceRequest] = Field(min_length=1, max_length=50)
    provider: Literal["catbox", "imgbb", "pixhost"] | None = None
    session_id: str | None = None


class ScreenshotUploadRequest(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=240)
    provider: Literal["catbox", "imgbb", "pixhost"] | None = None


class ReleaseInfoAnalyzeRequest(BaseModel):
    disc_selection: str | None = Field(default=None, max_length=5, pattern=r"^\d{2,5}$")
    representative_files: list[str] | None = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)
    root_key: Literal["downloads", "torrents", "images", "release_info"] = "downloads"
    relative_path: str
    title: str | None = None
    format: Literal["markdown", "bbcode", "text"] | None = None

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return normalize_relative_path(value, allow_empty=False)


class ReleaseInfoCreateRequest(ReleaseInfoAnalyzeRequest):
    output_relative_path: str = ""
    filename: str | None = None
    description: str | None = None
    overwrite: bool = False

    @field_validator("output_relative_path")
    @classmethod
    def validate_output_path(cls, value: str) -> str:
        return normalize_relative_path(value)


class ReleaseInfoUpdateRequest(BaseModel):
    metadata: dict[str, Any] | None = None
    description: str = Field(max_length=2_000_000)
    output_relative_path: str = ""
    write_file: bool = True
    overwrite: bool = True

    @field_validator("output_relative_path")
    @classmethod
    def validate_output_path(cls, value: str) -> str:
        return normalize_relative_path(value)


class TorrentCreateRequest(BaseModel):
    source_root_key: Literal["downloads", "torrents", "images", "release_info"] = "downloads"
    source_relative_path: str
    output_relative_path: str | None = None
    filename: str | None = None
    name: str | None = None
    trackers: list[str] | None = Field(default=None, max_length=20)
    private: bool | None = None
    piece_length: str | None = None
    source: str | None = None
    overwrite: bool = False

    @field_validator("source_relative_path")
    @classmethod
    def validate_source_path(cls, value: str) -> str:
        return normalize_relative_path(value, allow_empty=False)

    @field_validator("output_relative_path")
    @classmethod
    def validate_output_path(cls, value: str) -> str:
        return normalize_relative_path(value) if value is not None else None


class PublishCreateRequest(BaseModel):
    title: str = Field(default="", max_length=240)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_root_key: Literal["downloads", "torrents", "images", "release_info"] | None = None
    source_relative_path: str | None = None
    release_document_id: int | None = Field(default=None, ge=1)
    torrent_build_id: int | None = Field(default=None, ge=1)
    image_upload_ids: list[int] = Field(default_factory=list, max_length=240)
    description: str = Field(default="", max_length=2_000_000)

    @field_validator("source_relative_path")
    @classmethod
    def validate_source_path(cls, value: str | None) -> str | None:
        return normalize_relative_path(value) if value else None


class PublishUpdateRequest(BaseModel):
    metadata: dict[str, Any] | None = None
    title: str | None = Field(default=None, max_length=240)
    source_root_key: Literal["downloads", "torrents", "images", "release_info"] | None = None
    source_relative_path: str | None = None
    release_document_id: int | None = Field(default=None, ge=1)
    torrent_build_id: int | None = Field(default=None, ge=1)
    image_upload_ids: list[int] | None = Field(default=None, max_length=240)
    description: str | None = Field(default=None, max_length=2_000_000)

    @field_validator("source_relative_path")
    @classmethod
    def validate_source_path(cls, value: str | None) -> str | None:
        return normalize_relative_path(value) if value else None


class MetadataSaveRequest(BaseModel):
    id: int | None = Field(default=None, ge=1)
    fields: dict[str, Any] = Field(default_factory=dict)
    source_root_key: str = ""
    source_relative_path: str = ""


class PTGenRequest(BaseModel):
    url: str = Field(max_length=500)


def create_app(settings: Settings | None = None) -> FastAPI:
    services = Services.build(settings or Settings.from_env())
    app_settings = services.settings

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await services.startup()
        try:
            yield
        finally:
            await services.shutdown()

    app = FastAPI(
        title=app_settings.app_name,
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.services = services
    from .v2.router import register as register_v2
    register_v2(app, services)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

    def get_services(request: Request) -> Services:
        return request.app.state.services

    def get_session(
        request: Request,
        service_container: Services = Depends(get_services),
    ) -> SessionData:
        token = request.cookies.get(service_container.sessions.cookie_name)
        session = service_container.sessions.parse(token)
        if not session:
            raise HTTPException(status_code=401, detail="请先登录控制台")
        return session

    def require_csrf(
        request: Request,
        session: SessionData = Depends(get_session),
    ) -> SessionData:
        header = request.headers.get("X-CSRF-Token", "")
        if not header or not hmac.compare_digest(header, session.csrf):
            raise HTTPException(status_code=403, detail="CSRF 校验失败")
        return session

    def client_key(request: Request) -> str:
        real_ip = request.headers.get("X-Real-IP", "").strip()
        return real_ip or (request.client.host if request.client else "unknown")

    @app.exception_handler(ValidationError)
    async def validation_error_handler(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(WorkspaceError)
    async def workspace_error_handler(_: Request, exc: WorkspaceError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(NamingError)
    async def naming_error_handler(_: Request, exc: NamingError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(FileRenameError)
    async def file_rename_error_handler(_: Request, exc: FileRenameError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(FileConflictError)
    async def file_conflict_error_handler(_: Request, exc: FileConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(OperationPreviewExpired)
    async def operation_preview_expired_handler(
        _: Request, exc: OperationPreviewExpired
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "code": "preview_expired"},
        )

    @app.exception_handler(FileServiceError)
    async def file_service_error_handler(_: Request, exc: FileServiceError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ImageHostError)
    async def image_host_error_handler(_: Request, exc: ImageHostError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(TorrentError)
    async def torrent_error_handler(_: Request, exc: TorrentError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ReleaseInfoError)
    async def release_info_error_handler(_: Request, exc: ReleaseInfoError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(PublishError)
    async def publish_error_handler(_: Request, exc: PublishError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    @app.get("/settings", response_class=HTMLResponse)
    async def index(
        request: Request,
        service_container: Services = Depends(get_services),
    ) -> Response:
        token = request.cookies.get(service_container.sessions.cookie_name)
        session = service_container.sessions.parse(token)
        if not session:
            response = templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "app_name": app_settings.app_name,
                    "asset_version": asset_version(),
                },
            )
            response.headers["Cache-Control"] = "no-store"
            return response
        response = templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "app_name": app_settings.app_name,
                "csrf": session.csrf,
                "username": session.username,
                "asset_version": asset_version(),
            },
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/api/auth/login")
    async def login(
        payload: LoginRequest,
        request: Request,
        response: Response,
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        key = client_key(request)
        if not service_container.login_limiter.check(key):
            raise HTTPException(status_code=429, detail="登录尝试过多，请稍后再试")
        username_ok = hmac.compare_digest(payload.username, app_settings.admin_username)
        password_ok = service_container.passwords.verify(payload.password)
        if not username_ok or not password_ok:
            service_container.login_limiter.fail(key)
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        service_container.login_limiter.reset(key)
        token, session = service_container.sessions.issue(payload.username)
        response.set_cookie(
            service_container.sessions.cookie_name,
            token,
            max_age=app_settings.session_max_age_seconds,
            httponly=True,
            secure=app_settings.secure_cookies,
            samesite="lax",
            path="/",
        )
        return {"authenticated": True, "csrf": session.csrf}

    @app.get("/api/auth/session")
    async def session_info(
        session: SessionData = Depends(get_session),
    ) -> dict[str, Any]:
        return {
            "authenticated": True,
            "username": session.username,
            "csrf": session.csrf,
        }

    @app.post("/api/auth/logout")
    async def logout(
        response: Response,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, bool]:
        response.delete_cookie(
            service_container.sessions.cookie_name,
            path="/",
            secure=app_settings.secure_cookies,
            httponly=True,
            samesite="lax",
        )
        return {"ok": True}

    @app.get("/api/bili/status")
    async def bili_status(
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.bili.status()

    @app.post("/api/bili/qr")
    async def create_bili_qr(
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            return await service_container.bili.client.create_qr()
        except BiliAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/bili/qr/{session_id}")
    async def poll_bili_qr(
        session_id: str,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", session_id):
            raise HTTPException(status_code=400, detail="二维码会话无效")
        try:
            result = await service_container.bili.client.poll_qr(session_id)
        except BiliAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if result.get("status") == "authorized":
            cookies = result.pop("cookies", {})
            account = result.get("account") or {}
            service_container.bili.save_login(cookies, account)
            result["account"] = service_container.bili.status()
        return result

    @app.post("/api/bili/logout")
    async def bili_logout(
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, bool]:
        service_container.bili.logout()
        return {"ok": True}

    @app.post("/api/bili/tv/qr")
    async def create_tv_qr(
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            return await service_container.bili.tv_client.create_qr()
        except BiliAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/bili/tv/qr/{session_id}")
    async def poll_tv_qr(
        session_id: str,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", session_id):
            raise HTTPException(status_code=400, detail="二维码会话无效")
        try:
            result = await service_container.bili.tv_client.poll_qr(session_id)
        except BiliAPIError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if result.get("status") == "authorized":
            access_token = result.pop("access_token", "")
            refresh_token = result.pop("refresh_token", None)
            service_container.bili.save_tv_login(
                access_token, refresh_token, result.get("account") or {}
            )
            result["account"] = service_container.bili.status()
        return result

    @app.post("/api/bili/tv/logout")
    async def tv_logout(
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, bool]:
        service_container.bili.tv_logout()
        return {"ok": True}

    @app.post("/api/download/naming/preview")
    def preview_download_naming(
        payload: NamePreviewRequest,
        _: SessionData = Depends(require_csrf),
    ) -> dict[str, Any]:
        if payload.options.mode == "source":
            return {"items": []}
        items = []
        for facts in payload.items:
            identifier = facts.identifier or ""
            episode_id = identifier[2:] if identifier.startswith("ep") else None
            plan = download_plan(payload.title, facts, payload.options, episode_id=episode_id)
            items.append(initial_filename(plan))
        return {"items": items}

    @app.post("/api/resolve")
    async def resolve(
        payload: ResolveRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        url = await canonicalize_bilibili_url(payload.url)
        try:
            resolved = await service_container.runner.resolve(url, payload.api_mode)
        except ValidationError:
            raise
        except DownloadFailure as exc:
            raise HTTPException(
                status_code=422 if exc.auth_required else 502, detail=str(exc)
            ) from exc
        service_container.database.save_history(
            source_url=url,
            api_mode=resolved.api_mode,
            title=resolved.title,
            uploader=resolved.uploader,
            duration=resolved.duration,
            is_playlist=resolved.is_playlist,
            playlist_count=resolved.playlist_count,
            default_episode_id=resolved.default_episode_id,
            formats=resolved.formats,
            episodes=resolved.episodes,
        )
        naming_items = episode_facts(resolved.episodes)
        naming_video = url_facts(url)
        naming_video.chapter = getattr(resolved, "naming_chapter", None)
        naming_parts = getattr(resolved, "naming_parts", None) or []
        first_part = getattr(resolved, "naming_part", None) or (naming_parts[0] if naming_parts else None)
        if first_part and not naming_video.number:
            naming_video.number_kind, naming_video.number = "part", first_part
        return {
            "source_url": url,
            "naming_items": {key: facts.model_dump() for key, facts in naming_items.items()},
            "naming_video": naming_video.model_dump(),
            "naming_parts": naming_parts,
            "title": resolved.title,
            "uploader": resolved.uploader,
            "duration": resolved.duration,
            "webpage_url": resolved.webpage_url,
            "is_playlist": resolved.is_playlist,
            "playlist_count": resolved.playlist_count,
            "formats": resolved.formats,
            "api_mode": resolved.api_mode,
            "episodes": resolved.episodes,
            "default_episode_id": resolved.default_episode_id,
            "warning": resolved.warning,
            "bili_logged_in": service_container.bili.status()["logged_in"],
            "tv_logged_in": service_container.bili.status()["tv_logged_in"],
        }

    @app.post("/api/episodes/formats")
    async def episode_formats(
        payload: EpisodeFormatsRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            result = await service_container.runner.episode_formats(payload.url, payload.api_mode)
        except ValidationError:
            raise
        except DownloadFailure as exc:
            raise HTTPException(
                status_code=422 if exc.auth_required else 502, detail=str(exc)
            ) from exc
        status = service_container.bili.status()
        return {
            **result,
            "api_mode": payload.api_mode,
            "bili_logged_in": status["logged_in"],
            "tv_logged_in": status["tv_logged_in"],
        }

    @app.post("/api/episodes/formats/batch")
    async def batch_episode_formats(
        payload: BatchEpisodeFormatsRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            result = await service_container.runner.batch_episode_formats(
                payload.url, payload.episode_ids, payload.api_mode,
                **({"detailed": True} if payload.detailed else {}),
            )
        except ValidationError:
            raise
        except DownloadFailure as exc:
            raise HTTPException(
                status_code=422 if exc.auth_required else 502, detail=str(exc)
            ) from exc
        status = service_container.bili.status()
        return {
            **result,
            "bili_logged_in": status["logged_in"],
            "tv_logged_in": status["tv_logged_in"],
        }

    @app.get("/api/jobs")
    async def list_jobs(
        limit: int = Query(default=100, ge=1, le=500),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> list[dict[str, Any]]:
        jobs = [
            project_download_job(job)
            for job in service_container.database.list_jobs(limit)
        ]
        for job in jobs:
            job.pop("log_path", None)
        return jobs

    @app.post("/api/jobs")
    async def create_job(
        payload: JobCreateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        url = await canonicalize_bilibili_url(payload.url)
        selector = validate_format_selector(payload.format_selector)
        log_path = create_job_log_path(service_container.settings)
        stored_settings = service_container.database.get_settings()
        output_template = payload.output_template or stored_settings.get(
            "single_output_template", ""
        )
        if not output_template:
            raise HTTPException(status_code=422, detail="缺少默认下载命名模板")
        output_dir = service_container.files.resolve_new_job_output(payload.output_dir)
        smart = payload.naming and payload.naming.mode == "smart"
        job_id = service_container.database.create_job(
            source_url=without_video_part(url) if smart and payload.all_parts else url,
            kind="manual",
            api_mode=payload.api_mode,
            output_template=output_template,
            output_dir=output_dir,
            format_selector=selector,
            all_parts=payload.all_parts,
            title=payload.title,
            uploader=payload.uploader,
            log_path=str(log_path),
            naming_plan=download_plan(payload.title, url_facts(url), payload.naming, all_parts=payload.all_parts)
            if smart else None,
        )
        service_container.manager.wake()
        return {"id": job_id}

    @app.post("/api/jobs/batch")
    async def create_jobs_batch(
        payload: BatchJobCreateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        url = await canonicalize_bilibili_url(payload.url)
        try:
            resolved = await service_container.runner.resolve(url, payload.api_mode)
        except ValidationError:
            raise
        except DownloadFailure as exc:
            raise HTTPException(
                status_code=422 if exc.auth_required else 502, detail=str(exc)
            ) from exc
        if not resolved.episodes:
            raise HTTPException(status_code=422, detail="该链接没有可选集数，请使用普通下载")
        episode_map = {str(item["id"]): item for item in resolved.episodes}
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_id in payload.episode_ids:
            episode_id = str(raw_id).strip()
            if not episode_id or episode_id in seen:
                continue
            episode = episode_map.get(episode_id)
            if not episode:
                raise HTTPException(status_code=422, detail=f"集数 {episode_id} 不属于该番剧")
            seen.add(episode_id)
            selected.append(episode)
        if not selected:
            raise HTTPException(status_code=422, detail="请至少选择一集")
        if len(selected) > 600:
            raise HTTPException(status_code=422, detail="单次最多创建 600 个任务")
        stored_settings = service_container.database.get_settings()
        output_template = payload.output_template or stored_settings.get(
            "playlist_output_template", ""
        )
        if not output_template:
            raise HTTPException(status_code=422, detail="缺少合集默认下载命名模板")
        output_dir = service_container.files.resolve_new_job_output(payload.output_dir)
        episode_selectors = payload.episode_selectors
        if episode_selectors is not None:
            if set(episode_selectors) != seen or any(
                not exact_tracks(value) for value in episode_selectors.values()
            ):
                raise HTTPException(status_code=422, detail="逐集画质与所选剧集不匹配，请重新检测")
            selector = ""
        elif payload.format_profile is not None:
            selector = validate_format_selector(
                compile_format_profile(
                    height=payload.format_profile.height,
                    fps_high=payload.format_profile.fps_high,
                    vcodec=payload.format_profile.vcodec,
                    dynamic_range=payload.format_profile.dynamic_range,
                )
            )
        elif len(selected) > 1:
            selector = "bv*+ba/b"
        else:
            selector = validate_format_selector(payload.format_selector)
        jobs = []
        facts = episode_facts(resolved.episodes)
        for episode in selected:
            template = render_episode_template(
                output_template,
                season_title=resolved.title,
                index=int(episode.get("group_index") or episode.get("index") or 0),
                title=str(episode.get("title") or ""),
                episode_id=str(episode["id"]),
            )
            jobs.append(
                {
                    "source_url": episode.get("url")
                    or f"https://www.bilibili.com/bangumi/play/ep{episode['id']}",
                    "kind": "manual",
                    "api_mode": payload.api_mode,
                    "format_selector": episode_selectors[str(episode["id"])] if episode_selectors is not None else selector,
                    "all_parts": False,
                    "output_template": template,
                    "output_dir": output_dir,
                    "naming_plan": download_plan(resolved.title, facts[str(episode["id"])], payload.naming,
                                                  episode_id=str(episode["id"]))
                    if payload.naming and payload.naming.mode == "smart" else None,
                    "title": episode.get("title"),
                    "uploader": resolved.uploader,
                    "log_path": str(create_job_log_path(service_container.settings)),
                }
            )
        job_ids = service_container.database.create_jobs(jobs)
        service_container.manager.wake()
        return {"ids": job_ids, "count": len(job_ids)}

    @app.post("/api/jobs/{job_id}/retry")
    async def retry_job(
        job_id: int,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, bool]:
        job = service_container.database.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        if job["status"] not in {"failed", "cancelled", "auth_required"}:
            raise HTTPException(status_code=409, detail="当前任务不能重试")
        service_container.database.update_job(
            job_id,
            status="queued",
            error=None,
            progress=0,
            downloaded_bytes=0,
            total_bytes=None,
            speed=None,
            eta=None,
            started_at=None,
            finished_at=None,
            retry_count=int(job["retry_count"]) + 1,
        )
        service_container.manager.wake()
        return {"ok": True}

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(
        job_id: int,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, bool]:
        cancelled = await service_container.manager.cancel_job(job_id)
        if not cancelled:
            raise HTTPException(status_code=409, detail="任务无法取消")
        return {"ok": True}

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(
        job_id: int,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, bool]:
        job = service_container.database.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        if job["status"] in {"queued", "running"}:
            raise HTTPException(status_code=409, detail="运行中的任务需先取消")
        if not service_container.database.delete_job(job_id):
            raise HTTPException(status_code=409, detail="任务状态已变化，请刷新后重试")
        raw_log_path = job.get("log_path")
        if raw_log_path:
            log_path = Path(raw_log_path).resolve()
            logs_dir = service_container.settings.logs_dir.resolve()
            if log_path.is_relative_to(logs_dir):
                try:
                    log_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/log")
    async def job_log(
        job_id: int,
        lines: int = Query(default=120, ge=1, le=500),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        job = service_container.database.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        log_path = Path(job["log_path"]) if job.get("log_path") else None
        if not log_path or not log_path.exists():
            return {"content": ""}
        try:
            raw_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return {"content": ""}
        return {"content": "\n".join(raw_lines[-lines:])}

    @app.get("/api/history")
    async def list_history(
        limit: int = Query(default=100, ge=1, le=200),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> list[dict[str, Any]]:
        return service_container.database.list_history(limit)

    @app.get("/api/history/{history_id}")
    async def history_detail(
        history_id: int,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        record = service_container.database.get_history(history_id)
        if not record:
            raise HTTPException(status_code=404, detail="解析历史不存在")
        try:
            formats = json.loads(record.pop("formats_json") or "[]")
        except json.JSONDecodeError:
            formats = []
        try:
            episodes = json.loads(record.pop("episodes_json") or "[]")
        except json.JSONDecodeError:
            episodes = []
        status = service_container.bili.status()
        return {
            **record,
            "is_playlist": bool(record.get("is_playlist")),
            "formats": formats,
            "episodes": episodes,
            "bili_logged_in": status["logged_in"],
            "tv_logged_in": status["tv_logged_in"],
        }

    @app.delete("/api/history/{history_id}")
    async def delete_history(
        history_id: int,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, bool]:
        if not service_container.database.get_history(history_id):
            raise HTTPException(status_code=404, detail="解析历史不存在")
        service_container.database.delete_history(history_id)
        return {"ok": True}

    @app.delete("/api/history")
    async def clear_history(
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, bool]:
        service_container.database.clear_history()
        return {"ok": True}

    @app.get("/api/workspace/roots")
    async def workspace_roots(
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> list[dict[str, Any]]:
        return [root.payload() for root in service_container.files.roots("workspace")]

    @app.get("/api/files/roots")
    async def files_roots(
        scope: Literal["browse", "mounts"] = "browse",
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> list[dict[str, Any]]:
        return [root.payload() for root in service_container.files.roots(scope)]

    @app.get("/api/files/browse")
    async def files_browse(
        root_key: str,
        path: str = "",
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=100, ge=1, le=1000),
        scope: Literal["browse", "mounts"] = "browse",
        sort: Literal["natural", "name", "mtime", "size"] = "natural",
        direction: Literal["asc", "desc"] = "asc",
        focus: str | None = None,
        search: str = Query(default="", max_length=200),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.files.browse(
            root_key=root_key,
            relative_path=path,
            page=page,
            page_size=page_size,
            scope=scope,
            sort=sort,
            direction=direction,
            focus=focus,
            search=search,
        )

    @app.get("/api/files/meta")
    async def files_meta(
        root_key: str,
        path: str,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.files.meta(root_key=root_key, relative_path=path)

    @app.get("/api/files/text")
    async def files_text(
        root_key: str,
        path: str,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.files.read_text(root_key=root_key, relative_path=path)

    @app.put("/api/files/text")
    async def save_files_text(
        payload: FileTextSaveRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.files.save_text(
            root_key=payload.root_key,
            relative_path=payload.relative_path,
            content=payload.content,
            fingerprint=payload.fingerprint,
        )

    @app.get("/api/files/image")
    async def files_image(
        root_key: str,
        path: str,
        download: bool = False,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> FileResponse:
        image_path, media_type = service_container.files.image(
            root_key=root_key, relative_path=path
        )
        return FileResponse(
            image_path,
            media_type=media_type,
            filename=image_path.name if download else None,
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @app.get("/api/uploads/status")
    async def image_upload_status(
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.image_hosts.status()

    @app.get("/api/uploads")
    async def list_image_uploads(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        session_id: str | None = None,
        status: Literal["queued", "uploading", "completed", "failed"] | None = None,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        records = service_container.database.list_image_uploads(
            session_id=session_id,
            status=status,
            limit=limit,
            offset=offset,
        )
        return {
            "items": records,
            "status": service_container.image_hosts.status(),
            "limit": limit,
            "offset": offset,
        }

    @app.post("/api/uploads")
    async def create_image_uploads(
        payload: ImageUploadRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        for item in payload.items:
            record = await asyncio.to_thread(
                service_container.image_hosts.upload_file,
                root_key=item.root_key,
                relative_path=item.relative_path,
                provider=payload.provider,
                session_id=payload.session_id,
                session_item_id=item.session_item_id,
            )
            records.append(record)
        return {"items": records}

    @app.get("/api/uploads/formats")
    async def image_upload_formats(
        variant: Literal["original", "thumbnail"] = "original",
        ids: list[int] = Query(default_factory=list),
        format: Literal["raw", "bbcode", "html", "markdown"] = "bbcode",
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        if not ids:
            raise HTTPException(status_code=422, detail="请选择至少一张已上传图片")
        return service_container.image_hosts.format_links(ids, format, variant)

    @app.get("/api/uploads/{upload_id}")
    async def get_image_upload(
        upload_id: int,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        record = service_container.database.get_image_upload(upload_id)
        if record is None:
            raise HTTPException(status_code=404, detail="图床记录不存在")
        return record

    @app.post("/api/uploads/{upload_id}/retry")
    async def retry_image_upload(
        upload_id: int,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(service_container.image_hosts.retry, upload_id)

    @app.get("/api/files/collage/status")
    async def collage_status(
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.collage.status()

    @app.get("/api/files/screenshots/status")
    async def screenshot_status(
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.screenshots.status()

    @app.get("/api/files/screenshots/sessions")
    async def screenshot_sessions(
        limit: int = Query(default=50, ge=1, le=200),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return {"sessions": service_container.screenshots.list_sessions(limit=limit)}

    @app.post("/api/files/screenshots", status_code=202)
    async def create_screenshot_session(
        payload: ScreenshotCreateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        prepared = service_container.screenshots.prepare_create(
            payload.model_dump(exclude_none=True)
        )
        task_input = prepared["task_input"]
        try:
            task, created = service_container.processing.enqueue(
                project_id=None,
                task_type="screenshot",
                input_data=task_input,
                idempotency_key=f"screenshot:{prepared['session_id']}",
            )
            service_container.screenshots.attach_task(prepared["session_id"], int(task["id"]))
        except Exception:
            service_container.screenshots.discard_session(prepared["session_id"])
            raise
        return {
            "task": task,
            "created": created,
            "session_id": prepared["session_id"],
            "output_relative_path": prepared["output_relative_path"],
        }

    @app.get("/api/files/screenshots/{session_id}")
    async def get_screenshot_session(
        session_id: str,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        session = service_container.screenshots.get_session(session_id)
        return service_container.image_hosts.attach_to_session(session)

    @app.post("/api/files/screenshots/{session_id}/upload")
    async def upload_screenshot_items(
        session_id: str,
        payload: ScreenshotUploadRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        session = service_container.screenshots.get_session(session_id)
        items_by_id = {
            str(item.get("id")): item
            for video in session.get("videos") or []
            for item in video.get("items") or []
        }
        records: list[dict[str, Any]] = []
        for item_id in payload.item_ids:
            item = items_by_id.get(str(item_id))
            if item is None:
                raise HTTPException(status_code=422, detail="截图不属于当前会话")
            if item.get("status") != "completed" or item.get("available") is False:
                raise HTTPException(status_code=422, detail="失败或缺失的截图不能上传")
            record = await asyncio.to_thread(
                service_container.image_hosts.upload_file,
                root_key="images",
                relative_path=str(item.get("relative_path") or ""),
                provider=payload.provider,
                session_id=session_id,
                session_item_id=str(item_id),
            )
            records.append(record)
        updated = service_container.image_hosts.attach_to_session(
            service_container.screenshots.get_session(session_id)
        )
        return {"session_id": session_id, "uploads": records, "session": updated}

    @app.post("/api/files/screenshots/{session_id}/items/{item_id}/recapture")
    async def recapture_screenshot(
        session_id: str,
        item_id: str,
        payload: ScreenshotRecaptureRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.screenshots.recapture(
            session_id, item_id, timestamp=payload.timestamp
        )

    @app.delete("/api/files/screenshots/{session_id}/items/{item_id}")
    async def delete_screenshot(
        session_id: str,
        item_id: str,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.screenshots.delete_item(session_id, item_id)

    @app.put("/api/files/screenshots/{session_id}/items/order")
    async def order_screenshots(
        session_id: str,
        payload: ScreenshotOrderRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.screenshots.reorder_items(session_id, payload.orders)

    @app.post("/api/files/screenshots/{session_id}/collage", status_code=202)
    async def create_screenshot_collage(
        session_id: str,
        payload: ScreenshotCollageRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            prepared = service_container.screenshots.prepare_collage(
                session_id, payload.model_dump(exclude_none=True)
            )
        except ScreenshotConflictError as exc:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": {
                        "code": "screenshot_collage_conflicts",
                        "message": str(exc),
                        "conflicts": exc.conflicts,
                    }
                },
            )
        except ScreenshotError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        task_input = {
            key: value for key, value in prepared.items() if key not in {"outputs", "conflicts"}
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                {"input": task_input, "outputs": prepared["outputs"]},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        task, created = service_container.processing.enqueue(
            project_id=None,
            task_type="screenshot_collage",
            input_data=task_input,
            idempotency_key=f"screenshot-collage:{fingerprint}",
        )
        service_container.screenshots.attach_collage_task(session_id, int(task["id"]))
        return {
            "task": task,
            "created": created,
            "outputs": prepared["outputs"],
        }

    @app.post("/api/files/collage", status_code=202)
    async def create_collage_task(
        payload: CollageCreateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            prepared = service_container.collage.prepare(payload.model_dump())
        except CollageConflictError as exc:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": {
                        "code": "collage_conflicts",
                        "message": str(exc),
                        "conflicts": exc.conflicts,
                    }
                },
            )
        except CollageError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        input_data = {
            key: value for key, value in prepared.items() if key not in {"outputs", "conflicts"}
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "input": input_data,
                    "outputs": prepared["outputs"],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        task, created = service_container.processing.enqueue(
            project_id=None,
            task_type="collage",
            input_data=input_data,
            idempotency_key=f"collage:{fingerprint}",
        )
        return {
            "task": task,
            "created": created,
            "outputs": prepared["outputs"],
        }

    @app.post("/api/files/rename")
    async def rename_file_or_directory(
        payload: FileOrdinaryRenameRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.files.rename(
            root_key=payload.root_key,
            relative_path=payload.relative_path,
            new_name=payload.new_name,
            fingerprint=payload.fingerprint,
        )

    @app.post("/api/files/operations/preview")
    async def preview_file_operations(
        payload: FileOperationPreviewRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.files.preview_operations(
            operation=payload.operation,
            sources=payload.sources,
            target=payload.target,
            conflict_policy=payload.conflict_policy,
        )

    @app.post("/api/files/operations/apply")
    async def apply_file_operations(
        payload: FileOperationApplyRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        prepared = service_container.files.prepare_operation(
            token=payload.token, confirm_overwrite=payload.confirm_overwrite
        )
        task, created = service_container.processing.enqueue(
            project_id=None,
            task_type="file_operation",
            input_data=prepared,
            idempotency_key=f"file_operation:{payload.token}",
        )
        service_container.files.previews.remove(payload.token)
        return {"task": task, "created": created, "task_id": task["id"]}

    @app.post("/api/files/rename/preview")
    async def files_rename_preview(
        payload: FileRenameRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return preview_file_rename(
            service_container.settings,
            root_key=payload.root_key,
            directory=payload.directory,
            files=payload.files,
            template=payload.template,
            episode_mode=payload.episode_mode,
            start_episode=payload.start_episode,
            episode_step=payload.episode_step,
            episode_offset=payload.episode_offset,
            episode_direction=payload.episode_direction,
            episode_scope=payload.episode_scope,
            file_sort=payload.file_sort,
            episode_overrides=payload.episode_overrides,
            media=payload.media,
            file_values=payload.file_values,
            values=payload.values,
            manual_names=payload.manual_names,
            root_paths=service_container.files.roots_map("browse"),
        )

    @app.post("/api/files/rename/analyze")
    async def files_rename_analyze(
        payload: FileRenameAnalyzeRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return await analyze_file_rename(
            service_container.settings,
            root_key=payload.root_key,
            directory=payload.directory,
            files=payload.files,
            roots=service_container.files.roots_map("browse"),
        )

    @app.post("/api/files/rename/apply")
    async def files_rename_apply(
        payload: FileRenameApplyRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return apply_file_rename(
            service_container.settings,
            root_key=payload.root_key,
            directory=payload.directory,
            files=payload.files,
            template=payload.template,
            episode_mode=payload.episode_mode,
            start_episode=payload.start_episode,
            episode_step=payload.episode_step,
            episode_offset=payload.episode_offset,
            episode_direction=payload.episode_direction,
            episode_scope=payload.episode_scope,
            file_sort=payload.file_sort,
            episode_overrides=payload.episode_overrides,
            media=payload.media,
            file_values=payload.file_values,
            values=payload.values,
            manual_names=payload.manual_names,
            fingerprint=payload.fingerprint,
            root_paths=service_container.files.roots_map("browse"),
        )

    @app.get("/api/release-info/status")
    async def release_info_status(
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.release_info.status()

    @app.get("/api/release-info")
    async def list_release_documents(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return {"items": service_container.release_info.list_documents(limit=limit, offset=offset)}

    @app.post("/api/release-info/analyze")
    async def analyze_release_info(
        payload: ReleaseInfoAnalyzeRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(service_container.release_info.analyze, payload.model_dump())

    @app.post("/api/release-info")
    async def create_release_document(
        payload: ReleaseInfoCreateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(service_container.release_info.create, payload.model_dump())

    @app.get("/api/release-info/{document_id}")
    async def get_release_document(
        document_id: int,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.release_info.get(document_id)

    @app.put("/api/release-info/{document_id}")
    async def update_release_document(
        document_id: int,
        payload: ReleaseInfoUpdateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            service_container.release_info.update,
            document_id,
            payload.model_dump(),
        )

    @app.get("/api/torrents")
    async def list_torrent_builds(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return {"items": service_container.torrents.list_builds(limit=limit, offset=offset)}

    @app.post("/api/torrents")
    async def create_torrent(
        payload: TorrentCreateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(service_container.torrents.create, payload.model_dump())

    @app.get("/api/torrents/{build_id}")
    async def get_torrent_build(
        build_id: int,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        record = service_container.database.get_torrent_build(build_id)
        if record is None:
            raise HTTPException(status_code=404, detail="种子记录不存在")
        record["download_url"] = f"/api/torrents/{build_id}/file"
        return record

    @app.get("/api/torrents/{build_id}/file")
    async def download_torrent_file(
        build_id: int,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> FileResponse:
        path = service_container.torrents.file_path(build_id)
        return FileResponse(
            path,
            media_type="application/x-bittorrent",
            filename=path.name,
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/api/publish/sessions")
    async def list_publish_sessions(
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return {"items": service_container.publishing.list_sessions(limit=limit, offset=offset)}

    @app.post("/api/publish/sessions")
    async def create_publish_session(
        payload: PublishCreateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.publishing.create(payload.model_dump())

    @app.get("/api/publish/sessions/{session_id}")
    async def get_publish_session(
        session_id: int,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.publishing.get(session_id)

    @app.put("/api/publish/sessions/{session_id}")
    async def update_publish_session(
        session_id: int,
        payload: PublishUpdateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.publishing.update(session_id, payload.model_dump())

    @app.get("/api/media/search")
    async def search_media(
        query: str = Query(min_length=1, max_length=200),
        media_type: Literal["tv", "movie"] = "tv",
        year: int | None = None,
        page: int = Query(default=1, ge=1, le=500),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ):
        return await asyncio.to_thread(
            service_container.metadata.search, query, media_type, year, page
        )

    @app.get("/api/media/details/{media_type}/{tmdb_id}")
    async def detail_media(
        media_type: Literal["tv", "movie"],
        tmdb_id: int,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ):
        return await asyncio.to_thread(service_container.metadata.detail, media_type, tmdb_id)

    @app.get("/api/media/ptgen/search")
    async def search_ptgen_media(
        query: str = Query(min_length=1, max_length=200),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ):
        return await asyncio.to_thread(service_container.metadata.ptgen_search, query)

    @app.post("/api/media/ptgen")
    async def ptgen_media(
        payload: PTGenRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ):
        return await asyncio.to_thread(service_container.metadata.ptgen, payload.url)

    @app.get("/api/media/metadata")
    async def list_media_metadata(
        _: SessionData = Depends(get_session), service_container: Services = Depends(get_services)
    ):
        return {"items": service_container.metadata.list()}

    @app.post("/api/media/metadata")
    async def save_media_metadata(
        payload: MetadataSaveRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ):
        return service_container.metadata.save(payload.model_dump())

    @app.get("/api/media/optical")
    async def media_optical(
        root_key: str,
        relative_path: str,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ):
        from .media_tools import optical_choices

        path, _relative = service_container.files.resolve(
            root_key, relative_path, must_exist=True, allow_empty=False
        )
        return await asyncio.to_thread(optical_choices, path)

    @app.post("/api/torrents/jobs", status_code=202)
    async def enqueue_torrent(
        payload: TorrentCreateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ):
        data = service_container.torrents.with_defaults(payload.model_dump(exclude_none=True))
        service_container.files.resolve(
            data["source_root_key"],
            data["source_relative_path"],
            must_exist=True,
            allow_empty=False,
        )
        task, created = service_container.processing.enqueue(
            project_id=None, task_type="torrent_build", input_data=data
        )
        return {"task": task, "created": created}

    @app.post("/api/release-info/jobs", status_code=202)
    async def enqueue_report(
        payload: ReleaseInfoCreateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ):
        data = service_container.release_info.with_defaults(payload.model_dump(exclude_none=True))
        service_container.files.resolve(
            data["root_key"], data["relative_path"], must_exist=True, allow_empty=False
        )
        task, created = service_container.processing.enqueue(
            project_id=None, task_type="release_report", input_data=data
        )
        return {"task": task, "created": created}

    @app.post("/api/uploads/jobs", status_code=202)
    async def enqueue_upload(
        payload: ImageUploadRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ):
        data = payload.model_dump(exclude_none=True)
        data["provider"] = data.get("provider") or service_container.database.get_settings().get(
            "image_host_provider", "catbox"
        )
        for item in data["items"]:
            service_container.files.image(
                root_key=item["root_key"], relative_path=item["relative_path"]
            )
        task, created = service_container.processing.enqueue(
            project_id=None, task_type="image_upload", input_data=data
        )
        return {"task": task, "created": created}

    @app.get("/api/workspace/browse")
    async def workspace_browse(
        root_key: str,
        path: str = "",
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=200, ge=1, le=1000),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return service_container.files.browse(
            root_key=root_key,
            relative_path=path,
            page=page,
            page_size=page_size,
            scope="workspace",
        )

    @app.post("/api/workspace/scan")
    async def workspace_scan(
        payload: WorkspaceScanRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        project = service_container.database.get_release_project(payload.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        resolved, relative = resolve_workspace_path(
            service_container.settings,
            payload.root_key,
            payload.relative_path,
            must_exist=True,
        )
        if not resolved.is_dir() and not resolved.is_file():
            raise HTTPException(status_code=422, detail="扫描路径无效")
        metadata = dict(project.get("metadata") or {})
        metadata.update({"root_key": payload.root_key, "source_path": relative})
        service_container.database.update_release_project(
            payload.project_id,
            download_dir=relative,
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )
        task, created = service_container.processing.enqueue(
            project_id=payload.project_id,
            task_type="scan",
            input_data={
                "root_key": payload.root_key,
                "relative_path": relative,
                "season": payload.season,
            },
            idempotency_key=f"scan:{payload.project_id}:{payload.root_key}:{relative}",
        )
        return {"task": task, "created": created}

    @app.get("/api/releases")
    async def list_releases(
        status: str | None = None,
        query: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> list[dict[str, Any]]:
        return service_container.database.list_release_projects(
            status=status, query=query, limit=limit, offset=offset
        )

    @app.post("/api/releases")
    async def create_release(
        payload: ReleaseProjectCreateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        for job_id in payload.job_ids:
            if not service_container.database.get_job(job_id):
                raise HTTPException(status_code=404, detail=f"下载任务 #{job_id} 不存在")
        if (
            payload.naming_profile_id is not None
            and not service_container.database.get_naming_profile(payload.naming_profile_id)
        ):
            raise HTTPException(status_code=404, detail="命名配置不存在")
        source_path = payload.source_path or payload.download_dir
        metadata = dict(payload.metadata)
        metadata.update({"root_key": payload.root_key, "source_path": source_path})
        project_id = service_container.database.create_release_project(
            title=payload.title.strip(),
            media_type=payload.media_type,
            source_type=payload.source_type,
            source_url=payload.source_url,
            year=payload.year,
            season=payload.season,
            expected_episodes=payload.expected_episodes,
            metadata=metadata,
            download_dir=source_path,
            work_dir=payload.work_dir,
            final_dir=payload.final_dir,
            naming_profile_id=payload.naming_profile_id,
            naming_template=payload.naming_template,
        )
        service_container.database.link_release_jobs(project_id, payload.job_ids)
        service_container.database.create_release_event(
            project_id=project_id,
            event_type="project_created",
            message="创建资源项目",
            payload={"job_ids": payload.job_ids, "root_key": payload.root_key},
        )
        project = service_container.database.get_release_project(project_id)
        if not project:
            raise HTTPException(status_code=500, detail="项目创建失败")
        project["jobs"] = service_container.database.list_release_jobs(project_id)
        return project

    @app.get("/api/releases/{project_id}")
    async def get_release(
        project_id: int,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        project = service_container.database.get_release_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        project["jobs"] = service_container.database.list_release_jobs(project_id)
        project["file_count"] = len(service_container.database.list_release_files(project_id))
        project["active_task_count"] = service_container.database.active_project_task_count(
            project_id
        )
        return project

    @app.patch("/api/releases/{project_id}")
    async def update_release(
        project_id: int,
        payload: ReleaseProjectUpdateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        project = service_container.database.get_release_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        values = payload.model_dump(exclude_unset=True)
        required_fields = {
            "title",
            "media_type",
            "source_type",
            "status",
            "download_dir",
            "work_dir",
            "final_dir",
            "last_rename_target",
        }
        invalid_nulls = sorted(
            key for key in required_fields if key in values and values[key] is None
        )
        if invalid_nulls:
            raise HTTPException(
                status_code=422,
                detail=f"字段不能为空：{', '.join(invalid_nulls)}",
            )
        metadata = values.pop("metadata", None)
        if metadata is not None:
            values["metadata_json"] = json.dumps(
                {**dict(project.get("metadata") or {}), **metadata},
                ensure_ascii=False,
            )
        if "naming_profile_id" in values and values["naming_profile_id"] is not None:
            if not service_container.database.get_naming_profile(values["naming_profile_id"]):
                raise HTTPException(status_code=404, detail="命名配置不存在")
        if values:
            service_container.database.update_release_project(project_id, **values)
            service_container.database.create_release_event(
                project_id=project_id,
                event_type="project_updated",
                message="更新项目信息",
                payload={"fields": sorted(values)},
            )
        updated = service_container.database.get_release_project(project_id)
        if not updated:
            raise HTTPException(status_code=404, detail="项目不存在")
        return updated

    @app.delete("/api/releases/{project_id}")
    async def delete_release(
        project_id: int,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        project = service_container.database.get_release_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        if service_container.database.active_project_task_count(project_id):
            raise HTTPException(status_code=409, detail="项目仍有活动任务，需先取消")
        retained_files = 0
        for record in service_container.database.list_release_files(project_id):
            try:
                path, _ = resolve_workspace_path(
                    service_container.settings,
                    record["root_key"],
                    record["relative_path"],
                    must_exist=False,
                )
                retained_files += int(path.exists())
            except WorkspaceError:
                continue
        log_paths = service_container.database.list_project_task_log_paths(project_id)
        service_container.database.delete_release_project(project_id)
        logs_dir = service_container.settings.logs_dir.resolve()
        removed_logs = 0
        for raw_path in log_paths:
            log_path = Path(raw_path).resolve()
            if not log_path.is_relative_to(logs_dir):
                continue
            try:
                log_path.unlink(missing_ok=True)
                removed_logs += 1
            except OSError:
                pass
        return {
            "ok": True,
            "retained_file_count": retained_files,
            "removed_log_count": removed_logs,
            "message": "仅删除项目记录，媒体文件保留在原位置",
        }

    @app.get("/api/releases/{project_id}/files")
    async def list_release_files(
        project_id: int,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        if not service_container.database.get_release_project(project_id):
            raise HTTPException(status_code=404, detail="项目不存在")
        return {
            "files": service_container.database.list_release_files(project_id),
            "links": service_container.database.list_release_file_links(project_id),
        }

    @app.patch("/api/releases/{project_id}/files/{file_id}")
    async def update_release_file(
        project_id: int,
        file_id: int,
        payload: ReleaseFileUpdateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        record = service_container.database.get_release_file(file_id)
        if not record or int(record["project_id"]) != project_id:
            raise HTTPException(status_code=404, detail="文件记录不存在")
        values = payload.model_dump(exclude_unset=True)
        if values:
            service_container.database.update_release_file(file_id, **values)
            service_container.database.create_release_event(
                project_id=project_id,
                event_type="file_updated",
                message=f"更新文件解析：{record['relative_path']}",
                payload={"file_id": file_id, "fields": sorted(values)},
            )
        return service_container.database.get_release_file(file_id) or {}

    @app.get("/api/releases/{project_id}/jobs")
    async def list_release_jobs(
        project_id: int,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> list[dict[str, Any]]:
        if not service_container.database.get_release_project(project_id):
            raise HTTPException(status_code=404, detail="项目不存在")
        return service_container.database.list_release_jobs(project_id)

    @app.post("/api/releases/{project_id}/jobs")
    async def link_release_jobs(
        project_id: int,
        payload: ReleaseJobsRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        if not service_container.database.get_release_project(project_id):
            raise HTTPException(status_code=404, detail="项目不存在")
        for job_id in payload.job_ids:
            if not service_container.database.get_job(job_id):
                raise HTTPException(status_code=404, detail=f"下载任务 #{job_id} 不存在")
        if payload.mode == "replace":
            existing = service_container.database.list_release_jobs(project_id)
            for job in existing:
                if int(job["id"]) not in payload.job_ids:
                    service_container.database.unlink_release_job(project_id, int(job["id"]))
        service_container.database.link_release_jobs(project_id, payload.job_ids)
        service_container.database.create_release_event(
            project_id=project_id,
            event_type="jobs_linked",
            message="更新关联下载任务",
            payload={"job_ids": payload.job_ids, "mode": payload.mode},
        )
        return {"jobs": service_container.database.list_release_jobs(project_id)}

    @app.get("/api/releases/{project_id}/events")
    async def list_release_events(
        project_id: int,
        limit: int = Query(default=100, ge=1, le=500),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> list[dict[str, Any]]:
        if not service_container.database.get_release_project(project_id):
            raise HTTPException(status_code=404, detail="项目不存在")
        return service_container.database.list_release_events(project_id, limit)

    @app.get("/api/naming/profiles")
    async def list_naming_profiles(
        media_type: Literal["movie", "series"] | None = None,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> list[dict[str, Any]]:
        profiles = service_container.database.list_naming_profiles()
        if media_type:
            profiles = [item for item in profiles if item["media_type"] == media_type]
        return profiles

    @app.post("/api/naming/profiles")
    async def create_naming_profile(
        payload: NamingProfileCreateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            profile_id = service_container.database.create_naming_profile(**payload.model_dump())
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="命名配置名称已存在") from exc
        return service_container.database.get_naming_profile(profile_id) or {}

    @app.patch("/api/naming/profiles/{profile_id}")
    async def update_naming_profile(
        profile_id: int,
        payload: NamingProfileUpdateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        try:
            updated = service_container.database.update_naming_profile(
                profile_id, **payload.model_dump()
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="命名配置名称已存在") from exc
        if not updated:
            raise HTTPException(status_code=404, detail="命名配置不存在")
        return service_container.database.get_naming_profile(profile_id) or {}

    @app.delete("/api/naming/profiles/{profile_id}")
    async def delete_naming_profile(
        profile_id: int,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, bool]:
        try:
            deleted = service_container.database.delete_naming_profile(profile_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="命名配置不存在")
        return {"ok": True}

    @app.post("/api/releases/{project_id}/rename/preview")
    async def preview_release_rename(
        project_id: int,
        payload: RenamePreviewRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        preview = create_preview(
            service_container.settings,
            service_container.database,
            project_id,
            template=payload.template,
            target_mode=payload.target_mode,
            selected_file_ids=payload.file_ids,
        )
        service_container.previews.put(preview)
        project = service_container.database.get_release_project(project_id)
        return {
            "token": preview.token,
            "target_mode": preview.target_mode,
            "batch_id": preview.batch_id,
            "operations": preview.operations,
            "warnings": preview.warnings,
            "project_status": project["status"] if project else None,
        }

    @app.post("/api/releases/{project_id}/rename/apply")
    async def apply_release_rename(
        project_id: int,
        payload: RenameApplyRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        preview = service_container.previews.get(payload.preview_token, project_id)
        if not preview.operations:
            raise HTTPException(status_code=409, detail="预览中没有可执行的变更")
        task, created = service_container.processing.enqueue(
            project_id=project_id,
            task_type="rename_apply",
            input_data={"preview_token": payload.preview_token},
            idempotency_key=f"rename_apply:{project_id}:{preview.batch_id}",
        )
        return {"task": task, "created": created, "batch_id": preview.batch_id}

    @app.post("/api/releases/{project_id}/rename/rollback")
    async def rollback_release_rename(
        project_id: int,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        if not service_container.database.get_release_project(project_id):
            raise HTTPException(status_code=404, detail="项目不存在")
        batch_id = service_container.database.latest_rename_batch(project_id)
        if not batch_id:
            raise HTTPException(status_code=409, detail="没有可回滚的重命名批次")
        task, created = service_container.processing.enqueue(
            project_id=project_id,
            task_type="rename_rollback",
            input_data={"batch_id": batch_id},
            idempotency_key=f"rename_rollback:{project_id}:{batch_id}",
        )
        return {"task": task, "created": created, "batch_id": batch_id}

    @app.get("/api/releases/{project_id}/checks")
    async def list_release_checks(
        project_id: int,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> list[dict[str, Any]]:
        if not service_container.database.get_release_project(project_id):
            raise HTTPException(status_code=404, detail="项目不存在")
        return service_container.database.list_release_checks(project_id)

    @app.post("/api/releases/{project_id}/checks")
    async def run_release_checks(
        project_id: int,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        if not service_container.database.get_release_project(project_id):
            raise HTTPException(status_code=404, detail="项目不存在")
        task, created = service_container.processing.enqueue(
            project_id=project_id,
            task_type="media_check",
            input_data={},
            idempotency_key=f"media_check:{project_id}",
        )
        return {"task": task, "created": created}

    @app.get("/api/tasks")
    async def list_tasks(
        project_id: int | None = None,
        status: str | None = None,
        task_type: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> list[dict[str, Any]]:
        return service_container.database.list_processing_tasks(
            project_id=project_id,
            status=status,
            task_type=task_type,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/tasks/{task_id}")
    async def get_task(
        task_id: int,
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        task = service_container.database.get_processing_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="后台任务不存在")
        return task

    @app.post("/api/tasks/{task_id}/retry")
    async def retry_task(
        task_id: int,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        task = service_container.database.get_processing_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="后台任务不存在")
        if task["task_type"] == "file_operation":
            raise HTTPException(status_code=409, detail="文件操作不能重试，请重新检查后再执行")
        if not service_container.processing.retry(task_id):
            raise HTTPException(status_code=409, detail="当前任务不能重试")
        return {"task": service_container.database.get_processing_task(task_id)}

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(
        task_id: int,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        task = service_container.database.get_processing_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="后台任务不存在")
        if not service_container.processing.cancel(task_id):
            raise HTTPException(status_code=409, detail="当前任务不能取消")
        return {"task": service_container.database.get_processing_task(task_id)}

    @app.get("/api/tasks/{task_id}/logs")
    async def get_task_logs(
        task_id: int,
        lines: int = Query(default=200, ge=1, le=500),
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, str]:
        task = service_container.database.get_processing_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="后台任务不存在")
        raw_path = task.get("log_path")
        if not raw_path:
            return {"content": ""}
        log_path = Path(raw_path).resolve()
        if not log_path.is_relative_to(service_container.settings.logs_dir.resolve()):
            return {"content": ""}
        return {"content": task_log_content(str(log_path), lines=lines)}

    @app.get("/api/integrations/status")
    async def get_integration_status(
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return integration_status(service_container.settings)

    def render_status(service_container):
        from .color import rendering_capabilities

        capability = rendering_capabilities(tuple(service_container.settings.ffmpeg_command))
        return " · ".join(
            [
                "libplacebo 实测可用" if capability["libplacebo"] else "libplacebo 渲染不可用",
                "zscale 可用" if capability["zscale"] else "zscale 不可用",
            ]
        )

    def diagnostic_values(service_container: Services) -> dict[str, Any]:
        runtime = service_container.settings
        if service_container.runner.aria2_executable:
            aria2_status = "可用"
        elif not runtime.aria2_enabled:
            aria2_status = "已在设置中关闭"
        else:
            aria2_status = "未检测到"
        ffprobe_status = "可用" if shutil.which(runtime.ffprobe_command[0]) else "未检测到"
        ffmpeg = runtime.ffmpeg_command[0]
        ffmpeg_status = (
            "可用"
            if (Path(ffmpeg).is_file() if os.path.isabs(ffmpeg) else shutil.which(ffmpeg))
            else "未检测到"
        )
        collage_status = service_container.collage.status()
        release_tools = service_container.release_info.status()
        return {
            "ytdlp_version": runtime.ytdlp_version,
            "ffprobe_status": ffprobe_status,
            "ffmpeg_status": ffmpeg_status,
            "collage_font_status": ("可用" if collage_status["font"] else "未检测到中文字体"),
            "screenshot_render_status": render_status(service_container),
            "mediainfo_status": "可用" if release_tools["mediainfo"] else "未检测到",
            "bdinfo_status": "可用" if release_tools["bdinfo"] else "未检测到",
            "aria2_status": aria2_status,
            "timezone": runtime.timezone,
            "mount_paths": " · ".join(
                f"{root.label}: {root.path}" for root in service_container.files.roots("mounts")
            ),
        }

    def settings_values(service_container: Services) -> dict[str, Any]:
        stored = service_container.database.get_settings()
        values = static_values(stored, service_container.settings)
        values.update(service_container.runtime_settings.current_values())
        values.update(diagnostic_values(service_container))
        return values

    def settings_payload(service_container: Services) -> dict[str, Any]:
        stored = service_container.database.get_settings()
        values = settings_values(service_container)
        custom_sources = values.get("rename_custom_sources", [])
        if not isinstance(custom_sources, list):
            custom_sources = []
        return {
            "single_output_template": stored.get("single_output_template", ""),
            "playlist_output_template": stored.get("playlist_output_template", ""),
            "download_root": str(service_container.files.category_root("downloads").path),
            "directories": service_container.files.directory_settings(),
            "mount_roots": [root.payload() for root in service_container.files.roots("mounts")],
            "download_default_output_dir": values.get("download_default_output_dir", ""),
            "rename_default_season": values.get("rename_default_season", "01"),
            "rename_default_source": values.get("rename_default_source", ""),
            "rename_custom_sources": [
                str(item) for item in custom_sources if isinstance(item, str)
            ],
            "app_default_view": values.get("app_default_view", "files"),
            "file_browser_page_size": values.get("file_browser_page_size", 100),
            "file_browser_default_sort": values.get("file_browser_default_sort", "natural"),
            "file_browser_default_direction": values.get("file_browser_default_direction", "asc"),
            "ytdlp_version": values.get("ytdlp_version"),
            "log_retention_days": values.get("log_retention_days"),
            "secrets_configured": {
                key: bool(stored.get(key))
                for key in SECRET_SETTING_KEYS
            },
            "values": values,
        }

    @app.get("/api/settings/schema")
    async def get_settings_schema(
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return schema_payload(service_container.runtime_settings.env_defaults)

    @app.get("/api/settings")
    async def get_settings(
        _: SessionData = Depends(get_session),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        return settings_payload(service_container)

    @app.put("/api/settings")
    async def update_settings(
        payload: SettingsUpdateRequest,
        _: SessionData = Depends(require_csrf),
        service_container: Services = Depends(get_services),
    ) -> dict[str, Any]:
        requested = dict(payload.values or {})
        for field in (
            "single_output_template",
            "playlist_output_template",
            "download_default_output_dir",
            "rename_default_season",
            "rename_default_source",
            "rename_custom_sources",
        ):
            value = getattr(payload, field)
            if value is not None:
                requested[field] = value

        unknown = set(requested) - set(DEFINITIONS_BY_KEY)
        if unknown:
            names = "、".join(sorted(unknown))
            raise HTTPException(status_code=422, detail=f"未知设置项：{names}")

        runtime_input = {
            key: value for key, value in requested.items() if key in RUNTIME_SETTING_KEYS
        }
        static_input = {
            key: value for key, value in requested.items() if key not in RUNTIME_SETTING_KEYS
        }
        secret_updates: dict[str, str] = {}
        for key in SECRET_SETTING_KEYS:
            if key not in static_input:
                continue
            raw_secret = static_input.pop(key)
            definition = DEFINITIONS_BY_KEY[key]
            parsed_secret = parse_setting_value(definition, raw_secret, service_container.settings)
            if parsed_secret:
                secret_updates[key] = service_container.secrets.encrypt_json(str(parsed_secret))
        current_runtime = service_container.runtime_settings.current_values()
        runtime_parsed, runtime_stored = service_container.runtime_settings.prepare(runtime_input)
        conflicting_runtime_change = any(
            key in RUNTIME_IDLE_KEYS and runtime_parsed[key] != current_runtime.get(key)
            for key in runtime_parsed
        )
        if conflicting_runtime_change and service_container.runner.has_active_operation():
            raise HTTPException(
                status_code=409,
                detail="当前有解析或下载正在运行，请完成或取消后再修改下载引擎与线路探测设置",
            )

        updates = parse_stored_values(static_input, service_container.settings)
        updates.update(runtime_stored)
        updates.update(secret_updates)

        if payload.directories is not None:
            unknown_directories = set(payload.directories) - {
                "downloads",
                "torrents",
                "images",
                "release_info",
            }
            if unknown_directories:
                raise HTTPException(status_code=422, detail="未知的目录配置类型")
            for key, locator in payload.directories.items():
                service_container.files.validate_directory_locator(
                    locator.mount_key, locator.relative_path
                )
                updates[f"{key}_root_mount"] = locator.mount_key
                updates[f"{key}_root_relative"] = normalize_relative_path(locator.relative_path)

        previous_runtime = dict(current_runtime)
        service_container.runtime_settings.apply(runtime_parsed)
        try:
            if updates:
                service_container.database.update_settings(updates)
        except Exception:
            service_container.runtime_settings.apply(previous_runtime)
            raise
        if set(updates) & {"tmdb_api_key", "ptgen_url", "ptgen_api_key", "v2_bangumi_enabled", "v2_bangumi_api_key"}:
            service_container.v2.configure()
        if runtime_parsed:
            service_container.runner.refresh_aria2_executable()
        return settings_payload(service_container)

    return app
