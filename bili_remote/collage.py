from __future__ import annotations

import math
import os
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

from .collage_style import (
    CollageFrame,
    CollageMediaInfo,
    find_cjk_font,
    format_timestamp,
    render_collage,
    style_metrics,
)
from .config import Settings
from .file_service import (
    VIDEO_EXTENSIONS,
    FileService,
    FileServiceError,
    OperationCancelled,
    validate_filename,
)
from .media_probe import (
    probe_executable,
    probe_json_sync,
    video_details_from_payload,
)
from .workspace import normalize_relative_path

CONTENT_PRESETS: dict[str, tuple[int, int]] = {
    "movie": (90, 120),
    "series": (90, 180),
    "anime": (90, 90),
    "custom": (0, 0),
}
MAX_SOURCES = 100
MAX_COLUMNS = 10
MAX_ROWS = 12
MAX_TOTAL = MAX_COLUMNS * MAX_ROWS
MIN_WIDTH = 240
MAX_WIDTH = 1920
MAX_CANVAS_PIXELS = 150_000_000
MAX_CANVAS_SIDE = 20_000
TEMP_MAX_AGE_SECONDS = 24 * 60 * 60
FFMPEG_TIMEOUT_SECONDS = 60
FFPROBE_TIMEOUT_SECONDS = 30
_INVALID_FILENAME_CHARS = str.maketrans(
    {character: "_" for character in '<>:"/\\|?*'}
)
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class CollageError(FileServiceError):
    pass


