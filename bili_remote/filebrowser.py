from __future__ import annotations

import asyncio
import ctypes
import errno
import hashlib
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from .config import Settings
from .media_probe import EMPTY_MEDIA, probe_media_file
from .naming import TEMPLATE_VARIABLES
from .workspace import (
    VIDEO_EXTENSIONS,
    WorkspaceError,
    browse_directory,
    normalize_relative_path,
    parse_episode_details,
    resolve_workspace_path,
)


ROOT_LABELS = {
    "downloads": "资源下载目录",
    "torrents": "种子目录",
    "images": "图片目录",
    "release_info": "发布信息目录",
}
_TEMPLATE_FIELD = re.compile(
    r"\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?::(?P<format>0(?P<width>[1-9]\d?)d))?\}"
)
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_MEDIA_FIELDS = {"resolution", "video_codec", "audio_codec", "hdr"}
_PER_FILE_VALUE_FIELDS = TEMPLATE_VARIABLES - {"episode", "ext", "original_name"}
_EPISODE_DIRECTIONS = {"increment", "decrement"}
_EPISODE_SCOPES = {"all", "detected", "missing"}
_FILE_SORTS = {"natural", "name", "name_desc", "mtime", "mtime_desc", "selection"}
_OMIT_IF_EMPTY = {"title_zh", "title_en", "title", "year", "group", "hdr"}
_OMIT_SENTINEL = "\x00OMIT\x00"


def _natural_name_key(name: str) -> tuple[tuple[int, Any], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", name)
        if part
    )


def _ordered_file_names(
    files: list[str], *, sort_mode: str, directory_path: Path
) -> list[str]:
    if sort_mode == "selection":
        return list(files)
    if sort_mode in {"name", "name_desc"}:
        ordered = sorted(files, key=str.casefold)
        return list(reversed(ordered)) if sort_mode == "name_desc" else ordered
    if sort_mode == "natural":
        return sorted(files, key=_natural_name_key)

    def modified_key(name: str) -> tuple[int, tuple[tuple[int, Any], ...]]:
        try:
            modified = (directory_path / name).lstat().st_mtime_ns
        except OSError:
            modified = 0
        return modified, _natural_name_key(name)

    ordered = sorted(files, key=modified_key)
    return list(reversed(ordered)) if sort_mode == "mtime_desc" else ordered


class FileRenameError(ValueError):
    pass


def browser_roots(
    settings: Settings, root_paths: dict[str, Path] | None = None
) -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    for key, label in ROOT_LABELS.items():
        path = (root_paths or settings.file_browser_roots)[key]
        try:
            path.mkdir(parents=True, exist_ok=True)
            resolved = path.resolve()
        except OSError as exc:
            raise WorkspaceError(f"无法创建或读取{label}：{exc}") from exc
        roots.append(
            {
                "key": key,
                "label": label,
                "path": str(resolved),
                "writable": resolved.is_dir() and (resolved.stat().st_mode & 0o200) != 0,
            }
        )
    return roots


def browse_files(
    settings: Settings,
    *,
    root_key: str,
    relative_path: str = "",
    page: int = 1,
    page_size: int | None = None,
    roots: dict[str, Path] | None = None,
) -> dict[str, Any]:
    result = browse_directory(
        settings,
        root_key=root_key,
        relative_path=relative_path,
        page=page,
        page_size=page_size,
        roots=roots or settings.file_browser_roots,
    )
    result["root_label"] = ROOT_LABELS[root_key]
    return result


