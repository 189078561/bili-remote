from __future__ import annotations

import errno
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .config import Settings
from .database import Database
from .workspace import normalize_relative_path, path_state, resolve_workspace_path


TEMPLATE_VARIABLES = {
    "title_zh", "title_en",
    "title",
    "year",
    "season",
    "episode",
    "resolution",
    "source",
    "video_codec",
    "audio_codec",
    "hdr",
    "group",
    "ext",
    "original_name",
}
OPTIONAL_VARIABLES = {
    "title_zh", "title_en",
    "year",
    "season",
    "episode",
    "resolution",
    "source",
    "video_codec",
    "audio_codec",
    "hdr",
    "group",
}
_TEMPLATE_FIELD = re.compile(
    r"\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?::(?P<format>0(?P<width>[1-9]\d?)d))?\}"
)
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESOLUTION = re.compile(r"(?i)(?<!\d)(2160p|1080p|720p|576p|480p)(?!\d)")
_VIDEO_CODEC = re.compile(r"(?i)(?<!\w)(x265|h\.?265|hevc|x264|h\.?264|avc|av1)(?!\w)")
_AUDIO_CODEC = re.compile(r"(?i)(?<!\w)(dts(?:-hd)?|truehd|flac|aac|eac3|ddp?|ac3)(?!\w)")
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
LANGUAGE_SUFFIXES = {
    "zh-Hans": "chs",
    "zh-Hant": "cht",
    "ja": "jpn",
    "en": "eng",
    "ko": "kor",
}


class NamingError(ValueError):
    pass


@dataclass(slots=True)
class RenamePreview:
    token: str
    project_id: int
    target_mode: str
    batch_id: str
    operations: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    created_monotonic: float = field(default_factory=time.monotonic)


class PreviewStore:
    def __init__(self, *, max_entries: int = 100, ttl_seconds: int = 30 * 60) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, RenamePreview] = {}

    def put(self, preview: RenamePreview) -> None:
        self._prune()
        self._entries[preview.token] = preview
        while len(self._entries) > self.max_entries:
            oldest = min(self._entries.values(), key=lambda item: item.created_monotonic)
            self._entries.pop(oldest.token, None)

    def get(self, token: str, project_id: int) -> RenamePreview:
        preview = self._entries.get(token)
        if not preview:
            raise NamingError("重命名预览已失效，请重新预览")
        if preview.project_id != project_id:
            raise NamingError("重命名预览与项目不匹配")
        if time.monotonic() - preview.created_monotonic > self.ttl_seconds:
            self._entries.pop(token, None)
            raise NamingError("重命名预览已过期，请重新预览")
        return preview

    def remove(self, token: str) -> None:
        self._entries.pop(token, None)

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [
            token
            for token, preview in self._entries.items()
            if now - preview.created_monotonic > self.ttl_seconds
        ]
        for token in expired:
            self._entries.pop(token, None)


@dataclass(slots=True)
class TemplateField:
    name: str
    width: int | None = None


def parse_naming_template(template: str) -> list[TemplateField]:
    value = (template or "").strip()
    if not value:
        raise NamingError("命名模板不能为空")
    if "/" in value or "\\" in value:
        raise NamingError("命名模板不能包含路径分隔符")
    fields: list[TemplateField] = []
    position = 0
    for match in _TEMPLATE_FIELD.finditer(value):
        prefix = value[position : match.start()]
        if "{" in prefix or "}" in prefix:
            raise NamingError("命名模板包含未闭合或不支持的花括号")
        name = match.group("name")
        if name not in TEMPLATE_VARIABLES:
            raise NamingError(f"未知命名变量：{name}")
        width = int(match.group("width")) if match.group("width") else None
        if width and name not in {"season", "episode"}:
            raise NamingError("只有 {season} 和 {episode} 支持 :02d 格式")
        fields.append(TemplateField(name=name, width=width))
        position = match.end()
    remainder = value[position:]
    if "{" in remainder or "}" in remainder:
        raise NamingError("命名模板包含未闭合或不支持的花括号")
    return fields


def sanitize_filename_component(value: str, *, fallback: str = "untitled") -> str:
    cleaned = _INVALID_FILENAME.sub("_", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = fallback
    stem = cleaned.split(".", 1)[0].upper()
    if stem in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:180].rstrip(" .")


