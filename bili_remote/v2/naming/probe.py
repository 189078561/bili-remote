"""V2 projection of the shared ffprobe entry point; no file or database writes."""
from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any
from copy import deepcopy
import json

from bili_remote import media_probe


TECHNICAL_FIELDS = (
    "width", "height", "resolution", "video_codec", "audio_codec",
    "audio_channels", "frame_rate", "bit_depth", "hdr", "duration_seconds", "container",
)


def _field(value: Any, source_ref: str | None) -> dict:
    return {"value": value, "source": "ffprobe", "source_ref": source_ref,
            "manual": False, "confirmed": value is not None,
            "confidence": 1.0 if value is not None else None,
            "updated_at": datetime.now(timezone.utc).isoformat()}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (ValueError, TypeError):
        return None
    return result if result > 0 else None


def _rate(value: Any) -> dict | None:
    try:
        rate = Fraction(str(value))
    except (ValueError, ZeroDivisionError, TypeError):
        return None
    if not 0 < rate <= 1000:
        return None
    return {"numerator": rate.numerator, "denominator": rate.denominator}


def _depth(stream: dict) -> int | None:
    depth = _positive_int(stream.get("bits_per_raw_sample"))
    if depth:
        return depth
    # Pixel-format component depths are facts reported by ffprobe. Codec names,
    # profiles and output resolution alone do not establish a bit depth.
    pixel_format = str(stream.get("pix_fmt") or "")
    match = re.fullmatch(r"(?:yuva?\d+p|gbra?p|gray|ya)(\d+)(?:le|be)?", pixel_format)
    if match:
        return int(match.group(1))
    packed = re.fullmatch(r"p[024]?(?:10|12|16)(?:le|be)", pixel_format)
    if packed:
        return int(re.search(r"(10|12|16)", pixel_format).group(1))
    if pixel_format in {"yuv420p", "yuv422p", "yuv444p", "yuv410p", "yuv411p",
                        "yuv440p", "yuvj420p", "yuvj422p", "yuvj444p", "yuva420p",
                        "nv12", "nv21", "rgb24", "bgr24", "rgba", "bgra", "gray", "gbrp"}:
        return 8
    return None


def _hdr(stream: dict, depth: int | None) -> list[str] | None:
    side = stream.get("side_data_list") or []
    side_types = [str(item.get("side_data_type") or "").lower()
                  for item in side if isinstance(item, dict)]
    labels = []
    if any("dovi" in item or "dolby vision" in item for item in side_types):
        labels.append("DV")
    if any("hdr10+" in item or "2094-40" in item or "2094_40" in item for item in side_types):
        labels.append("HDR10+")
    transfer = str(stream.get("color_transfer") or "").lower()
    if transfer in {"arib-std-b67", "arib_std_b67"}:
        labels.append("HLG")
    elif transfer in {"smpte2084", "smpte-st-2084", "smpte_st_2084"}:
        mastering = any("mastering display metadata" in item for item in side_types)
        labels.append("HDR10" if mastering and depth and depth >= 10 else "HDR")
    elif not labels and transfer in {"bt709", "smpte170m", "smpte240m", "gamma22", "gamma28", "iec61966-2-1"}:
        labels.append("SDR")
    return labels or None


