from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

from .collage import CONTENT_PRESETS, output_name_for
from .collage_style import (
    CollageFrame,
    CollageMediaInfo,
    find_cjk_font,
    format_timestamp,
    render_collage,
    render_still,
)
from .color import color_metadata, conversion_filter, rendering_capabilities
from .config import Settings
from .file_service import (
    VIDEO_EXTENSIONS,
    FileService,
    FileServiceError,
    OperationCancelled,
)
from .media_probe import (
    probe_executable,
    probe_json_sync,
    video_details_from_payload,
)
from .media_tools import defaults, disc_root, optical_input
from .workspace import normalize_relative_path

MAX_SOURCES = 100
MAX_ITEMS_PER_VIDEO = 120
MAX_TOTAL_ITEMS = 1000
MAX_COLUMNS = 10
MAX_ROWS = 12
MIN_WIDTH = 240
MAX_WIDTH = 3840
MIN_COLLAGE_WIDTH = 240
MAX_COLLAGE_WIDTH = 1920
MIN_GAP = 0
MAX_GAP = 40
MAX_CANVAS_PIXELS = 150_000_000
MAX_CANVAS_SIDE = 20_000
FFMPEG_TIMEOUT_SECONDS = 60
FFPROBE_TIMEOUT_SECONDS = 30
TEMP_MAX_AGE_SECONDS = 24 * 60 * 60
_SESSION_ID = re.compile(r"^[a-f0-9]{32}$")
_TIMESTAMP = re.compile(
    r"^(?:(?P<hours>\d{1,3}):)?(?P<minutes>\d{1,2}):(?P<seconds>\d{1,2}(?:\.\d{1,3})?)$"
)
_INVALID_FILENAME_CHARS = str.maketrans({character: "_" for character in '<>:"/\\|?*'})
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ScreenshotError(FileServiceError):
    pass


class FrameUnavailable(ScreenshotError):
    """A valid seek produced no frame; an earlier candidate may still succeed."""


class ScreenshotNotFoundError(ScreenshotError):
    pass


class ScreenshotConflictError(ScreenshotError):
    def __init__(self, message: str, conflicts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.conflicts = conflicts


def _timestamp_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def parse_timestamp(value: str) -> float:
    text = str(value or "").strip()
    match = _TIMESTAMP.fullmatch(text)
    if not match:
        raise ScreenshotError("时间格式应为 HH:MM:SS 或 HH:MM:SS.mmm")
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes"))
    seconds = float(match.group("seconds"))
    if minutes >= 60 or seconds >= 60:
        raise ScreenshotError("时间格式应为 HH:MM:SS 或 HH:MM:SS.mmm")
    return hours * 3600 + minutes * 60 + seconds


def parse_timestamps(values: Any) -> list[float]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_values: list[Any] = re.split(r"[\r\n,]+", values)
    elif isinstance(values, (list, tuple)):
        raw_values = list(values)
    else:
        raise ScreenshotError("截图时间点必须是字符串列表")

    parsed: list[float] = []
    seen: set[float] = set()
    for raw in raw_values:
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            value = float(raw)
            if not math.isfinite(value) or value < 0:
                raise ScreenshotError("截图时间点必须是有限的非负数字")
            label = format_timestamp(value)
            value = round(value, 6)
        else:
            label = str(raw or "").strip()
            if not label:
                continue
            value = round(parse_timestamp(label), 6)
        if value in seen:
            raise ScreenshotError(f"截图时间点重复：{label}")
        seen.add(value)
        parsed.append(value)
    if len(parsed) > MAX_ITEMS_PER_VIDEO:
        raise ScreenshotError(f"每个视频最多指定 {MAX_ITEMS_PER_VIDEO} 个截图时间点")
    return sorted(parsed)


def sample_random_times(
    duration: float,
    skip_start: float,
    skip_end: float,
    total: int,
    rng: random.Random | random.SystemRandom | None = None,
) -> list[float]:
    effective = duration - skip_start - skip_end
    if not math.isfinite(effective) or effective < 1:
        raise ScreenshotError("跳过片头片尾后有效时长不足 1 秒")
    if total < 1 or total > MAX_ITEMS_PER_VIDEO:
        raise ScreenshotError(f"每个视频的截图数量必须在 1 到 {MAX_ITEMS_PER_VIDEO} 之间")
    random_source = rng or random.SystemRandom()
    segment = effective / total
    times: list[float] = []
    seen: set[float] = set()
    for index in range(total):
        low = skip_start + index * segment
        high = low + segment
        margin = min(segment * 0.2, 0.05)
        start = low + margin
        end = high - margin
        candidate = random_source.uniform(start, end) if end > start else low
        candidate = round(candidate, 6)
        attempts = 0
        while candidate in seen and attempts < 1000:
            attempts += 1
            step = min(segment / 1000, 0.001)
            candidate = round(candidate + step, 6)
            if candidate >= high:
                candidate = round(low + step * (attempts % 100), 6)
        if candidate in seen:
            raise ScreenshotError("有效时长过短，无法生成互不重复的截图时间")
        seen.add(candidate)
        times.append(candidate)
    return sorted(times)


def _bounded_integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ScreenshotError(f"{label}无效")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ScreenshotError(f"{label}无效") from exc
    if not math.isfinite(number) or not number.is_integer():
        raise ScreenshotError(f"{label}必须是整数")
    parsed = int(number)
    if parsed < minimum or parsed > maximum:
        raise ScreenshotError(f"{label}必须在 {minimum} 到 {maximum} 之间")
    return parsed


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ScreenshotError(f"{label}无效")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ScreenshotError(f"{label}无效") from exc
    if not math.isfinite(parsed):
        raise ScreenshotError(f"{label}无效")
    return parsed


def _fingerprint(path: Path) -> str:
    info = path.stat()
    if path.is_dir():
        entries = []
        for child in sorted(path.rglob("*")):
            if child.is_symlink():
                raise ScreenshotError("光盘目录包含符号链接")
            stat_info = child.stat()
            entries.append(f"{child.relative_to(path)}:{stat_info.st_size}:{stat_info.st_mtime_ns}")
        return hashlib.sha256("\n".join(entries).encode()).hexdigest()
    return hashlib.sha256(
        f"{info.st_mode}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}".encode()
    ).hexdigest()


def _safe_stem(name: str, *, suffix: str = "") -> str:
    stem = Path(name).stem
    cleaned = stem.translate(_INVALID_FILENAME_CHARS)
    cleaned = "".join("_" if ord(character) < 32 else character for character in cleaned).strip(
        " ."
    )
    if not cleaned:
        cleaned = "video"
    cleaned = cleaned[:120].rstrip(" .") or "video"
    if cleaned.split(".", 1)[0].upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return f"{cleaned}{suffix}"


def _join_relative(*parts: str) -> str:
    return "/".join(part.strip("/") for part in parts if part and part.strip("/"))


def _upload_placeholder() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "url": None,
        "thumbnail_url": None,
        "error": "图床尚未配置",
        "provider": None,
        "uploaded_at": None,
    }