async def analyze_file_rename(
    settings: Settings,
    *,
    root_key: str,
    directory: str,
    files: list[str],
    roots: dict[str, Path] | None = None,
) -> dict[str, Any]:
    if not files:
        raise WorkspaceError("请至少选择一个文件")
    if len(files) > 1000:
        raise WorkspaceError("单次最多分析 1000 个文件")
    directory_path, normalized_directory = resolve_workspace_path(
        settings,
        root_key,
        directory,
        must_exist=True,
        roots=roots or settings.file_browser_roots,
    )
    if not directory_path.is_dir():
        raise WorkspaceError("所选目录不存在或不是目录")
    root = (roots or settings.file_browser_roots)[root_key].resolve()
    items: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    for raw_name in files:
        parsed = parse_episode_details(raw_name)
        item: dict[str, Any] = {
            "name": raw_name,
            "original_name": raw_name,
            "extension": Path(raw_name).suffix.lower().lstrip("."),
            "season": parsed["season"],
            "episode": parsed["episode"],
            "episode_candidates": parsed["episode_candidates"],
            "episode_ambiguous": parsed["episode_ambiguous"],
            "season_ambiguous": parsed["season_ambiguous"],
            "media": dict(EMPTY_MEDIA),
            "status": "failed",
            "error": None,
        }
        try:
            name = normalize_relative_path(raw_name, allow_empty=False)
            if "/" in name or name != PurePosixPath(name).name:
                raise WorkspaceError("只能分析当前目录中的文件")
            if name in seen_sources:
                raise WorkspaceError("文件被重复选择")
            seen_sources.add(name)
            item["name"] = name
            item["original_name"] = name
            item["extension"] = Path(name).suffix.lower().lstrip(".")
            source = directory_path / name
            if not os.path.lexists(source):
                raise WorkspaceError("源文件不存在，请刷新目录")
            if source.is_dir():
                raise WorkspaceError("当前工具只分析文件，不处理目录")
            resolved_source = source.resolve(strict=True)
            if resolved_source != root and not resolved_source.is_relative_to(root):
                raise WorkspaceError("路径超出工作根目录")
            source.lstat()
        except (OSError, WorkspaceError) as exc:
            item["error"] = str(exc) or "无法读取源文件状态"
            items.append(item)
            continue
        if source.suffix.lower() not in VIDEO_EXTENSIONS:
            item["status"] = "skipped"
            item["error"] = "非视频文件，未执行媒体探测"
            items.append(item)
            continue
        item["status"] = "pending"
        item["path"] = source
        items.append(item)

    semaphore = asyncio.Semaphore(4)

    async def probe(item: dict[str, Any]) -> None:
        async with semaphore:
            result = await asyncio.to_thread(
                probe_media_file, settings, item.pop("path")
            )
        item.update(result)

    await asyncio.gather(*(probe(item) for item in items if "path" in item))
    recognized_seasons = sorted(
        {
            int(item["season"])
            for item in items
            if isinstance(item.get("season"), int)
        }
    )
    return {
        "root_key": root_key,
        "directory": normalized_directory,
        "items": items,
        "seasons": recognized_seasons,
        "season_suggestion": recognized_seasons[0] if len(recognized_seasons) == 1 else None,
        "mixed_seasons": len(recognized_seasons) > 1,
    }


def _parse_template(template: str) -> tuple[list[str], bool]:
    value = (template or "").strip()
    if not value:
        raise WorkspaceError("命名模板不能为空")
    if "/" in value or "\\" in value:
        raise WorkspaceError("命名模板不能包含路径分隔符")
    names: list[str] = []
    position = 0
    for match in _TEMPLATE_FIELD.finditer(value):
        prefix = value[position : match.start()]
        if "{" in prefix or "}" in prefix:
            raise WorkspaceError("命名模板包含未闭合或不支持的花括号")
        name = match.group("name")
        if name not in TEMPLATE_VARIABLES:
            raise WorkspaceError(f"未知命名变量：{name}")
        if match.group("width") and name not in {"season", "episode"}:
            raise WorkspaceError("只有 {season} 和 {episode} 支持 :02d 格式")
        names.append(name)
        position = match.end()
    if "{" in value[position:] or "}" in value[position:]:
        raise WorkspaceError("命名模板包含未闭合或不支持的花括号")
    return names, "ext" in names


