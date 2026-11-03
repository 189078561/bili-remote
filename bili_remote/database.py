from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS auth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cookies_encrypted TEXT NOT NULL,
    account_mid INTEGER,
    account_name TEXT,
    vip_status INTEGER NOT NULL DEFAULT 0,
    vip_due_date TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('manual', 'subscription', 'seed')),
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'auth_required')
    ),
    title TEXT,
    uploader TEXT,
    api_mode TEXT NOT NULL DEFAULT 'web',
    format_selector TEXT NOT NULL DEFAULT 'bv*+ba/b',
    all_parts INTEGER NOT NULL DEFAULT 0,
    output_template TEXT NOT NULL,
    output_dir TEXT NOT NULL DEFAULT '',
    subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
    progress REAL NOT NULL DEFAULT 0,
    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER,
    speed REAL,
    eta INTEGER,
    result_path TEXT,
    log_path TEXT,
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_subscription ON jobs(subscription_id, status);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL,
    title TEXT NOT NULL,
    output_template TEXT NOT NULL,
    output_dir TEXT NOT NULL DEFAULT '',
    include_existing INTEGER NOT NULL DEFAULT 0,
    interval_minutes INTEGER NOT NULL DEFAULT 60,
    enabled INTEGER NOT NULL DEFAULT 1,
    initial_sync_done INTEGER NOT NULL DEFAULT 0,
    last_run_at TEXT,
    next_run_at TEXT,
    last_status TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tv_auth (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    account_mid INTEGER,
    account_name TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS parse_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL,
    api_mode TEXT NOT NULL DEFAULT 'web',
    title TEXT NOT NULL,
    uploader TEXT,
    duration INTEGER,
    is_playlist INTEGER NOT NULL DEFAULT 0,
    playlist_count INTEGER,
    default_episode_id TEXT,
    formats_json TEXT NOT NULL DEFAULT '[]',
    episodes_json TEXT NOT NULL DEFAULT '[]',
    parse_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_url, api_mode)
);

CREATE INDEX IF NOT EXISTS idx_parse_history_updated
    ON parse_history(updated_at DESC);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


WORKSPACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS naming_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL CHECK (media_type IN ('movie', 'series')),
    template TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS release_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK (
        media_type IN ('movie', 'series', 'anime', 'single', 'bluray')
    ),
    source_type TEXT NOT NULL CHECK (
        source_type IN ('bilibili', 'local', 'bluray', 'other')
    ),
    source_url TEXT,
    year INTEGER,
    season INTEGER,
    expected_episodes INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'ready', 'processing', 'review', 'completed', 'failed')
    ),
    download_dir TEXT NOT NULL DEFAULT '',
    work_dir TEXT NOT NULL DEFAULT '',
    final_dir TEXT NOT NULL DEFAULT '',
    last_rename_target TEXT NOT NULL DEFAULT 'in_place' CHECK (
        last_rename_target IN ('in_place', 'releases')
    ),
    naming_profile_id INTEGER REFERENCES naming_profiles(id) ON DELETE SET NULL,
    naming_template TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_release_projects_status_updated
    ON release_projects(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS release_project_jobs (
    project_id INTEGER NOT NULL REFERENCES release_projects(id) ON DELETE CASCADE,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_release_project_jobs_job
    ON release_project_jobs(job_id);

CREATE TABLE IF NOT EXISTS release_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES release_projects(id) ON DELETE CASCADE,
    root_key TEXT NOT NULL CHECK (root_key IN ('downloads', 'imports', 'staging', 'releases')),
    relative_path TEXT NOT NULL,
    file_role TEXT NOT NULL CHECK (
        file_role IN ('video', 'subtitle', 'iso', 'bdmv', 'other')
    ),
    size_bytes INTEGER NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    missing_at TEXT,
    season INTEGER,
    episode INTEGER,
    title TEXT,
    language TEXT,
    scan_result_json TEXT NOT NULL DEFAULT '{}',
    corrected_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, root_key, relative_path)
);

CREATE INDEX IF NOT EXISTS idx_release_files_project_role
    ON release_files(project_id, file_role, relative_path);

CREATE TABLE IF NOT EXISTS release_file_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES release_projects(id) ON DELETE CASCADE,
    video_file_id INTEGER NOT NULL REFERENCES release_files(id) ON DELETE CASCADE,
    subtitle_file_id INTEGER NOT NULL REFERENCES release_files(id) ON DELETE CASCADE,
    link_type TEXT NOT NULL DEFAULT 'external',
    confidence REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(video_file_id, subtitle_file_id)
);

CREATE INDEX IF NOT EXISTS idx_release_file_links_project
    ON release_file_links(project_id, video_file_id);

