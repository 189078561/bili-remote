from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .config import Settings
from .database import Database


VIDEO_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".m2ts", ".ts", ".mts", ".mov", ".wmv", ".flv", ".webm"
}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".pgs", ".sup", ".sub"}
ISO_EXTENSIONS = {".iso"}
BDMV_DIRECTORY = "BDMV"

_EPISODE_PATTERNS = (
    (
        "SxxExx",
        re.compile(
            r"(?i)(?:^|[^a-z0-9])s(?P<season>\d{1,3})[ ._-]*"
            r"ep?(?P<episode>\d{1,4})(?:[^0-9]|$)"
        ),
    ),
    (
        "Sxx - yy",
        re.compile(
            r"(?i)(?:^|[^a-z0-9])s(?P<season>\d{1,3})\s*[-–—]\s*"
            r"(?P<episode>\d{1,4})(?:[^0-9]|$)"
        ),
    ),
    (
        "第x季第y集",
        re.compile(
            r"第\s*(?P<season>\d{1,3})\s*季[^\d]{0,20}"
            r"第\s*(?P<episode>\d{1,4})\s*[集话話]"
        ),
    ),
    (
        "第y集",
        re.compile(r"第\s*(?P<episode>\d{1,4})\s*[集话話]"),
    ),
    (
        "NxM",
        re.compile(
            r"(?i)(?<!\d)(?P<season>\d{1,2})x(?P<episode>\d{1,4})(?!\d)"
        ),
    ),
    (
        "Exx",
        re.compile(r"(?i)(?:^|[^a-z0-9])ep?(?P<episode>\d{1,4})(?:[^0-9]|$)"),
    ),
)
_SEASON_ONLY_PATTERN = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:s|season)\s*(?P<season>\d{1,2})(?:[^0-9]|$)"
)
_CHINESE_SEASON_PATTERN = re.compile(r"第\s*(?P<season>\d{1,3})\s*季")
_BRACKET_TAG = re.compile(r"[\[【(（][^\]】)）]{1,40}[\]】)）]")
_LANGUAGE_PATTERNS = (
    ("zh-Hans", re.compile(r"(?i)(?:^|[._\-\s])(?:chs|sc|zh-cn|zh-hans|简|简体)(?:$|[._\-\s])")),
    ("zh-Hant", re.compile(r"(?i)(?:^|[._\-\s])(?:cht|tc|zh-tw|zh-hant|繁|繁体)(?:$|[._\-\s])")),
    ("ja", re.compile(r"(?i)(?:^|[._\-\s])(?:jpn|jp|ja|japanese|日语|日語)(?:$|[._\-\s])")),
    ("en", re.compile(r"(?i)(?:^|[._\-\s])(?:eng|en|english|英语|英語)(?:$|[._\-\s])")),
    ("ko", re.compile(r"(?i)(?:^|[._\-\s])(?:kor|ko|korean|韩语|韓語)(?:$|[._\-\s])")),
)


class WorkspaceError(ValueError):
    pass


@dataclass(slots=True)
class ScanSummary:
    scanned: int
    supported: int
    unsupported: int
    missing: int
    warnings: list[str]
    counts: dict[str, int]


def normalize_relative_path(value: str | None, *, allow_empty: bool = True) -> str:
    raw = (value or "").strip().replace("\\", "/")
    if "\x00" in raw:
        raise WorkspaceError("路径包含非法字符")
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise WorkspaceError("只允许工作根目录内的相对路径")
    parts: list[str] = []
    for part in PurePosixPath(raw or ".").parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise WorkspaceError("路径不能包含上级目录")
        parts.append(part)
    result = "/".join(parts)
    if not result and not allow_empty:
        raise WorkspaceError("路径不能为空")
    return result


def root_path(
    settings: Settings,
    root_key: str,
    *,
    roots: Mapping[str, Path] | None = None,
) -> Path:
    root = (roots or settings.workspace_roots).get(root_key)
    if root is None:
        raise WorkspaceError("未知的工作目录")
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def resolve_workspace_path(
    settings: Settings,
    root_key: str,
    relative_path: str | None,
    *,
    must_exist: bool = False,
    allow_empty: bool = True,
    follow_final_symlink: bool = True,
    roots: Mapping[str, Path] | None = None,
) -> tuple[Path, str]:
    root = root_path(settings, root_key, roots=roots)
    relative = normalize_relative_path(relative_path, allow_empty=allow_empty)
    lexical = root / relative
    candidate = lexical.resolve(strict=False)
    if candidate != root and not candidate.is_relative_to(root):
        raise WorkspaceError("路径超出工作根目录")
    if must_exist and not lexical.exists():
        raise WorkspaceError("路径不存在")
    return (candidate if follow_final_symlink else lexical), relative