class CollageConflictError(CollageError):
    def __init__(self, message: str, conflicts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.conflicts = conflicts


@dataclass(frozen=True, slots=True)
class CollageSource:
    root_key: str
    relative_path: str
    name: str
    size_bytes: int
    mtime_ns: int

    def payload(self) -> dict[str, Any]:
        return {
            "root_key": self.root_key,
            "relative_path": self.relative_path,
            "name": self.name,
            "size_bytes": self.size_bytes,
            "mtime_ns": self.mtime_ns,
        }


@dataclass(frozen=True, slots=True)
class CollageSpec:
    sources: tuple[CollageSource, ...]
    layout_mode: str
    columns: int
    rows: int
    total: int
    thumbnail_width: int
    content_type: str
    skip_start_seconds: float
    skip_end_seconds: float
    output_relative_path: str
    overwrite: bool

    def payload(self) -> dict[str, Any]:
        return {
            "sources": [source.payload() for source in self.sources],
            "layout_mode": self.layout_mode,
            "columns": self.columns,
            "rows": self.rows,
            "total": self.total,
            "thumbnail_width": self.thumbnail_width,
            "content_type": self.content_type,
            "skip_start_seconds": self.skip_start_seconds,
            "skip_end_seconds": self.skip_end_seconds,
            "output_relative_path": self.output_relative_path,
            "overwrite": self.overwrite,
        }


def output_name_for(source_name: str) -> str:
    stem = Path(source_name).stem
    cleaned = stem.translate(_INVALID_FILENAME_CHARS)
    cleaned = "".join(
        "_" if ord(character) < 32 else character for character in cleaned
    ).strip(" .")
    if not cleaned:
        cleaned = "video"
    cleaned = cleaned[:180].rstrip(" .") or "video"
    if cleaned.split(".", 1)[0].upper() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return validate_filename(f"{cleaned}_collage.jpg")


def sample_times(
    duration: float,
    skip_start: float,
    skip_end: float,
    total: int,
) -> list[float]:
    effective = duration - skip_start - skip_end
    if not math.isfinite(effective) or effective < 1:
        raise CollageError("跳过片头片尾后有效时长不足 1 秒")
    return [skip_start + effective * (index + 0.5) / total for index in range(total)]


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise CollageError(f"{label}无效")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CollageError(f"{label}无效") from exc
    if not math.isfinite(parsed):
        raise CollageError(f"{label}无效")
    return parsed


def _bounded_integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise CollageError(f"{label}无效")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise CollageError(f"{label}无效") from exc
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise CollageError(f"{label}必须是整数")
    parsed = int(numeric)
    if parsed < minimum or parsed > maximum:
        raise CollageError(f"{label}必须在 {minimum} 到 {maximum} 之间")
    return parsed


class CollageService:
    def __init__(self, settings: Settings, files: FileService) -> None:
        self.settings = settings
        self.files = files

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
                "columns": MAX_COLUMNS,
                "rows": MAX_ROWS,
                "total": MAX_TOTAL,
                "thumbnail_width": [MIN_WIDTH, MAX_WIDTH],
            },
        }

    def prepare(self, data: Mapping[str, Any]) -> dict[str, Any]:
        status = self.status()
        if not status["available"]:
            missing = []
            if not status["ffmpeg"]:
                missing.append("ffmpeg")
            if not status["ffprobe"]:
                missing.append("ffprobe")
            if not status["font"]:
                missing.append("中文字体")
            raise CollageError(f"缩略图拼接依赖不可用：{'、'.join(missing)}")

        spec = self._parse_spec(data)
        output_dir = self._resolve_output_directory(spec.output_relative_path)
        outputs = self._output_payload(spec)
        conflicts = self._target_conflicts(output_dir, spec, outputs)
        hard_conflicts = [item for item in conflicts if item.get("hard")]
        if hard_conflicts or (conflicts and not spec.overwrite):
            raise CollageConflictError("输出文件存在冲突", conflicts)
        return {
            **spec.payload(),
            "outputs": outputs,
            "conflicts": conflicts,
            "columns": spec.columns,
            "rows": spec.rows,
        }

    def run(
        self,
        data: Mapping[str, Any],
        *,
        task_id: int,
        log: Callable[[str], None],
        should_cancel: Callable[[], bool],
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        spec = self._parse_spec(data)
        output_dir = self._resolve_output_directory(spec.output_relative_path)
        outputs = self._output_payload(spec)
        self._ensure_batch_names_unique(outputs)
        ffmpeg = self._resolve_executable(self.settings.ffmpeg_command)
        ffmpeg_command = self.settings.ffmpeg_command
        ffprobe = probe_executable(self.settings)
        font = find_cjk_font()
        if not ffmpeg or not ffprobe or font is None:
            raise CollageError("缩略图拼接依赖不可用")

        temporary_root = Path(
            tempfile.mkdtemp(
                prefix=f"collage-{task_id}-",
                dir=self.settings.runtime_dir,
            )
        )
        results: list[dict[str, Any]] = []
        completed = 0
        failed = 0
        try:
            for index, (source, output) in enumerate(zip(spec.sources, outputs, strict=True)):
                if should_cancel():
                    raise OperationCancelled
                current = {
                    "index": index,
                    "source": source.payload(),
                    "output": output,
                    "status": "running",
                }
                self._emit_progress(
                    progress,
                    spec,
                    completed=completed,
                    failed=failed,
                    current=source.name,
                    results=results,
                )
                try:
                    item = self._process_source(
                        source,
                        output,
                        spec,
                        output_dir=output_dir,
                        temporary_root=temporary_root,
                        ffmpeg=ffmpeg_command,
                        font=font,
                        log=log,
                        should_cancel=should_cancel,
                    )
                    completed += 1
                    item["status"] = "completed"
                    log(f"已生成：{source.relative_path} → {output['output_relative_path']}")
                except OperationCancelled:
                    raise
                except Exception as exc:
                    failed += 1
                    message = str(exc) or exc.__class__.__name__
                    item = {
                        **current,
                        "status": "failed",
                        "error": message,
                    }
                    log(f"处理失败：{source.relative_path}：{message}")
                results.append(item)
                self._emit_progress(
                    progress,
                    spec,
                    completed=completed,
                    failed=failed,
                    current="",
                    results=results,
                )
            task_status = (
                "completed"
                if failed == 0
                else "partial"
                if completed > 0
                else "failed"
            )
            return {
                "status": task_status,
                "total": len(spec.sources),
                "succeeded": completed,
                "failed": failed,
                "columns": spec.columns,
                "rows": spec.rows,
                "thumbnail_width": spec.thumbnail_width,
                "output_relative_path": spec.output_relative_path,
                "outputs": outputs,
                "items": results,
            }
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    def cleanup_stale_temporary_directories(self) -> None:
        cutoff = time.time() - TEMP_MAX_AGE_SECONDS
        for path in self.settings.runtime_dir.glob("collage-*"):
            try:
                if path.is_dir() and path.stat().st_mtime < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue

    def _parse_spec(self, data: Mapping[str, Any]) -> CollageSpec:
        raw_sources = data.get("sources") or []
        if not isinstance(raw_sources, list) or not raw_sources:
            raise CollageError("请至少选择一个视频")
        if len(raw_sources) > MAX_SOURCES:
            raise CollageError(f"单次最多处理 {MAX_SOURCES} 个视频")
        sources: list[CollageSource] = []
        seen: set[tuple[str, str]] = set()
        for raw in raw_sources:
            if not isinstance(raw, Mapping):
                raise CollageError("视频来源无效")
            root_key = str(raw.get("root_key") or "")
            relative_path = normalize_relative_path(
                str(raw.get("relative_path") or ""),
                allow_empty=False,
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
            if path.is_symlink() or not path.is_file():
                raise CollageError("缩略图拼接只支持普通视频文件")
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                raise CollageError(f"不支持的文件类型：{path.name}")
            info = path.stat()
            sources.append(
                CollageSource(
                    root_key=root_key,
                    relative_path=relative_path,
                    name=path.name,
                    size_bytes=int(info.st_size),
                    mtime_ns=int(info.st_mtime_ns),
                )
            )
        if not sources:
            raise CollageError("请至少选择一个视频")

        layout_mode = str(data.get("layout_mode") or "grid")
        if layout_mode not in {"grid", "count"}:
            raise CollageError("布局模式无效")
        if layout_mode == "grid":
            columns = _bounded_integer(
                data.get("columns") if data.get("columns") is not None else 5,
                "列数",
                1,
                MAX_COLUMNS,
            )
            rows = _bounded_integer(
                data.get("rows") if data.get("rows") is not None else 4,
                "行数",
                1,
                MAX_ROWS,
            )
            total = columns * rows
        else:
            columns = _bounded_integer(
                data.get("columns") if data.get("columns") is not None else 5,
                "列数",
                1,
                MAX_COLUMNS,
            )
            total = _bounded_integer(
                data.get("total") if data.get("total") is not None else 20,
                "截图数量",
                1,
                MAX_TOTAL,
            )
            rows = math.ceil(total / columns)
            if rows > MAX_ROWS:
                raise CollageError(f"行数不能超过 {MAX_ROWS}")

        width = _bounded_integer(
            data.get("thumbnail_width")
            if data.get("thumbnail_width") is not None
            else 480,
            "缩略图宽度",
            MIN_WIDTH,
            MAX_WIDTH,
        )
        content_type = str(data.get("content_type") or "anime")
        if content_type not in CONTENT_PRESETS:
            raise CollageError("视频内容类型无效")
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
            raise CollageError("跳过时长必须在 0 到 86400 秒之间")
        output_relative_path = normalize_relative_path(
            str(data.get("output_relative_path") or "")
        )
        return CollageSpec(
            sources=tuple(sources),
            layout_mode=layout_mode,
            columns=columns,
            rows=rows,
            total=total,
            thumbnail_width=width,
            content_type=content_type,
            skip_start_seconds=round(skip_start, 3),
            skip_end_seconds=round(skip_end, 3),
            output_relative_path=output_relative_path,
            overwrite=bool(data.get("overwrite")),
        )

    def _resolve_output_directory(self, relative_path: str) -> Path:
        directory, _ = self.files.resolve(
            "images",
            relative_path,
            scope="browse",
            must_exist=True,
            allow_empty=True,
        )
        if directory.is_symlink() or not directory.is_dir():
            raise CollageError("输出目录不存在或不是普通目录")
        if not os.access(directory, os.R_OK | os.W_OK | os.X_OK):
            raise CollageError("输出目录不可读写")
        return directory

    def _output_payload(self, spec: CollageSpec) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        for index, source in enumerate(spec.sources):
            name = output_name_for(source.name)
            relative = "/".join(
                part
                for part in (spec.output_relative_path, name)
                if part
            )
            outputs.append(
                {
                    "index": index,
                    "source_root_key": source.root_key,
                    "source_relative_path": source.relative_path,
                    "source_name": source.name,
                    "output_root_key": "images",
                    "output_name": name,
                    "output_relative_path": relative,
                }
            )
        self._ensure_batch_names_unique(outputs)
        return outputs

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
            raise CollageConflictError("批量输出文件名冲突", conflicts)

    def _target_conflicts(
        self,
        output_dir: Path,
        spec: CollageSpec,
        outputs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        siblings: list[tuple[str, Path]] = []
        try:
            with os.scandir(output_dir) as entries:
                for entry in entries:
                    siblings.append((entry.name.casefold(), Path(entry.path)))
        except OSError as exc:
            raise CollageError(f"无法读取输出目录：{exc}") from exc
        conflicts: list[dict[str, Any]] = []
        for output in outputs:
            target = output_dir / str(output["output_name"])
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
                if sibling.name == target.name:
                    continue
                if folded == target.name.casefold():
                    hard = True
                    reason = f"与现有文件大小写冲突：{sibling.name}"
                    break
            if reason:
                conflicts.append(
                    {
                        "index": output["index"],
                        "source_name": output["source_name"],
                        "output_name": output["output_name"],
                        "reason": reason,
                        "hard": hard,
                    }
                )
        return conflicts

    def _ensure_runtime_target(self, output_dir: Path, output: dict[str, Any]) -> Path:
        target = output_dir / str(output["output_name"])
        if os.path.lexists(target):
            if target.is_symlink():
                raise CollageError("目标路径是符号链接")
            if target.is_dir():
                raise CollageError("目标路径是目录")
        for sibling in output_dir.iterdir():
            if sibling.name == target.name:
                continue
            if sibling.name.casefold() == target.name.casefold():
                raise CollageError(f"目标名称与现有文件冲突：{sibling.name}")
        return target

    def _process_source(
        self,
        source: CollageSource,
        output: dict[str, Any],
        spec: CollageSpec,
        *,
        output_dir: Path,
        temporary_root: Path,
        ffmpeg: tuple[str, ...],
        font: Path,
        log: Callable[[str], None],
        should_cancel: Callable[[], bool],
    ) -> dict[str, Any]:
        source_path, _ = self.files.resolve(
            source.root_key,
            source.relative_path,
            scope="browse",
            must_exist=True,
            allow_empty=False,
        )
        if source_path.is_symlink() or not source_path.is_file():
            raise CollageError("源视频已变化或不可访问")
        info = source_path.stat()
        if (
            int(info.st_size) != source.size_bytes
            or int(info.st_mtime_ns) != source.mtime_ns
        ):
            raise CollageError("源视频状态已变化，请刷新后重试")
        target = self._ensure_runtime_target(output_dir, output)
        if os.path.lexists(target) and not spec.overwrite:
            raise CollageError("目标文件已存在；请确认覆盖后重试")

        log(f"读取媒体信息：{source.relative_path}")
        payload = probe_json_sync(
            self.settings,
            source_path,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
        details = video_details_from_payload(payload)
        duration = details["duration_seconds"]
        if duration is None:
            raise CollageError("无法获取视频时长")
        times = sample_times(
            float(duration),
            spec.skip_start_seconds,
            spec.skip_end_seconds,
            spec.total,
        )
        media_info = CollageMediaInfo(
            filename=source.name,
            size_bytes=source.size_bytes,
            width=details["width"],
            height=details["height"],
            aspect_ratio=details["aspect_ratio"],
            fps=details["fps"],
            video_codec=details["video_codec"],
            audio_codec=details["audio_codec"],
            duration_seconds=duration,
        )

        frames: list[CollageFrame] = []
        for index, target_time in enumerate(times, start=1):
            if should_cancel():
                raise OperationCancelled
            actual_time, frame_path = self._extract_frame_with_retries(
                ffmpeg,
                source_path,
                target_time,
                duration,
                spec.skip_start_seconds,
                spec.skip_end_seconds,
                temporary_root / f"frame-{index:03d}.png",
                spec.thumbnail_width,
                should_cancel,
            )
            frames.append(CollageFrame(frame_path, actual_time))
            log(f"截图 {index}/{len(times)}：{format_timestamp(actual_time)}")

        rendered = temporary_root / f"collage-{uuid.uuid4().hex}.jpg"
        metrics = style_metrics(spec.thumbnail_width)
        estimated_height = spec.rows * (
            spec.thumbnail_width * 3 + metrics.timestamp_height
        )
        estimated_width = spec.columns * spec.thumbnail_width
        if (
            estimated_width > MAX_CANVAS_SIDE
            or estimated_height > MAX_CANVAS_SIDE * 2
        ):
            raise CollageError("最终画布尺寸过大，请减少行数或缩略图宽度")
        render_info = render_collage(
            frames,
            media_info,
            columns=spec.columns,
            rows=spec.rows,
            thumbnail_width=spec.thumbnail_width,
            output_path=rendered,
            font_path=font,
        )
        width = int(render_info["width"])
        height = int(render_info["height"])
        if width > MAX_CANVAS_SIDE or height > MAX_CANVAS_SIDE:
            raise CollageError("最终画布尺寸过大")
        if width * height > MAX_CANVAS_PIXELS:
            raise CollageError("最终画布像素过多，请减少截图或缩略图宽度")
        self._commit_output(rendered, target, overwrite=spec.overwrite)
        return {
            **output,
            "source_path": source.relative_path,
            "status": "completed",
            "media": {
                "width": media_info.width,
                "height": media_info.height,
                "aspect_ratio": media_info.aspect_ratio,
                "fps": media_info.fps,
                "video_codec": media_info.video_codec,
                "audio_codec": media_info.audio_codec,
                "duration_seconds": media_info.duration_seconds,
            },
            "sample_timestamps": [
                format_timestamp(frame.timestamp_seconds) for frame in frames
            ],
            "output_size_bytes": target.stat().st_size,
            "output_width": width,
            "output_height": height,
        }

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
        should_cancel: Callable[[], bool],
    ) -> tuple[float, Path]:
        effective = max(1.0, duration - skip_start - skip_end)
        offsets = (0.0, 0.01, -0.01, 0.025, -0.025)
        lower = skip_start + 0.05
        upper = duration - skip_end - 0.05
        seen: set[float] = set()
        for offset in offsets:
            candidate = min(upper, max(lower, target_time + effective * offset))
            candidate = round(candidate, 3)
            if candidate in seen:
                continue
            seen.add(candidate)
            output_path.unlink(missing_ok=True)
            command = [
                *ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{candidate:.3f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                f"scale={width}:-2:flags=lanczos",
                "-y",
                str(output_path),
            ]
            self._run_ffmpeg(command, should_cancel)
            if not output_path.is_file() or output_path.stat().st_size == 0:
                continue
            if not self._is_dark_frame(output_path):
                return candidate, output_path
        output_path.unlink(missing_ok=True)
        raise CollageError("附近画面均为黑帧或提取失败")

    def _run_ffmpeg(
        self,
        command: list[str],
        should_cancel: Callable[[], bool],
    ) -> None:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise CollageError(f"无法启动 ffmpeg：{exc}") from exc
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
                raise CollageError("ffmpeg 截图超时")
            time.sleep(0.05)
        _, stderr = process.communicate()
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip()
            raise CollageError(message.splitlines()[-1][:300] if message else "ffmpeg 截图失败")

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
            raise CollageError("无法读取 FFmpeg 生成的截图") from exc
        return mean < 8 and deviation < 5

    def _commit_output(self, rendered: Path, target: Path, *, overwrite: bool) -> None:
        temporary = target.parent / f".bili-remote-collage-{uuid.uuid4().hex}.tmp"
        try:
            with rendered.open("rb") as source, temporary.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
                destination.flush()
                os.fsync(destination.fileno())
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
            if overwrite:
                if os.path.lexists(target):
                    if target.is_symlink() or target.is_dir():
                        raise CollageError("目标路径类型不允许覆盖")
                os.replace(temporary, target)
            else:
                if os.path.lexists(target):
                    raise CollageError("目标文件已存在；请确认覆盖后重试")
                try:
                    os.link(temporary, target)
                except FileExistsError as exc:
                    raise CollageError("目标文件已存在；请确认覆盖后重试") from exc
                temporary.unlink(missing_ok=True)
            try:
                directory_fd = os.open(target.parent, os.O_RDONLY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _emit_progress(
        self,
        progress: Callable[[dict[str, Any]], None] | None,
        spec: CollageSpec,
        *,
        completed: int,
        failed: int,
        current: str,
        results: list[dict[str, Any]],
    ) -> None:
        if progress is None:
            return
        progress(
            {
                "status": "running",
                "total": len(spec.sources),
                "succeeded": completed,
                "failed": failed,
                "current": current,
                "columns": spec.columns,
                "rows": spec.rows,
                "items": results,
            }
        )

    @staticmethod
    def _resolve_executable(command: tuple[str, ...]) -> str | None:
        if not command:
            return None
        executable = command[0]
        if os.path.isabs(executable):
            return executable if Path(executable).is_file() else None
        return shutil.which(executable)