def _invalid_filename(name: str) -> str | None:
    if not name or name in {".", ".."}:
        return "文件名不能为空"
    if _INVALID_FILENAME.search(name):
        return "文件名包含非法字符（< > : \" / \\ | ? * 或控制字符）"
    if name != name.strip() or name.endswith("."):
        return "文件名不能以空格或句点结尾"
    if len(name.encode("utf-8")) > 255:
        return "文件名超过 255 字节"
    if name.split(".", 1)[0].upper() in _RESERVED_NAMES:
        return "文件名是系统保留名称"
    return None


def _render_name(
    template: str,
    *,
    values: dict[str, str],
    original_name: str,
    extension: str,
    episode: int | None,
    has_ext: bool,
) -> tuple[str, list[str]]:
    warnings: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name == "original_name":
            value: Any = original_name
        elif name == "ext":
            value = extension
        elif name == "episode":
            value = episode
        else:
            value = values.get(name, "")
        if name in _OMIT_IF_EMPTY and (value is None or str(value).strip() == ""):
            return _OMIT_SENTINEL
        if value is None or str(value).strip() == "":
            raise WorkspaceError(f"未填写模板变量 {{{name}}}")
        if match.group("width"):
            try:
                return str(int(value)).zfill(int(match.group("width")))
            except (TypeError, ValueError) as exc:
                raise WorkspaceError(f"模板变量 {{{name}}} 必须是数字") from exc
        return str(value)

    rendered = _TEMPLATE_FIELD.sub(replace, template).strip()
    if _OMIT_SENTINEL in rendered:
        rendered = re.sub(
            rf"([._ -]?){re.escape(_OMIT_SENTINEL)}([._ -]?)",
            lambda match: next(
                (separator for separator in (".", "_", " ") if separator in match.groups()),
                match.group(1) or match.group(2) or "",
            ),
            rendered,
        )
        rendered = re.sub(r"([._ -])\1+", r"\1", rendered)
    if has_ext and extension and rendered.casefold().endswith(f".{extension.casefold()}"):
        stem = rendered[: -(len(extension) + 1)].strip(" ._-")
        if not stem:
            raise WorkspaceError("模板变量均为空，无法生成文件名")
        rendered = f"{stem}.{extension}"
    else:
        rendered = rendered.strip(" ._-")
        if not rendered or (has_ext and rendered.casefold() == extension.casefold()):
            raise WorkspaceError("模板变量均为空，无法生成文件名")
    if not has_ext and extension and not rendered.casefold().endswith(f".{extension.casefold()}"):
        rendered = f"{rendered}.{extension}"
        warnings.append("模板未包含 {ext}，已保留原文件扩展名")
    return rendered, warnings