def _inside_root(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve(strict=False)
        return resolved == root or resolved.is_relative_to(root)
    except OSError:
        return False


def browse_directory(
    settings: Settings,
    *,
    root_key: str,
    relative_path: str = "",
    page: int = 1,
    page_size: int | None = None,
    roots: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    directory, relative = resolve_workspace_path(
        settings, root_key, relative_path, must_exist=True, roots=roots
    )
    if not directory.is_dir():
        raise WorkspaceError("浏览路径不是目录")
    limit = min(max(page_size or settings.workspace_browse_limit, 1), 1000)
    page = max(page, 1)
    items: list[dict[str, Any]] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                entry_path = Path(entry.path)
                try:
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                if entry.is_symlink():
                    is_dir = entry.is_dir(follow_symlinks=True)
                suffix = entry_path.suffix.lower()
                items.append(
                    {
                        "name": entry.name,
                        "relative_path": "/".join(
                            part for part in (relative, entry.name) if part
                        ),
                        "is_dir": is_dir,
                        "is_symlink": entry.is_symlink(),
                        "allowed": _inside_root(entry_path, directory),
                        "kind": (
                            "directory"
                            if is_dir
                            else "video"
                            if suffix in VIDEO_EXTENSIONS
                            else "subtitle"
                            if suffix in SUBTITLE_EXTENSIONS
                            else "iso"
                            if suffix in ISO_EXTENSIONS
                            else "other"
                        ),
                        "size_bytes": 0 if is_dir else stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    }
                )
    except OSError as exc:
        raise WorkspaceError(f"无法读取目录：{exc}") from exc
    items.sort(key=lambda item: (not item["is_dir"], str(item["name"]).casefold()))
    total = len(items)
    start = (page - 1) * limit
    return {
        "root_key": root_key,
        "relative_path": relative,
        "parent": "/".join(PurePosixPath(relative).parts[:-1]) if relative else None,
        "items": items[start : start + limit],
        "page": page,
        "page_size": limit,
        "total": total,
        "has_more": start + limit < total,
    }


def parse_episode_details(name: str) -> dict[str, Any]:
    stem = Path(name).stem
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[int | None, int]] = set()
    for pattern_name, pattern in _EPISODE_PATTERNS:
        for match in pattern.finditer(stem):
            values = match.groupdict()
            episode = int(values["episode"])
            season = int(values["season"]) if values.get("season") is not None else None
            # Bilibili-style names such as "S190 - 190" use the leading number as
            # an index, not a season. For normal season ranges such as "S01 - 02"
            # the two numbers differ and are interpreted as season/episode.
            if pattern_name == "Sxx - yy" and season is not None:
                if season == episode and season > 99:
                    season = None
            key = (season, episode)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "season": season,
                    "episode": episode,
                    "pattern": pattern_name,
                }
            )

    seasons = {item["season"] for item in candidates if item["season"] is not None}
    episodes = {item["episode"] for item in candidates}
    season = next(iter(seasons)) if len(seasons) == 1 else None
    episode = next(iter(episodes)) if len(episodes) == 1 else None
    return {
        "season": season,
        "episode": episode,
        "episode_candidates": candidates,
        "episode_ambiguous": len(episodes) > 1,
        "season_ambiguous": len(seasons) > 1,
    }


def parse_episode_metadata(name: str) -> dict[str, int | None]:
    parsed = parse_episode_details(name)
    return {"season": parsed["season"], "episode": parsed["episode"]}


def parse_media_name(name: str, *, season_hint: int | None = None) -> dict[str, Any]:
    stem = Path(name).stem
    parsed = parse_episode_metadata(name)
    season = parsed["season"] if parsed["season"] is not None else season_hint
    episode = parsed["episode"]
    if season is None:
        match = _SEASON_ONLY_PATTERN.search(stem)
        if match:
            season = int(match.group("season"))
        else:
            match = _CHINESE_SEASON_PATTERN.search(stem)
            if match:
                season = int(match.group("season"))
    cleaned = _BRACKET_TAG.sub(" ", stem)
    cleaned = re.sub(r"(?i)\bs\d{1,3}e\d{1,4}\b.*$", "", cleaned)
    cleaned = re.sub(r"第\s*\d{1,4}\s*[集话話].*$", "", cleaned)
    cleaned = re.sub(r"[._]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_")
    return {
        "title": cleaned or stem,
        "season": season,
        "episode": episode,
    }


def infer_subtitle_language(name: str) -> str | None:
    stem = Path(name).stem
    padded = f" {stem.lower()} "
    for language, pattern in _LANGUAGE_PATTERNS:
        if pattern.search(padded):
            return language
    return None


