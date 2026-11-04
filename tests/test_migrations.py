from __future__ import annotations

import sqlite3

from bili_remote.database import Database


def test_old_database_upgrades_preserves_data_and_backs_up(settings) -> None:
    settings.ensure_directories()
    with sqlite3.connect(settings.database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE auth (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cookies_encrypted TEXT NOT NULL,
                account_mid INTEGER,
                account_name TEXT,
                vip_status INTEGER NOT NULL DEFAULT 0,
                vip_due_date TEXT,
                updated_at TEXT NOT NULL
            );
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
            CREATE TABLE parse_history (
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
            INSERT INTO auth VALUES (1, 'encrypted', 42, 'Tester', 1, NULL, '2026-01-01');
            INSERT INTO jobs(
                source_url, kind, status, output_template, created_at
            ) VALUES ('https://www.bilibili.com/video/BV1', 'manual', 'completed',
                      '%(title)s.%(ext)s', '2026-01-01');
            INSERT INTO parse_history(
                source_url, title, created_at, updated_at
            ) VALUES ('https://www.bilibili.com/video/BV1', 'Existing',
                      '2026-01-01', '2026-01-01');
            """
        )

    database = Database(settings.database_path)
    database.initialize()
    versions = [row["version"] for row in database.fetch_all(
        "SELECT version FROM schema_migrations ORDER BY version"
    )]
    assert versions == [1, 2, 3, 4, 5]
    assert database.get_auth()["account_name"] == "Tester"
    assert database.get_job(1)["status"] == "completed"
    assert database.list_history()[0]["title"] == "Existing"
    assert database.list_naming_profiles()
    backups = list((settings.data_dir / "backups").glob("bili-remote-schema-v*.sqlite3"))
    assert {path.name.split("-schema-")[1].split("-")[0] for path in backups} == {
        "v1",
        "v2",
        "v3",
        "v4",
        "v5",
    }

    before = {path.name for path in backups}
    Database(settings.database_path).initialize()
    after = {
        path.name
        for path in (settings.data_dir / "backups").glob(
            "bili-remote-schema-v*.sqlite3"
        )
    }
    assert after == before


def test_failed_migration_does_not_record_version(settings, monkeypatch) -> None:
    settings.ensure_directories()
    Database(settings.database_path).initialize()
    from bili_remote import database as database_module

    monkeypatch.setattr(
        database_module,
        "MIGRATIONS",
        ((6, "broken", "CREATE TABLE broken_table(id INTEGER); INVALID SQL;"),),
    )
    database = Database(settings.database_path)
    try:
        database.initialize()
    except sqlite3.DatabaseError:
        pass
    else:
        raise AssertionError("broken migration should fail")
    versions = {
        row["version"]
        for row in database.fetch_all("SELECT version FROM schema_migrations")
    }
    assert 6 not in versions
    assert list((settings.data_dir / "backups").glob("bili-remote-schema-v6-*.sqlite3"))