class ScreenshotService:
    def __init__(
        self,
        settings: Settings,
        files: FileService,
        *,
        rng: random.Random | random.SystemRandom | None = None,
    ) -> None:
        self.settings = settings
        self.files = files
        self.sessions_dir = settings.data_dir / "screenshot-sessions"
        self.rng = rng

    def status(self) -> dict[str, Any]:
        ffprobe = probe_executable(self.settings)
        ffmpeg = self._resolve_executable(self.settings.ffmpeg_command)
        font = find_cjk_font()
        return {
            "available": bool(ffprobe and ffmpeg and font),
            "ffprobe": ffprobe,
            "ffmpeg": ffmpeg,
            "font": str(font) if font else None,
            "limits": {
                "sources": MAX_SOURCES,
                "items_per_video": MAX_ITEMS_PER_VIDEO,
                "total_items": MAX_TOTAL_ITEMS,
                "columns": MAX_COLUMNS,
                "rows": MAX_ROWS,
                "width": [0, MAX_WIDTH],
                "collage_width": [MIN_COLLAGE_WIDTH, MAX_COLLAGE_WIDTH],
                "gap": [MIN_GAP, MAX_GAP],
            },
        }

    def list_sessions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        if not self.sessions_dir.is_dir():
            return sessions
        for path in self.sessions_dir.glob("*.json"):
            try:
                manifest = self._read_manifest_path(path)
            except ScreenshotError:
                continue
            sessions.append(self._session_summary(manifest))
        sessions.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return sessions[: min(max(limit, 1), 200)]

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self._public_session(self._load_manifest(session_id))

    def prepare_create(self, data: Mapping[str, Any]) -> dict[str, Any]:
        workflow = data.get("output_mode")
        if workflow not in {None, "single", "collage"}:
            raise ScreenshotError("缩略图输出模式无效")
        explicit_total = data.get("total") is not None
        workflow_options = (
            defaults(
                getattr(self.files, "database", None),
                self.settings,
                {},
                {
                    "layout_mode": "collage_default_layout_mode",
                    "columns": "collage_default_columns",
                    "rows": "collage_default_rows",
                    "thumbnail_width": "collage_default_width",
                    "gap": "collage_default_gap",
                    "output_relative_path": "collage_output_relative_path",
                    "timestamps": "screenshot_default_timestamps",
                },
            )
            if workflow
            else {}
        )
        if workflow:
            for key in ("columns", "rows", "thumbnail_width"):
                if data.get(key) is not None:
                    workflow_options[key] = data[key]
            if data.get("columns") is not None or data.get("rows") is not None:
                workflow_options["layout_mode"] = "grid"
        status = self.status()
        if not status["available"]:
            missing = []
            if not status["ffmpeg"]:
                missing.append("ffmpeg")
            if not status["ffprobe"]:
                missing.append("ffprobe")
            if not status["font"]:
                missing.append("中文字体")
            raise ScreenshotError(f"视频截图依赖不可用：{'、'.join(missing)}")

        data = defaults(
            getattr(self.files, "database", None),
            self.settings,
            dict(data),
            {
                "total": "collage_default_total",
                "width": "screenshot_default_width",
                "content_type": "collage_default_content_type",
                "format": "screenshot_default_format",
                "style": "screenshot_default_style",
                "hdr_processor": "screenshot_hdr_processor",
                "sampling_mode": "screenshot_default_sampling",
                "auto_upload": "screenshot_auto_upload",
                "upload_variant": "screenshot_upload_variant",
                "output_relative_path": "screenshot_output_relative_path",
            },
        )
        if data["content_type"] == "custom":
            data = defaults(
                getattr(self.files, "database", None),
                self.settings,
                data,
                {
                    "skip_start_seconds": "collage_custom_skip_start",
                    "skip_end_seconds": "collage_custom_skip_end",
                },
            )
        if data["format"] not in {"png", "jpg"} or data["style"] not in {"script", "plain"}:
            raise ScreenshotError("截图格式或样式无效")
        if data["hdr_processor"] not in {"auto", "libplacebo", "zscale"}:
            raise ScreenshotError("色彩转换选项无效")
        sources = self._parse_sources(data.get("sources"))
        if not workflow and data.get("sampling_mode") == "timestamps":
            data["sampling_mode"] = "random"
        if workflow and data.get("sampling_mode") == "timestamps" and not data.get("timestamps"):
            data["timestamps"] = workflow_options.get("timestamps")
            if not data["timestamps"]:
                raise ScreenshotError("请先在设置中填写默认截图时间点")
        if (
            workflow == "collage"
            and not explicit_total
            and workflow_options["layout_mode"] == "grid"
        ):
            data["total"] = int(workflow_options["columns"]) * int(workflow_options["rows"])
        timestamps = parse_timestamps(data.get("timestamps"))
        if timestamps:
            total = len(timestamps)
            sampling_mode = "timestamps"
        else:
            total = _bounded_integer(
                data.get("total") if data.get("total") is not None else 20,
                "截图数量",
                1,
                MAX_ITEMS_PER_VIDEO,
            )
            sampling_mode = str(data.get("sampling_mode") or "random")
            if sampling_mode not in {"random", "uniform"}:
                raise ScreenshotError("截图取样方式无效")
        if workflow == "collage" and any(
            data.get(key) is not None for key in ("columns", "rows", "thumbnail_width")
        ):
            layout = self._parse_layout([{"items": [None] * total}], workflow_options)
            self._validate_canvas(layout)
        if len(sources) * total > MAX_TOTAL_ITEMS:
            raise ScreenshotError(
                f"单次任务最多生成 {MAX_TOTAL_ITEMS} 张截图，请减少视频或截图数量"
            )
        width = _bounded_integer(
            data.get("width") if data.get("width") is not None else 1920,
            "截图宽度",
            0,
            MAX_WIDTH,
        )
        if 0 < width < MIN_WIDTH:
            raise ScreenshotError(f"截图宽度必须为 0 或至少 {MIN_WIDTH} 像素")
        content_type = str(data.get("content_type") or "anime")
        if content_type not in CONTENT_PRESETS:
            raise ScreenshotError("视频内容类型无效")
        preset_start, preset_end = CONTENT_PRESETS[content_type]
        skip_start = _finite_number(
            data.get("skip_start_seconds")
            if data.get("skip_start_seconds") is not None
            else preset_start,
            "跳过片头时长",
        )
        skip_end = _finite_number(
            data.get("skip_end_seconds")
            if data.get("skip_end_seconds") is not None
            else preset_end,
            "跳过片尾时长",
        )
        if not 0 <= skip_start <= 86_400 or not 0 <= skip_end <= 86_400:
            raise ScreenshotError("跳过时长必须在 0 到 86400 秒之间")
        range_options = {}
        if data.get("start_percent") is not None or data.get("end_percent") is not None:
            start_percent = _finite_number(data.get("start_percent"), "开始百分比")
            end_percent = _finite_number(data.get("end_percent"), "结束百分比")
            if not 0 <= start_percent < end_percent <= 100:
                raise ScreenshotError("百分比范围需要满足 0 ≤ 开始 < 结束 ≤ 100")
            if data.get("timestamps"):
                raise ScreenshotError("百分比范围不能与指定时间点同时使用")
            range_options = {"start_percent": start_percent, "end_percent": end_percent}
        output_relative_path = normalize_relative_path(str(data.get("output_relative_path") or ""))
        output_dir = self._resolve_images_directory(output_relative_path)
        collage_base = (
            self._resolve_images_directory(workflow_options["output_relative_path"])
            if workflow == "collage"
            else None
        )

        session_id = uuid.uuid4().hex
        batch_name = (
            "screenshots-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + session_id[:8]
        )
        batch_relative = _join_relative(output_relative_path, batch_name)
        if workflow:
            workflow_options.pop("timestamps", None)
            workflow_options["output_relative_path"] = _join_relative(
                workflow_options["output_relative_path"], batch_name
            )
        batch_dir = output_dir / batch_name
        try:
            batch_dir.mkdir(mode=0o750)
            if collage_base is not None:
                (collage_base / batch_name).mkdir(mode=0o750, exist_ok=True)
                workflow_options["format"] = data["format"]
        except OSError as exc:
            if batch_dir.is_dir() and not any(batch_dir.iterdir()):
                batch_dir.rmdir()
            raise ScreenshotError(f"无法创建截图目录：{exc}") from exc

        videos: list[dict[str, Any]] = []
        for source in sources:
            videos.append(
                {
                    "id": uuid.uuid4().hex,
                    "source": {
                        **source.payload(),
                        "disc_selection": next(
                            (
                                raw.get("disc_selection")
                                for raw in data["sources"]
                                if raw["relative_path"] == source.relative_path
                                and raw["root_key"] == source.root_key
                            ),
                            None,
                        ),
                    },
                    "status": "pending",
                    "error": "",
                    "output_relative_path": _join_relative(
                        batch_relative,
                        _safe_stem(source.name, suffix=f"-{source.video_short_id}"),
                    ),
                    "media": {},
                    "items": [],
                }
            )
        manifest = {
            "version": 1,
            "id": session_id,
            "created_at": _timestamp_now(),
            "updated_at": _timestamp_now(),
            "status": "pending",
            "task_id": None,
            "output_root_key": "images",
            "output_relative_path": batch_relative,
            "params": {
                "output_mode": workflow,
                "collage_options": workflow_options,
                "format": data["format"],
                "style": data["style"],
                "hdr_processor": data["hdr_processor"],
                "auto_upload": data["auto_upload"],
                "upload_variant": data["upload_variant"],
                "upload_provider": getattr(self.files, "database", None)
                .get_settings()
                .get("image_host_provider", "catbox")
                if getattr(self.files, "database", None)
                else "catbox",
                "total": total,
                "width": width,
                "content_type": content_type,
                **range_options,
                "skip_start_seconds": round(skip_start, 3),
                "skip_end_seconds": round(skip_end, 3),
                "sampling_mode": sampling_mode,
                "timestamps": [round(value, 6) for value in timestamps],
            },
            "videos": videos,
        }
        try:
            self._write_manifest(manifest)
        except Exception:
            shutil.rmtree(batch_dir, ignore_errors=True)
            raise
        return {
            "session_id": session_id,
            "output_relative_path": batch_relative,
            "task_input": {
                "session_id": session_id,
                "total": total,
                "width": width,
                "content_type": content_type,
                **range_options,
                "skip_start_seconds": round(skip_start, 3),
                "skip_end_seconds": round(skip_end, 3),
                "sampling_mode": sampling_mode,
                "timestamps": [round(value, 6) for value in timestamps],
            },
        }

    def finish_output(self, session_id, *, task_id, log, should_cancel, progress=None):
        """Produce final montages durably; a retry keeps already committed outputs."""
        manifest = self._load_manifest(session_id)
        if manifest.get("params", {}).get("output_mode") != "collage":
            return None
        outputs = dict(manifest.get("final_outputs") or {})
        videos = manifest.get("videos") or []
        for video in videos:
            if should_cancel():
                raise OperationCancelled
            video_id = str(video["id"])
            previous = outputs.get(video_id)
            render_input = [
                (item["id"], item.get("raw_fingerprint") or item.get("fingerprint"))
                for item in video.get("items", [])
                if item.get("status") == "completed"
            ]
            signature = hashlib.sha256(json.dumps(render_input).encode()).hexdigest()
            owned_output = False
            if previous and previous.get("status") == "completed":
                target, _ = self.files.resolve(
                    "images", previous["output_relative_path"], must_exist=False
                )
                owned_output = target.is_file() and previous.get("fingerprint") == _fingerprint(
                    target
                )
                if owned_output and previous.get("render_input") == signature:
                    continue
            payload = {
                **manifest["params"]["collage_options"],
                "overwrite": owned_output,
                "selections": [
                    {
                        "video_id": video_id,
                        "item_ids": [
                            str(item["id"])
                            for item in video.get("items", [])
                            if item.get("status") == "completed"
                        ],
                    }
                ],
            }
            if progress:
                progress(
                    {
                        "stage": "collage",
                        "current": video["source"]["name"],
                        "total": len(videos),
                        "succeeded": sum(x.get("status") == "completed" for x in outputs.values()),
                    }
                )
            try:
                prepared = self.prepare_collage(session_id, payload)
                result = self.run_collage(
                    prepared, task_id=task_id, log=log, should_cancel=should_cancel
                )
                outputs[video_id] = result["items"][0]
                if outputs[video_id]["status"] == "completed":
                    target, _ = self.files.resolve(
                        "images", outputs[video_id]["output_relative_path"], must_exist=True
                    )
                    outputs[video_id].update(
                        render_input=signature, fingerprint=_fingerprint(target)
                    )
            except OperationCancelled:
                raise
            except Exception as exc:
                outputs[video_id] = {
                    "video_id": video_id,
                    "status": "failed",
                    "error": str(exc),
                    "source_name": video["source"]["name"],
                }
                log(f"拼图失败：{exc}")
            manifest = self._load_manifest(session_id)
            manifest["final_outputs"] = outputs
            self._write_manifest(manifest)
        succeeded = sum(item.get("status") == "completed" for item in outputs.values())
        result = {
            "stage": "collage",
            "session_id": session_id,
            "items": list(outputs.values()),
            "total": len(videos),
            "succeeded": succeeded,
            "failed": len(videos) - succeeded,
            "status": "completed"
            if succeeded == len(videos)
            else "partial"
            if succeeded
            else "failed",
        }
        manifest["collage_result"] = result
        self._write_manifest(manifest)
        return result

    def discard_session(self, session_id: str) -> None:
        try:
            manifest = self._load_manifest(session_id)
        except ScreenshotError:
            return
        path = self._session_file(session_id)
        path.unlink(missing_ok=True)
        batch, _ = self.files.resolve(
            "images",
            str(manifest.get("output_relative_path") or ""),
            scope="browse",
            must_exist=False,
            allow_empty=False,
        )
        try:
            if batch.is_dir() and not any(batch.iterdir()):
                batch.rmdir()
        except OSError:
            pass

    def attach_task(self, session_id: str, task_id: int) -> None:
        manifest = self._load_manifest(session_id)
        manifest["task_id"] = int(task_id)
        manifest["status"] = "queued"
        manifest["updated_at"] = _timestamp_now()
        self._write_manifest(manifest)

    def attach_collage_task(self, session_id: str, task_id: int) -> None:
        manifest = self._load_manifest(session_id)
        manifest["collage_task_id"] = int(task_id)
        self._write_manifest(manifest)

    def run(
        self,
        data: Mapping[str, Any],
        *,
        task_id: int,
        log: Callable[[str], None],
        should_cancel: Callable[[], bool],
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        session_id = str(data.get("session_id") or "")
        manifest = self._load_manifest(session_id)
        if manifest.get("task_id") not in {None, task_id}:
            raise ScreenshotError("截图会话已绑定其他任务")
        manifest["task_id"] = task_id
        manifest["status"] = "running"
        manifest["updated_at"] = _timestamp_now()
        self._write_manifest(manifest)

        params = manifest.get("params") or {}
        width = int(params.get("width") or 0)
        total = int(params.get("total") or 0)
        skip_start = float(params.get("skip_start_seconds") or 0)
        skip_end = float(params.get("skip_end_seconds") or 0)
        timestamps = parse_timestamps(params.get("timestamps"))
        ffmpeg_path = self._resolve_executable(self.settings.ffmpeg_command)
        if not ffmpeg_path or not probe_executable(self.settings):
            raise ScreenshotError("视频截图依赖不可用")
        ffmpeg = self.settings.ffmpeg_command

        temporary_root = Path(
            tempfile.mkdtemp(prefix=f"screenshot-{task_id}-", dir=self.settings.runtime_dir)
        )
        video_successes = 0
        video_failures = 0
        image_successes = 0
        image_failures = 0
        try:
            for video_index, video in enumerate(manifest.get("videos") or [], start=1):
                if should_cancel():
                    raise OperationCancelled
                source = video.get("source") or {}
                source_name = str(source.get("name") or "视频")
                video["status"] = "running"
                video["error"] = ""
                manifest["updated_at"] = _timestamp_now()
                self._write_manifest(manifest)
                self._emit_progress(
                    progress,
                    manifest,
                    current=source_name,
                    video_index=video_index,
                    image_successes=image_successes,
                    image_failures=image_failures,
                )
                video_image_successes = 0
                video_image_failures = 0
                try:
                    source_path, _ = self.files.resolve(
                        str(source.get("root_key") or ""),
                        str(source.get("relative_path") or ""),
                        scope="browse",
                        must_exist=True,
                        allow_empty=False,
                    )
                    if source_path.is_symlink() or (
                        not source_path.is_file() and not disc_root(source_path)
                    ):
                        raise ScreenshotError("源视频已变化或不可访问")
                    current_fingerprint = _fingerprint(source_path)
                    if current_fingerprint != source.get("fingerprint"):
                        raise ScreenshotError("源视频状态已变化，请刷新后重试")
                    log(f"读取媒体信息：{source.get('relative_path')}")
                    optical = optical_input(source_path, source.get("disc_selection"))
                    payload = probe_json_sync(
                        self.settings, optical["probe"], timeout=FFPROBE_TIMEOUT_SECONDS
                    )
                    if optical["kind"] == "dvd":
                        from .media_probe import probe_command
                        from .media_tools import run_tool

                        command = probe_command(self.settings, source_path)
                        command[-1] = optical["input"]
                        scanned = run_tool(
                            command, timeout=FFPROBE_TIMEOUT_SECONDS, should_cancel=should_cancel
                        )
                        if scanned.returncode:
                            raise ScreenshotError("无法探测 DVD 完整标题组")
                        payload = json.loads(scanned.stdout)
                    details = video_details_from_payload(payload)
                    duration = optical.get("duration_seconds") or details["duration_seconds"]
                    if duration is None:
                        raise ScreenshotError("无法获取视频时长")
                    if params.get("start_percent") is not None:
                        # Resolve independently for every video, using its actual duration.
                        skip_start = float(duration) * float(params["start_percent"]) / 100
                        skip_end = float(duration) * (100 - float(params["end_percent"])) / 100
                    video_stream = self._video_stream(payload)
                    media = {
                        "filename": source_name,
                        "size_bytes": int(source.get("size_bytes") or 0),
                        "width": details["width"],
                        "height": details["height"],
                        "aspect_ratio": details["aspect_ratio"],
                        "fps": details["fps"],
                        "video_codec": details["video_codec"],
                        "audio_codec": details["audio_codec"],
                        "duration_seconds": float(duration),
                        "sample_aspect_ratio": str(video_stream.get("sample_aspect_ratio") or ""),
                    }
                    media.update(color_metadata(payload))
                    media["input"] = optical["input"]
                    media["input_args"] = optical["input_args"]
                    color_chain, actual_processor = conversion_filter(
                        media,
                        params.get("hdr_processor", "auto"),
                        rendering_capabilities(tuple(ffmpeg))
                        if media.get("dv_profile")
                        or media.get("color_primaries") == "bt2020"
                        or media.get("color_transfer") in {"smpte2084", "arib-std-b67"}
                        else {},
                    )
                    media["color_requested"] = params.get("hdr_processor", "auto")
                    media["color_filter"] = color_chain
                    media["color_processor"] = actual_processor
                    video["media"] = media
                    log(f"色彩转换：{actual_processor}")
                    if timestamps:
                        invalid = [value for value in timestamps if value > float(duration) - 0.05]
                        if invalid:
                            formatted = "、".join(format_timestamp(value) for value in invalid[:5])
                            raise ScreenshotError(f"指定截图时间点超出视频时长：{formatted}")
                        times = timestamps
                    else:
                        times = sample_random_times(
                            float(duration),
                            skip_start,
                            skip_end,
                            total,
                            self.rng,
                        )
                    if not timestamps and params.get("sampling_mode") == "uniform":
                        effective = float(duration) - skip_start - skip_end
                        if effective <= 0:
                            raise ScreenshotError("跳过片头片尾后没有有效时长")
                        times = [skip_start + effective * (i + 0.5) / total for i in range(total)]
                    if video.get("planned_times"):
                        times = video["planned_times"]
                    else:
                        video["planned_times"] = times
                        self._write_manifest(manifest)
                    video_dir_relative = str(video.get("output_relative_path") or "")
                    video_dir, _ = self.files.resolve(
                        "images",
                        video_dir_relative,
                        scope="browse",
                        must_exist=False,
                        allow_empty=False,
                    )
                    try:
                        video_dir.mkdir(parents=True, exist_ok=True)
                    except OSError as exc:
                        raise ScreenshotError(f"无法创建截图目录：{exc}") from exc
                    previous_items = list(video.get("items") or [])
                    video["items"] = []
                    for item_index, target_time in enumerate(times, start=1):
                        if should_cancel():
                            raise OperationCancelled
                        previous = next(
                            (
                                entry
                                for entry in previous_items
                                if entry.get("index") == item_index - 1
                                and entry.get("status") == "completed"
                            ),
                            None,
                        )
                        if previous:
                            self._verified_item_path(previous)
                            video["items"].append(previous)
                            image_successes += 1
                            video_image_successes += 1
                            continue
                        item_id = uuid.uuid4().hex
                        item_relative = _join_relative(
                            video_dir_relative,
                            self._screenshot_filename(target_time, item_index).rsplit(".", 1)[0]
                            + "."
                            + params.get("format", "jpg"),
                        )
                        item = {
                            "id": item_id,
                            "index": item_index - 1,
                            "status": "pending",
                            "target_timestamp_seconds": round(target_time, 6),
                            "actual_timestamp_seconds": round(target_time, 6),
                            "timestamp": format_timestamp(target_time),
                            "relative_path": item_relative,
                            "size_bytes": 0,
                            "fingerprint": "",
                            "error": "",
                            "last_error": "",
                            "created_at": _timestamp_now(),
                            "upload": _upload_placeholder(),
                        }
                        try:
                            frame_path = temporary_root / f"{video['id']}-{item_index:04d}.png"
                            actual_time, extracted = self._extract_frame_with_retries(
                                ffmpeg,
                                source_path,
                                target_time,
                                float(duration),
                                skip_start,
                                skip_end,
                                frame_path,
                                width,
                                media,
                                should_cancel,
                            )
                            target_path, _ = self.files.resolve(
                                "images",
                                item_relative,
                                scope="browse",
                                must_exist=False,
                                allow_empty=False,
                            )
                            size_bytes, fingerprint, raw_path = self._save_variants(
                                extracted, target_path, actual_time, params
                            )
                            item["raw_relative_path"] = _join_relative(
                                video_dir_relative, raw_path.name
                            )
                            item["raw_fingerprint"] = _fingerprint(raw_path)
                            item.update(
                                {
                                    "status": "completed",
                                    "actual_timestamp_seconds": round(actual_time, 6),
                                    "timestamp": format_timestamp(actual_time),
                                    "size_bytes": size_bytes,
                                    "fingerprint": fingerprint,
                                    "error": "",
                                }
                            )
                            extracted.unlink(missing_ok=True)
                            video_image_successes += 1
                            image_successes += 1
                            log(f"截图 {item_index}/{len(times)}：{format_timestamp(actual_time)}")
                        except OperationCancelled:
                            raise
                        except Exception as exc:
                            message = str(exc) or exc.__class__.__name__
                            item.update({"status": "failed", "error": message})
                            video_image_failures += 1
                            image_failures += 1
                            log(f"截图失败 {item_index}/{len(times)}：{message}")
                        video["items"].append(item)
                        manifest["updated_at"] = _timestamp_now()
                        self._write_manifest(manifest)
                        self._emit_progress(
                            progress,
                            manifest,
                            current=source_name,
                            video_index=video_index,
                            image_successes=image_successes,
                            image_failures=image_failures,
                        )
                    if video_image_successes and video_image_failures:
                        video["status"] = "partial"
                    elif video_image_successes:
                        video["status"] = "completed"
                    else:
                        video["status"] = "failed"
                        video["error"] = "所有截图均提取失败"
                    if video_image_successes:
                        video_successes += 1
                    else:
                        video_failures += 1
                except OperationCancelled:
                    raise
                except Exception as exc:
                    message = str(exc) or exc.__class__.__name__
                    video["status"] = "failed"
                    video["error"] = message
                    video_failures += 1
                    log(f"视频截图失败：{source.get('relative_path')}：{message}")
                manifest["updated_at"] = _timestamp_now()
                self._write_manifest(manifest)
                self._emit_progress(
                    progress,
                    manifest,
                    current="",
                    video_index=video_index,
                    image_successes=image_successes,
                    image_failures=image_failures,
                )

            if image_successes == 0:
                manifest["status"] = "failed"
            elif image_failures or video_failures:
                manifest["status"] = "partial"
            else:
                manifest["status"] = "completed"
            manifest["updated_at"] = _timestamp_now()
            self._write_manifest(manifest)
            return {
                "status": manifest["status"],
                "session_id": session_id,
                "total": len(manifest.get("videos") or []),
                "succeeded": video_successes,
                "failed": video_failures,
                "image_total": sum(
                    len(video.get("items") or []) or int(manifest["params"]["total"])
                    for video in manifest.get("videos", [])
                ),
                "image_succeeded": image_successes,
                "image_failed": image_failures,
                "output_relative_path": manifest.get("output_relative_path"),
            }
        except OperationCancelled:
            manifest["status"] = "cancelled"
            manifest["updated_at"] = _timestamp_now()
            self._write_manifest(manifest)
            raise
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    def recapture(
        self,
        session_id: str,
        item_id: str,
        *,
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        manifest = self._load_manifest(session_id)
        video, item = self._find_item(manifest, item_id)
        media = video.get("media") or {}
        duration = float(media.get("duration_seconds") or 0)
        if duration <= 0:
            raise ScreenshotError("会话缺少有效的视频时长，请重新生成截图")
        target_time = (
            parse_timestamp(timestamp)
            if timestamp and str(timestamp).strip()
            else float(
                item.get("actual_timestamp_seconds") or item.get("target_timestamp_seconds") or 0
            )
        )
        if target_time < 0 or target_time > duration - 0.05:
            raise ScreenshotError("截图时间超出视频有效时长")
        source = video.get("source") or {}
        source_path, _ = self.files.resolve(
            str(source.get("root_key") or ""),
            str(source.get("relative_path") or ""),
            scope="browse",
            must_exist=True,
            allow_empty=False,
        )
        if source_path.is_symlink() or (not source_path.is_file() and not disc_root(source_path)):
            raise ScreenshotError("源视频已变化或不可访问")
        if _fingerprint(source_path) != source.get("fingerprint"):
            raise ScreenshotError("源视频状态已变化，请打开新的截图会话")
        target, _ = self.files.resolve(
            "images",
            str(item.get("relative_path") or ""),
            scope="browse",
            must_exist=False,
            allow_empty=False,
        )
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ScreenshotError("截图目标不是普通文件")
        target.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg_path = self._resolve_executable(self.settings.ffmpeg_command)
        if not ffmpeg_path:
            raise ScreenshotError("ffmpeg 不可用")
        ffmpeg = self.settings.ffmpeg_command
        width = int((manifest.get("params") or {}).get("width") or 0)
        temporary_root = Path(
            tempfile.mkdtemp(prefix="screenshot-recapture-", dir=self.settings.runtime_dir)
        )
        try:
            frame_path = temporary_root / "recapture.png"
            try:
                actual_time, extracted = self._extract_frame_with_retries(
                    ffmpeg,
                    source_path,
                    target_time,
                    duration,
                    0,
                    0,
                    frame_path,
                    width,
                    media,
                    lambda: False,
                    lower=0.0,
                    upper=max(0.0, duration - 0.05),
                )
                size_bytes, fingerprint, raw_path = self._save_variants(
                    extracted, target, actual_time, manifest.get("params") or {}, replace=True
                )
                item["raw_relative_path"] = str(
                    Path(item["relative_path"]).with_name(raw_path.name)
                )
                item["raw_fingerprint"] = _fingerprint(raw_path)
            except Exception as exc:
                item["last_error"] = str(exc) or exc.__class__.__name__
                manifest["updated_at"] = _timestamp_now()
                self._write_manifest(manifest)
                raise
            item.update(
                {
                    "status": "completed",
                    "target_timestamp_seconds": round(target_time, 6),
                    "actual_timestamp_seconds": round(actual_time, 6),
                    "timestamp": format_timestamp(actual_time),
                    "size_bytes": size_bytes,
                    "fingerprint": fingerprint,
                    "error": "",
                    "last_error": "",
                }
            )
            video["status"] = (
                "completed"
                if all(entry.get("status") == "completed" for entry in video.get("items") or [])
                else video.get("status")
            )
            manifest["updated_at"] = _timestamp_now()
            self._write_manifest(manifest)
            return self._public_session(manifest)
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    def delete_item(self, session_id: str, item_id: str) -> dict[str, Any]:
        manifest = self._load_manifest(session_id)
        video, item = self._find_item(manifest, item_id)
        target, _ = self.files.resolve(
            "images",
            str(item.get("relative_path") or ""),
            scope="browse",
            must_exist=False,
            allow_empty=False,
        )
        if target.is_symlink():
            raise ScreenshotError("不允许删除符号链接")
        if target.exists() and not target.is_file():
            raise ScreenshotError("截图目标不是普通文件")
        if target.exists():
            try:
                if item.get("raw_relative_path"):
                    self._verified_item_path(item, raw=True).unlink(missing_ok=True)
                target.unlink()
            except OSError as exc:
                raise ScreenshotError(f"无法删除截图：{exc}") from exc
        video["items"] = [entry for entry in video.get("items") or [] if entry.get("id") != item_id]
        for index, entry in enumerate(video["items"]):
            entry["index"] = index
        if not video["items"]:
            video["status"] = "pending" if video.get("status") == "failed" else video.get("status")
        manifest["updated_at"] = _timestamp_now()
        self._write_manifest(manifest)
        return self._public_session(manifest)

    def reorder_items(self, session_id: str, orders: Mapping[str, list[str]]) -> dict[str, Any]:
        manifest = self._load_manifest(session_id)
        videos = {str(video.get("id")): video for video in manifest.get("videos") or []}
        for video_id, item_ids in orders.items():
            video = videos.get(str(video_id))
            if video is None:
                raise ScreenshotError("截图排序包含未知视频")
            if not isinstance(item_ids, list):
                raise ScreenshotError("截图顺序无效")
            if len(item_ids) != len(set(item_ids)):
                raise ScreenshotError("截图顺序包含重复项目")
            existing = [str(item.get("id")) for item in video.get("items") or []]
            if set(item_ids) != set(existing) or len(item_ids) != len(existing):
                raise ScreenshotError("截图顺序必须包含该视频的全部截图")
            by_id = {str(item.get("id")): item for item in video.get("items") or []}
            video["items"] = [by_id[str(item_id)] for item_id in item_ids]
            for index, item in enumerate(video["items"]):
                item["index"] = index
        manifest["updated_at"] = _timestamp_now()
        self._write_manifest(manifest)
        return self._public_session(manifest)

    def prepare_collage(self, session_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        data = defaults(
            getattr(self.files, "database", None),
            self.settings,
            dict(data),
            {
                "layout_mode": "collage_default_layout_mode",
                "columns": "collage_default_columns",
                "rows": "collage_default_rows",
                "thumbnail_width": "collage_default_width",
                "gap": "collage_default_gap",
                "output_relative_path": "collage_output_relative_path",
            },
        )
        manifest = self._load_manifest(session_id)
        selections = self._parse_selections(manifest, data.get("selections"))
        layout = self._parse_layout(selections, data)
        output_relative_path = normalize_relative_path(str(data.get("output_relative_path") or ""))
        output_dir = self._resolve_images_directory(output_relative_path)
        outputs: list[dict[str, Any]] = []
        for index, selection in enumerate(selections):
            video = selection["video"]
            source = video.get("source") or {}
            output_name = output_name_for(str(source.get("name") or "video.mp4"))
            if data.get("format") == "png":
                output_name = str(Path(output_name).with_suffix(".png"))
            outputs.append(
                {
                    "index": index,
                    "video_id": str(video.get("id")),
                    "source_name": str(source.get("name") or "视频"),
                    "output_root_key": "images",
                    "output_name": output_name,
                    "output_relative_path": _join_relative(output_relative_path, output_name),
                }
            )
        self._ensure_batch_names_unique(outputs)
        conflicts = self._target_conflicts(output_dir, outputs)
        overwrite = bool(data.get("overwrite"))
        if any(item.get("hard") for item in conflicts) or (conflicts and not overwrite):
            raise ScreenshotConflictError("拼图输出文件存在冲突", conflicts)
        return {
            "session_id": session_id,
            "selections": [
                {
                    "video_id": item["video_id"],
                    "item_ids": item["item_ids"],
                }
                for item in selections
            ],
            "format": data.get("format", "jpg"),
            **layout,
            "output_relative_path": output_relative_path,
            "overwrite": overwrite,
            "outputs": outputs,
            "conflicts": conflicts,
        }

    def run_collage(
        self,
        data: Mapping[str, Any],
        *,
        task_id: int,
        log: Callable[[str], None],
        should_cancel: Callable[[], bool],
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        session_id = str(data.get("session_id") or "")
        manifest = self._load_manifest(session_id)
        selections = self._parse_selections(manifest, data.get("selections"))
        layout = self._parse_layout(selections, data)
        output_relative_path = normalize_relative_path(str(data.get("output_relative_path") or ""))
        output_dir = self._resolve_images_directory(output_relative_path)
        outputs = []
        for index, selection in enumerate(selections):
            source = selection["video"].get("source") or {}
            outputs.append(
                {
                    "index": index,
                    "video_id": str(selection["video"].get("id")),
                    "source_name": str(source.get("name") or "视频"),
                    "output_root_key": "images",
                    "output_name": output_name_for(str(source.get("name") or "video.mp4")),
                }
            )
            if data.get("format") == "png":
                outputs[-1]["output_name"] = str(
                    Path(outputs[-1]["output_name"]).with_suffix(".png")
                )
            outputs[-1]["output_relative_path"] = _join_relative(
                output_relative_path, outputs[-1]["output_name"]
            )
        self._ensure_batch_names_unique(outputs)
        ffmpeg = self._resolve_executable(self.settings.ffmpeg_command)
        font = find_cjk_font()
        if not ffmpeg or not font:
            raise ScreenshotError("拼图依赖不可用")
        overwrite = bool(data.get("overwrite"))
        temporary_root = Path(
            tempfile.mkdtemp(prefix=f"screenshot-collage-{task_id}-", dir=self.settings.runtime_dir)
        )
        results: list[dict[str, Any]] = []
        succeeded = 0
        failed = 0
        try:
            for index, selection in enumerate(selections):
                if should_cancel():
                    raise OperationCancelled
                video = selection["video"]
                source = video.get("source") or {}
                output = outputs[index]
                if progress:
                    progress(
                        {
                            "status": "running",
                            "total": len(selections),
                            "succeeded": succeeded,
                            "failed": failed,
                            "current": str(source.get("name") or "视频"),
                            "items": results,
                        }
                    )
                try:
                    frames = [
                        CollageFrame(
                            self._verified_item_path(item, raw=True),
                            float(item.get("actual_timestamp_seconds") or 0),
                        )
                        for item in selection["items"]
                    ]
                    media = self._media_info(video.get("media") or {})
                    output_name = str(output["output_name"])
                    target = output_dir / output_name
                    if os.path.lexists(target):
                        if target.is_symlink() or target.is_dir():
                            raise ScreenshotError("目标路径类型不允许覆盖")
                        if not overwrite:
                            raise ScreenshotError("目标文件已存在；请确认覆盖后重试")
                    rendered = temporary_root / (f"collage-{index:04d}" + Path(output_name).suffix)
                    self._validate_canvas(layout)
                    render_info = render_collage(
                        frames,
                        media,
                        columns=int(layout["columns"]),
                        rows=int(layout["rows"]),
                        thumbnail_width=int(layout["thumbnail_width"]),
                        output_path=rendered,
                        font_path=font,
                        gap=int(layout["gap"]),
                    )
                    self._validate_rendered_collage(rendered)
                    self._commit_file(rendered, target, overwrite=overwrite)
                    succeeded += 1
                    results.append(
                        {
                            "index": index,
                            "video_id": str(video.get("id")),
                            "source_name": str(source.get("name") or "视频"),
                            "status": "completed",
                            "output_root_key": "images",
                            "output_name": output_name,
                            "output_relative_path": output["output_relative_path"],
                            "output_size_bytes": target.stat().st_size,
                            "output_width": int(render_info["width"]),
                            "output_height": int(render_info["height"]),
                            "sample_timestamps": render_info["timestamps"],
                            "columns": int(render_info["columns"]),
                            "rows": int(render_info["rows"]),
                            "media": {
                                "width": media.width,
                                "height": media.height,
                                "aspect_ratio": media.aspect_ratio,
                                "fps": media.fps,
                                "video_codec": media.video_codec,
                                "audio_codec": media.audio_codec,
                                "duration_seconds": media.duration_seconds,
                            },
                        }
                    )
                    log(
                        f"已生成拼图：{source.get('relative_path')} → "
                        f"{output['output_relative_path']}"
                    )
                except OperationCancelled:
                    raise
                except Exception as exc:
                    failed += 1
                    message = str(exc) or exc.__class__.__name__
                    results.append(
                        {
                            "index": index,
                            "video_id": str(video.get("id")),
                            "source_name": str(source.get("name") or "视频"),
                            "status": "failed",
                            "error": message,
                        }
                    )
                    log(f"拼图失败：{source.get('relative_path')}：{message}")
            task_status = "completed" if failed == 0 else "partial" if succeeded else "failed"
            result = {
                "status": task_status,
                "session_id": session_id,
                "total": len(selections),
                "succeeded": succeeded,
                "failed": failed,
                **layout,
                "output_relative_path": output_relative_path,
                "items": results,
            }
            manifest = self._load_manifest(session_id)
            manifest["collage_result"] = result
            self._write_manifest(manifest)
            return result
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    def cleanup_stale_temporary_directories(self) -> None:
        cutoff = time.time() - TEMP_MAX_AGE_SECONDS
        for pattern in ("screenshot-*", "screenshot-collage-*", "screenshot-recapture-*"):
            for path in self.settings.runtime_dir.glob(pattern):
                try:
                    if path.is_dir() and path.stat().st_mtime < cutoff:
                        shutil.rmtree(path, ignore_errors=True)
                except OSError:
                    continue

    def _parse_sources(self, raw_sources: Any) -> list[Any]:
        if not isinstance(raw_sources, list) or not raw_sources:
            raise ScreenshotError("请至少选择一个视频")
        if len(raw_sources) > MAX_SOURCES:
            raise ScreenshotError(f"单次最多处理 {MAX_SOURCES} 个视频")
        sources: list[Any] = []
        seen: set[tuple[str, str]] = set()
        for raw in raw_sources:
            if not isinstance(raw, Mapping):
                raise ScreenshotError("视频来源无效")
            root_key = str(raw.get("root_key") or "")
            relative_path = normalize_relative_path(
                str(raw.get("relative_path") or ""), allow_empty=False
            )
            key = (root_key, relative_path)
            if key in seen:
                continue
            seen.add(key)
            path, _ = self.files.resolve(
                root_key,
                relative_path,
                scope="browse",
                must_exist=True,
                allow_empty=False,
            )
            if path.is_symlink() or (not path.is_file() and not disc_root(path)):
                raise ScreenshotError("截图仅支持普通视频或光盘目录")
            if not path.is_dir() and path.suffix.lower() not in VIDEO_EXTENSIONS:
                raise ScreenshotError(f"不支持的文件类型：{path.name}")
            info = path.stat()
            sources.append(
                _Source(
                    root_key=root_key,
                    relative_path=relative_path,
                    name=path.name,
                    size_bytes=int(info.st_size),
                    mtime_ns=int(info.st_mtime_ns),
                    fingerprint=_fingerprint(path),
                    video_short_id=uuid.uuid4().hex[:6],
                )
            )
        if not sources:
            raise ScreenshotError("请至少选择一个视频")
        return sources

    def _resolve_images_directory(self, relative_path: str) -> Path:
        directory, _ = self.files.resolve(
            "images",
            relative_path,
            scope="browse",
            must_exist=True,
            allow_empty=True,
        )
        if directory.is_symlink() or not directory.is_dir():
            raise ScreenshotError("输出目录无效")
        if not os.access(directory, os.R_OK | os.W_OK | os.X_OK):
            raise ScreenshotError("输出目录不可写")
        return directory

    def _parse_layout(
        self, selections: list[dict[str, Any]], data: Mapping[str, Any]
    ) -> dict[str, Any]:
        layout_mode = str(data.get("layout_mode") or "auto")
        if layout_mode == "count":
            layout_mode = "auto"
        elif layout_mode == "grid":
            layout_mode = "custom"
        if layout_mode not in {"auto", "custom"}:
            raise ScreenshotError("拼图布局模式无效")
        columns = _bounded_integer(
            data.get("columns") if data.get("columns") is not None else 5,
            "列数",
            1,
            MAX_COLUMNS,
        )
        counts = [len(item["items"]) for item in selections]
        if not counts or any(count < 1 for count in counts):
            raise ScreenshotError("每个参与拼图的视频至少需要一张截图")
        maximum = max(counts)
        if layout_mode == "auto":
            rows = math.ceil(maximum / columns)
            if rows > MAX_ROWS:
                raise ScreenshotError(f"行数不能超过 {MAX_ROWS}")
        else:
            rows = _bounded_integer(
                data.get("rows") if data.get("rows") is not None else 4,
                "行数",
                1,
                MAX_ROWS,
            )
            if columns * rows < maximum:
                raise ScreenshotError("自定义网格容量不足，无法容纳所有选中截图")
        thumbnail_width = _bounded_integer(
            data.get("thumbnail_width") if data.get("thumbnail_width") is not None else 480,
            "拼图缩略图宽度",
            MIN_COLLAGE_WIDTH,
            MAX_COLLAGE_WIDTH,
        )
        gap = _bounded_integer(
            data.get("gap") if data.get("gap") is not None else 3,
            "缩略图间距",
            MIN_GAP,
            MAX_GAP,
        )
        return {
            "layout_mode": layout_mode,
            "columns": columns,
            "rows": rows,
            "thumbnail_width": thumbnail_width,
            "gap": gap,
        }

    def _parse_selections(self, manifest: Mapping[str, Any], raw: Any) -> list[dict[str, Any]]:
        videos = {str(video.get("id")): video for video in manifest.get("videos") or []}
        if raw is None:
            raw = [
                {
                    "video_id": video_id,
                    "item_ids": [
                        str(item.get("id"))
                        for item in video.get("items") or []
                        if item.get("status") == "completed"
                    ],
                }
                for video_id, video in videos.items()
            ]
        if not isinstance(raw, list) or not raw:
            raise ScreenshotError("请至少选择一个视频的截图")
        selections: list[dict[str, Any]] = []
        seen_videos: set[str] = set()
        for entry in raw:
            if not isinstance(entry, Mapping):
                raise ScreenshotError("截图选择无效")
            video_id = str(entry.get("video_id") or "")
            if video_id in seen_videos:
                raise ScreenshotError("同一个视频不能重复选择")
            seen_videos.add(video_id)
            video = videos.get(video_id)
            if video is None:
                raise ScreenshotError("截图选择包含未知视频")
            raw_ids = entry.get("item_ids") or []
            if not isinstance(raw_ids, list) or not raw_ids:
                raise ScreenshotError("每个视频至少选择一张截图")
            if len(raw_ids) != len(set(map(str, raw_ids))):
                raise ScreenshotError("截图选择包含重复项目")
            by_id = {str(item.get("id")): item for item in video.get("items") or []}
            items = []
            for item_id in raw_ids:
                item = by_id.get(str(item_id))
                if item is None:
                    raise ScreenshotError("截图选择包含不属于该视频的项目")
                if item.get("status") != "completed":
                    raise ScreenshotError("失败或缺失的截图不能参与拼图")
                self._verified_item_path(item)
                items.append(item)
            selections.append(
                {
                    "video": video,
                    "items": items,
                    "video_id": video_id,
                    "item_ids": [str(item["id"]) for item in items],
                }
            )
        return selections

    def _verified_item_path(self, item: Mapping[str, Any], raw=False) -> Path:
        if raw and item.get("raw_relative_path"):
            item = {
                **item,
                "relative_path": item["raw_relative_path"],
                "fingerprint": item.get("raw_fingerprint"),
            }
        path, _ = self.files.resolve(
            "images",
            str(item.get("relative_path") or ""),
            scope="browse",
            must_exist=True,
            allow_empty=False,
        )
        if path.is_symlink() or not path.is_file():
            raise ScreenshotError("截图文件缺失或不是普通文件")
        if item.get("fingerprint") and _fingerprint(path) != item.get("fingerprint"):
            raise ScreenshotError("截图文件已被其他操作修改，请重新加载会话")
        return path

    def _find_item(
        self, manifest: Mapping[str, Any], item_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        for video in manifest.get("videos") or []:
            for item in video.get("items") or []:
                if str(item.get("id")) == str(item_id):
                    return video, item
        raise ScreenshotError("当前会话中找不到该截图")

    def _session_file(self, session_id: str) -> Path:
        if not _SESSION_ID.fullmatch(str(session_id)):
            raise ScreenshotNotFoundError("截图会话不存在")
        return self.sessions_dir / f"{session_id}.json"

    def _load_manifest(self, session_id: str) -> dict[str, Any]:
        path = self._session_file(session_id)
        return self._read_manifest_path(path)

    def _read_manifest_path(self, path: Path) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise ScreenshotNotFoundError("截图会话不存在")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ScreenshotError("截图会话清单损坏") from exc
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ScreenshotError("截图会话版本不受支持")
        if not _SESSION_ID.fullmatch(str(data.get("id") or "")):
            raise ScreenshotError("截图会话标识无效")
        return data

    def _write_manifest(self, manifest: Mapping[str, Any]) -> None:
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        path = self._session_file(str(manifest.get("id") or ""))
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        temporary = self.sessions_dir / f".{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                stat.S_IRUSR | stat.S_IWUSR,
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise ScreenshotError(f"无法保存截图会话：{exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _public_session(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        data = json.loads(json.dumps(manifest, ensure_ascii=False))
        completed = 0
        failed = 0
        missing = 0
        for video in data.get("videos") or []:
            for item in video.get("items") or []:
                item["root_key"] = "images"
                item.setdefault("upload", _upload_placeholder())
                path = None
                try:
                    path, _ = self.files.resolve(
                        "images",
                        str(item.get("relative_path") or ""),
                        scope="browse",
                        must_exist=True,
                        allow_empty=False,
                    )
                except FileServiceError:
                    pass
                available = bool(path and path.is_file() and not path.is_symlink())
                item["available"] = available
                if item.get("status") == "completed" and not available:
                    missing += 1
                elif item.get("status") == "completed":
                    completed += 1
                elif item.get("status") == "failed":
                    failed += 1
        data["counts"] = {
            "videos": len(data.get("videos") or []),
            "completed": completed,
            "failed": failed,
            "missing": missing,
            "total": completed + failed + missing,
        }
        data["output_root_key"] = "images"
        return data

    def _session_summary(self, manifest: Mapping[str, Any]) -> dict[str, Any]:
        images = sum(len(video.get("items") or []) for video in manifest.get("videos") or [])
        failed = sum(
            1
            for video in manifest.get("videos") or []
            for item in video.get("items") or []
            if item.get("status") == "failed"
        )
        return {
            "id": str(manifest.get("id") or ""),
            "created_at": manifest.get("created_at"),
            "updated_at": manifest.get("updated_at"),
            "status": manifest.get("status"),
            "task_id": manifest.get("task_id"),
            "source_count": len(manifest.get("videos") or []),
            "sources": [video.get("source") or {} for video in manifest.get("videos") or []],
            "image_count": images,
            "failed_count": failed,
            "output_root_key": "images",
            "output_relative_path": manifest.get("output_relative_path") or "",
        }

    @staticmethod
    def _screenshot_filename(timestamp: float, index: int) -> str:
        total = max(0, int(math.floor(timestamp + 1e-6)))
        hours, remainder = divmod(total, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}h{minutes:02d}m{seconds:02d}s-{index:04d}.jpg"

    @staticmethod
    def _video_stream(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        streams = payload.get("streams") or []
        return next(
            (stream for stream in streams if stream.get("codec_type") == "video"),
            {},
        )

    @staticmethod
    def _media_info(media: Mapping[str, Any]) -> CollageMediaInfo:
        return CollageMediaInfo(
            filename=str(media.get("filename") or "未知"),
            size_bytes=int(media.get("size_bytes") or 0),
            width=int(media["width"]) if media.get("width") else None,
            height=int(media["height"]) if media.get("height") else None,
            aspect_ratio=str(media.get("aspect_ratio") or ""),
            fps=float(media["fps"]) if media.get("fps") is not None else None,
            video_codec=str(media.get("video_codec") or ""),
            audio_codec=str(media.get("audio_codec") or ""),
            duration_seconds=(
                float(media["duration_seconds"])
                if media.get("duration_seconds") is not None
                else None
            ),
        )

    def _extract_frame_with_retries(
        self,
        ffmpeg: tuple[str, ...],
        source: Path,
        target_time: float,
        duration: float,
        skip_start: float,
        skip_end: float,
        output_path: Path,
        width: int,
        media: Mapping[str, Any],
        should_cancel: Callable[[], bool],
        *,
        lower: float | None = None,
        upper: float | None = None,
    ) -> tuple[float, Path]:
        effective = max(1.0, duration - skip_start - skip_end)
        lower_bound = skip_start + 0.05 if lower is None else max(0.0, lower)
        upper_bound = duration - skip_end - 0.05 if upper is None else max(0.0, upper)
        if lower_bound > upper_bound:
            raise ScreenshotError("截图时间范围无效")
        offsets = (0.0, 0.005, -0.005, 0.012, -0.012)
        candidates = [target_time + effective * offset for offset in offsets]
        # Near EOF, duration includes the final frame's display interval. Small
        # percentage offsets may all seek beyond its timestamp, especially at
        # low frame rates. Add frame-sized backward retries, within user bounds.
        fps = media.get("fps")
        try:
            frame_interval = 1 / float(fps)
            if not math.isfinite(frame_interval) or frame_interval <= 0:
                raise ValueError
        except (TypeError, ValueError, ZeroDivisionError):
            frame_interval = 1.0
        candidates.extend(target_time - frame_interval * frames for frames in (1, 2))
        seen: set[float] = set()
        for candidate in candidates:
            candidate = min(upper_bound, max(lower_bound, candidate))
            candidate = round(candidate, 3)
            if candidate in seen:
                continue
            seen.add(candidate)
            output_path.unlink(missing_ok=True)
            command = [
                *ffmpeg,
                *(
                    ["-init_hw_device", "vulkan=bili:llvmpipe", "-filter_hw_device", "bili"]
                    if media.get("color_processor") == "libplacebo"
                    else []
                ),
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{candidate:.3f}",
                *(media.get("input_args") or []),
                "-i",
                str(media.get("input") or source),
                "-frames:v",
                "1",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                ",".join(
                    filter(None, [media.get("color_filter"), self._scale_filter(width, media)])
                ),
                "-y",
                str(output_path),
            ]
            try:
                self._run_ffmpeg(command, should_cancel)
            except FrameUnavailable:
                continue
            except ScreenshotError:
                if (
                    media.get("color_requested") != "auto"
                    or media.get("color_processor") != "libplacebo"
                ):
                    raise
                chain, processor = conversion_filter(
                    media, "zscale", rendering_capabilities(tuple(ffmpeg))
                )
                media["color_filter"], media["color_processor"] = chain, processor
                # Repeat this timestamp with the checked compatible path.
                return self._extract_frame_with_retries(
                    ffmpeg,
                    source,
                    target_time,
                    duration,
                    skip_start,
                    skip_end,
                    output_path,
                    width,
                    media,
                    should_cancel,
                    lower=lower,
                    upper=upper,
                )
            if not output_path.is_file() or output_path.stat().st_size == 0:
                continue
            if not self._is_dark_frame(output_path):
                return candidate, output_path
        output_path.unlink(missing_ok=True)
        raise ScreenshotError("附近画面均为黑帧或提取失败")

    @staticmethod
    def _scale_filter(width: int, media: Mapping[str, Any]) -> str:
        if width <= 0:
            return "scale=trunc(iw*sar/2)*2:ih:flags=lanczos,setsar=1"
        sar_text = str(media.get("sample_aspect_ratio") or "")
        sar_ratio = 1.0
        if re.fullmatch(r"\d+:\d+", sar_text):
            numerator, denominator = sar_text.split(":", 1)
            if int(denominator) > 0 and int(numerator) > 0:
                sar_ratio = int(numerator) / int(denominator)
        source_width = int(media.get("width") or 0)
        source_height = int(media.get("height") or 0)
        if source_width <= 0 or source_height <= 0:
            return f"scale={width}:-2:flags=lanczos,setsar=1"
        target_height = max(
            2,
            int(round(width * source_height / (source_width * sar_ratio))),
        )
        if target_height % 2:
            target_height += 1
        return f"scale={width}:{target_height}:flags=lanczos,setsar=1"

    def _run_ffmpeg(self, command: list[str], should_cancel: Callable[[], bool]) -> None:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise ScreenshotError(f"无法启动 ffmpeg：{exc}") from exc
        deadline = time.monotonic() + FFMPEG_TIMEOUT_SECONDS
        while process.poll() is None:
            if should_cancel():
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                raise OperationCancelled
            if time.monotonic() > deadline:
                process.kill()
                process.wait(timeout=2)
                raise ScreenshotError("ffmpeg 截图超时")
            time.sleep(0.05)
        _, stderr = process.communicate()
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            if "Nothing was written into output file" in message and "received no packets" in message:
                raise FrameUnavailable("该时间点没有可输出的视频帧")
            raise ScreenshotError(message.splitlines()[-1][:300] if message else "ffmpeg 截图失败")

    @staticmethod
    def _is_dark_frame(path: Path) -> bool:
        try:
            with Image.open(path) as image:
                gray = image.convert("L")
                gray.thumbnail((96, 96))
                stats = ImageStat.Stat(gray)
                mean = float(stats.mean[0])
                deviation = float(stats.stddev[0])
        except OSError as exc:
            raise ScreenshotError("无法读取 FFmpeg 生成的截图") from exc
        return mean < 8 and deviation < 5

    def _save_variants(self, frame, target, timestamp, params, replace=False):
        raw = target.with_name(target.stem + "-raw" + target.suffix)
        staged_raw = target.with_name("." + uuid.uuid4().hex + target.suffix)
        staged_style = target.with_name("." + uuid.uuid4().hex + target.suffix)
        backups = {}
        committed = []
        try:
            self._save_screenshot_jpg(frame, staged_raw)
            if params.get("style", "plain") == "script":
                render_still(staged_raw, staged_style, timestamp)
            else:
                shutil.copyfile(staged_raw, staged_style)
            for destination in (raw, target):
                if destination.is_symlink() or (destination.exists() and not destination.is_file()):
                    raise ScreenshotError("截图目标不是普通文件")
                if destination.exists():
                    if not replace:
                        raise ScreenshotError("截图目标已存在")
                    backup = destination.with_name("." + uuid.uuid4().hex + ".bak")
                    shutil.copyfile(destination, backup)
                    backups[destination] = backup
            for stage, destination in ((staged_raw, raw), (staged_style, target)):
                self._commit_file(stage, destination, overwrite=replace)
                committed.append(destination)
            return target.stat().st_size, _fingerprint(target), raw
        except Exception:
            for destination in committed:
                if destination in backups:
                    os.replace(backups[destination], destination)
                else:
                    destination.unlink(missing_ok=True)
            raise
        finally:
            for stage in (staged_raw, staged_style, *backups.values()):
                stage.unlink(missing_ok=True)

    def _save_screenshot_jpg(
        self, frame_path: Path, target: Path, *, replace: bool = False
    ) -> tuple[int, str]:
        temporary = target.parent / f".bili-remote-screenshot-{uuid.uuid4().hex}.tmp"
        try:
            with Image.open(frame_path) as image:
                image.convert("RGB").save(
                    temporary,
                    "PNG" if target.suffix.lower() == ".png" else "JPEG",
                    **(
                        {"quality": 95, "subsampling": 0, "optimize": True}
                        if target.suffix.lower() != ".png"
                        else {}
                    ),
                )
            with temporary.open("rb") as handle:
                os.fsync(handle.fileno())
            if replace:
                if target.is_symlink() or (target.exists() and not target.is_file()):
                    raise ScreenshotError("截图目标不是普通文件")
                os.replace(temporary, target)
            else:
                if os.path.lexists(target):
                    raise ScreenshotError("截图目标已存在")
                os.link(temporary, target)
                temporary.unlink(missing_ok=True)
            return int(target.stat().st_size), _fingerprint(target)
        except OSError as exc:
            raise ScreenshotError(f"无法写入截图文件：{exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _ensure_batch_names_unique(outputs: list[dict[str, Any]]) -> None:
        seen: dict[str, int] = {}
        conflicts: list[dict[str, Any]] = []
        for output in outputs:
            folded = str(output["output_name"]).casefold()
            previous = seen.get(folded)
            if previous is not None:
                conflicts.append(
                    {
                        "index": output["index"],
                        "source_name": output["source_name"],
                        "output_name": output["output_name"],
                        "reason": (
                            "批量输出重名："
                            f"{outputs[previous]['source_name']} 与 {output['source_name']}"
                        ),
                        "hard": True,
                    }
                )
            else:
                seen[folded] = int(output["index"])
        if conflicts:
            raise ScreenshotConflictError("拼图输出文件名冲突", conflicts)

    @staticmethod
    def _target_conflicts(output_dir: Path, outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            siblings = [(entry.name.casefold(), entry.name) for entry in os.scandir(output_dir)]
        except OSError as exc:
            raise ScreenshotError(f"无法读取输出目录：{exc}") from exc
        conflicts: list[dict[str, Any]] = []
        for output in outputs:
            name = str(output["output_name"])
            target = output_dir / name
            hard = False
            reason = ""
            if os.path.lexists(target):
                if target.is_symlink():
                    hard = True
                    reason = "目标路径是符号链接"
                elif target.is_dir():
                    hard = True
                    reason = "目标路径是目录"
                else:
                    reason = "目标文件已存在"
            for folded, sibling in siblings:
                if sibling == name:
                    continue
                if folded == name.casefold():
                    hard = True
                    reason = f"与现有文件大小写冲突：{sibling}"
                    break
            if reason:
                conflicts.append(
                    {
                        "index": output["index"],
                        "source_name": output["source_name"],
                        "output_name": name,
                        "reason": reason,
                        "hard": hard,
                    }
                )
        return conflicts

    @staticmethod
    def _validate_canvas(layout: Mapping[str, Any]) -> None:
        width = int(layout["columns"]) * int(layout["thumbnail_width"])
        height = int(layout["rows"]) * int(layout["thumbnail_width"])
        if width > MAX_CANVAS_SIDE or height > MAX_CANVAS_SIDE * 2:
            raise ScreenshotError("最终画布尺寸过大，请减少行数或缩略图宽度")

    @staticmethod
    def _validate_rendered_collage(rendered: Path) -> None:
        try:
            with Image.open(rendered) as image:
                width, height = image.size
        except OSError as exc:
            raise ScreenshotError("无法读取生成后的拼图") from exc
        if (
            width < 1
            or height < 1
            or width > MAX_CANVAS_SIDE
            or height > MAX_CANVAS_SIDE * 2
            or width * height > MAX_CANVAS_PIXELS
        ):
            raise ScreenshotError("最终画布尺寸过大，请减少行数或缩略图宽度")

    @staticmethod
    def _commit_file(rendered: Path, target: Path, *, overwrite: bool) -> None:
        temporary = target.parent / f".bili-remote-collage-{uuid.uuid4().hex}.tmp"
        try:
            with rendered.open("rb") as source, temporary.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
                destination.flush()
                os.fsync(destination.fileno())
            os.chmod(
                temporary,
                stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH,
            )
            if overwrite:
                if os.path.lexists(target) and (target.is_symlink() or target.is_dir()):
                    raise ScreenshotError("目标路径类型不允许覆盖")
                os.replace(temporary, target)
            else:
                if os.path.lexists(target):
                    raise ScreenshotError("目标文件已存在；请确认覆盖后重试")
                try:
                    os.link(temporary, target)
                except FileExistsError as exc:
                    raise ScreenshotError("目标文件已存在；请确认覆盖后重试") from exc
                temporary.unlink(missing_ok=True)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _resolve_executable(command: tuple[str, ...]) -> str | None:
        if not command:
            return None
        executable = command[0]
        if os.path.isabs(executable):
            return executable if Path(executable).is_file() else None
        return shutil.which(executable)

    @staticmethod
    def _emit_progress(
        progress: Callable[[dict[str, Any]], None] | None,
        manifest: Mapping[str, Any],
        *,
        current: str,
        video_index: int,
        image_successes: int,
        image_failures: int,
    ) -> None:
        if progress is None:
            return
        videos = manifest.get("videos") or []
        progress(
            {
                "status": "running",
                "session_id": manifest.get("id"),
                "total": len(videos),
                "succeeded": sum(
                    1 for video in videos if video.get("status") in {"completed", "partial"}
                ),
                "failed": sum(1 for video in videos if video.get("status") == "failed"),
                "current": current,
                "video_index": video_index,
                "image_total": sum(
                    len(video.get("items") or []) or int(manifest["params"]["total"])
                    for video in manifest.get("videos", [])
                ),
                "image_succeeded": image_successes,
                "image_failed": image_failures,
            }
        )


class _Source:
    __slots__ = (
        "root_key",
        "relative_path",
        "name",
        "size_bytes",
        "mtime_ns",
        "fingerprint",
        "video_short_id",
    )

    def __init__(
        self,
        *,
        root_key: str,
        relative_path: str,
        name: str,
        size_bytes: int,
        mtime_ns: int,
        fingerprint: str,
        video_short_id: str,
    ) -> None:
        self.root_key = root_key
        self.relative_path = relative_path
        self.name = name
        self.size_bytes = size_bytes
        self.mtime_ns = mtime_ns
        self.fingerprint = fingerprint
        self.video_short_id = video_short_id

    def payload(self) -> dict[str, Any]:
        return {
            "root_key": self.root_key,
            "relative_path": self.relative_path,
            "name": self.name,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
            "fingerprint": self.fingerprint,
        }


__all__ = [
    "MAX_ITEMS_PER_VIDEO",
    "MAX_TOTAL_ITEMS",
    "ScreenshotConflictError",
    "ScreenshotError",
    "ScreenshotNotFoundError",
    "ScreenshotService",
    "parse_timestamp",
    "sample_random_times",
]
