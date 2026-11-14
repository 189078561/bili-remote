from __future__ import annotations

from pathlib import Path

from bili_remote.v2.download.status import project_download_job


def job(**overrides):
    value = {
        "id": 7,
        "title": "synthetic download",
        "status": "running",
        "progress": 42,
        "started_at": "2026-11-15T01:00:00+00:00",
        "finished_at": None,
        "result_path": None,
    }
    value.update(overrides)
    return value


def test_running_progress_is_downloading_and_complete_progress_is_merging():
    assert project_download_job(job(progress=42))["display_status"] == "downloading"
    assert project_download_job(job(progress=100))["display_status"] == "merging"


def test_completed_job_exposes_regular_output_size_and_duration(tmp_path: Path):
    output = tmp_path / "merged-output.mkv"
    output.write_bytes(b"final-output" * 17)

    projected = project_download_job(
        job(
            status="completed",
            progress=100,
            result_path=str(output),
            started_at="2026-11-15T01:00:00+00:00",
            finished_at="2026-11-15T01:01:05+00:00",
        )
    )

    assert projected["display_status"] == "completed"
    assert projected["final_size_bytes"] == len(b"final-output" * 17)
    assert projected["download_duration_seconds"] == 65


def test_missing_or_non_regular_result_path_is_not_reported_as_final_size(tmp_path: Path):
    missing = project_download_job(
        job(status="completed", result_path=str(tmp_path / "missing.mkv"))
    )
    directory = project_download_job(job(status="completed", result_path=str(tmp_path)))

    assert missing["final_size_bytes"] is None
    assert directory["final_size_bytes"] is None


def test_only_completed_jobs_read_the_result_file():
    def forbidden(_path):
        raise AssertionError("terminal non-completed jobs must not stat a result")

    for status in ("failed", "cancelled", "auth_required"):
        projected = project_download_job(
            job(status=status, result_path="/does/not/matter"), file_size=forbidden
        )
        assert projected["final_size_bytes"] is None


def test_invalid_or_naive_duration_is_ignored_without_raising():
    assert (
        project_download_job(
            job(
                status="completed",
                started_at="2026-11-15T01:00:00",
                finished_at="2026-11-15T01:01:00+00:00",
            )
        )["download_duration_seconds"]
        is None
    )
    assert (
        project_download_job(job(status="completed", started_at="not-a-date"))[
            "download_duration_seconds"
        ]
        is None
    )
