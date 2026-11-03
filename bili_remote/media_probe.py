from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import Settings

EMPTY_MEDIA = {
    "resolution": "",
    "video_codec": "",
    "audio_codec": "",
    "hdr": "",
}

_VIDEO_CODECS = {
    "avc": "H264",
    "h264": "H264",
    "hevc": "H265",
    "h265": "H265",
    "mpeg4": "XviD",
    "msmpeg4v3": "XviD",
    "mpeg2video": "MPEG2",
    "vc1": "VC-1",
}
_AUDIO_CODECS = {
    "aac": "AAC",
    "ac3": "AC3",
    "eac3": "EAC3",
    "dts": "DTS",
    "truehd": "TrueHD",
    "flac": "FLAC",
    "opus": "OPUS",
    "vorbis": "Vorbis",
    "mp3": "MP3",
}


def probe_executable(settings: Settings) -> str | None:
    command = settings.ffprobe_command
    if not command:
        return None
    executable = command[0]
    if os.path.isabs(executable):
        return executable if Path(executable).is_file() else None
    return shutil.which(executable)


def probe_command(settings: Settings, path: Path) -> list[str]:
    return [
        *settings.ffprobe_command,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]


async def probe_json(
    settings: Settings, path: Path, *, timeout: float = 60
) -> dict[str, Any]:
    execution_path = probe_executable(settings)
    if not execution_path:
        raise FileNotFoundError("ffprobe 不可用")
    process = await asyncio.create_subprocess_exec(
        *probe_command(settings, path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("ffprobe 超时") from None
    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or "ffprobe 返回失败")
    try:
        return json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe 返回了无效 JSON") from exc


def probe_json_sync(
    settings: Settings, path: Path, *, timeout: float = 30
) -> dict[str, Any]:
    execution_path = probe_executable(settings)
    if not execution_path:
        raise FileNotFoundError("ffprobe 不可用")
    try:
        completed = subprocess.run(
            probe_command(settings, path),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("ffprobe 超时") from exc
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or "ffprobe 返回失败")
    try:
        return json.loads(completed.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe 返回了无效 JSON") from exc


def _clean_codec(value: Any) -> str:
    return str(value or "").strip().replace(" ", "-").upper()


def normalize_video_codec(value: Any) -> str:
    codec = str(value or "").strip().lower()
    if not codec:
        return ""
    return _VIDEO_CODECS.get(codec, _clean_codec(codec))


def normalize_audio_codec(value: Any, profile: Any = "") -> str:
    codec = str(value or "").strip().lower()
    if not codec:
        return ""
    if codec.startswith("pcm_"):
        return "Uncompressed"
    if codec == "dts":
        profile_text = str(profile or "").strip().lower()
        if "dts-hd" in profile_text or "dts hd" in profile_text:
            return "DTS-HD"
        return "DTS"
    return _AUDIO_CODECS.get(codec, _clean_codec(codec))


def normalize_resolution(width: Any, height: Any) -> str:
    try:
        width_value = int(width)
        height_value = int(height)
    except (TypeError, ValueError):
        return ""
    if width_value >= 7680 or height_value >= 4320:
        return "4320p"
    if width_value >= 3840 or height_value >= 2160:
        return "2160p"
    if width_value >= 2560 and height_value >= 1440:
        return "2K"
    if width_value >= 1920 or height_value >= 1080:
        return "1080p"
    if width_value >= 1280 or height_value >= 720:
        return "720p"
    return f"{height_value}p" if height_value > 0 else ""


def _hdr_from_stream(stream: dict[str, Any]) -> str:
    side_data = stream.get("side_data_list") or []
    side_text = json.dumps(side_data, ensure_ascii=False).lower()
    tags_text = json.dumps(stream.get("tags") or {}, ensure_ascii=False).lower()
    combined = f"{side_text} {tags_text}"
    if "dovi" in combined or "dolby vision" in combined:
        return "DV"
    if "hdr10+" in combined or "smpte st 2094" in combined or "smpte-st-2094" in combined:
        return "HDR10+"
    transfer = str(stream.get("color_transfer") or "").strip().lower()
    if transfer in {"arib-std-b67", "arib_std_b67"}:
        return "HLG"
    if transfer in {"smpte2084", "smpte-st-2084", "smpte_st_2084"}:
        return "HDR"
    if "mastering display metadata" in side_text and transfer:
        return "HDR"
    return ""


def media_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    streams = payload.get("streams") or []
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"), None
    )
    if not video_stream:
        raise RuntimeError("未发现有效视频流")
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"), None
    )
    height = video_stream.get("height")
    return {
        "resolution": normalize_resolution(video_stream.get("width"), height),
        "video_codec": normalize_video_codec(video_stream.get("codec_name")),
        "audio_codec": normalize_audio_codec(
            audio_stream.get("codec_name") if audio_stream else None,
            audio_stream.get("profile") if audio_stream else None,
        ),
        "hdr": _hdr_from_stream(video_stream),
    }


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _frame_rate(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        try:
            top = float(numerator)
            bottom = float(denominator)
        except ValueError:
            return None
        if not math.isfinite(top) or not math.isfinite(bottom) or bottom <= 0:
            return None
        result = top / bottom
    else:
        parsed = _number(text)
        result = parsed if parsed is not None else 0
    return result if math.isfinite(result) and 0 < result <= 1000 else None


def _aspect_ratio(width: int, height: int, reported: Any) -> str:
    text = str(reported or "").strip()
    if re.fullmatch(r"\d{1,5}:\d{1,5}", text) and not text.startswith("0:"):
        return text
    if width <= 0 or height <= 0:
        return ""
    divisor = math.gcd(width, height)
    if divisor <= 0:
        return ""
    return f"{width // divisor}:{height // divisor}"


def video_details_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract the fields required by the collage information bar.

    Missing values stay empty/None so callers never present guesses as probed
    metadata.
    """

    streams = payload.get("streams") or []
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"), None
    )
    if not video_stream:
        raise RuntimeError("未发现有效视频流")
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"), None
    )
    format_data = payload.get("format") or {}
    # A container may keep running for a longer audio stream after the video
    # stream has ended. Sampling against the container duration then requests
    # frames past the end of the picture. Prefer the video stream duration and
    # use the container duration only when the stream does not report one.
    duration = _number(video_stream.get("duration"))
    if duration is None or duration <= 0:
        duration = _number(format_data.get("duration"))
    try:
        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
    except (TypeError, ValueError):
        width = 0
        height = 0
    fps = _frame_rate(video_stream.get("avg_frame_rate"))
    if fps is None:
        fps = _frame_rate(video_stream.get("r_frame_rate"))
    return {
        "width": width if width > 0 else None,
        "height": height if height > 0 else None,
        "aspect_ratio": _aspect_ratio(
            width,
            height,
            video_stream.get("display_aspect_ratio"),
        ),
        "fps": fps,
        "duration_seconds": duration if duration and duration > 0 else None,
        "video_codec": normalize_video_codec(video_stream.get("codec_name")),
        "audio_codec": normalize_audio_codec(
            audio_stream.get("codec_name") if audio_stream else None,
            audio_stream.get("profile") if audio_stream else None,
        ),
    }


def probe_media_file(settings: Settings, path: Path) -> dict[str, Any]:
    executable = probe_executable(settings)
    if not executable:
        return {
            "status": "unavailable",
            "tool": None,
            "media": dict(EMPTY_MEDIA),
            "error": "ffprobe 不可用",
        }
    try:
        payload = probe_json_sync(settings, path)
        media = media_from_payload(payload)
    except (RuntimeError, OSError, ValueError) as exc:
        return {
            "status": "failed",
            "tool": executable,
            "media": dict(EMPTY_MEDIA),
            "error": str(exc) or exc.__class__.__name__,
        }
    return {
        "status": "completed",
        "tool": executable,
        "media": media,
        "error": None,
    }