def render_naming_template(template: str, context: dict[str, Any]) -> tuple[str, list[str]]:
    fields = parse_naming_template(template)
    warnings: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        value = context.get(name)
        if value is None or str(value).strip() == "":
            if name in OPTIONAL_VARIABLES and name not in warnings:
                warnings.append(f"缺少可选值：{name}")
            value = ""
        if match.group("width"):
            try:
                return str(int(value)).zfill(int(match.group("width")))
            except (TypeError, ValueError):
                return ""
        return str(value)

    rendered = _TEMPLATE_FIELD.sub(replace, template)
    rendered = re.sub(r"([._ -])\1+", r"\1", rendered).strip(" ._-")
    return sanitize_filename_component(rendered), warnings


def _context_from_file(
    project: dict[str, Any], file_record: dict[str, Any]
) -> dict[str, Any]:
    scan = file_record.get("scan_result") or {}
    metadata = project.get("metadata") or {}
    name = Path(file_record["relative_path"]).name
    stem = Path(name).stem
    resolution = scan.get("resolution") or metadata.get("resolution")
    if not resolution:
        match = _RESOLUTION.search(stem)
        resolution = match.group(1) if match else None
    video_codec = scan.get("video_codec") or metadata.get("video_codec")
    if not video_codec:
        match = _VIDEO_CODEC.search(stem)
        video_codec = match.group(1).replace(".", "").upper() if match else None
    audio_codec = scan.get("audio_codec") or metadata.get("audio_codec")
    if not audio_codec:
        match = _AUDIO_CODEC.search(stem)
        audio_codec = match.group(1).upper() if match else None
    return {
        "title": project.get("title") or file_record.get("title") or stem,
        "year": project.get("year"),
        "season": (
            file_record.get("season")
            if file_record.get("season") is not None
            else project.get("season")
        ),
        "episode": file_record.get("episode"),
        "resolution": resolution,
        "source": metadata.get("source") or scan.get("source"),
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "group": metadata.get("group"),
        "ext": Path(name).suffix.lstrip(".").lower(),
        "original_name": stem,
    }


def _source_prefix(project: dict[str, Any]) -> str:
    metadata = project.get("metadata") or {}
    return normalize_relative_path(
        metadata.get("source_path") or project.get("download_dir") or ""
    )


def _strip_project_prefix(relative_path: str, prefix: str) -> str:
    if not prefix:
        return relative_path
    if relative_path == prefix:
        return PurePosixPath(relative_path).name
    marker = f"{prefix}/"
    if relative_path.startswith(marker):
        return relative_path[len(marker) :]
    return relative_path


def _target_relative(project: dict[str, Any], source_relative: str, name: str) -> str:
    prefix = _source_prefix(project)
    remainder = _strip_project_prefix(source_relative, prefix)
    parent = PurePosixPath(remainder).parent
    final_dir = normalize_relative_path(project.get("final_dir") or "")
    if not final_dir:
        final_dir = sanitize_filename_component(project["title"])
    parts = [final_dir]
    if str(parent) not in {"", "."}:
        parts.append(str(parent))
    parts.append(name)
    return "/".join(parts)


