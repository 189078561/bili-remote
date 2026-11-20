"""Exact track selectors and the public episode quality preview."""

import math
import re
from typing import Any


_TRACK_ID = r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}"
_TRACK_SELECTOR = re.compile(rf"\(({_TRACK_ID})(?:\+({_TRACK_ID}))?\)")
_FORMAT_ALIASES = {"b", "best", "bv", "bestvideo", "ba", "bestaudio", "w", "worst", "wv", "worstvideo", "wa", "worstaudio", "all", "mergeall"}


def exact_tracks(selector: str) -> tuple[str, ...]:
    """Parenthesized track IDs opt into keeping the confirmed tracks on retry."""
    match = _TRACK_SELECTOR.fullmatch(selector)
    if not match:
        return ()
    tracks = tuple(track for track in match.groups() if track)
    return tracks if all(track.lower() not in _FORMAT_ALIASES for track in tracks) else ()


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def preview_tracks(formats: list[dict[str, Any]], duration: Any) -> list[dict[str, Any]]:
    tracks = [
        item for item in formats
        if re.fullmatch(_TRACK_ID, str(item.get("format_id") or ""))
        and str(item.get("format_id")).lower() not in _FORMAT_ALIASES
    ]
    if not tracks:
        return []
    audio = [
        item for item in tracks
        if item.get("vcodec") == "none" and item.get("acodec") not in (None, "", "none")
    ]
    best_audio = max(audio, key=lambda item: _positive(item.get("tbr")) or 0, default=None)
    seconds = _positive(duration)

    def track_size(item: dict[str, Any]) -> float | None:
        bitrate = _positive(item.get("tbr"))
        return _positive(item.get("filesize")) or (bitrate * 125 * seconds if bitrate and seconds else None)

    public = []
    for item in tracks:
        parts = [item["format_id"]]
        bytes_count = track_size(item)
        estimated = not _positive(item.get("filesize"))
        if item.get("acodec") in (None, "", "none"):
            if item.get("vcodec") in (None, "", "none") or best_audio is None:
                continue
            parts.append(best_audio["format_id"])
            audio_bytes = track_size(best_audio)
            estimated = estimated or not _positive(best_audio.get("filesize"))
            bytes_count = bytes_count + audio_bytes if bytes_count and audio_bytes else None
        public.append({
            **item,
            "locked_selector": f"({'+'.join(map(str, parts))})",
            "size_with_audio": round(bytes_count) if bytes_count else None,
            "size_estimated": bool(bytes_count and estimated),
        })
    return public