def _source_relative_path(directory: str, name: str) -> str:
    return f"{directory}/{name}" if directory else name


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def preview_file_rename(
    settings: Settings,
    *,
    root_key: str,
    directory: str,
    files: list[str],
    template: str,
    episode_mode: str = "detected",
    start_episode: int = 1,
    episode_step: int = 1,
    episode_offset: int = 0,
    episode_direction: str = "increment",
    episode_scope: str = "all",
    file_sort: str | None = None,
    episode_overrides: dict[str, Any] | None = None,
    media: dict[str, dict[str, Any]] | None = None,
    file_values: dict[str, dict[str, Any]] | None = None,
    values: dict[str, Any] | None = None,
    manual_names: dict[str, str] | None = None,
    root_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    if episode_mode not in {"detected", "start", "offset"}:
        raise WorkspaceError("不支持的集数修正模式")
    if start_episode < 0:
        raise WorkspaceError("起始集数不能为负数")
    if episode_step < 1:
        raise WorkspaceError("集数递增必须大于 0")
    if episode_direction not in _EPISODE_DIRECTIONS:
        raise WorkspaceError("不支持的集数方向")
    if episode_scope not in _EPISODE_SCOPES:
        raise WorkspaceError("不支持的集数作用范围")
    if file_sort is not None and file_sort not in _FILE_SORTS:
        raise WorkspaceError("不支持的文件排序方式")
    if not files:
        raise WorkspaceError("请至少选择一个文件")
    if len(files) > 1000:
        raise WorkspaceError("单次最多重命名 1000 个文件")

    normalized_values = {
        key: str(value).strip()
        for key, value in (values or {}).items()
        if key in TEMPLATE_VARIABLES and value is not None
    }
    overrides = {
        key: str(value).strip()
        for key, value in (manual_names or {}).items()
        if str(value).strip()
    }
    unknown_overrides = set(overrides) - set(files)
    if unknown_overrides:
        raise WorkspaceError("逐文件名称与所选文件不匹配")
    episode_values = {
        key: value
        for key, value in (episode_overrides or {}).items()
        if value is not None
    }
    unknown_episode_overrides = set(episode_values) - set(files)
    if unknown_episode_overrides:
        raise WorkspaceError("逐文件集数与所选文件不匹配")
    media_values: dict[str, dict[str, str]] = {}
    unknown_media_files = set(media or {}) - set(files)
    if unknown_media_files:
        raise WorkspaceError("逐文件媒体参数与所选文件不匹配")
    for file_name, file_media in (media or {}).items():
        if not isinstance(file_media, dict):
            raise WorkspaceError("逐文件媒体参数格式无效")
        media_values[file_name] = {
            key: str(value).strip()
            for key, value in file_media.items()
            if key in _MEDIA_FIELDS and value is not None
        }
    per_file_values: dict[str, dict[str, str]] = {}
    unknown_value_files = set(file_values or {}) - set(files)
    if unknown_value_files:
        raise WorkspaceError("逐文件命名参数与所选文件不匹配")
    for file_name, file_value_map in (file_values or {}).items():
        if not isinstance(file_value_map, dict):
            raise WorkspaceError("逐文件命名参数格式无效")
        unknown_fields = set(file_value_map) - _PER_FILE_VALUE_FIELDS
        if unknown_fields:
            raise WorkspaceError(
                f"逐文件命名参数包含不支持的字段：{', '.join(sorted(unknown_fields))}"
            )
        per_file_values[file_name] = {
            key: str(value).strip()
            for key, value in file_value_map.items()
            if value is not None
        }

    template_names, has_ext = _parse_template(template)
    directory_path, normalized_directory = resolve_workspace_path(
        settings,
        root_key,
        directory,
        must_exist=True,
        roots=root_paths or settings.file_browser_roots,
    )
    if not directory_path.is_dir():
        raise WorkspaceError("所选目录不存在或不是目录")
    root = (root_paths or settings.file_browser_roots)[root_key].resolve()
    with os.scandir(directory_path) as entries:
        existing_names = {entry.name.casefold() for entry in entries}

    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    operations: list[dict[str, Any]] = []
    source_states: list[dict[str, Any]] = []
    target_names: set[str] = set()
    seen_sources: set[str] = set()

    effective_file_sort = file_sort or (
        "natural" if episode_mode == "start" else "selection"
    )
    ordered_files = _ordered_file_names(
        files, sort_mode=effective_file_sort, directory_path=directory_path
    )
    sequence_index = 0
    for raw_name in ordered_files:
        try:
            name = normalize_relative_path(raw_name, allow_empty=False)
        except WorkspaceError as exc:
            errors.append({"file": raw_name, "message": str(exc)})
            continue
        if "/" in name or name != PurePosixPath(name).name:
            errors.append({"file": raw_name, "message": "只能重命名当前目录中的文件"})
            continue
        if name in seen_sources:
            errors.append({"file": name, "message": "文件被重复选择"})
            continue
        seen_sources.add(name)

        source = directory_path / name
        if not os.path.lexists(source):
            errors.append({"file": name, "message": "源文件不存在，请刷新目录"})
            continue
        if source.is_dir():
            errors.append(
                {"file": name, "message": "当前工具只重命名文件，不处理目录"}
            )
            continue
        try:
            resolved_source = source.resolve(strict=True)
            if resolved_source != root and not resolved_source.is_relative_to(root):
                raise OSError("路径超出工作根目录")
            stat = source.lstat()
        except OSError:
            errors.append({"file": name, "message": "无法读取源文件状态"})
            continue

        parsed = parse_episode_details(name)
        detected_episode = parsed["episode"]
        detected_season = parsed["season"]
        final_episode: int | None = None
        episode_origin = "detected" if detected_episode is not None else "unknown"
        if name in episode_values:
            raw_episode = str(episode_values[name]).strip()
            if raw_episode:
                try:
                    final_episode = int(raw_episode)
                except (TypeError, ValueError):
                    errors.append({"file": name, "message": "最终集数必须是整数"})
                    continue
            episode_origin = "manual"
        elif episode_mode == "detected":
            final_episode = detected_episode
        else:
            applies = (
                episode_scope == "all"
                or (episode_scope == "detected" and detected_episode is not None)
                or (episode_scope == "missing" and detected_episode is None)
            )
            if not applies:
                final_episode = detected_episode
            elif episode_mode == "start":
                direction = 1 if episode_direction == "increment" else -1
                final_episode = start_episode + sequence_index * episode_step * direction
                sequence_index += 1
                episode_origin = "sequence"
            elif detected_episode is not None:
                final_episode = detected_episode + episode_offset
                episode_origin = "offset"
        if final_episode is not None and final_episode < 0:
            errors.append({"file": name, "message": "最终集数不能为负数"})
            continue

        extension = source.suffix.lstrip(".")
        manual_name = overrides.get(name)
        render_values = dict(normalized_values)
        if "season" in template_names and not render_values.get("season"):
            render_values["season"] = "01"
        render_values.update(per_file_values.get(name, {}))
        render_values.update(
            {
                key: value
                for key, value in media_values.get(name, {}).items()
                if value
            }
        )
        file_media = {
            key: render_values.get(key, "") for key in sorted(_MEDIA_FIELDS)
        }
        try:
            if manual_name:
                target_name = manual_name
                file_warnings: list[str] = []
            else:
                target_name, file_warnings = _render_name(
                    template,
                    values=render_values,
                    original_name=source.stem,
                    extension=extension,
                    episode=final_episode,
                    has_ext=has_ext,
                )
        except WorkspaceError as exc:
            errors.append({"file": name, "message": str(exc)})
            continue

        filename_error = _invalid_filename(target_name)
        if filename_error:
            errors.append({"file": name, "message": filename_error})
        target_key = target_name.casefold()
        if target_key in target_names:
            errors.append(
                {"file": name, "message": f"多个文件将生成相同目标：{target_name}"}
            )
        target_names.add(target_key)

        changed = target_name != name
        if changed and target_key in existing_names:
            errors.append(
                {"file": name, "message": f"目标已存在或仅大小写冲突：{target_name}"}
            )
        elif changed and os.path.lexists(directory_path / target_name):
            errors.append({"file": name, "message": f"目标已存在：{target_name}"})

        for warning in file_warnings:
            message = f"{name}: {warning}"
            if message not in warnings:
                warnings.append(message)
        operations.append(
            {
                "source_name": name,
                "source_path": _source_relative_path(normalized_directory, name),
                "target_name": target_name,
                "target_path": _source_relative_path(normalized_directory, target_name),
                "detected_season": detected_season,
                "detected_episode": detected_episode,
                "episode_candidates": parsed["episode_candidates"],
                "episode_ambiguous": parsed["episode_ambiguous"],
                "season_ambiguous": parsed["season_ambiguous"],
                "final_episode": final_episode,
                "episode": final_episode,
                "episode_origin": episode_origin,
                "final_season": render_values.get("season"),
                "source": render_values.get("source", ""),
                "extension": extension,
                "media": file_media,
                "changed": changed,
                "manual": bool(manual_name),
            }
        )
        source_states.append(
            {
                "name": name,
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
                "inode": int(stat.st_ino),
            }
        )

    digest = _fingerprint(
        {
            "root_key": root_key,
            "directory": normalized_directory,
            "template": template,
            "episode_mode": episode_mode,
            "start_episode": start_episode,
            "episode_step": episode_step,
            "episode_offset": episode_offset,
            "episode_direction": episode_direction,
            "episode_scope": episode_scope,
            "file_sort": effective_file_sort,
            "episode_overrides": episode_values,
            "media": media_values,
            "file_values": per_file_values,
            "values": normalized_values,
            "manual_names": overrides,
            "sources": source_states,
            "operations": [
                [
                    item["source_name"],
                    item["target_name"],
                    item["final_episode"],
                    item["media"],
                ]
                for item in operations
            ],
        }
    )
    return {
        "root_key": root_key,
        "directory": normalized_directory,
        "operations": operations,
        "errors": errors,
        "warnings": warnings,
        "valid": not errors,
        "changed_count": sum(1 for item in operations if item["changed"]),
        "fingerprint": digest,
    }


_LIBC = ctypes.CDLL(None, use_errno=True) if sys.platform.startswith("linux") else None


def _rename_no_replace(source: Path, target: Path) -> None:
    if _LIBC is not None:
        renameat2 = getattr(_LIBC, "renameat2", None)
        if renameat2 is not None:
            result = renameat2(
                _AT_FDCWD,
                os.fsencode(source),
                _AT_FDCWD,
                os.fsencode(target),
                _RENAME_NOREPLACE,
            )
            if result == 0:
                return
            error_number = ctypes.get_errno()
            if error_number not in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
                raise OSError(error_number, os.strerror(error_number), str(target))
    if os.path.lexists(target):
        raise FileExistsError(errno.EEXIST, "目标已存在", str(target))
    os.rename(source, target)


def apply_file_rename(
    settings: Settings,
    *,
    root_key: str,
    directory: str,
    files: list[str],
    template: str,
    episode_mode: str = "detected",
    start_episode: int = 1,
    episode_step: int = 1,
    episode_offset: int = 0,
    episode_direction: str = "increment",
    episode_scope: str = "all",
    file_sort: str | None = None,
    episode_overrides: dict[str, Any] | None = None,
    media: dict[str, dict[str, Any]] | None = None,
    file_values: dict[str, dict[str, Any]] | None = None,
    values: dict[str, Any] | None = None,
    manual_names: dict[str, str] | None = None,
    fingerprint: str,
    root_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    plan = preview_file_rename(
        settings,
        root_key=root_key,
        directory=directory,
        files=files,
        template=template,
        episode_mode=episode_mode,
        start_episode=start_episode,
        episode_step=episode_step,
        episode_offset=episode_offset,
        episode_direction=episode_direction,
        episode_scope=episode_scope,
        file_sort=file_sort,
        episode_overrides=episode_overrides,
        media=media,
        file_values=file_values,
        values=values,
        manual_names=manual_names,
        root_paths=root_paths,
    )
    if not plan["valid"]:
        details = "；".join(item["message"] for item in plan["errors"])
        raise FileRenameError(f"重命名检查失败：{details}")
    if plan["changed_count"] == 0:
        raise FileRenameError("没有需要重命名的文件")
    if fingerprint != plan["fingerprint"]:
        raise FileRenameError("文件状态或重命名参数已变化，请重新预览")

    directory_path, normalized_directory = resolve_workspace_path(
        settings,
        root_key,
        directory,
        must_exist=True,
        roots=root_paths or settings.file_browser_roots,
    )
    changed = [item for item in plan["operations"] if item["changed"]]
    applied: list[dict[str, Any]] = []
    try:
        for operation in changed:
            source = directory_path / operation["source_name"]
            target = directory_path / operation["target_name"]
            _rename_no_replace(source, target)
            applied.append(operation)
    except OSError as exc:
        rollback_failures = 0
        for operation in reversed(applied):
            try:
                _rename_no_replace(
                    directory_path / operation["target_name"],
                    directory_path / operation["source_name"],
                )
            except OSError:
                rollback_failures += 1
        suffix = (
            "，部分操作未能回滚，请刷新目录检查"
            if rollback_failures
            else "，已回滚本次操作"
        )
        raise FileRenameError(f"重命名失败：{exc}{suffix}") from exc

    return {
        "root_key": root_key,
        "directory": normalized_directory,
        "renamed": changed,
        "renamed_count": len(changed),
    }