def _tree_size(path: Path) -> tuple[int, int]:
    total = 0
    latest = 0
    try:
        for current, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [item for item in dirs if item != BDMV_DIRECTORY or Path(current) == path]
            for filename in files:
                try:
                    stat = (Path(current) / filename).stat()
                except OSError:
                    continue
                total += stat.st_size
                latest = max(latest, stat.st_mtime_ns)
    except OSError:
        pass
    try:
        latest = max(latest, path.stat().st_mtime_ns)
    except OSError:
        pass
    return total, latest


def path_state(path: Path) -> tuple[int, int]:
    if path.is_dir():
        return _tree_size(path)
    stat = path.stat()
    return int(stat.st_size), int(stat.st_mtime_ns)


def discover_media(
    settings: Settings,
    root_key: str,
    relative_path: str,
    *,
    season_hint: int | None = None,
    max_files: int | None = None,
    max_depth: int | None = None,
) -> list[dict[str, Any]]:
    source, base_relative = resolve_workspace_path(
        settings, root_key, relative_path, must_exist=True
    )
    root = root_path(settings, root_key)
    try:
        source_parts = source.relative_to(root).parts
    except ValueError:
        source_parts = ()
    if BDMV_DIRECTORY in source_parts:
        bdmv_index = source_parts.index(BDMV_DIRECTORY)
        bdmv_path = root.joinpath(*source_parts[: bdmv_index + 1])
        bdmv_relative = bdmv_path.relative_to(root).as_posix()
        size, mtime_ns = _tree_size(bdmv_path)
        return [
            {
                "root_key": root_key,
                "relative_path": bdmv_relative,
                "file_role": "bdmv",
                "size_bytes": size,
                "mtime_ns": mtime_ns,
                "season": season_hint,
                "episode": None,
                "title": bdmv_path.parent.name,
                "language": None,
                "scan_result": {"directory": True},
            }
        ]
    limit = max_files or settings.workspace_scan_max_files
    depth_limit = max_depth or settings.workspace_scan_max_depth
    discovered: list[dict[str, Any]] = []
    seen = 0

    def add(path: Path, role: str, stat_path: Path | None = None) -> None:
        stat_source = stat_path or path
        stat = stat_source.stat()
        if source.is_file() and path == source:
            rel = base_relative
        else:
            rel = path.relative_to(source).as_posix()
            if base_relative:
                rel = f"{base_relative}/{rel}"
        parsed = parse_media_name(path.name, season_hint=season_hint)
        discovered.append(
            {
                "root_key": root_key,
                "relative_path": rel,
                "file_role": role,
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "season": parsed["season"],
                "episode": parsed["episode"],
                "title": parsed["title"],
                "language": infer_subtitle_language(path.name) if role == "subtitle" else None,
                "scan_result": {"extension": path.suffix.lower()},
            }
        )

    if source.is_file():
        suffix = source.suffix.lower()
        role = (
            "video"
            if suffix in VIDEO_EXTENSIONS
            else "subtitle"
            if suffix in SUBTITLE_EXTENSIONS
            else "iso"
            if suffix in ISO_EXTENSIONS
            else "other"
        )
        add(source, role)
        return discovered

    for current, dirs, files in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        rel_depth = len(current_path.relative_to(source).parts)
        if rel_depth > depth_limit:
            dirs[:] = []
            continue
        kept_dirs: list[str] = []
        for directory_name in sorted(dirs):
            directory = current_path / directory_name
            if directory_name == BDMV_DIRECTORY:
                if not _inside_root(directory, root):
                    continue
                seen += 1
                if seen > limit:
                    raise WorkspaceError(f"扫描文件数超过限制 {limit}")
                rel = directory.relative_to(source).as_posix()
                if base_relative:
                    rel = f"{base_relative}/{rel}"
                size, mtime_ns = _tree_size(directory)
                discovered.append(
                    {
                        "root_key": root_key,
                        "relative_path": rel,
                        "file_role": "bdmv",
                        "size_bytes": size,
                        "mtime_ns": mtime_ns,
                        "season": season_hint,
                        "episode": None,
                        "title": directory.parent.name,
                        "language": None,
                        "scan_result": {"directory": True},
                    }
                )
                continue
            if _inside_root(directory, root):
                kept_dirs.append(directory_name)
        dirs[:] = kept_dirs
        for filename in sorted(files):
            path = current_path / filename
            suffix = path.suffix.lower()
            role = (
                "video"
                if suffix in VIDEO_EXTENSIONS
                else "subtitle"
                if suffix in SUBTITLE_EXTENSIONS
                else "iso"
                if suffix in ISO_EXTENSIONS
                else None
            )
            if role is None:
                continue
            if not _inside_root(path, root):
                continue
            seen += 1
            if seen > limit:
                raise WorkspaceError(f"扫描文件数超过限制 {limit}")
            add(path, role)
    return discovered