def probe_from_payload(payload: dict, *, source_ref: str | None = None,
                       version: str | None = None) -> dict:
    """Convert one real ffprobe payload; never borrow another file's fields."""
    streams = payload.get("streams") or []
    if not isinstance(streams, list):
        raise ValueError("ffprobe 视频流列表无效")
    videos = [item for item in streams if isinstance(item, dict)
              and item.get("codec_type") == "video"
              and not (item.get("disposition") or {}).get("attached_pic")]
    if not videos:
        raise ValueError("未发现有效视频流")
    video = next((item for item in videos if (item.get("disposition") or {}).get("default")), videos[0])
    audios = [item for item in streams if isinstance(item, dict) and item.get("codec_type") == "audio"]
    audio = next((item for item in audios if (item.get("disposition") or {}).get("default")), audios[0] if audios else {})
    width, height = _positive_int(video.get("width")), _positive_int(video.get("height"))
    codec = media_probe.normalize_video_codec(video.get("codec_name")) or None
    codec = {"H264": "H.264", "H265": "H.265"}.get(codec, codec)
    # The shared legacy normalizer labels MPEG-4 ASP as XviD. That encoder
    # cannot be established from codec_name, so V2 keeps the codec identity.
    if str(video.get("codec_name") or "").lower() in {"mpeg4", "msmpeg4v3"}:
        codec = "MPEG-4" if video.get("codec_name") == "mpeg4" else "MSMPEG4V3"
    depth = _depth(video)
    format_data = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    duration = None
    for candidate in (video.get("duration"), format_data.get("duration")):
        try:
            parsed = float(candidate)
        except (ValueError, TypeError):
            continue
        if math.isfinite(parsed) and parsed > 0:
            duration = parsed
            break
    values = {
        "width": width, "height": height,
        "resolution": media_probe.normalize_resolution(width, height) or None,
        "video_codec": codec,
        "audio_codec": media_probe.normalize_audio_codec(audio.get("codec_name"), audio.get("profile")) or None,
        "audio_channels": audio.get("channel_layout") or _positive_int(audio.get("channels")),
        "frame_rate": _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate")),
        "bit_depth": depth, "hdr": _hdr(video, depth), "duration_seconds": duration,
        "container": format_data.get("format_name") or None,
    }
    return {**{key: _field(value, source_ref) for key, value in values.items()},
            "probe_tool": "ffprobe", "probe_version": version, "probe_error": None}


class MediaProbeAdapter:
    """Uses the existing subprocess implementation and caller-validated paths."""

    def __init__(self, settings: Any, *, timeout: float = 60):
        self.settings = settings
        self.timeout = timeout
        self._version: str | None = None
        self._version_checked = False
        self._version_lock = asyncio.Lock()
        self._evidence = {}

    def evidence_for(self, ref):
        return deepcopy(self._evidence.get(json.dumps(ref, sort_keys=True)))

    async def _get_version(self) -> str | None:
        async with self._version_lock:
            return await self._version_once()

    async def _version_once(self) -> str | None:
        if self._version_checked:
            return self._version
        self._version_checked = True
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *self.settings.ffprobe_command, "-version",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(process.communicate(), min(self.timeout, 5))
            match = re.match(rb"ffprobe version ([^\s]+)", stdout)
            if process.returncode == 0 and match:
                self._version = match.group(1).decode("ascii", errors="replace")
        except (OSError, TimeoutError):
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
        return self._version

    async def probe(self, path: Path, *, source_ref=None, previous=None) -> dict:
        ref = source_ref.get("relative_path") if isinstance(source_ref, dict) else source_ref
        try:
            payload = await media_probe.probe_json(self.settings, Path(path), timeout=self.timeout)
            result = probe_from_payload(payload, source_ref=ref, version=await self._get_version())
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            # Do not expose server paths or stderr in public MediaProbe objects.
            message = "ffprobe 不可用" if isinstance(exc, FileNotFoundError) else "媒体探测失败，参数未知"
            if "超时" in str(exc):
                message = "ffprobe 超时，参数未知"
            result = {**{key: _field(None, ref) for key in TECHNICAL_FIELDS},
                    "probe_tool": "ffprobe", "probe_version": self._version,
                    "probe_error": message}
        self._evidence[json.dumps(source_ref, sort_keys=True)] = deepcopy(result)
        while len(self._evidence) > 1000:
            self._evidence.pop(next(iter(self._evidence)))
        for name in TECHNICAL_FIELDS:
            old = (previous or {}).get(name)
            if isinstance(old, dict) and old.get("manual"):
                result[name] = deepcopy(old)
        return result