CREATE TABLE IF NOT EXISTS processing_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES release_projects(id) ON DELETE CASCADE,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'running', 'completed', 'failed', 'cancelled')
    ),
    input_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    log_path TEXT,
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_processing_tasks_status_created
    ON processing_tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_processing_tasks_project_created
    ON processing_tasks(project_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_processing_tasks_active_idempotency
    ON processing_tasks(task_type, idempotency_key)
    WHERE status IN ('queued', 'running') AND idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS rename_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    task_id INTEGER REFERENCES processing_tasks(id) ON DELETE SET NULL,
    project_id INTEGER NOT NULL REFERENCES release_projects(id) ON DELETE CASCADE,
    file_id INTEGER REFERENCES release_files(id) ON DELETE SET NULL,
    source_root_key TEXT NOT NULL,
    source_relative_path TEXT NOT NULL,
    target_root_key TEXT NOT NULL,
    target_relative_path TEXT NOT NULL,
    source_size_bytes INTEGER NOT NULL DEFAULT 0,
    source_mtime_ns INTEGER NOT NULL DEFAULT 0,
    target_size_bytes INTEGER,
    target_mtime_ns INTEGER,
    status TEXT NOT NULL DEFAULT 'applied' CHECK (
        status IN ('applied', 'rolled_back', 'failed')
    ),
    error TEXT,
    created_at TEXT NOT NULL,
    rolled_back_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_rename_operations_project_batch
    ON rename_operations(project_id, batch_id, created_at DESC);

CREATE TABLE IF NOT EXISTS release_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES release_projects(id) ON DELETE CASCADE,
    task_id INTEGER REFERENCES processing_tasks(id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_release_checks_project_created
    ON release_checks(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS release_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES release_projects(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_release_events_project_created
    ON release_events(project_id, created_at DESC);

INSERT OR IGNORE INTO naming_profiles(
    name, media_type, template, is_default, created_at, updated_at
) VALUES
    ('电影默认', 'movie',
     '{title}.{year}.{resolution}.{source}.{video_codec}-{group}',
     1, datetime('now'), datetime('now')),
    ('剧集默认', 'series',
     '{title}.S{season:02d}E{episode:02d}.{resolution}.{source}.{video_codec}-{group}',
     1, datetime('now'), datetime('now'));
"""


RELEASE_TOOLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS image_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued' CHECK (
        status IN ('queued', 'uploading', 'completed', 'failed')
    ),
    source_kind TEXT NOT NULL DEFAULT 'file' CHECK (
        source_kind IN ('file', 'screenshot')
    ),
    source_root_key TEXT NOT NULL,
    source_relative_path TEXT NOT NULL,
    session_id TEXT,
    session_item_id TEXT,
    source_name TEXT NOT NULL,
    source_size_bytes INTEGER NOT NULL DEFAULT 0,
    source_fingerprint TEXT,
    remote_url TEXT,
    thumbnail_url TEXT,
    remote_id TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_image_uploads_created
    ON image_uploads(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_image_uploads_session_item
    ON image_uploads(session_id, session_item_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS release_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_root_key TEXT NOT NULL,
    source_relative_path TEXT NOT NULL DEFAULT '',
    output_root_key TEXT NOT NULL DEFAULT 'release_info',
    output_relative_path TEXT,
    format TEXT NOT NULL DEFAULT 'markdown' CHECK (
        format IN ('markdown', 'bbcode', 'text')
    ),
    media_json TEXT NOT NULL DEFAULT '{}',
    file_list_json TEXT NOT NULL DEFAULT '[]',
    generated_text TEXT NOT NULL DEFAULT '',
    user_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'saved', 'failed')
    ),
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_release_documents_updated
    ON release_documents(updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS torrent_builds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_root_key TEXT NOT NULL,
    source_relative_path TEXT NOT NULL,
    output_root_key TEXT NOT NULL DEFAULT 'torrents',
    output_relative_path TEXT NOT NULL,
    info_hash TEXT NOT NULL,
    piece_length INTEGER NOT NULL,
    total_size_bytes INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    private INTEGER NOT NULL DEFAULT 1,
    trackers_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'completed' CHECK (
        status IN ('completed', 'failed')
    ),
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_torrent_builds_created
    ON torrent_builds(created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS publish_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (
        status IN ('draft', 'ready', 'failed')
    ),
    source_root_key TEXT,
    source_relative_path TEXT,
    description TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    image_upload_ids_json TEXT NOT NULL DEFAULT '[]',
    release_document_id INTEGER REFERENCES release_documents(id) ON DELETE SET NULL,
    torrent_build_id INTEGER REFERENCES torrent_builds(id) ON DELETE SET NULL,
    bbcode TEXT NOT NULL DEFAULT '',
    html TEXT NOT NULL DEFAULT '',
    markdown TEXT NOT NULL DEFAULT '',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    qbittorrent_status TEXT,
    qbittorrent_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_publish_sessions_updated
    ON publish_sessions(updated_at DESC, id DESC);
"""


MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (2, "release_workspace", WORKSPACE_SCHEMA),
    (3, "release_tools", RELEASE_TOOLS_SCHEMA),
    (4, "media_metadata", """
        CREATE TABLE IF NOT EXISTS media_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fields_json TEXT NOT NULL DEFAULT '{}',
            source_root_key TEXT NOT NULL DEFAULT '',
            source_relative_path TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_media_metadata_source ON media_metadata(source_root_key,source_relative_path);
    """),
)


DEFAULT_SETTINGS = {
    "single_output_template": "Bilibili/%(uploader)s/%(title)s [%(id)s].%(ext)s",
    "playlist_output_template": (
        "Bilibili/%(playlist_title)s/S%(playlist_index)02d - "
        "%(title)s [%(id)s].%(ext)s"
    ),
    "downloads_root_mount": "downloads",
    "downloads_root_relative": "",
    "torrents_root_mount": "torrents",
    "torrents_root_relative": "",
    "images_root_mount": "images",
    "images_root_relative": "",
    "release_info_root_mount": "release_info",
    "release_info_root_relative": "",
    "download_default_output_dir": "",
    "rename_default_season": "01",
    "rename_default_source": "",
    "rename_custom_sources": "[]",
    "app_default_view": "files",
    "file_browser_page_size": "100",
    "file_browser_default_sort": "natural",
    "file_browser_default_direction": "asc",
    "image_host_provider": "catbox",
    "image_host_imgbb_api_key": "",
    "image_host_timeout_seconds": "60",
    "torrent_default_trackers": "[]",
    "torrent_default_private": "true",
    "torrent_default_piece_length": "auto",
    "torrent_default_source": "",
    "qbittorrent_url": "",
    "qbittorrent_username": "",
    "qbittorrent_password": "",
    "qbittorrent_category": "bili-remote",
    "qbittorrent_save_path": "",
    "qbittorrent_paused": "false",
    "qbittorrent_timeout_seconds": "15",
    "release_info_default_format": "markdown",
    "release_info_include_mediainfo": "true",
}


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )

        applied = {
            int(row["version"])
            for row in self.fetch_all("SELECT version FROM schema_migrations")
        }
        existing_tables = {
            row["name"]
            for row in self.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        if 1 not in applied:
            if existing_tables - {"schema_migrations"}:
                self._backup_before_migration(1)
            with self.connect() as connection:
                connection.executescript(SCHEMA)
                job_columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(jobs)")
                }
                if "api_mode" not in job_columns:
                    connection.execute(
                        "ALTER TABLE jobs ADD COLUMN api_mode TEXT NOT NULL DEFAULT 'web'"
                    )
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) "
                    "VALUES (1, 'bilibili_baseline', ?)",
                    (utc_now(),),
                )

        for version, name, migration in MIGRATIONS:
            if version in applied:
                continue
            self._backup_before_migration(version)
            with self.connect() as connection:
                connection.executescript(migration)
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) "
                    "VALUES (?, ?, ?)",
                    (version, name, utc_now()),
                )

        with self.connect() as connection:
            for key, value in DEFAULT_SETTINGS.items():
                connection.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, value),
                )
            connection.execute(
                """
                UPDATE jobs
                SET status='queued', started_at=NULL, finished_at=NULL
                WHERE status='running'
                """
            )

    def _backup_before_migration(self, version: int) -> Path:
        backup_dir = self.path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        target = backup_dir / f"bili-remote-schema-v{version}-{stamp}.sqlite3"
        source = sqlite3.connect(self.path)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        backups = sorted(backup_dir.glob("bili-remote-schema-v*.sqlite3"))
        for old in backups[:-5]:
            try:
                old.unlink()
            except OSError:
                pass
        return target

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fetch_one(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(query, params).fetchone()
            return dict(row) if row else None

    def fetch_all(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> int:
        with self.connect() as connection:
            cursor = connection.execute(query, params)
            return int(cursor.lastrowid or 0)

    def executemany(self, query: str, values: list[tuple[Any, ...]]) -> None:
        with self.connect() as connection:
            connection.executemany(query, values)

    # Authentication -------------------------------------------------
    def save_auth(
        self,
        encrypted_cookies: str,
        account: dict[str, Any] | None,
    ) -> None:
        account = account or {}
        self.execute(
            """
            INSERT INTO auth(
                id, cookies_encrypted, account_mid, account_name, vip_status,
                vip_due_date, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                cookies_encrypted=excluded.cookies_encrypted,
                account_mid=excluded.account_mid,
                account_name=excluded.account_name,
                vip_status=excluded.vip_status,
                vip_due_date=excluded.vip_due_date,
                updated_at=excluded.updated_at
            """,
            (
                encrypted_cookies,
                account.get("mid"),
                account.get("uname"),
                int(bool(account.get("vip_status"))),
                account.get("vip_due_date"),
                utc_now(),
            ),
        )

    def get_auth(self) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM auth WHERE id=1")

    def clear_auth(self) -> None:
        self.execute("DELETE FROM auth WHERE id=1")

    def save_tv_auth(
        self,
        access_token_encrypted: str,
        refresh_token_encrypted: str | None,
        account: dict[str, Any] | None,
    ) -> None:
        account = account or {}
        self.execute(
            """
            INSERT INTO tv_auth(
                id, access_token_encrypted, refresh_token_encrypted,
                account_mid, account_name, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                access_token_encrypted=excluded.access_token_encrypted,
                refresh_token_encrypted=excluded.refresh_token_encrypted,
                account_mid=excluded.account_mid,
                account_name=excluded.account_name,
                updated_at=excluded.updated_at
            """,
            (
                access_token_encrypted,
                refresh_token_encrypted,
                account.get("mid"),
                account.get("uname"),
                utc_now(),
            ),
        )

    def get_tv_auth(self) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM tv_auth WHERE id=1")

    def clear_tv_auth(self) -> None:
        self.execute("DELETE FROM tv_auth WHERE id=1")

    # Settings --------------------------------------------------------
    def get_settings(self) -> dict[str, str]:
        return {
            row["key"]: row["value"]
            for row in self.fetch_all("SELECT key, value FROM settings")
        }

    def update_settings(self, values: dict[str, str]) -> None:
        self.executemany(
            """
            INSERT INTO settings(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            list(values.items()),
        )

    # Jobs ------------------------------------------------------------
    def create_job(
        self,
        *,
        source_url: str,
        kind: str,
        output_template: str,
        output_dir: str,
        api_mode: str = "web",
        format_selector: str = "bv*+ba/b",
        all_parts: bool = False,
        title: str | None = None,
        uploader: str | None = None,
        log_path: str | None = None,
    ) -> int:
        return self.create_jobs(
            [
                {
                    "source_url": source_url,
                    "kind": kind,
                    "output_template": output_template,
                    "output_dir": output_dir,
                    "api_mode": api_mode,
                    "format_selector": format_selector,
                    "all_parts": all_parts,
                    "title": title,
                    "uploader": uploader,
                    "log_path": log_path,
                }
            ]
        )[0]

    def create_jobs(self, jobs: list[dict[str, Any]]) -> list[int]:
        if not jobs:
            return []
        now = utc_now()
        ids: list[int] = []
        with self.connect() as connection:
            for job in jobs:
                cursor = connection.execute(
                    """
                    INSERT INTO jobs(
                        source_url, kind, status, title, uploader, api_mode,
                        format_selector, all_parts, output_template, output_dir,
                        log_path, created_at
                    ) VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job["source_url"],
                        job.get("kind", "manual"),
                        job.get("title"),
                        job.get("uploader"),
                        job.get("api_mode", "web"),
                        job.get("format_selector", "bv*+ba/b"),
                        int(bool(job.get("all_parts"))),
                        job["output_template"],
                        job.get("output_dir", ""),
                        job.get("log_path"),
                        now,
                    ),
                )
                ids.append(int(cursor.lastrowid or 0))
        return ids

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def delete_job(self, job_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM jobs WHERE id=? AND status NOT IN ('queued', 'running')",
                (job_id,),
            )
            return cursor.rowcount == 1

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT * FROM jobs
            ORDER BY CASE status
                         WHEN 'running' THEN 0
                         WHEN 'queued' THEN 1
                         ELSE 2
                     END,
                     CASE WHEN status IN ('queued', 'running') THEN id END ASC,
                     id DESC
            LIMIT ?
            """,
            (min(limit, 500),),
        )

    def next_queued_job(self) -> dict[str, Any] | None:
        return self.fetch_one(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY id ASC LIMIT 1"
        )

    def claim_job(self, job_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs SET status='running', started_at=?, finished_at=NULL,
                    progress=0, error=NULL
                WHERE id=? AND status='queued'
                """,
                (utc_now(), job_id),
            )
            return cursor.rowcount == 1

    def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "status",
            "title",
            "uploader",
            "format_selector",
            "progress",
            "downloaded_bytes",
            "total_bytes",
            "speed",
            "eta",
            "result_path",
            "error",
            "retry_count",
            "started_at",
            "finished_at",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unsupported job fields: {sorted(invalid)}")
        assignments = ", ".join(f"{key}=?" for key in fields)
        self.execute(
            f"UPDATE jobs SET {assignments} WHERE id=?",
            (*fields.values(), job_id),
        )

    def old_log_paths(self, before: str) -> list[str]:
        rows = self.fetch_all(
            "SELECT log_path FROM jobs WHERE finished_at IS NOT NULL AND finished_at<? "
            "AND log_path IS NOT NULL",
            (before,),
        )
        return [row["log_path"] for row in rows if row["log_path"]]

    # Parse history ---------------------------------------------------
    def save_history(
        self,
        *,
        source_url: str,
        api_mode: str,
        title: str,
        uploader: str,
        duration: int | None,
        is_playlist: bool,
        playlist_count: int | None,
        default_episode_id: str | None,
        formats: list[dict[str, Any]],
        episodes: list[dict[str, Any]],
    ) -> None:
        now = utc_now()
        self.execute(
            """
            INSERT INTO parse_history(
                source_url, api_mode, title, uploader, duration, is_playlist,
                playlist_count, default_episode_id, formats_json, episodes_json,
                parse_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(source_url, api_mode) DO UPDATE SET
                title=excluded.title,
                uploader=excluded.uploader,
                duration=excluded.duration,
                is_playlist=excluded.is_playlist,
                playlist_count=excluded.playlist_count,
                default_episode_id=excluded.default_episode_id,
                formats_json=excluded.formats_json,
                episodes_json=excluded.episodes_json,
                parse_count=parse_count + 1,
                updated_at=excluded.updated_at
            """,
            (
                source_url,
                api_mode,
                title,
                uploader,
                duration,
                int(is_playlist),
                playlist_count,
                default_episode_id,
                json.dumps(formats, ensure_ascii=False),
                json.dumps(episodes, ensure_ascii=False),
                now,
                now,
            ),
        )
        self.execute(
            """
            DELETE FROM parse_history
            WHERE id NOT IN (
                SELECT id FROM parse_history
                ORDER BY updated_at DESC, id DESC
                LIMIT 200
            )
            """
        )

    def list_history(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT id, source_url, api_mode, title, uploader, duration,
                   is_playlist, playlist_count, default_episode_id,
                   parse_count, created_at, updated_at
            FROM parse_history
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (min(limit, 200),),
        )

    def get_history(self, history_id: int) -> dict[str, Any] | None:
        return self.fetch_one(
            "SELECT * FROM parse_history WHERE id=?", (history_id,)
        )

    def delete_history(self, history_id: int) -> None:
        self.execute("DELETE FROM parse_history WHERE id=?", (history_id,))

    def clear_history(self) -> None:
        self.execute("DELETE FROM parse_history")

    # Release projects ------------------------------------------------
    def create_release_project(
        self,
        *,
        title: str,
        media_type: str,
        source_type: str,
        source_url: str | None = None,
        year: int | None = None,
        season: int | None = None,
        expected_episodes: int | None = None,
        metadata: dict[str, Any] | None = None,
        download_dir: str = "",
        work_dir: str = "",
        final_dir: str = "",
        naming_profile_id: int | None = None,
        naming_template: str | None = None,
    ) -> int:
        now = utc_now()
        return self.execute(
            """
            INSERT INTO release_projects(
                title, media_type, source_type, source_url, year, season,
                expected_episodes, metadata_json, status, download_dir, work_dir,
                final_dir, last_rename_target, naming_profile_id, naming_template,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, 'in_place', ?, ?, ?, ?)
            """,
            (
                title,
                media_type,
                source_type,
                source_url,
                year,
                season,
                expected_episodes,
                json.dumps(metadata or {}, ensure_ascii=False),
                download_dir,
                work_dir,
                final_dir,
                naming_profile_id,
                naming_template,
                now,
                now,
            ),
        )

    @staticmethod
    def _decode_release_project(row: dict[str, Any]) -> dict[str, Any]:
        try:
            row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
        except json.JSONDecodeError:
            row["metadata"] = {}
        return row

    def get_release_project(self, project_id: int) -> dict[str, Any] | None:
        row = self.fetch_one(
            "SELECT * FROM release_projects WHERE id=?", (project_id,)
        )
        return self._decode_release_project(row) if row else None

    def list_release_projects(
        self,
        *,
        status: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("p.status=?")
            params.append(status)
        if query:
            clauses.append("(p.title LIKE ? OR p.source_url LIKE ?)")
            pattern = f"%{query}%"
            params.extend((pattern, pattern))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend((min(max(limit, 1), 500), max(offset, 0)))
        rows = self.fetch_all(
            f"""
            SELECT p.*,
                   (SELECT COUNT(*) FROM release_files f WHERE f.project_id=p.id) AS file_count,
                   (SELECT COUNT(*) FROM release_project_jobs j
                    WHERE j.project_id=p.id) AS job_count
            FROM release_projects p
            {where}
            ORDER BY p.updated_at DESC, p.id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params),
        )
        return [self._decode_release_project(row) for row in rows]

    def update_release_project(self, project_id: int, **fields: Any) -> bool:
        allowed = {
            "title",
            "media_type",
            "source_type",
            "source_url",
            "year",
            "season",
            "expected_episodes",
            "metadata_json",
            "status",
            "download_dir",
            "work_dir",
            "final_dir",
            "last_rename_target",
            "naming_profile_id",
            "naming_template",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unsupported project fields: {sorted(invalid)}")
        if not fields:
            return False
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE release_projects SET {assignments} WHERE id=?",
                (*fields.values(), project_id),
            )
            return cursor.rowcount == 1

    def delete_release_project(self, project_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM release_projects WHERE id=?", (project_id,)
            )
            return cursor.rowcount == 1

    def link_release_jobs(self, project_id: int, job_ids: list[int]) -> None:
        if not job_ids:
            return
        now = utc_now()
        self.executemany(
            "INSERT OR IGNORE INTO release_project_jobs(project_id, job_id, created_at) "
            "VALUES (?, ?, ?)",
            [(project_id, job_id, now) for job_id in job_ids],
        )
        self.touch_release_project(project_id, now=now)

    def touch_release_project(self, project_id: int, *, now: str | None = None) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE release_projects SET updated_at=? WHERE id=?",
                (now or utc_now(), project_id),
            )
            return cursor.rowcount == 1

    def transition_release_project(
        self,
        project_id: int,
        status: str,
        *,
        preserve_completed: bool = True,
    ) -> bool:
        if status not in {"draft", "ready", "processing", "review", "completed", "failed"}:
            raise ValueError("Unsupported release project status")
        clauses = "id=?"
        params: list[Any] = [status, utc_now(), project_id]
        if preserve_completed:
            clauses += " AND status!='completed'"
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE release_projects SET status=?, updated_at=? WHERE {clauses}",
                tuple(params),
            )
            return cursor.rowcount == 1

    def unlink_release_job(self, project_id: int, job_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM release_project_jobs WHERE project_id=? AND job_id=?",
                (project_id, job_id),
            )
            return cursor.rowcount == 1

    def list_release_jobs(self, project_id: int) -> list[dict[str, Any]]:
        return self.fetch_all(
            """
            SELECT j.*, lj.created_at AS linked_at
            FROM release_project_jobs lj
            JOIN jobs j ON j.id=lj.job_id
            WHERE lj.project_id=?
            ORDER BY j.id DESC
            """,
            (project_id,),
        )

    # Release files ---------------------------------------------------
    @staticmethod
    def _decode_release_file(row: dict[str, Any]) -> dict[str, Any]:
        for source, target in (
            ("scan_result_json", "scan_result"),
            ("corrected_json", "corrected"),
        ):
            try:
                row[target] = json.loads(row.pop(source) or "{}")
            except json.JSONDecodeError:
                row[target] = {}
        return row

    def upsert_release_file(
        self,
        *,
        project_id: int,
        root_key: str,
        relative_path: str,
        file_role: str,
        size_bytes: int,
        mtime_ns: int,
        season: int | None,
        episode: int | None,
        title: str | None,
        language: str | None,
        scan_result: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO release_files(
                    project_id, root_key, relative_path, file_role, size_bytes,
                    mtime_ns, missing_at, season, episode, title, language,
                    scan_result_json, corrected_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, '{}', ?, ?)
                ON CONFLICT(project_id, root_key, relative_path) DO UPDATE SET
                    file_role=excluded.file_role,
                    size_bytes=excluded.size_bytes,
                    mtime_ns=excluded.mtime_ns,
                    missing_at=NULL,
                    season=excluded.season,
                    episode=excluded.episode,
                    title=excluded.title,
                    language=excluded.language,
                    scan_result_json=excluded.scan_result_json,
                    updated_at=excluded.updated_at
                """,
                (
                    project_id,
                    root_key,
                    relative_path,
                    file_role,
                    size_bytes,
                    mtime_ns,
                    season,
                    episode,
                    title,
                    language,
                    json.dumps(scan_result or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT id FROM release_files WHERE project_id=? AND root_key=? "
                "AND relative_path=?",
                (project_id, root_key, relative_path),
            ).fetchone()
            return int(row["id"]) if row else 0

    def get_release_file(self, file_id: int) -> dict[str, Any] | None:
        row = self.fetch_one("SELECT * FROM release_files WHERE id=?", (file_id,))
        return self._decode_release_file(row) if row else None

    def list_release_files(
        self,
        project_id: int,
        *,
        file_role: str | None = None,
        include_missing: bool = True,
    ) -> list[dict[str, Any]]:
        clauses = ["project_id=?"]
        params: list[Any] = [project_id]
        if file_role:
            clauses.append("file_role=?")
            params.append(file_role)
        if not include_missing:
            clauses.append("missing_at IS NULL")
        rows = self.fetch_all(
            f"SELECT * FROM release_files WHERE {' AND '.join(clauses)} "
            "ORDER BY relative_path",
            tuple(params),
        )
        return [self._decode_release_file(row) for row in rows]

    def update_release_file(self, file_id: int, **fields: Any) -> bool:
        allowed = {
            "season",
            "episode",
            "title",
            "language",
            "corrected_json",
            "missing_at",
            "size_bytes",
            "mtime_ns",
            "scan_result_json",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unsupported file fields: {sorted(invalid)}")
        if not fields:
            return False
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE release_files SET {assignments} WHERE id=?",
                (*fields.values(), file_id),
            )
            return cursor.rowcount == 1

    def update_release_file_location(
        self,
        file_id: int,
        *,
        root_key: str,
        relative_path: str,
        size_bytes: int,
        mtime_ns: int,
    ) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE release_files SET root_key=?, relative_path=?, size_bytes=?, "
                "mtime_ns=?, missing_at=NULL, updated_at=? WHERE id=?",
                (
                    root_key,
                    relative_path,
                    size_bytes,
                    mtime_ns,
                    utc_now(),
                    file_id,
                ),
            )
            return cursor.rowcount == 1

    def mark_missing_release_files(
        self,
        project_id: int,
        root_key: str,
        prefix: str,
        seen_paths: set[str],
        *,
        now: str | None = None,
    ) -> int:
        rows = self.fetch_all(
            "SELECT id, relative_path FROM release_files "
            "WHERE project_id=? AND root_key=? AND missing_at IS NULL",
            (project_id, root_key),
        )
        missing_ids = [
            row["id"]
            for row in rows
            if (prefix == "" or row["relative_path"].startswith(prefix))
            and row["relative_path"] not in seen_paths
        ]
        if missing_ids:
            self.executemany(
                "UPDATE release_files SET missing_at=?, updated_at=? WHERE id=?",
                [(now or utc_now(), now or utc_now(), file_id) for file_id in missing_ids],
            )
        return len(missing_ids)

    def replace_release_file_links(
        self, project_id: int, links: list[tuple[int, int, str, float]]
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM release_file_links WHERE project_id=?", (project_id,)
            )
            if links:
                now = utc_now()
                connection.executemany(
                    "INSERT INTO release_file_links("
                    "project_id, video_file_id, subtitle_file_id, link_type, "
                    "confidence, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (project_id, video_id, subtitle_id, kind, confidence, now)
                        for video_id, subtitle_id, kind, confidence in links
                    ],
                )

    def list_release_file_links(self, project_id: int) -> list[dict[str, Any]]:
        return self.fetch_all(
            "SELECT * FROM release_file_links WHERE project_id=? "
            "ORDER BY video_file_id, subtitle_file_id",
            (project_id,),
        )

    # Tasks -----------------------------------------------------------
    @staticmethod
    def _decode_task(row: dict[str, Any]) -> dict[str, Any]:
        for source, target in (("input_json", "input"), ("result_json", "result")):
            try:
                row[target] = json.loads(row.pop(source) or "{}")
            except json.JSONDecodeError:
                row[target] = {}
        return row

    def find_active_processing_task(
        self, task_type: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        row = self.fetch_one(
            "SELECT * FROM processing_tasks WHERE task_type=? AND idempotency_key=? "
            "AND status IN ('queued', 'running') ORDER BY id DESC LIMIT 1",
            (task_type, idempotency_key),
        )
        return self._decode_task(row) if row else None

    def create_processing_task(
        self,
        *,
        project_id: int | None,
        task_type: str,
        input_data: dict[str, Any],
        log_path: str,
        idempotency_key: str,
    ) -> int:
        return self.execute(
            """
            INSERT INTO processing_tasks(
                project_id, task_type, status, input_json, result_json,
                idempotency_key, log_path, created_at
            ) VALUES (?, ?, 'queued', ?, '{}', ?, ?, ?)
            """,
            (
                project_id,
                task_type,
                json.dumps(input_data, ensure_ascii=False),
                idempotency_key,
                log_path,
                utc_now(),
            ),
        )

    def get_processing_task(self, task_id: int) -> dict[str, Any] | None:
        row = self.fetch_one("SELECT * FROM processing_tasks WHERE id=?", (task_id,))
        return self._decode_task(row) if row else None

    def list_processing_tasks(
        self,
        *,
        project_id: int | None = None,
        status: str | None = None,
        task_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("project_id=?")
            params.append(project_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        if task_type:
            clauses.append("task_type=?")
            params.append(task_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend((min(max(limit, 1), 500), max(offset, 0)))
        rows = self.fetch_all(
            f"SELECT * FROM processing_tasks {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            tuple(params),
        )
        return [self._decode_task(row) for row in rows]

    def next_queued_processing_task(self) -> dict[str, Any] | None:
        row = self.fetch_one(
            "SELECT * FROM processing_tasks WHERE status='queued' ORDER BY id ASC LIMIT 1"
        )
        return self._decode_task(row) if row else None

    def claim_processing_task(self, task_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE processing_tasks SET status='running', started_at=?, "
                "finished_at=NULL, error=NULL, cancel_requested=0 "
                "WHERE id=? AND status='queued'",
                (utc_now(), task_id),
            )
            return cursor.rowcount == 1

    def update_processing_task(self, task_id: int, **fields: Any) -> bool:
        allowed = {
            "status",
            "result_json",
            "error",
            "retry_count",
            "cancel_requested",
            "started_at",
            "finished_at",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unsupported task fields: {sorted(invalid)}")
        if not fields:
            return False
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE processing_tasks SET {assignments} WHERE id=?",
                (*fields.values(), task_id),
            )
            return cursor.rowcount == 1

    def retry_processing_task(self, task_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE processing_tasks
                SET status='queued', started_at=NULL, finished_at=NULL, error=NULL,
                    cancel_requested=0, retry_count=retry_count + 1
                WHERE id=? AND status IN ('failed', 'cancelled')
                """,
                (task_id,),
            )
            return cursor.rowcount == 1

    def cancel_processing_task(self, task_id: int) -> bool:
        with self.connect() as connection:
            queued = connection.execute(
                "UPDATE processing_tasks SET status='cancelled', finished_at=? "
                "WHERE id=? AND status='queued'",
                (utc_now(), task_id),
            )
            if queued.rowcount == 1:
                return True
            running = connection.execute(
                "UPDATE processing_tasks SET cancel_requested=1 "
                "WHERE id=? AND status='running'",
                (task_id,),
            )
            return running.rowcount == 1

    def prepare_processing_tasks_on_startup(self) -> None:
        now = utc_now()
        with self.connect() as connection:
            project_ids = [
                int(row["project_id"])
                for row in connection.execute(
                    "SELECT DISTINCT project_id FROM processing_tasks "
                    "WHERE status='running' AND project_id IS NOT NULL"
                )
            ]
            connection.execute(
                "UPDATE processing_tasks SET status='failed', finished_at=?, "
                "error='服务重启导致任务中断，请确认文件状态后手动重试' "
                "WHERE status='running'",
                (now,),
            )
            if project_ids:
                placeholders = ",".join("?" for _ in project_ids)
                connection.execute(
                    f"UPDATE release_projects SET status=CASE WHEN EXISTS ("
                    f"SELECT 1 FROM processing_tasks t WHERE t.project_id=release_projects.id "
                    f"AND t.status='queued') THEN 'processing' ELSE 'failed' END, "
                    f"updated_at=? "
                    f"WHERE status!='completed' AND id IN ({placeholders})",
                    (now, *project_ids),
                )

    def active_project_task_count(self, project_id: int) -> int:
        row = self.fetch_one(
            "SELECT COUNT(*) AS count FROM processing_tasks "
            "WHERE project_id=? AND status IN ('queued', 'running')",
            (project_id,),
        )
        return int(row["count"]) if row else 0

    def list_project_task_log_paths(self, project_id: int) -> list[str]:
        return [
            str(row["log_path"])
            for row in self.fetch_all(
                "SELECT log_path FROM processing_tasks "
                "WHERE project_id=? AND log_path IS NOT NULL",
                (project_id,),
            )
            if row.get("log_path")
        ]

    # Naming profiles -------------------------------------------------
    def list_naming_profiles(self) -> list[dict[str, Any]]:
        return self.fetch_all(
            "SELECT * FROM naming_profiles ORDER BY is_default DESC, name"
        )

    def get_naming_profile(self, profile_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM naming_profiles WHERE id=?", (profile_id,))

    def create_naming_profile(
        self, *, name: str, media_type: str, template: str, is_default: bool = False
    ) -> int:
        now = utc_now()
        with self.connect() as connection:
            if is_default:
                connection.execute(
                    "UPDATE naming_profiles SET is_default=0 WHERE media_type=?",
                    (media_type,),
                )
            cursor = connection.execute(
                "INSERT INTO naming_profiles(name, media_type, template, is_default, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (name, media_type, template, int(is_default), now, now),
            )
            return int(cursor.lastrowid or 0)

    def update_naming_profile(
        self,
        profile_id: int,
        *,
        name: str,
        template: str,
        is_default: bool,
    ) -> bool:
        now = utc_now()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT media_type FROM naming_profiles WHERE id=?", (profile_id,)
            ).fetchone()
            if not row:
                return False
            media_type = str(row["media_type"])
            if is_default:
                connection.execute(
                    "UPDATE naming_profiles SET is_default=0 WHERE media_type=?",
                    (media_type,),
                )
            cursor = connection.execute(
                "UPDATE naming_profiles SET name=?, template=?, is_default=?, "
                "updated_at=? WHERE id=?",
                (name, template, int(is_default), now, profile_id),
            )
            if cursor.rowcount != 1:
                return False
            if not is_default:
                default_row = connection.execute(
                    "SELECT COUNT(*) AS count FROM naming_profiles "
                    "WHERE media_type=? AND is_default=1",
                    (media_type,),
                ).fetchone()
                if default_row and int(default_row["count"]) == 0:
                    replacement = connection.execute(
                        "SELECT id FROM naming_profiles WHERE media_type=? "
                        "AND id<>? ORDER BY id LIMIT 1",
                        (media_type, profile_id),
                    ).fetchone()
                    if replacement:
                        connection.execute(
                            "UPDATE naming_profiles SET is_default=1, updated_at=? "
                            "WHERE id=?",
                            (now, replacement["id"]),
                        )
            return True

    def delete_naming_profile(self, profile_id: int) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT media_type, is_default FROM naming_profiles WHERE id=?",
                (profile_id,),
            ).fetchone()
            if not row:
                return False
            count = connection.execute(
                "SELECT COUNT(*) AS count FROM naming_profiles WHERE media_type=?",
                (row["media_type"],),
            ).fetchone()
            if count and int(count["count"]) <= 1:
                raise ValueError("每种媒体类型必须保留至少一个命名预设")
            connection.execute("DELETE FROM naming_profiles WHERE id=?", (profile_id,))
            if row["is_default"]:
                replacement = connection.execute(
                    "SELECT id FROM naming_profiles WHERE media_type=? "
                    "ORDER BY id LIMIT 1",
                    (row["media_type"],),
                ).fetchone()
                if replacement:
                    connection.execute(
                        "UPDATE naming_profiles SET is_default=1, updated_at=? "
                        "WHERE id=?",
                        (utc_now(), replacement["id"]),
                    )
            return True

    # Rename operations ----------------------------------------------
    def create_rename_operation(
        self,
        *,
        batch_id: str,
        task_id: int,
        project_id: int,
        file_id: int | None,
        source_root_key: str,
        source_relative_path: str,
        target_root_key: str,
        target_relative_path: str,
        source_size_bytes: int,
        source_mtime_ns: int,
    ) -> int:
        return self.execute(
            """
            INSERT INTO rename_operations(
                batch_id, task_id, project_id, file_id, source_root_key,
                source_relative_path, target_root_key, target_relative_path,
                source_size_bytes, source_mtime_ns, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?)
            """,
            (
                batch_id,
                task_id,
                project_id,
                file_id,
                source_root_key,
                source_relative_path,
                target_root_key,
                target_relative_path,
                source_size_bytes,
                source_mtime_ns,
                utc_now(),
            ),
        )

    def update_rename_operation(self, operation_id: int, **fields: Any) -> bool:
        allowed = {
            "target_size_bytes",
            "target_mtime_ns",
            "status",
            "error",
            "rolled_back_at",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unsupported rename fields: {sorted(invalid)}")
        assignments = ", ".join(f"{key}=?" for key in fields)
        self.execute(
            f"UPDATE rename_operations SET {assignments} WHERE id=?",
            (*fields.values(), operation_id),
        )
        return True

    def latest_rename_batch(self, project_id: int) -> str | None:
        row = self.fetch_one(
            "SELECT batch_id FROM rename_operations WHERE project_id=? "
            "AND status='applied' ORDER BY id DESC LIMIT 1",
            (project_id,),
        )
        return str(row["batch_id"]) if row else None

    def list_rename_operations(
        self, project_id: int, *, batch_id: str | None = None
    ) -> list[dict[str, Any]]:
        if batch_id:
            return self.fetch_all(
                "SELECT * FROM rename_operations WHERE project_id=? AND batch_id=? "
                "ORDER BY id",
                (project_id, batch_id),
            )
        return self.fetch_all(
            "SELECT * FROM rename_operations WHERE project_id=? ORDER BY id DESC",
            (project_id,),
        )

    # Checks and events ----------------------------------------------
    def create_release_check(
        self,
        *,
        project_id: int,
        task_id: int | None,
        status: str,
        result: dict[str, Any],
        error: str | None = None,
    ) -> int:
        return self.execute(
            "INSERT INTO release_checks(project_id, task_id, status, result_json, "
            "error, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                project_id,
                task_id,
                status,
                json.dumps(result, ensure_ascii=False),
                error,
                utc_now(),
            ),
        )

    def list_release_checks(self, project_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.fetch_all(
            "SELECT * FROM release_checks WHERE project_id=? ORDER BY id DESC LIMIT ?",
            (project_id, min(max(limit, 1), 100)),
        )
        for row in rows:
            try:
                row["result"] = json.loads(row.pop("result_json") or "{}")
            except json.JSONDecodeError:
                row["result"] = {}
        return rows

    def create_release_event(
        self,
        *,
        project_id: int,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> int:
        return self.execute(
            "INSERT INTO release_events(project_id, event_type, message, payload_json, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (
                project_id,
                event_type,
                message,
                json.dumps(payload or {}, ensure_ascii=False),
                utc_now(),
            ),
        )

    def list_release_events(
        self, project_id: int, limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = self.fetch_all(
            "SELECT * FROM release_events WHERE project_id=? ORDER BY id DESC LIMIT ?",
            (project_id, min(max(limit, 1), 500)),
        )
        for row in rows:
            try:
                row["payload"] = json.loads(row.pop("payload_json") or "{}")
            except json.JSONDecodeError:
                row["payload"] = {}
        return rows

    # Image hosting ---------------------------------------------------
    def create_image_upload(
        self,
        *,
        provider: str,
        source_kind: str,
        source_root_key: str,
        source_relative_path: str,
        source_name: str,
        source_size_bytes: int,
        source_fingerprint: str,
        session_id: str | None = None,
        session_item_id: str | None = None,
    ) -> int:
        now = utc_now()
        return self.execute(
            """
            INSERT INTO image_uploads(
                provider, status, source_kind, source_root_key,
                source_relative_path, session_id, session_item_id,
                source_name, source_size_bytes, source_fingerprint,
                created_at, updated_at
            ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                source_kind,
                source_root_key,
                source_relative_path,
                session_id,
                session_item_id,
                source_name,
                int(source_size_bytes),
                source_fingerprint,
                now,
                now,
            ),
        )

    def update_image_upload(self, upload_id: int, **fields: Any) -> bool:
        allowed = {
            "provider",
            "status",
            "remote_url",
            "thumbnail_url",
            "remote_id",
            "error",
            "attempts",
            "source_fingerprint",
            "source_size_bytes",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unsupported image upload fields: {sorted(invalid)}")
        if not fields:
            return False
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE image_uploads SET {assignments} WHERE id=?",
                (*fields.values(), upload_id),
            )
            return cursor.rowcount == 1

    def get_image_upload(self, upload_id: int) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM image_uploads WHERE id=?", (upload_id,))

    def list_image_uploads(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if session_id:
            clauses.append("session_id=?")
            params.append(session_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend((min(max(limit, 1), 500), max(offset, 0)))
        return self.fetch_all(
            f"""
            SELECT * FROM image_uploads {where}
            ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?
            """,
            tuple(params),
        )

    def latest_uploads_for_session_items(
        self, session_id: str
    ) -> dict[str, dict[str, Any]]:
        rows = self.fetch_all(
            """
            SELECT u.* FROM image_uploads u
            JOIN (
                SELECT session_item_id, MAX(id) AS max_id
                FROM image_uploads
                WHERE session_id=? AND session_item_id IS NOT NULL
                GROUP BY session_item_id
            ) latest ON latest.max_id=u.id
            """,
            (session_id,),
        )
        return {
            str(row["session_item_id"]): row
            for row in rows
            if row.get("session_item_id")
        }

    # Release information -------------------------------------------
    @staticmethod
    def _decode_release_document(row: dict[str, Any]) -> dict[str, Any]:
        try:
            row["media"] = json.loads(row.pop("media_json") or "{}")
        except json.JSONDecodeError:
            row["media"] = {}
        try:
            row["file_list"] = json.loads(row.pop("file_list_json") or "[]")
        except json.JSONDecodeError:
            row["file_list"] = []
        return row

    def create_release_document(
        self,
        *,
        title: str,
        source_root_key: str,
        source_relative_path: str,
        output_root_key: str,
        format: str,
        media: dict[str, Any],
        file_list: list[dict[str, Any]],
        generated_text: str,
        user_text: str = "",
        output_relative_path: str | None = None,
        status: str = "draft",
        error: str | None = None,
    ) -> int:
        now = utc_now()
        return self.execute(
            """
            INSERT INTO release_documents(
                title, source_root_key, source_relative_path,
                output_root_key, output_relative_path, format,
                media_json, file_list_json, generated_text, user_text,
                status, error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                source_root_key,
                source_relative_path,
                output_root_key,
                output_relative_path,
                format,
                json.dumps(media, ensure_ascii=False),
                json.dumps(file_list, ensure_ascii=False),
                generated_text,
                user_text,
                status,
                error,
                now,
                now,
            ),
        )

    def get_release_document(self, document_id: int) -> dict[str, Any] | None:
        row = self.fetch_one("SELECT * FROM release_documents WHERE id=?", (document_id,))
        return self._decode_release_document(row) if row else None

    def list_release_documents(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        rows = self.fetch_all(
            """
            SELECT * FROM release_documents
            ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?
            """,
            (min(max(limit, 1), 500), max(offset, 0)),
        )
        return [self._decode_release_document(row) for row in rows]

    def update_release_document(self, document_id: int, **fields: Any) -> bool:
        allowed = {
            "media_json",
            "title",
            "format",
            "output_relative_path",
            "generated_text",
            "user_text",
            "status",
            "error",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unsupported release document fields: {sorted(invalid)}")
        if not fields:
            return False
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE release_documents SET {assignments} WHERE id=?",
                (*fields.values(), document_id),
            )
            return cursor.rowcount == 1

    def delete_release_document(self, document_id: int) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM release_documents WHERE id=?", (document_id,)
            )
            return cursor.rowcount == 1

    # Torrent builds --------------------------------------------------
    @staticmethod
    def _decode_torrent_build(row: dict[str, Any]) -> dict[str, Any]:
        try:
            row["trackers"] = json.loads(row.pop("trackers_json") or "[]")
        except json.JSONDecodeError:
            row["trackers"] = []
        row["private"] = bool(row.get("private"))
        return row

    def create_torrent_build(
        self,
        *,
        name: str,
        source_root_key: str,
        source_relative_path: str,
        output_root_key: str,
        output_relative_path: str,
        info_hash: str,
        piece_length: int,
        total_size_bytes: int,
        file_count: int,
        private: bool,
        trackers: list[str],
        source: str,
    ) -> int:
        now = utc_now()
        return self.execute(
            """
            INSERT INTO torrent_builds(
                name, source_root_key, source_relative_path,
                output_root_key, output_relative_path, info_hash,
                piece_length, total_size_bytes, file_count, private,
                trackers_json, source, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?, ?)
            """,
            (
                name,
                source_root_key,
                source_relative_path,
                output_root_key,
                output_relative_path,
                info_hash,
                int(piece_length),
                int(total_size_bytes),
                int(file_count),
                int(private),
                json.dumps(trackers, ensure_ascii=False),
                source,
                now,
                now,
            ),
        )

    def get_torrent_build(self, build_id: int) -> dict[str, Any] | None:
        row = self.fetch_one("SELECT * FROM torrent_builds WHERE id=?", (build_id,))
        return self._decode_torrent_build(row) if row else None

    def list_torrent_builds(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        rows = self.fetch_all(
            """
            SELECT * FROM torrent_builds
            ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
            """,
            (min(max(limit, 1), 500), max(offset, 0)),
        )
        return [self._decode_torrent_build(row) for row in rows]

    # Publish sessions ------------------------------------------------
    @staticmethod
    def _decode_publish_session(row: dict[str, Any]) -> dict[str, Any]:
        for source, target, fallback in (
            ("metadata_json", "metadata", {}),
            ("image_upload_ids_json", "image_upload_ids", []),
            ("warnings_json", "warnings", []),
        ):
            try:
                row[target] = json.loads(row.pop(source) or json.dumps(fallback))
            except json.JSONDecodeError:
                row[target] = fallback
        return row

    def create_publish_session(
        self,
        *,
        title: str,
        status: str,
        source_root_key: str | None,
        source_relative_path: str | None,
        description: str,
        metadata: dict[str, Any],
        image_upload_ids: list[int],
        release_document_id: int | None,
        torrent_build_id: int | None,
        bbcode: str,
        html: str,
        markdown: str,
        warnings: list[str],
        qbittorrent_status: str | None = None,
        qbittorrent_error: str | None = None,
    ) -> int:
        now = utc_now()
        return self.execute(
            """
            INSERT INTO publish_sessions(
                title, status, source_root_key, source_relative_path,
                description, metadata_json, image_upload_ids_json,
                release_document_id, torrent_build_id, bbcode, html,
                markdown, warnings_json, qbittorrent_status,
                qbittorrent_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title,
                status,
                source_root_key,
                source_relative_path,
                description,
                json.dumps(metadata, ensure_ascii=False),
                json.dumps(image_upload_ids),
                release_document_id,
                torrent_build_id,
                bbcode,
                html,
                markdown,
                json.dumps(warnings, ensure_ascii=False),
                qbittorrent_status,
                qbittorrent_error,
                now,
                now,
            ),
        )

    def get_publish_session(self, session_id: int) -> dict[str, Any] | None:
        row = self.fetch_one("SELECT * FROM publish_sessions WHERE id=?", (session_id,))
        return self._decode_publish_session(row) if row else None

    def list_publish_sessions(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        rows = self.fetch_all(
            """
            SELECT * FROM publish_sessions
            ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?
            """,
            (min(max(limit, 1), 500), max(offset, 0)),
        )
        return [self._decode_publish_session(row) for row in rows]

    def update_publish_session(self, session_id: int, **fields: Any) -> bool:
        allowed = {
            "title",
            "status",
            "description",
            "metadata_json",
            "image_upload_ids_json",
            "release_document_id",
            "torrent_build_id",
            "bbcode",
            "html",
            "markdown",
            "warnings_json",
            "qbittorrent_status",
            "qbittorrent_error",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"Unsupported publish session fields: {sorted(invalid)}")
        if not fields:
            return False
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE publish_sessions SET {assignments} WHERE id=?",
                (*fields.values(), session_id),
            )
            return cursor.rowcount == 1


def merge_settings(defaults: dict[str, str], stored: dict[str, str]) -> dict[str, str]:
    return json.loads(json.dumps({**defaults, **stored}))