def build_subtitle_links(files: list[dict[str, Any]]) -> list[tuple[int, int, str, float]]:
    videos = [item for item in files if item["file_role"] in {"video", "iso"}]
    subtitles = [item for item in files if item["file_role"] == "subtitle"]
    links: list[tuple[int, int, str, float]] = []
    for subtitle in subtitles:
        subtitle_parent = PurePosixPath(subtitle["relative_path"]).parent
        subtitle_stem = Path(subtitle["relative_path"]).stem.casefold()
        best: tuple[float, dict[str, Any]] | None = None
        for video in videos:
            video_parent = PurePosixPath(video["relative_path"]).parent
            if video_parent != subtitle_parent:
                continue
            video_stem = Path(video["relative_path"]).stem.casefold()
            score = 0.0
            if subtitle_stem == video_stem:
                score = 0.99
            elif subtitle_stem.startswith(f"{video_stem}.") or subtitle_stem.startswith(
                f"{video_stem}_"
            ):
                score = 0.92
            elif (
                video.get("season") is not None
                and video.get("episode") is not None
                and video.get("season") == subtitle.get("season")
                and video.get("episode") == subtitle.get("episode")
            ):
                score = 0.78
            if score and (best is None or score > best[0]):
                best = (score, video)
        if best is not None:
            links.append((int(best[1]["id"]), int(subtitle["id"]), "external", best[0]))
    return links


def scan_project(
    settings: Settings,
    database: Database,
    project_id: int,
    *,
    root_key: str,
    relative_path: str,
    season_hint: int | None = None,
    log: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> ScanSummary:
    project = database.get_release_project(project_id)
    if not project:
        raise WorkspaceError("项目不存在")
    source, normalized = resolve_workspace_path(
        settings, root_key, relative_path, must_exist=True
    )
    if log:
        log(f"开始扫描 {root_key}:{normalized}")
    discovered = discover_media(
        settings,
        root_key,
        relative_path,
        season_hint=season_hint,
    )
    if should_cancel and should_cancel():
        raise WorkspaceError("任务已取消")
    ids: list[dict[str, Any]] = []
    counts = {"video": 0, "subtitle": 0, "iso": 0, "bdmv": 0, "other": 0}
    for item in discovered:
        if should_cancel and should_cancel():
            raise WorkspaceError("任务已取消")
        if log:
            log(f"发现 {item['file_role']}: {item['relative_path']}")
        file_id = database.upsert_release_file(project_id=project_id, **item)
        item["id"] = file_id
        counts[item["file_role"]] = counts.get(item["file_role"], 0) + 1
        ids.append(item)
    seen_paths = {item["relative_path"] for item in discovered}
    prefix = normalized
    missing = database.mark_missing_release_files(
        project_id, root_key, prefix, seen_paths
    )
    links = build_subtitle_links(ids)
    database.replace_release_file_links(project_id, links)
    warnings: list[str] = []
    if counts["video"] == 0 and counts["iso"] == 0 and counts["bdmv"] == 0:
        warnings.append("扫描结果中没有可发布的视频、ISO 或 BDMV 资源")
    unknown_languages = sum(
        1
        for item in ids
        if item["file_role"] == "subtitle" and not item.get("language")
    )
    if unknown_languages:
        warnings.append(f"{unknown_languages} 个字幕文件无法从文件名识别语言")
    unsupported = 0
    for current, _, files in os.walk(source, followlinks=False):
        if BDMV_DIRECTORY in Path(current).parts:
            continue
        for filename in files:
            suffix = Path(filename).suffix.lower()
            if (
                suffix
                and suffix not in VIDEO_EXTENSIONS
                and suffix not in SUBTITLE_EXTENSIONS
                and suffix not in ISO_EXTENSIONS
            ):
                unsupported += 1
    metadata = dict(project.get("metadata") or {})
    metadata.update(
        {
            "root_key": root_key,
            "source_path": normalized,
            "scan_counts": counts,
            "scan_warnings": warnings,
        }
    )
    database.update_release_project(
        project_id,
        download_dir=normalized,
        metadata_json=json.dumps(metadata, ensure_ascii=False),
    )
    if log:
        log(f"扫描完成：{counts}，缺失 {missing}，不支持 {unsupported}")
    return ScanSummary(
        scanned=len(discovered),
        supported=len(discovered),
        unsupported=unsupported,
        missing=missing,
        warnings=warnings,
        counts=counts,
    )
