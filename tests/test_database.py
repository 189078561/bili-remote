from __future__ import annotations

import sqlite3

from bili_remote.database import Database


def test_initialize_requeues_running_job(settings) -> None:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    job_id = database.create_job(
        source_url="https://www.bilibili.com/video/BV1",
        kind="manual",
        output_template="Bilibili/%(title)s.%(ext)s",
        output_dir="",
        api_mode="tv",
    )
    assert database.get_job(job_id)["api_mode"] == "tv"
    assert database.claim_job(job_id)
    assert database.get_job(job_id)["status"] == "running"

    Database(settings.database_path).initialize()
    assert database.get_job(job_id)["status"] == "queued"


def test_initialize_adds_api_mode_to_existing_jobs_table(settings) -> None:
    settings.ensure_directories()
    with sqlite3.connect(settings.database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_url TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT,
                uploader TEXT,
                format_selector TEXT NOT NULL DEFAULT 'bv*+ba/b',
                all_parts INTEGER NOT NULL DEFAULT 0,
                output_template TEXT NOT NULL,
                output_dir TEXT NOT NULL DEFAULT '',
                subscription_id INTEGER,
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
            CREATE INDEX idx_jobs_status_created ON jobs(status, created_at);
            CREATE INDEX idx_jobs_subscription ON jobs(subscription_id, status);
            """
        )
    database = Database(settings.database_path)
    database.initialize()
    columns = {
        row["name"]
        for row in database.fetch_all("PRAGMA table_info(jobs)")
    }
    assert "api_mode" in columns


def test_parse_history_deduplicates_and_keeps_latest_200(settings) -> None:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()

    def save(index: int) -> None:
        database.save_history(
            source_url=f"https://www.bilibili.com/video/BV{index}",
            api_mode="web",
            title=f"Video {index}",
            uploader="Tester",
            duration=90,
            is_playlist=False,
            playlist_count=None,
            default_episode_id=None,
            formats=[{"format_id": "126"}],
            episodes=[],
        )

    save(1)
    save(1)
    record = database.list_history(limit=10)[0]
    assert record["parse_count"] == 2
    assert len(database.list_history(limit=200)) == 1

    for index in range(2, 205):
        save(index)
    assert len(database.list_history(limit=200)) == 200
    detail = database.get_history(database.list_history(limit=1)[0]["id"])
    assert detail is not None
    assert detail["formats_json"] == '[{"format_id": "126"}]'
    assert database.get_history(999999) is None


def test_tv_auth_roundtrip_and_clear(settings) -> None:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    database.save_tv_auth(
        "encrypted-access",
        "encrypted-refresh",
        {"mid": 42, "uname": "TV Tester"},
    )
    record = database.get_tv_auth()
    assert record is not None
    assert record["account_mid"] == 42
    assert record["account_name"] == "TV Tester"
    database.clear_tv_auth()
    assert database.get_tv_auth() is None