def _preview_payload(
    project: dict[str, Any],
    files: list[dict[str, Any]],
    links: list[dict[str, Any]],
    *,
    template: str,
    target_mode: str,
    selected_file_ids: set[int] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    if target_mode not in {"in_place", "releases"}:
        raise NamingError("重命名目标无效")
    by_id = {int(item["id"]): item for item in files}
    linked_by_video: dict[int, list[dict[str, Any]]] = {}
    for link in links:
        subtitle = by_id.get(int(link["subtitle_file_id"]))
        if subtitle:
            linked_by_video.setdefault(int(link["video_file_id"]), []).append(subtitle)

    primary = [item for item in files if item["file_role"] in {"video", "iso", "bdmv"}]
    if selected_file_ids:
        selected_primary = {
            int(item["id"])
            for item in primary
            if int(item["id"]) in selected_file_ids
        }
        if not selected_primary:
            for file_id in selected_file_ids:
                for link in links:
                    if int(link["subtitle_file_id"]) == file_id:
                        selected_primary.add(int(link["video_file_id"]))
        primary = [item for item in primary if int(item["id"]) in selected_primary]
    if not primary:
        raise NamingError("没有可预览的视频、ISO 或 BDMV 资源")

    operations: list[dict[str, Any]] = []
    warnings: list[str] = []
    targets: set[str] = set()
    for file_record in primary:
        context = _context_from_file(project, file_record)
        is_directory = file_record["file_role"] == "bdmv"
        context["ext"] = "" if is_directory else context["ext"]
        name, field_warnings = render_naming_template(template, context)
        subtitle_base_name = name
        if not is_directory and "{ext}" in template:
            subtitle_base_name, _ = render_naming_template(
                template, {**context, "ext": ""}
            )
        for warning in field_warnings:
            message = f"{file_record['relative_path']}: {warning}"
            if message not in warnings:
                warnings.append(message)
        if not is_directory and "{ext}" not in template:
            name = f"{name}.{context['ext']}" if context["ext"] else name
        if is_directory and "." in name:
            name = name.rstrip(" .")
        target_parent = (
            str(PurePosixPath(file_record["relative_path"]).parent)
            if target_mode == "in_place"
            else str(
                PurePosixPath(
                    _target_relative(project, file_record["relative_path"], name)
                ).parent
            )
        )
        target_relative = (
            f"{target_parent}/{name}" if target_parent not in {"", "."} else name
        )
        if target_mode == "releases":
            target_relative = _target_relative(project, file_record["relative_path"], name)
        key = target_relative.casefold()
        if key in targets:
            raise NamingError(f"多个文件将生成相同目标：{target_relative}")
        targets.add(key)
        target_root_key = (
            file_record["root_key"] if target_mode == "in_place" else "releases"
        )
        if (
            target_relative != file_record["relative_path"]
            or target_root_key != file_record["root_key"]
        ):
            operations.append(
                {
                    "file_id": int(file_record["id"]),
                    "file_role": file_record["file_role"],
                    "source_root_key": file_record["root_key"],
                    "source_relative_path": file_record["relative_path"],
                    "target_root_key": target_root_key,
                    "target_relative_path": target_relative,
                    "source_size_bytes": int(file_record["size_bytes"]),
                    "source_mtime_ns": int(file_record["mtime_ns"]),
                }
            )
        for subtitle in linked_by_video.get(int(file_record["id"]), []):
            language = subtitle.get("language")
            suffix = LANGUAGE_SUFFIXES.get(str(language), "und")
            extension = Path(subtitle["relative_path"]).suffix
            subtitle_parent = target_parent
            counter = 1
            while True:
                discriminator = suffix if counter == 1 else f"{suffix}.{counter}"
                subtitle_name = f"{subtitle_base_name}.{discriminator}{extension}"
                subtitle_target = (
                    f"{subtitle_parent}/{subtitle_name}"
                    if subtitle_parent not in {"", "."}
                    else subtitle_name
                )
                subtitle_key = subtitle_target.casefold()
                if subtitle_key not in targets:
                    break
                counter += 1
            subtitle_root_key = (
                subtitle["root_key"] if target_mode == "in_place" else "releases"
            )
            if (
                subtitle_target.casefold() == subtitle["relative_path"].casefold()
                and subtitle_root_key == subtitle["root_key"]
            ):
                continue
            targets.add(subtitle_key)
            if not language:
                warnings.append(f"{subtitle['relative_path']}: 字幕语言未知")
            operations.append(
                {
                    "file_id": int(subtitle["id"]),
                    "file_role": "subtitle",
                    "source_root_key": subtitle["root_key"],
                    "source_relative_path": subtitle["relative_path"],
                    "target_root_key": subtitle_root_key,
                    "target_relative_path": subtitle_target,
                    "source_size_bytes": int(subtitle["size_bytes"]),
                    "source_mtime_ns": int(subtitle["mtime_ns"]),
                    "video_file_id": int(file_record["id"]),
                }
            )
    return operations, warnings


def create_preview(
    settings: Settings,
    database: Database,
    project_id: int,
    *,
    template: str | None = None,
    target_mode: str | None = None,
    selected_file_ids: list[int] | None = None,
) -> RenamePreview:
    project = database.get_release_project(project_id)
    if not project:
        raise NamingError("项目不存在")
    chosen_template = (
        template
        or project.get("naming_template")
        or ((database.get_naming_profile(project["naming_profile_id"]) or {}).get("template"))
    )
    if not chosen_template:
        profile_type = "movie" if project["media_type"] in {"movie", "single"} else "series"
        profile = next(
            (
                item
                for item in database.list_naming_profiles()
                if item["media_type"] == profile_type and item["is_default"]
            ),
            None,
        )
        chosen_template = profile["template"] if profile else "{title}"
    parse_naming_template(chosen_template)
    files = database.list_release_files(project_id, include_missing=False)
    links = database.list_release_file_links(project_id)
    operations, warnings = _preview_payload(
        project,
        files,
        links,
        template=chosen_template,
        target_mode=target_mode or project.get("last_rename_target") or "in_place",
        selected_file_ids=set(selected_file_ids or []),
    )
    if not operations:
        warnings.append("当前命名结果与源文件名一致，没有需要执行的变更")
    preview = RenamePreview(
        token=secrets.token_urlsafe(24),
        project_id=project_id,
        target_mode=target_mode or project.get("last_rename_target") or "in_place",
        batch_id=secrets.token_hex(12),
        operations=operations,
        warnings=warnings,
    )
    return preview


def _stat_path(path: Path) -> tuple[int, int]:
    return path_state(path)


def _path_occupied(path: Path) -> bool:
    return os.path.lexists(path)


def verify_operations(
    settings: Settings, operations: list[dict[str, Any]]
) -> None:
    targets: set[tuple[str, str]] = set()
    for operation in operations:
        source, source_relative = resolve_workspace_path(
            settings,
            operation["source_root_key"],
            operation["source_relative_path"],
            must_exist=True,
            allow_empty=False,
            follow_final_symlink=False,
        )
        target, target_relative = resolve_workspace_path(
            settings,
            operation["target_root_key"],
            operation["target_relative_path"],
            must_exist=False,
            allow_empty=False,
            follow_final_symlink=False,
        )
        if (
            source_relative == target_relative
            and operation["source_root_key"] == operation["target_root_key"]
        ):
            continue
        if not source.exists():
            raise NamingError(f"源文件不存在：{source_relative}")
        size, mtime_ns = _stat_path(source)
        if size != int(operation["source_size_bytes"]) or mtime_ns != int(
            operation["source_mtime_ns"]
        ):
            raise NamingError(f"源文件在预览后发生变化：{source_relative}")
        if _path_occupied(target):
            raise NamingError(f"目标已存在：{target_relative}")
        key = (operation["target_root_key"], target_relative.casefold())
        if key in targets:
            raise NamingError(f"目标路径重名：{target_relative}")
        targets.add(key)


def _move_path(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.move(str(source), str(target))


def _update_project_source_path(
    database: Database,
    project_id: int,
    *,
    root_key: str,
    relative_path: str,
) -> None:
    project = database.get_release_project(project_id)
    if not project:
        return
    metadata = dict(project.get("metadata") or {})
    metadata["root_key"] = root_key
    metadata["source_path"] = relative_path
    database.update_release_project(
        project_id,
        download_dir=relative_path,
        metadata_json=json.dumps(metadata, ensure_ascii=False),
    )


def apply_preview(
    settings: Settings,
    database: Database,
    preview: RenamePreview,
    *,
    task_id: int,
    log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    project = database.get_release_project(preview.project_id)
    if not project:
        raise NamingError("项目不存在")
    verify_operations(settings, preview.operations)
    applied = 0
    for operation in preview.operations:
        if should_cancel and should_cancel():
            raise NamingError("任务已取消")
        source, source_relative = resolve_workspace_path(
            settings,
            operation["source_root_key"],
            operation["source_relative_path"],
            must_exist=True,
            allow_empty=False,
            follow_final_symlink=False,
        )
        target, target_relative = resolve_workspace_path(
            settings,
            operation["target_root_key"],
            operation["target_relative_path"],
            must_exist=False,
            allow_empty=False,
            follow_final_symlink=False,
        )
        if (
            source_relative == target_relative
            and operation["source_root_key"] == operation["target_root_key"]
        ):
            continue
        size, mtime_ns = _stat_path(source)
        if size != int(operation["source_size_bytes"]) or mtime_ns != int(
            operation["source_mtime_ns"]
        ):
            raise NamingError(f"源文件在任务开始后发生变化：{source_relative}")
        if _path_occupied(target):
            raise NamingError(f"目标已存在：{target_relative}")
        _move_path(source, target)
        target_size, target_mtime = _stat_path(target)
        operation_id = database.create_rename_operation(
            batch_id=preview.batch_id,
            task_id=task_id,
            project_id=preview.project_id,
            file_id=operation.get("file_id"),
            source_root_key=operation["source_root_key"],
            source_relative_path=source_relative,
            target_root_key=operation["target_root_key"],
            target_relative_path=target_relative,
            source_size_bytes=int(operation["source_size_bytes"]),
            source_mtime_ns=int(operation["source_mtime_ns"]),
        )
        database.update_rename_operation(
            operation_id,
            target_size_bytes=target_size,
            target_mtime_ns=target_mtime,
        )
        if operation.get("file_id"):
            database.update_release_file_location(
                int(operation["file_id"]),
                root_key=operation["target_root_key"],
                relative_path=target_relative,
                size_bytes=target_size,
                mtime_ns=target_mtime,
            )
        applied += 1
        if log:
            log(f"已重命名：{source_relative} -> {target_relative}")
    metadata = project.get("metadata") or {}
    original_root = metadata.get("root_key")
    original_path = metadata.get("source_path") or project.get("download_dir")
    for operation in preview.operations:
        if (
            operation["source_root_key"] == original_root
            and operation["source_relative_path"] == original_path
        ):
            _update_project_source_path(
                database,
                preview.project_id,
                root_key=operation["target_root_key"],
                relative_path=operation["target_relative_path"],
            )
            break
    database.update_release_project(
        preview.project_id,
        last_rename_target=preview.target_mode,
        work_dir=(
            PurePosixPath(preview.operations[0]["target_relative_path"])
            .parent.as_posix()
            if preview.operations
            else project.get("work_dir", "")
        ),
    )
    return {"batch_id": preview.batch_id, "applied": applied, "target_mode": preview.target_mode}


def rollback_latest(
    settings: Settings,
    database: Database,
    project_id: int,
    *,
    log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    batch_id = database.latest_rename_batch(project_id)
    if not batch_id:
        raise NamingError("没有可回滚的重命名批次")
    operations = database.list_rename_operations(project_id, batch_id=batch_id)
    if not operations or any(item["status"] != "applied" for item in operations):
        raise NamingError("最近批次状态不完整，不能回滚")
    checks: list[tuple[dict[str, Any], Path, Path]] = []
    for operation in operations:
        target, target_relative = resolve_workspace_path(
            settings,
            operation["target_root_key"],
            operation["target_relative_path"],
            must_exist=True,
            allow_empty=False,
            follow_final_symlink=False,
        )
        source, source_relative = resolve_workspace_path(
            settings,
            operation["source_root_key"],
            operation["source_relative_path"],
            must_exist=False,
            allow_empty=False,
            follow_final_symlink=False,
        )
        if _path_occupied(source):
            raise NamingError(f"回滚源位置已被占用：{source_relative}")
        size, mtime_ns = _stat_path(target)
        if (
            operation.get("target_size_bytes") is not None
            and int(operation["target_size_bytes"]) != size
        ) or (
            operation.get("target_mtime_ns") is not None
            and int(operation["target_mtime_ns"]) != mtime_ns
        ):
            raise NamingError(f"目标文件在重命名后发生变化：{target_relative}")
        checks.append((operation, target, source))
    for operation, target, source in reversed(checks):
        if should_cancel and should_cancel():
            raise NamingError("任务已取消")
        _move_path(target, source)
        size, mtime_ns = _stat_path(source)
        database.update_rename_operation(
            int(operation["id"]),
            status="rolled_back",
            rolled_back_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
            target_size_bytes=size,
            target_mtime_ns=mtime_ns,
        )
        if operation.get("file_id"):
            database.update_release_file_location(
                int(operation["file_id"]),
                root_key=operation["source_root_key"],
                relative_path=operation["source_relative_path"],
                size_bytes=size,
                mtime_ns=mtime_ns,
            )
        if log:
            log(
                f"已回滚：{operation['target_relative_path']} -> "
                f"{operation['source_relative_path']}"
            )
    project = database.get_release_project(project_id)
    if project:
        metadata = project.get("metadata") or {}
        current_root = metadata.get("root_key")
        current_path = metadata.get("source_path") or project.get("download_dir")
        for operation in operations:
            if (
                operation["target_root_key"] == current_root
                and operation["target_relative_path"] == current_path
            ):
                _update_project_source_path(
                    database,
                    project_id,
                    root_key=operation["source_root_key"],
                    relative_path=operation["source_relative_path"],
                )
                break
    return {"batch_id": batch_id, "rolled_back": len(checks)}
