from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bili_remote.database import Database
from bili_remote.downloader import MaintenanceScheduler


def test_maintenance_removes_expired_job_logs(settings) -> None:
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    expired_log = settings.logs_dir / "expired.log"
    expired_log.write_text("old", encoding="utf-8")
    old_job = database.create_job(
        source_url="https://www.bilibili.com/video/BV1",
        kind="manual",
        output_template="%(title)s.%(ext)s",
        output_dir="",
        log_path=str(expired_log),
    )
    old_time = (datetime.now(UTC) - timedelta(days=45)).isoformat()
    database.update_job(old_job, status="completed", finished_at=old_time)

    recent_log = settings.logs_dir / "recent.log"
    recent_log.write_text("new", encoding="utf-8")
    recent_job = database.create_job(
        source_url="https://www.bilibili.com/video/BV2",
        kind="manual",
        output_template="%(title)s.%(ext)s",
        output_dir="",
        log_path=str(recent_log),
    )
    database.update_job(
        recent_job, status="completed", finished_at=datetime.now(UTC).isoformat()
    )

    scheduler = MaintenanceScheduler(settings, database)
    scheduler._cleanup_logs()
    assert not expired_log.exists()
    assert recent_log.exists()
