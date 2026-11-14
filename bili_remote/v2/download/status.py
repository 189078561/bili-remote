"""Read-only presentation fields for the existing download job rows."""

from __future__ import annotations

import stat
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

_TERMINAL = {"completed", "failed", "cancelled", "auth_required"}


def _result_file_size(path: Any) -> int | None:
    if not path:
        return None
    try:
        result = Path(path).stat()
    except (OSError, TypeError, ValueError):
        return None
    if not stat.S_ISREG(result.st_mode):
        return None
    return result.st_size if result.st_size >= 0 else None


def _duration_seconds(started_at: Any, finished_at: Any) -> int | None:
    if not started_at or not finished_at:
        return None
    try:
        started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        finished = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
        return max(0, int((finished - started).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


def project_download_job(
    job: Mapping[str, Any],
    *,
    file_size: Callable[[Any], int | None] = _result_file_size,
) -> dict[str, Any]:
    """Add display status, duration, and the post-processed output size.

    The legacy queue only has ``running``. A running job at 100% is in
    yt-dlp's post-processing phase, so it is safe to expose that as ``merging``
    without changing persisted status semantics.
    """

    item = dict(job)
    status = str(item.get("status") or "")
    display_status = status
    if status == "running":
        try:
            progress = float(item.get("progress") or 0)
        except (TypeError, ValueError):
            progress = 0
        display_status = "merging" if progress >= 99.5 else "downloading"

    item["display_status"] = display_status
    item["download_duration_seconds"] = (
        _duration_seconds(item.get("started_at"), item.get("finished_at"))
        if status in _TERMINAL
        else None
    )
    item["final_size_bytes"] = (
        file_size(item.get("result_path")) if status == "completed" else None
    )
    return item
