from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _split_command(value: str) -> tuple[str, ...]:
    parts = tuple(shlex.split(value, posix=os.name != "nt"))
    if not parts:
        raise ValueError("YTDLP_COMMAND cannot be empty")
    return parts


@dataclass(slots=True)
class Settings:
    app_name: str = "Bili Remote"
    data_dir: Path = Path("/data")
    download_root: Path = Path("/downloads")
    imports_root: Path = Path("/imports")
    staging_root: Path = Path("/staging")
    releases_root: Path = Path("/releases")
    torrents_root: Path = Path("/torrents")
    images_root: Path = Path("/images")
    release_info_root: Path = Path("/release-info")
    app_secret: str = ""
    admin_username: str = "admin"
    admin_password: str = ""
    admin_password_hash: str = ""
    secure_cookies: bool = True
    session_max_age_seconds: int = 12 * 60 * 60
    ytdlp_command: tuple[str, ...] = ("yt-dlp",)
    ytdlp_version: str = "2026.8.19"
    aria2_enabled: bool = True
    aria2c_command: str = "aria2c"
    aria2_connections: int = 8
    aria2_resume_retries: int = 2
    aria2_min_split_size: str = "4M"
    aria2_piece_size: str = "4M"
    timezone: str = "Asia/Shanghai"
    log_retention_days: int = 30
    resolve_timeout_seconds: int = 120
    tv_cdn_probe_enabled: bool = True
    cdn_probe_timeout_seconds: float = 3.0
    cdn_probe_sample_bytes: int = 512 * 1024
    cdn_probe_connections: int = 8
    line_compare_enabled: bool = True
    max_stored_qr_sessions: int = 5
    allowed_origins: tuple[str, ...] = field(default_factory=tuple)
    ffprobe_command: tuple[str, ...] = ("ffprobe",)
    ffmpeg_command: tuple[str, ...] = ("ffmpeg",)
    workspace_scan_max_files: int = 20000
    workspace_scan_max_depth: int = 20
    workspace_browse_limit: int = 200
    minfo_base_url: str = ""
    minfo_api_token: str = ""
    minfo_timeout_seconds: float = 10.0
    ptgen_base_url: str = ""
    ptgen_api_token: str = ""
    ptgen_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.getenv("DATA_DIR", "/data")).expanduser().resolve()
        download_root = Path(
            os.getenv("DOWNLOAD_ROOT_CONTAINER", "/downloads")
        ).expanduser().resolve()
        imports_root = Path(os.getenv("IMPORTS_ROOT", "/imports")).expanduser().resolve()
        staging_root = Path(os.getenv("STAGING_ROOT", "/staging")).expanduser().resolve()
        releases_root = Path(
            os.getenv("RELEASES_ROOT", "/releases")
        ).expanduser().resolve()
        torrents_root = Path(
            os.getenv("TORRENTS_ROOT", "/torrents")
        ).expanduser().resolve()
        images_root = Path(
            os.getenv("IMAGES_ROOT", "/images")
        ).expanduser().resolve()
        release_info_root = Path(
            os.getenv("RELEASE_INFO_ROOT", "/release-info")
        ).expanduser().resolve()
        return cls(
            app_name=os.getenv("APP_NAME", "Bili Remote"),
            data_dir=data_dir,
            download_root=download_root,
            imports_root=imports_root,
            staging_root=staging_root,
            releases_root=releases_root,
            torrents_root=torrents_root,
            images_root=images_root,
            release_info_root=release_info_root,
            app_secret=os.getenv("APP_SECRET", ""),
            admin_username=os.getenv("ADMIN_USERNAME", "admin"),
            admin_password=os.getenv("ADMIN_PASSWORD", ""),
            admin_password_hash=os.getenv("ADMIN_PASSWORD_HASH", ""),
            secure_cookies=_as_bool(os.getenv("SECURE_COOKIES"), True),
            session_max_age_seconds=int(
                os.getenv("SESSION_MAX_AGE_SECONDS", str(12 * 60 * 60))
            ),
            ytdlp_command=_split_command(os.getenv("YTDLP_COMMAND", "yt-dlp")),
            ytdlp_version=os.getenv("YTDLP_VERSION", "2026.8.19"),
            aria2_enabled=_as_bool(os.getenv("ARIA2_ENABLED"), True),
            aria2c_command=os.getenv("ARIA2C_COMMAND", "aria2c"),
            aria2_connections=int(os.getenv("ARIA2_CONNECTIONS", "8")),
            aria2_resume_retries=int(os.getenv("ARIA2_RESUME_RETRIES", "2")),
            aria2_min_split_size=os.getenv("ARIA2_MIN_SPLIT_SIZE", "4M"),
            aria2_piece_size=os.getenv("ARIA2_PIECE_SIZE", "4M"),
            timezone=os.getenv("TZ", "Asia/Shanghai"),
            log_retention_days=int(os.getenv("LOG_RETENTION_DAYS", "30")),
            resolve_timeout_seconds=int(os.getenv("RESOLVE_TIMEOUT_SECONDS", "120")),
            tv_cdn_probe_enabled=_as_bool(os.getenv("TV_CDN_PROBE"), True),
            cdn_probe_timeout_seconds=float(
                os.getenv("CDN_PROBE_TIMEOUT_SECONDS", "3.0")
            ),
            cdn_probe_sample_bytes=int(
                os.getenv("CDN_PROBE_SAMPLE_BYTES", str(512 * 1024))
            ),
            cdn_probe_connections=int(os.getenv("CDN_PROBE_CONNECTIONS", "8")),
            line_compare_enabled=_as_bool(os.getenv("LINE_COMPARE"), True),
            max_stored_qr_sessions=int(os.getenv("MAX_STORED_QR_SESSIONS", "5")),
            allowed_origins=tuple(
                item.strip()
                for item in os.getenv("ALLOWED_ORIGINS", "").split(",")
                if item.strip()
            ),
            ffprobe_command=_split_command(os.getenv("FFPROBE_COMMAND", "ffprobe")),
            ffmpeg_command=_split_command(os.getenv("FFMPEG_COMMAND", "ffmpeg")),
            workspace_scan_max_files=int(
                os.getenv("WORKSPACE_SCAN_MAX_FILES", "20000")
            ),
            workspace_scan_max_depth=int(
                os.getenv("WORKSPACE_SCAN_MAX_DEPTH", "20")
            ),
            workspace_browse_limit=int(
                os.getenv("WORKSPACE_BROWSE_LIMIT", "200")
            ),
            minfo_base_url=os.getenv("MINFO_BASE_URL", "").strip(),
            minfo_api_token=os.getenv("MINFO_API_TOKEN", "").strip(),
            minfo_timeout_seconds=float(
                os.getenv("MINFO_TIMEOUT_SECONDS", "10")
            ),
            ptgen_base_url=os.getenv("PTGEN_BASE_URL", "").strip(),
            ptgen_api_token=os.getenv("PTGEN_API_TOKEN", "").strip(),
            ptgen_timeout_seconds=float(
                os.getenv("PTGEN_TIMEOUT_SECONDS", "10")
            ),
        )

    def validate(self) -> None:
        if not self.app_secret:
            raise ValueError("APP_SECRET is required")
        if len(self.app_secret) < 32:
            raise ValueError("APP_SECRET must contain at least 32 characters")
        if not self.admin_password and not self.admin_password_hash:
            raise ValueError("ADMIN_PASSWORD or ADMIN_PASSWORD_HASH is required")
        if not self.ytdlp_command:
            raise ValueError("YTDLP_COMMAND is required")
        if not 1 <= self.aria2_connections <= 16:
            raise ValueError("ARIA2_CONNECTIONS must be between 1 and 16")
        if not 0 <= self.aria2_resume_retries <= 3:
            raise ValueError("ARIA2_RESUME_RETRIES must be between 0 and 3")
        if not 0.5 <= self.cdn_probe_timeout_seconds <= 10.0:
            raise ValueError("CDN_PROBE_TIMEOUT_SECONDS must be between 0.5 and 10")
        if not 64 * 1024 <= self.cdn_probe_sample_bytes <= 8 * 1024 * 1024:
            raise ValueError(
                "CDN_PROBE_SAMPLE_BYTES must be between 65536 and 8388608"
            )
        if not 1 <= self.cdn_probe_connections <= 16:
            raise ValueError("CDN_PROBE_CONNECTIONS must be between 1 and 16")
        for name, value in (
            ("ARIA2_MIN_SPLIT_SIZE", self.aria2_min_split_size),
            ("ARIA2_PIECE_SIZE", self.aria2_piece_size),
        ):
            if not re.fullmatch(r"\d+[KMG]", value.upper()):
                raise ValueError(f"{name} must look like 4M")
        if self.session_max_age_seconds < 300:
            raise ValueError("SESSION_MAX_AGE_SECONDS must be at least 300")
        if self.log_retention_days < 1:
            raise ValueError("LOG_RETENTION_DAYS must be positive")
        if not 100 <= self.workspace_scan_max_files <= 1_000_000:
            raise ValueError("WORKSPACE_SCAN_MAX_FILES must be between 100 and 1000000")
        if not 1 <= self.workspace_scan_max_depth <= 100:
            raise ValueError("WORKSPACE_SCAN_MAX_DEPTH must be between 1 and 100")
        if not 10 <= self.workspace_browse_limit <= 1000:
            raise ValueError("WORKSPACE_BROWSE_LIMIT must be between 10 and 1000")
        for name, value in (
            ("MINFO_TIMEOUT_SECONDS", self.minfo_timeout_seconds),
            ("PTGEN_TIMEOUT_SECONDS", self.ptgen_timeout_seconds),
        ):
            if not 1 <= value <= 120:
                raise ValueError(f"{name} must be between 1 and 120")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "bili-remote.sqlite3"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def runtime_dir(self) -> Path:
        return self.data_dir / "runtime"

    def ensure_directories(self) -> None:
        for path in (
            self.data_dir,
            self.download_root,
            self.imports_root,
            self.staging_root,
            self.releases_root,
            self.torrents_root,
            self.images_root,
            self.release_info_root,
            self.logs_dir,
            self.runtime_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def workspace_roots(self) -> dict[str, Path]:
        return {
            "downloads": self.download_root,
            "imports": self.imports_root,
            "staging": self.staging_root,
            "releases": self.releases_root,
        }

    @property
    def file_browser_roots(self) -> dict[str, Path]:
        return {
            "downloads": self.download_root,
            "torrents": self.torrents_root,
            "images": self.images_root,
            "release_info": self.release_info_root,
        }

    @property
    def mount_roots(self) -> dict[str, Path]:
        return {
            "data": self.data_dir,
            "downloads": self.download_root,
            "imports": self.imports_root,
            "staging": self.staging_root,
            "releases": self.releases_root,
            "torrents": self.torrents_root,
            "images": self.images_root,
            "release_info": self.release_info_root,
        }
