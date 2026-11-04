from __future__ import annotations

from contextvars import ContextVar

current_mutation_task = ContextVar("current_mutation_task", default=None)

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from .config import Settings
from .database import Database
from .security import redact_sensitive
from .workspace import WorkspaceError, normalize_relative_path

RootScope = Literal["browse", "mounts", "workspace"]
OperationKind = Literal["copy", "move", "delete"]
ConflictPolicy = Literal["abort", "skip", "overwrite"]

BROWSE_ROOTS = {
    "downloads": "资源下载目录",
    "torrents": "种子目录",
    "images": "图片目录",
    "release_info": "发布信息目录",
}
WORKSPACE_ROOTS = {
    "downloads": "下载目录",
    "imports": "导入目录",
    "staging": "暂存目录",
    "releases": "发布目录",
}
MOUNT_ROOTS = {
    "data": "数据目录",
    "downloads": "资源下载挂载",
    "imports": "导入挂载",
    "staging": "暂存挂载",
    "releases": "发布挂载",
    "torrents": "种子挂载",
    "images": "图片挂载",
    "release_info": "发布信息挂载",
}
CATEGORY_DEFAULT_MOUNT = {
    "downloads": "downloads",
    "torrents": "torrents",
    "images": "images",
    "release_info": "release_info",
}

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".csv",
    ".log",
    ".nfo",
    ".srt",
    ".ass",
    ".ssa",
    ".vtt",
    ".ini",
    ".conf",
    ".cfg",
    ".toml",
    ".properties",
}
IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".m2ts",
    ".ts",
    ".mts",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
}
TEXT_MAX_BYTES = 2 * 1024 * 1024
OPERATION_LIMIT = 200
PREVIEW_TTL_SECONDS = 30 * 60

_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_ACTIVE_DOWNLOAD_SUFFIXES = (".part", ".aria2", ".ytdl")
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_DATA_RESERVED = {
    "bili-remote.sqlite3", "bili-remote.sqlite3-wal", "bili-remote.sqlite3-shm",
    "backups", "logs", "runtime",
}


class FileServiceError(WorkspaceError):
    pass


class FileConflictError(FileServiceError):
    pass


class OperationCancelled(Exception):
    pass


@dataclass(frozen=True, slots=True)
class RootDescriptor:
    key: str
    label: str
    path: Path
    mount_key: str | None = None
    relative_path: str = ""
    protected: bool = True

    @property
    def exists(self) -> bool:
        return self.path.is_dir()

    @property
    def writable(self) -> bool:
        return self.path.is_dir() and os.access(self.path, os.W_OK | os.X_OK)

    def payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "path": str(self.path),
            "mount_key": self.mount_key,
            "relative_path": self.relative_path,
            "writable": self.writable,
            "available": self.exists,
            "protected": self.protected,
        }


@dataclass(slots=True)
class OperationPreview:
    token: str
    operation: str
    conflict_policy: str
    items: list[dict[str, Any]]
    created_monotonic: float = time.monotonic()


class OperationPreviewStore:
    def __init__(self, *, max_entries: int = 100) -> None:
        self.max_entries = max_entries
        self._entries: dict[str, OperationPreview] = {}

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [
            token
            for token, preview in self._entries.items()
            if now - preview.created_monotonic > PREVIEW_TTL_SECONDS
        ]
        for token in expired:
            self._entries.pop(token, None)
        while len(self._entries) > self.max_entries:
            oldest = min(
                self._entries.values(), key=lambda item: item.created_monotonic
            )
            self._entries.pop(oldest.token, None)

    def put(self, preview: OperationPreview) -> None:
        self._prune()
        self._entries[preview.token] = preview

    def get(self, token: str) -> OperationPreview:
        self._prune()
        preview = self._entries.get(token)
        if not preview:
            raise FileConflictError("操作预览已失效，请重新检查")
        if time.monotonic() - preview.created_monotonic > PREVIEW_TTL_SECONDS:
            self._entries.pop(token, None)
            raise FileConflictError("操作预览已过期，请重新检查")
        return preview

    def remove(self, token: str) -> None:
        self._entries.pop(token, None)


@dataclass(frozen=True, slots=True)
class MutationSnapshot:
    """Active paths that must not be moved, renamed or deleted."""

    exact_paths: tuple[Path, ...] = ()
    tree_paths: tuple[Path, ...] = ()
    download_dirs: tuple[Path, ...] = ()
    writing_paths: tuple[Path, ...] = ()


def _natural_key(value: str) -> tuple[tuple[int, Any], ...]:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
        if part
    )


def _path_type(path: Path) -> str:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        return "symlink"
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    if stat.S_ISREG(info.st_mode):
        return "file"
    return "other"


def _has_casefold_collision(path: Path) -> bool:
    """Return whether another sibling differs only by filename case."""
    try:
        with os.scandir(path.parent) as entries:
            return any(
                entry.name != path.name
                and entry.name.casefold() == path.name.casefold()
                for entry in entries
            )
    except OSError as exc:
        raise FileServiceError(f"无法检查目标目录：{exc}") from exc


def _quick_fingerprint(path: Path) -> str:
    info = path.lstat()
    payload = f"{info.st_mode}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}".encode()
    return hashlib.sha256(payload).hexdigest()


def _path_fingerprint(path: Path) -> str:
    """Fingerprint an object, including directory descendants without following links."""
    digest = hashlib.sha256()
    stack: list[tuple[str, Path]] = [("", path)]
    while stack:
        relative, current = stack.pop()
        try:
            info = current.lstat()
        except FileNotFoundError:
            digest.update(f"missing:{relative}\0".encode())
            continue
        digest.update(
            f"{relative}\0{info.st_mode}:{info.st_ino}:{info.st_size}:"
            f"{info.st_mtime_ns}\0".encode()
        )
        if stat.S_ISLNK(info.st_mode):
            try:
                digest.update(f"link:{os.readlink(current)}\0".encode())
            except OSError:
                digest.update(b"link:unreadable\0")
            continue
        if stat.S_ISDIR(info.st_mode):
            try:
                names = sorted(entry.name for entry in os.scandir(current))
            except OSError:
                digest.update(b"unreadable\0")
                continue
            for name in reversed(names):
                child_relative = f"{relative}/{name}" if relative else name
                stack.append((child_relative, current / name))
    return digest.hexdigest()


def _content_fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_filename(name: str) -> str:
    if not isinstance(name, str):
        raise FileServiceError("文件名无效")
    value = name
    if not value or value in {".", ".."}:
        raise FileServiceError("文件名不能为空")
    if len(value) > 240:
        raise FileServiceError("文件名过长")
    if _INVALID_FILENAME.search(value):
        raise FileServiceError("文件名包含非法字符")
    if value.rstrip(" .") != value:
        raise FileServiceError("文件名不能以空格或句点结尾")
    if value.split(".", 1)[0].upper() in _RESERVED_NAMES:
        raise FileServiceError("文件名是系统保留名称")
    return value


class FileService:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self.previews = OperationPreviewStore()

    # Roots and path validation ---------------------------------------
    def _stored_settings(self) -> dict[str, str]:
        return self.database.get_settings()

    def mount_roots(self) -> dict[str, RootDescriptor]:
        return {
            key: RootDescriptor(
                key=key,
                label=MOUNT_ROOTS[key],
                path=path.resolve(),
                mount_key=key,
                relative_path="",
            )
            for key, path in self.settings.mount_roots.items()
        }

    def category_root(
        self, key: str, values: dict[str, str] | None = None
    ) -> RootDescriptor:
        if key not in BROWSE_ROOTS:
            raise FileServiceError("未知的文件目录")
        values = values if values is not None else self._stored_settings()
        mount_key = values.get(f"{key}_root_mount", CATEGORY_DEFAULT_MOUNT[key])
        relative = values.get(f"{key}_root_relative", "")
        mounts = self.settings.mount_roots
        if mount_key not in mounts:
            mount_key = CATEGORY_DEFAULT_MOUNT[key]
        path = self._join_mount(mounts[mount_key], relative, must_exist=False)
        return RootDescriptor(
            key=key,
            label=BROWSE_ROOTS[key],
            path=path,
            mount_key=mount_key,
            relative_path=relative,
        )

    def roots(self, scope: RootScope = "browse") -> list[RootDescriptor]:
        if scope == "mounts":
            return list(self.mount_roots().values())
        if scope == "workspace":
            return [
                RootDescriptor(
                    key=key,
                    label=WORKSPACE_ROOTS[key],
                    path=path.resolve(),
                    relative_path="",
                )
                for key, path in self.settings.workspace_roots.items()
            ]
        return [self.category_root(key) for key in BROWSE_ROOTS]

    def roots_map(self, scope: RootScope = "browse") -> dict[str, Path]:
        return {root.key: root.path for root in self.roots(scope)}

    def root(self, root_key: str, scope: RootScope = "browse") -> RootDescriptor:
        if scope == "browse":
            if root_key not in BROWSE_ROOTS:
                raise FileServiceError("未知的文件目录")
            return self.category_root(root_key)
        if scope == "mounts":
            mounts = self.mount_roots()
            if root_key not in mounts:
                raise FileServiceError("未知的文件目录")
            return mounts[root_key]
        if scope == "workspace":
            roots = self.settings.workspace_roots
            if root_key not in roots:
                raise FileServiceError("未知的文件目录")
            return RootDescriptor(
                key=root_key,
                label=WORKSPACE_ROOTS.get(root_key, root_key),
                path=roots[root_key].resolve(),
                relative_path="",
            )
        for descriptor in self.roots(scope):
            if descriptor.key == root_key:
                return descriptor
        raise FileServiceError("未知的文件目录")

    def _cached_root(
        self,
        cache: dict[tuple[str, RootScope], RootDescriptor],
        root_key: str,
        scope: RootScope,
    ) -> RootDescriptor:
        key = (root_key, scope)
        if key not in cache:
            cache[key] = self.root(root_key, scope)
        return cache[key]

    def _join_mount(
        self,
        mount_path: Path,
        relative_path: str | None,
        *,
        must_exist: bool = False,
    ) -> Path:
        relative = normalize_relative_path(relative_path)
        base = mount_path.expanduser().resolve()
        current = base
        for part in PurePosixPath(relative).parts if relative else ():
            current = current / part
            if current.exists() and current.is_symlink():
                raise FileServiceError("路径包含符号链接，已拒绝访问")
        try:
            resolved = current.resolve(strict=False)
        except OSError as exc:
            raise FileServiceError(f"路径无法解析：{exc}") from exc
        if resolved != base and not resolved.is_relative_to(base):
            raise FileServiceError("路径超出允许的挂载目录")
        if must_exist and not current.exists():
            raise FileServiceError("路径不存在")
        return current

    def resolve(
        self,
        root_key: str,
        relative_path: str | None,
        *,
        scope: RootScope = "browse",
        must_exist: bool = False,
        allow_empty: bool = True,
        root_descriptor: RootDescriptor | None = None,
    ) -> tuple[Path, str]:
        descriptor = root_descriptor or self.root(root_key, scope)
        relative = normalize_relative_path(relative_path, allow_empty=allow_empty)
        candidate = self._join_mount(
            descriptor.path, relative, must_exist=must_exist
        )
        if self._reserved_data_path(candidate):
            raise FileServiceError("该路径属于应用运行数据，禁止通过文件管理器访问")
        if candidate.exists() and candidate.is_symlink():
            raise FileServiceError("不允许对符号链接执行此操作")
        return candidate, relative

    def validate_directory_locator(
        self,
        mount_key: str,
        relative_path: str,
        *,
        require_writable: bool = True,
    ) -> tuple[RootDescriptor, str]:
        mounts = self.mount_roots()
        if mount_key not in mounts:
            raise FileServiceError("未知的挂载目录")
        descriptor = mounts[mount_key]
        relative = normalize_relative_path(relative_path)
        path = self._join_mount(descriptor.path, relative, must_exist=True)
        if not path.is_dir():
            raise FileServiceError("所选路径不是目录")
        if self._reserved_data_path(path):
            raise FileServiceError("不能将应用运行目录用作文件目录")
        if require_writable and not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            raise FileServiceError("所选目录不可读写")
        return descriptor, relative

    def directory_settings(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for key, label in BROWSE_ROOTS.items():
            root = self.category_root(key)
            result.append(
                {
                    "key": key,
                    "label": label,
                    "mount_key": root.mount_key,
                    "relative_path": root.relative_path,
                    "path": str(root.path),
                    "available": root.exists,
                    "writable": root.writable,
                }
            )
        return result

    def default_download_output_dir(self) -> str:
        return normalize_relative_path(
            self._stored_settings().get("download_default_output_dir", "")
        )

    def resolve_new_job_output(self, output_dir: str | None) -> str:
        configured = self.default_download_output_dir() if output_dir is None else output_dir
        relative = normalize_relative_path(configured)
        root = self.category_root("downloads")
        if not root.exists:
            raise FileServiceError("默认下载目录不存在或不可访问")
        target = self._join_mount(root.path, relative)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FileServiceError(f"无法创建下载目录：{exc}") from exc
        if target.is_symlink():
            raise FileServiceError("下载目录不能是符号链接")
        return str(target.resolve())

    # Browsing and metadata -------------------------------------------
    def _reserved_data_path(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.settings.data_dir.resolve())
        except ValueError:
            return False
        return bool(relative.parts and relative.parts[0] in _DATA_RESERVED)

    @staticmethod
    def _active_download_files(directory: Path) -> set[Path]:
        found: set[Path] = set()
        if not directory.is_dir():
            return found
        stack = [directory]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if stat.S_ISLNK(info.st_mode):
                            continue
                        child = current / entry.name
                        if stat.S_ISDIR(info.st_mode):
                            stack.append(child)
                        elif entry.name.endswith(_ACTIVE_DOWNLOAD_SUFFIXES):
                            found.add(child.resolve(strict=False))
            except OSError:
                continue
        return found

    def _dependency_snapshot(self, *, exclude_task_id: int | None = None) -> MutationSnapshot:
        """Read task dependencies once so directory scans stay database-constant."""
        if exclude_task_id is None:
            exclude_task_id = current_mutation_task.get()
        values = self._stored_settings()
        exact_paths: set[Path] = set()
        tree_paths: set[Path] = set()
        download_dirs: set[Path] = set()

        def add_dependency(root: Path, relative: str) -> None:
            try:
                relative_path = normalize_relative_path(relative, allow_empty=True)
            except WorkspaceError:
                return
            candidate = (root / relative_path).resolve(strict=False)
            try:
                candidate.relative_to(root.resolve())
            except ValueError:
                return
            if candidate.is_dir():
                tree_paths.add(candidate)
            else:
                exact_paths.add(candidate)

        try:
            jobs = self.database.fetch_all(
                "SELECT output_dir, result_path FROM jobs "
                "WHERE status IN ('queued', 'running')"
            )
            for job in jobs:
                raw = str(job.get("output_dir") or "")
                if raw:
                    candidate = Path(raw)
                    output = (
                        candidate
                        if candidate.is_absolute()
                        else self.settings.download_root / candidate
                    ).resolve(strict=False)
                    download_dirs.add(output)
                result_raw = str(job.get("result_path") or "")
                if result_raw:
                    result = Path(result_raw)
                    exact_paths.add(
                        (
                            result
                            if result.is_absolute()
                            else self.settings.download_root / result
                        ).resolve(strict=False)
                    )
        except Exception:
            pass

        writing_paths: set[Path] = set()
        for directory in download_dirs:
            writing_paths.update(self._active_download_files(directory))

        try:
            task_filter = " AND t.id!=?" if exclude_task_id is not None else ""
            rows = self.database.fetch_all(
                "SELECT f.root_key, f.relative_path FROM release_files f "
                "JOIN processing_tasks t ON t.project_id=f.project_id "
                "WHERE t.status IN ('queued', 'running') AND f.missing_at IS NULL"
                + task_filter,
                (exclude_task_id,) if exclude_task_id is not None else (),
            )
            workspace_roots = self.settings.workspace_roots
            for row in rows:
                root = workspace_roots.get(str(row.get("root_key") or ""))
                if root:
                    add_dependency(root, str(row.get("relative_path") or ""))
        except Exception:
            pass

        try:
            task_filter = " AND id!=?" if exclude_task_id is not None else ""
            tasks = self.database.fetch_all(
                "SELECT id, project_id, task_type, input_json FROM processing_tasks "
                "WHERE status IN ('queued', 'running')" + task_filter,
                (exclude_task_id,) if exclude_task_id is not None else (),
            )
            workspace_roots = self.settings.workspace_roots
            for task in tasks:
                try:
                    payload = json.loads(task.get("input_json") or "{}")
                except json.JSONDecodeError:
                    continue
                if task.get("project_id"):
                    root = workspace_roots.get(str(payload.get("root_key") or ""))
                    if root:
                        add_dependency(root, str(payload.get("relative_path") or ""))
                if task.get("task_type", "").startswith("v2_"):
                    refs = list(payload.get("resources", []))
                    if payload.get("resource"):
                        refs.append(payload["resource"])
                    if payload.get("batch_id"):
                        for operation in self.database.fetch_all("SELECT root_key,source_path,temporary_path,target_path FROM v2_rename_items WHERE batch_id=?", (payload["batch_id"],)):
                            refs.extend({"root_key": operation["root_key"], "relative_path": operation[key]} for key in ("source_path", "temporary_path", "target_path"))
                    for ref in refs:
                        if ref.get("root_key") in BROWSE_ROOTS:
                            add_dependency(self.category_root(ref["root_key"], values).path, ref["relative_path"])
                if task.get("task_type") != "file_operation":
                    continue
                for item in payload.get("items") or []:
                    for key in ("source", "target"):
                        record = item.get(key) if isinstance(item, dict) else None
                        if not isinstance(record, dict):
                            continue
                        root_key = str(record.get("root_key") or "")
                        if root_key not in BROWSE_ROOTS:
                            continue
                        root = self.category_root(root_key, values).path
                        add_dependency(root, str(record.get("relative_path") or ""))
        except Exception:
            pass

        return MutationSnapshot(
            exact_paths=tuple(sorted(exact_paths, key=str)),
            tree_paths=tuple(sorted(tree_paths, key=str)),
            download_dirs=tuple(sorted(download_dirs, key=str)),
            writing_paths=tuple(sorted(writing_paths, key=str)),
        )

    def _mutation_protection(
        self,
        path: Path,
        *,
        relative_path: str,
        snapshot: MutationSnapshot | None = None,
    ) -> str | None:
        if not relative_path:
            return "配置根目录受保护"
        if self._reserved_data_path(path):
            return "应用运行数据受保护"
        snapshot = snapshot or self._dependency_snapshot()
        candidate = path
        for dependency in (*snapshot.exact_paths, *snapshot.tree_paths):
            if candidate == dependency or dependency.is_relative_to(candidate):
                return "该目录包含活动任务使用的文件"
        for dependency in snapshot.tree_paths:
            if candidate.is_relative_to(dependency):
                return "该对象属于活动任务目录"
        for dependency in snapshot.download_dirs:
            if candidate == dependency:
                return "下载任务正在使用此目录"
            if dependency.is_relative_to(candidate):
                return "该目录包含活动下载目录"
            if candidate.is_relative_to(dependency):
                for writing in snapshot.writing_paths:
                    if candidate == writing or writing.is_relative_to(candidate):
                        return "下载任务正在写入此对象"
        return None

    def _mutation_protected(
        self,
        path: Path,
        *,
        relative_path: str,
        snapshot: MutationSnapshot | None = None,
    ) -> bool:
        return (
            self._mutation_protection(
                path, relative_path=relative_path, snapshot=snapshot
            )
            is not None
        )

    def _target_protection(
        self, path: Path, *, snapshot: MutationSnapshot | None = None
    ) -> str | None:
        if self._reserved_data_path(path):
            return "目标属于应用运行数据"
        snapshot = snapshot or self._dependency_snapshot()
        candidate = path
        for dependency in (
            *snapshot.exact_paths,
            *snapshot.tree_paths,
            *snapshot.download_dirs,
        ):
            if (
                candidate == dependency
                or candidate.is_relative_to(dependency)
                or dependency.is_relative_to(candidate)
            ):
                return "目标路径正被活动任务使用"
        return None

    def _target_protected(
        self, path: Path, *, snapshot: MutationSnapshot | None = None
    ) -> bool:
        return self._target_protection(path, snapshot=snapshot) is not None

    @staticmethod
    def _tree_has_symlink(path: Path) -> bool:
        stack = [path]
        while stack:
            current = stack.pop()
            try:
                info = current.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode):
                return True
            if stat.S_ISDIR(info.st_mode):
                try:
                    stack.extend(current.iterdir())
                except OSError:
                    continue
        return False

    def _item_payload(
        self,
        root: RootDescriptor,
        parent_relative: str,
        entry_name: str,
        entry_stat: os.stat_result,
        *,
        is_dir: bool,
        is_symlink: bool,
        snapshot: MutationSnapshot | None = None,
    ) -> dict[str, Any]:
        relative = "/".join(
            part for part in (parent_relative, entry_name) if part
        )
        path = root.path / relative
        allowed = not is_symlink and os.access(
            path, (os.R_OK | os.X_OK) if is_dir else os.R_OK
        )
        protection_reason = self._mutation_protection(
            path, relative_path=relative, snapshot=snapshot
        )
        protected = protection_reason is not None
        if self._reserved_data_path(path):
            allowed = False
        suffix = Path(entry_name).suffix.lower()
        if is_symlink:
            kind = "symlink"
        elif is_dir:
            kind = "directory"
        elif suffix in VIDEO_EXTENSIONS:
            kind = "video"
        elif suffix in IMAGE_TYPES:
            kind = "image"
        elif suffix in TEXT_EXTENSIONS or entry_name == ".env":
            kind = "text"
        else:
            kind = "other"
        can_preview = allowed and not is_dir
        capabilities = {
            "select": allowed,
            "preview": can_preview,
            "edit_text": can_preview
            and (suffix in TEXT_EXTENSIONS or entry_name == ".env"),
            "rename": allowed and not protected,
            "copy": allowed,
            "move": allowed and not protected,
            "delete": allowed and not protected,
        }
        return {
            "name": entry_name,
            "relative_path": relative,
            "parent_relative_path": parent_relative,
            "is_dir": is_dir,
            "is_symlink": is_symlink,
            "allowed": allowed,
            "protected": protected,
            "protection_reason": protection_reason,
            "kind": kind,
            "extension": suffix.lstrip("."),
            "size_bytes": 0 if is_dir else int(entry_stat.st_size),
            "mtime_ns": int(entry_stat.st_mtime_ns),
            "fingerprint": hashlib.sha256(
                f"{entry_stat.st_mode}:{entry_stat.st_ino}:"
                f"{entry_stat.st_size}:{entry_stat.st_mtime_ns}".encode()
            ).hexdigest(),
            "capabilities": capabilities,
        }

    def browse(
        self,
        *,
        root_key: str,
        relative_path: str = "",
        page: int = 1,
        page_size: int | None = None,
        scope: RootScope = "browse",
        sort: str = "natural",
        direction: str = "asc",
        focus: str | None = None,
        search: str = "",
    ) -> dict[str, Any]:
        root = self.root(root_key, scope)
        if not root.exists:
            raise FileServiceError("目录不存在或当前不可访问")
        directory, normalized = self.resolve(
            root_key, relative_path, scope=scope, must_exist=True
        )
        if not directory.is_dir():
            raise FileServiceError("浏览路径不是目录")
        if sort not in {"natural", "name", "mtime", "size"}:
            raise FileServiceError("不支持的排序方式")
        if direction not in {"asc", "desc"}:
            raise FileServiceError("不支持的排序方向")
        if page_size is None:
            try:
                stored_page_size = int(
                    self._stored_settings().get("file_browser_page_size", "0")
                )
            except (TypeError, ValueError):
                stored_page_size = 0
            page_size = stored_page_size or self.settings.workspace_browse_limit
        limit = min(max(page_size, 1), 1000)
        items: list[dict[str, Any]] = []
        snapshot = self._dependency_snapshot()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if search and search.casefold() not in entry.name.casefold():
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    is_symlink = entry.is_symlink()
                    is_dir = entry.is_dir(follow_symlinks=False)
                    items.append(
                        self._item_payload(
                            root,
                            normalized,
                            entry.name,
                            info,
                            is_dir=is_dir,
                            is_symlink=is_symlink,
                            snapshot=snapshot,
                        )
                    )
        except OSError as exc:
            raise FileServiceError(f"无法读取目录：{exc}") from exc

        def key(item: dict[str, Any]) -> Any:
            if sort == "mtime":
                return int(item["mtime_ns"]), _natural_key(item["name"])
            if sort == "size":
                return int(item["size_bytes"]), _natural_key(item["name"])
            if sort == "name":
                return item["name"].casefold()
            return _natural_key(item["name"])

        directories = sorted(
            (item for item in items if item["is_dir"]), key=key, reverse=direction == "desc"
        )
        files = sorted(
            (item for item in items if not item["is_dir"]),
            key=key,
            reverse=direction == "desc",
        )
        ordered = [*directories, *files]
        focus_page = None
        requested_page = max(page, 1)
        if focus:
            normalized_focus = normalize_relative_path(focus)
            for index, item in enumerate(ordered):
                if item["relative_path"] == normalized_focus:
                    focus_page = index // limit + 1
                    requested_page = focus_page
                    break
        total = len(ordered)
        start = (requested_page - 1) * limit
        return {
            "root_key": root_key,
            "root_label": root.label,
            "scope": scope,
            "relative_path": normalized,
            "parent": (
                "/".join(PurePosixPath(normalized).parts[:-1])
                if normalized
                else None
            ),
            "items": ordered[start : start + limit],
            "page": requested_page,
            "page_size": limit,
            "total": total,
            "has_more": start + limit < total,
            "focus_page": focus_page,
        }

    def meta(
        self,
        *,
        root_key: str,
        relative_path: str,
        scope: RootScope = "browse",
    ) -> dict[str, Any]:
        root = self.root(root_key, scope)
        path, relative = self.resolve(
            root_key, relative_path, scope=scope, must_exist=True, allow_empty=False
        )
        info = path.lstat()
        parent = "/".join(PurePosixPath(relative).parts[:-1])
        return self._item_payload(
            root,
            parent,
            path.name,
            info,
            is_dir=path.is_dir() and not path.is_symlink(),
            is_symlink=path.is_symlink(),
            snapshot=self._dependency_snapshot(),
        )

    # Text and image preview ------------------------------------------
    def _text_path(self, root_key: str, relative_path: str) -> tuple[Path, str]:
        path, relative = self.resolve(
            root_key,
            relative_path,
            must_exist=True,
            allow_empty=False,
        )
        if path.is_symlink() or not path.is_file():
            raise FileServiceError("只能读取普通文本文件")
        if path.suffix.lower() not in TEXT_EXTENSIONS and path.name != ".env":
            raise FileServiceError("不支持预览或编辑此文件类型")
        return path, relative

    @staticmethod
    def _decode_text(data: bytes) -> tuple[str, str, str]:
        if data.startswith(b"\xef\xbb\xbf"):
            return data.decode("utf-8-sig"), "utf-8", "UTF-8 BOM"
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding = "utf-16-le" if data.startswith(b"\xff\xfe") else "utf-16-be"
            label = "UTF-16 LE" if encoding.endswith("le") else "UTF-16 BE"
            return data.decode("utf-16"), encoding, label
        try:
            return data.decode("utf-8"), "utf-8", "UTF-8"
        except UnicodeDecodeError:
            try:
                return data.decode("gb18030"), "gb18030", "GB18030"
            except UnicodeDecodeError as exc:
                raise FileServiceError("无法识别文本编码") from exc

    def read_text(self, *, root_key: str, relative_path: str) -> dict[str, Any]:
        path, relative = self._text_path(root_key, relative_path)
        info = path.stat()
        if info.st_size > TEXT_MAX_BYTES:
            raise FileServiceError(f"文本文件超过 {TEXT_MAX_BYTES // 1024 // 1024} MiB 限制")
        data = path.read_bytes()
        if b"\x00" in data:
            raise FileServiceError("二进制文件不能作为文本编辑")
        controls = sum(
            byte < 32 and byte not in {9, 10, 13} for byte in data[:65536]
        )
        if controls and controls / max(1, len(data[:65536])) > 0.01:
            raise FileServiceError("二进制文件不能作为文本编辑")
        text, encoding, encoding_label = self._decode_text(data)
        newline = "CRLF" if b"\r\n" in data and data.count(b"\r\n") == data.count(b"\n") else "LF"
        return {
            "root_key": root_key,
            "relative_path": relative,
            "name": path.name,
            "content": text,
            "encoding": encoding,
            "encoding_label": encoding_label,
            "newline": newline,
            "size_bytes": int(info.st_size),
            "mtime_ns": int(info.st_mtime_ns),
            "fingerprint": _content_fingerprint(data),
            "editable": os.access(path, os.W_OK),
        }

    def save_text(
        self,
        *,
        root_key: str,
        relative_path: str,
        content: str,
        fingerprint: str,
    ) -> dict[str, Any]:
        if not isinstance(content, str):
            raise FileServiceError("文本内容无效")
        path, relative = self._text_path(root_key, relative_path)
        if not os.access(path, os.W_OK):
            raise FileServiceError("文件不可写")
        current = path.read_bytes()
        if _content_fingerprint(current) != fingerprint:
            raise FileConflictError("文件已被其他操作修改，请重新加载")
        _, encoding, _ = self._decode_text(current)
        crlf = b"\r\n" in current and current.count(b"\r\n") == current.count(b"\n")
        newline = "CRLF" if crlf else "LF"
        payload = content.replace("\r\n", "\n").replace("\r", "\n")
        if newline == "CRLF":
            payload = payload.replace("\n", "\r\n")
        if encoding == "utf-16-le":
            encoded = b"\xff\xfe" + payload.encode("utf-16-le")
        elif encoding == "utf-16-be":
            encoded = b"\xfe\xff" + payload.encode("utf-16-be")
        elif current.startswith(b"\xef\xbb\xbf"):
            encoded = b"\xef\xbb\xbf" + payload.encode("utf-8")
        else:
            try:
                encoded = payload.encode(encoding)
            except UnicodeEncodeError as exc:
                raise FileServiceError("编辑内容无法使用原文件编码保存") from exc
        if len(encoded) > TEXT_MAX_BYTES:
            raise FileServiceError("保存后的文本超过大小限制")
        info = path.stat()
        temp = path.parent / f".bili-remote-{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(
                temp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                stat.S_IMODE(info.st_mode),
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, stat.S_IMODE(info.st_mode))
            if _content_fingerprint(path.read_bytes()) != fingerprint:
                raise FileConflictError("文件已被其他操作修改，请重新加载")
            if path.is_symlink():
                raise FileServiceError("文件状态已变化，请重新加载")
            os.replace(temp, path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        updated = path.stat()
        return {
            "root_key": root_key,
            "relative_path": relative,
            "size_bytes": int(updated.st_size),
            "mtime_ns": int(updated.st_mtime_ns),
            "fingerprint": _content_fingerprint(encoded),
        }

    def image(self, *, root_key: str, relative_path: str) -> tuple[Path, str]:
        path, _ = self.resolve(
            root_key, relative_path, must_exist=True, allow_empty=False
        )
        if path.is_symlink() or not path.is_file():
            raise FileServiceError("只能预览普通图片文件")
        media_type = IMAGE_TYPES.get(path.suffix.lower())
        if not media_type:
            raise FileServiceError("不支持预览此图片格式")
        with path.open("rb") as handle:
            head = handle.read(32)
        signatures = {
            "image/jpeg": head.startswith(b"\xff\xd8\xff"),
            "image/png": head.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/gif": head.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": head.startswith(b"RIFF") and head[8:12] == b"WEBP",
        }
        if not signatures.get(media_type, False):
            raise FileServiceError("图片内容与扩展名不匹配")
        return path, media_type

    # Ordinary rename -------------------------------------------------
    def rename(
        self,
        *,
        root_key: str,
        relative_path: str,
        new_name: str,
        fingerprint: str | None = None,
    ) -> dict[str, Any]:
        source, relative = self.resolve(
            root_key, relative_path, must_exist=True, allow_empty=False
        )
        if source.is_symlink():
            raise FileServiceError("不允许重命名符号链接")
        if self._mutation_protected(source, relative_path=relative):
            raise FileConflictError("该对象受保护，不能重命名")
        if fingerprint and _quick_fingerprint(source) != fingerprint:
            raise FileConflictError("源对象状态已变化，请刷新后重试")
        target_name = validate_filename(new_name)
        if target_name == source.name:
            raise FileServiceError("名称没有变化")
        target = source.parent / target_name
        if os.path.lexists(target):
            raise FileConflictError("目标名称已存在")
        for sibling in source.parent.iterdir():
            if sibling != source and sibling.name.casefold() == target_name.casefold():
                raise FileConflictError("目标名称与现有文件冲突")
        rename_no_replace(source, target)
        new_relative = "/".join(
            part for part in (PurePosixPath(relative).parent.as_posix(), target_name)
            if part and part != "."
        )
        return {
            "root_key": root_key,
            "old_relative_path": relative,
            "new_relative_path": new_relative,
            "old_name": source.name,
            "new_name": target_name,
        }

    # Batch operations -------------------------------------------------
    def _source_item(
        self,
        source: dict[str, Any],
        operation: str,
        *,
        snapshot: MutationSnapshot | None = None,
        root_cache: dict[tuple[str, RootScope], RootDescriptor] | None = None,
    ) -> tuple[RootDescriptor, Path, str, os.stat_result]:
        root_key = str(source.get("root_key") or "")
        relative_path = str(source.get("relative_path") or "")
        if not relative_path:
            raise FileServiceError("不能对配置根目录执行此操作")
        root = (
            self._cached_root(root_cache, root_key, "browse")
            if root_cache is not None
            else self.root(root_key, "browse")
        )
        path, relative = self.resolve(
            root_key,
            relative_path,
            must_exist=True,
            allow_empty=False,
            root_descriptor=root,
        )
        if path.is_symlink():
            raise FileServiceError("不允许对符号链接执行批量操作")
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
            raise FileServiceError("只允许操作普通文件或目录")
        if operation in {"move", "delete"} and self._mutation_protected(
            path, relative_path=relative, snapshot=snapshot
        ):
            raise FileConflictError("对象正在被其他任务使用或属于受保护目录")
        return root, path, relative, info

    @staticmethod
    def _tree_summary(path: Path) -> tuple[int, int]:
        """Count descendants of a directory, excluding the directory itself."""
        count = 0
        size = 0
        try:
            stack = list(path.iterdir())
        except OSError:
            return 0, 0
        while stack:
            current = stack.pop()
            try:
                info = current.lstat()
            except OSError:
                continue
            if stat.S_ISDIR(info.st_mode):
                count += 1
                try:
                    stack.extend(
                        child for child in current.iterdir() if not child.is_symlink()
                    )
                except OSError:
                    pass
            else:
                count += 1
                size += int(info.st_size)
        return count, size

    def preview_operations(
        self,
        *,
        operation: str,
        sources: list[dict[str, Any]],
        target: dict[str, Any] | None,
        conflict_policy: str = "abort",
    ) -> dict[str, Any]:
        if operation not in {"copy", "move", "delete"}:
            raise FileServiceError("不支持的文件操作")
        if conflict_policy not in {"abort", "skip", "overwrite"}:
            raise FileServiceError("不支持的冲突处理方式")
        if not sources:
            raise FileServiceError("请至少选择一个对象")
        if len(sources) > OPERATION_LIMIT:
            raise FileServiceError(f"单次最多操作 {OPERATION_LIMIT} 个顶层对象")
        target_root: RootDescriptor | None = None
        target_dir: Path | None = None
        target_relative = ""
        snapshot = self._dependency_snapshot()
        root_cache: dict[tuple[str, RootScope], RootDescriptor] = {}
        if operation in {"copy", "move"}:
            if not target:
                raise FileServiceError("请选择目标目录")
            target_root_key = str(target.get("root_key") or "")
            target_relative = str(target.get("relative_path") or "")
            target_root = self._cached_root(root_cache, target_root_key, "browse")
            target_dir, target_relative = self.resolve(
                target_root_key,
                target_relative,
                must_exist=True,
                root_descriptor=target_root,
            )
            if not target_dir.is_dir():
                raise FileServiceError("目标路径不是目录")
            if self._target_protected(target_dir, snapshot=snapshot):
                raise FileConflictError("目标目录受保护，不能写入")

        preview_items: list[dict[str, Any]] = []
        target_names: set[tuple[str, str]] = set()
        for index, source in enumerate(sources):
            entry: dict[str, Any] = {
                "index": index,
                "source": {
                    "root_key": str(source.get("root_key") or ""),
                    "relative_path": str(source.get("relative_path") or ""),
                },
                "target": None,
                "status": "ready",
                "reasons": [],
                "will_overwrite": False,
            }
            try:
                _, source_path, source_relative, source_stat = self._source_item(
                    source,
                    operation,
                    snapshot=snapshot,
                    root_cache=root_cache,
                )
                entry["source"]["relative_path"] = source_relative
                entry["fingerprint"] = _path_fingerprint(source_path)
                entry["object_type"] = (
                    "directory" if stat.S_ISDIR(source_stat.st_mode) else "file"
                )
                entry["size_bytes"] = int(source_stat.st_size)
                source_is_directory = stat.S_ISDIR(source_stat.st_mode)
                if source_is_directory:
                    entry["descendant_count"], entry["descendant_bytes"] = (
                        self._tree_summary(source_path)
                    )
                    if operation in {"copy", "move"} and self._tree_has_symlink(
                        source_path
                    ):
                        entry["status"] = "blocked"
                        entry["reasons"].append(
                            "目录中包含符号链接，拒绝复制或移动"
                        )
                if target_dir is not None:
                    target_path = target_dir / source_path.name
                    target_key = str(target_root.key if target_root else "")
                    target_relative_path = "/".join(
                        part
                        for part in (target_relative, source_path.name)
                        if part
                    )
                    entry["target"] = {
                        "root_key": target_key,
                        "relative_path": target_relative_path,
                    }
                    if target_path.resolve(strict=False) == source_path.resolve(
                        strict=False
                    ):
                        entry["status"] = "blocked"
                        entry["reasons"].append("目标与源对象相同")
                    if source_path.is_dir() and target_dir.resolve(
                        strict=False
                    ).is_relative_to(source_path.resolve(strict=False)):
                        entry["status"] = "blocked"
                        entry["reasons"].append("不能把目录复制或移动到自身子目录")
                    duplicate_key = (target_key, target_relative_path.casefold())
                    if duplicate_key in target_names:
                        entry["status"] = "blocked"
                        entry["reasons"].append("批内目标名称重复")
                    target_names.add(duplicate_key)
                    if self._target_protected(target_path, snapshot=snapshot):
                        entry["status"] = "blocked"
                        entry["reasons"].append("目标路径受保护")
                    if _has_casefold_collision(target_path):
                        entry["status"] = "blocked"
                        entry["reasons"].append("目标名称与现有对象大小写冲突")
                    elif os.path.lexists(target_path):
                        if target_path.is_symlink():
                            entry["status"] = "blocked"
                            entry["reasons"].append("目标为符号链接，拒绝覆盖")
                        elif _path_type(target_path) != entry["object_type"]:
                            entry["status"] = "blocked"
                            entry["reasons"].append(
                                "目标类型与源对象不同，拒绝覆盖"
                            )
                        elif conflict_policy == "overwrite":
                            entry["will_overwrite"] = True
                        elif conflict_policy == "skip":
                            entry["status"] = "skipped"
                            entry["reasons"].append("目标已存在，本次跳过")
                        else:
                            entry["status"] = "conflict"
                            entry["reasons"].append("目标已存在")
            except WorkspaceError as exc:
                entry["status"] = "blocked"
                entry["reasons"].append(str(exc))
            preview_items.append(entry)

        token = secrets.token_urlsafe(24)
        preview = OperationPreview(
            token=token,
            operation=operation,
            conflict_policy=conflict_policy,
            items=preview_items,
        )
        self.previews.put(preview)
        return {
            "token": token,
            "operation": operation,
            "conflict_policy": conflict_policy,
            "items": preview_items,
            "summary": self._operation_summary(preview_items),
            "requires_overwrite_confirmation": any(
                item.get("will_overwrite") for item in preview_items
            ),
        }

    @staticmethod
    def _operation_summary(items: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "total": len(items),
            "ready": sum(item["status"] == "ready" for item in items),
            "conflict": sum(item["status"] == "conflict" for item in items),
            "blocked": sum(item["status"] == "blocked" for item in items),
            "skipped": sum(item["status"] == "skipped" for item in items),
        }

    def prepare_operation(
        self, *, token: str, confirm_overwrite: bool
    ) -> dict[str, Any]:
        preview = self.previews.get(token)
        summary = self._operation_summary(preview.items)
        if summary["blocked"]:
            raise FileConflictError("存在无法执行的项目，请先取消并重新选择")
        if preview.conflict_policy == "abort" and summary["conflict"]:
            raise FileConflictError("存在目标冲突，请选择跳过或明确覆盖")
        if (
            preview.conflict_policy == "overwrite"
            and any(item.get("will_overwrite") for item in preview.items)
            and not confirm_overwrite
        ):
            raise FileConflictError("覆盖目标需要再次明确确认")
        return {
            "token": token,
            "operation": preview.operation,
            "conflict_policy": preview.conflict_policy,
            "confirm_overwrite": confirm_overwrite,
            "items": preview.items,
        }

    def execute_operation(
        self,
        payload: dict[str, Any],
        *,
        task_id: int,
        log: Callable[[str], None],
        should_cancel: Callable[[], bool],
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        operation = str(payload.get("operation") or "")
        items = list(payload.get("items") or [])
        results: list[dict[str, Any]] = []
        succeeded = 0
        failed = 0
        skipped = 0

        def publish(status: str = "running") -> None:
            if progress:
                progress(
                    {
                        "operation": operation,
                        "status": status,
                        "total": len(items),
                        "processed": len(results),
                        "succeeded": succeeded,
                        "failed": failed,
                        "skipped": skipped,
                        "items": results,
                    }
                )

        snapshot = self._dependency_snapshot(exclude_task_id=task_id)
        root_cache: dict[tuple[str, RootScope], RootDescriptor] = {}
        publish()
        for position, item in enumerate(items, start=1):
            if should_cancel():
                raise OperationCancelled("cancelled")
            source = item.get("source") or {}
            target = item.get("target") or {}
            result: dict[str, Any] = {
                "source": source,
                "target": target or None,
                "status": "failed",
            }
            if item.get("status") == "skipped":
                skipped += 1
                result["status"] = "skipped"
                result["message"] = "目标已存在，本次跳过"
                results.append(result)
                publish()
                continue
            try:
                source_root = self._cached_root(
                    root_cache, str(source.get("root_key") or ""), "browse"
                )
                source_path, source_relative = self.resolve(
                    source_root.key,
                    str(source.get("relative_path") or ""),
                    must_exist=True,
                    allow_empty=False,
                    root_descriptor=source_root,
                )
                if source_path.is_symlink():
                    raise FileServiceError("源对象已变为符号链接")
                if _path_fingerprint(source_path) != item.get("fingerprint"):
                    raise FileConflictError("源对象状态已变化")
                source_is_directory = source_path.is_dir()
                if (
                    operation in {"copy", "move"}
                    and source_is_directory
                    and self._tree_has_symlink(source_path)
                ):
                    raise FileServiceError("目录中包含符号链接，拒绝复制或移动")
                if operation in {"move", "delete"} and self._mutation_protected(
                    source_path,
                    relative_path=source_relative,
                    snapshot=snapshot,
                ):
                    raise FileConflictError("源对象正在被其他任务使用")
                if operation == "delete":
                    log(f"删除 {source_root.key}:{source_relative}")
                    self._remove_tree(source_path, should_cancel=should_cancel)
                    result["status"] = "completed"
                elif operation in {"copy", "move"}:
                    if not target:
                        raise FileServiceError("缺少目标路径")
                    target_root = self._cached_root(
                        root_cache, str(target.get("root_key") or ""), "browse"
                    )
                    target_path, target_relative = self.resolve(
                        target_root.key,
                        str(target.get("relative_path") or ""),
                        must_exist=False,
                        allow_empty=False,
                        root_descriptor=target_root,
                    )
                    if not target_path.parent.is_dir():
                        raise FileServiceError("目标父目录不存在")
                    if self._target_protected(target_path, snapshot=snapshot):
                        raise FileConflictError("目标目录受保护")
                    overwrite = bool(item.get("will_overwrite"))
                    if _has_casefold_collision(target_path):
                        raise FileConflictError("目标名称与现有对象大小写冲突")
                    if os.path.lexists(target_path) and overwrite:
                        if target_path.is_symlink() or _path_type(
                            target_path
                        ) != ("directory" if source_is_directory else "file"):
                            raise FileConflictError(
                                "目标类型与源对象不同，拒绝覆盖"
                            )
                    log(
                        f"{'复制' if operation == 'copy' else '移动'} "
                        f"{source_root.key}:{source_relative} -> "
                        f"{target_root.key}:{target_relative}"
                    )
                    if operation == "copy":
                        self._copy_path(
                            source_path,
                            target_path,
                            overwrite=overwrite,
                            should_cancel=should_cancel,
                        )
                    else:
                        partial = self._move_path(
                            source_path,
                            target_path,
                            overwrite=overwrite,
                            should_cancel=should_cancel,
                        )
                        if partial:
                            raise FileServiceError(
                                "目标已写入，但源对象删除失败，请检查后手动处理"
                            )
                    result["status"] = "completed"
                else:
                    raise FileServiceError("不支持的文件操作")
                succeeded += 1
            except OperationCancelled:
                result["status"] = "cancelled"
                result["message"] = "任务已取消"
                results.append(result)
                publish("cancelled")
                raise
            except Exception as exc:
                failed += 1
                message = redact_sensitive(str(exc))[:1000]
                result["status"] = "failed"
                result["message"] = message
                log(f"失败：{message}")
            results.append(result)
            publish("running" if position < len(items) else "completed")

        status = "failed" if failed else "completed"
        final = {
            "operation": operation,
            "status": status,
            "total": len(items),
            "processed": len(results),
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "partial": failed > 0 and succeeded > 0,
            "items": results,
        }
        if progress:
            progress(final)
        log(
            f"文件操作结束：成功 {succeeded}，失败 {failed}，跳过 {skipped}"
        )
        return final

    # Filesystem primitives -------------------------------------------
    def _copy_path(
        self,
        source: Path,
        target: Path,
        *,
        overwrite: bool,
        should_cancel: Callable[[], bool],
    ) -> None:
        if _has_casefold_collision(target):
            raise FileConflictError("目标名称与现有对象大小写冲突")
        if os.path.lexists(target) and not overwrite:
            raise FileConflictError("目标已存在")
        if os.path.lexists(target) and overwrite:
            if target.is_symlink() or _path_type(source) != _path_type(target):
                raise FileConflictError("目标类型与源对象不同，拒绝覆盖")
        staged = target.parent / f".bili-remote-{uuid.uuid4().hex}.part"
        try:
            self._copy_any(source, staged, should_cancel=should_cancel)
            self._commit_staged(staged, target, overwrite=overwrite)
        finally:
            if os.path.lexists(staged):
                self._remove_tree(staged)

    def _move_path(
        self,
        source: Path,
        target: Path,
        *,
        overwrite: bool,
        should_cancel: Callable[[], bool],
    ) -> bool:
        if _has_casefold_collision(target):
            raise FileConflictError("目标名称与现有对象大小写冲突")
        if os.path.lexists(target) and not overwrite:
            raise FileConflictError("目标已存在")
        if os.path.lexists(target) and overwrite:
            if target.is_symlink() or _path_type(source) != _path_type(target):
                raise FileConflictError("目标类型与源对象不同，拒绝覆盖")
        if not overwrite:
            try:
                rename_no_replace(source, target)
                return False
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
        else:
            try:
                rename_no_replace(source, target)
                return False
            except FileExistsError:
                backup = target.parent / f".bili-remote-{uuid.uuid4().hex}.bak"
                os.rename(target, backup)
                try:
                    rename_no_replace(source, target)
                except OSError:
                    os.rename(backup, target)
                    raise
                self._remove_tree(backup)
                return False
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
        self._copy_path(
            source, target, overwrite=overwrite, should_cancel=should_cancel
        )
        try:
            self._remove_tree(source, should_cancel=lambda: False)
        except OSError:
            return True
        return False

    def _commit_staged(
        self, staged: Path, target: Path, *, overwrite: bool
    ) -> None:
        if not os.path.lexists(target):
            rename_no_replace(staged, target)
            return
        if not overwrite:
            raise FileConflictError("目标已存在")
        if target.is_symlink() or _path_type(staged) != _path_type(target):
            raise FileConflictError("目标类型与源对象不同，拒绝覆盖")
        backup = target.parent / f".bili-remote-{uuid.uuid4().hex}.bak"
        os.rename(target, backup)
        try:
            rename_no_replace(staged, target)
        except OSError:
            os.rename(backup, target)
            raise
        self._remove_tree(backup)

    def _copy_any(
        self,
        source: Path,
        target: Path,
        *,
        should_cancel: Callable[[], bool],
    ) -> None:
        if should_cancel():
            raise OperationCancelled("cancelled")
        info = source.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise FileServiceError("不允许复制符号链接")
        if stat.S_ISDIR(info.st_mode):
            target.mkdir(mode=stat.S_IMODE(info.st_mode))
            for child in source.iterdir():
                self._copy_any(
                    child, target / child.name, should_cancel=should_cancel
                )
            shutil.copystat(source, target, follow_symlinks=False)
            return
        with source.open("rb") as reader, target.open("xb") as writer:
            while True:
                if should_cancel():
                    raise OperationCancelled("cancelled")
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        shutil.copystat(source, target, follow_symlinks=False)

    def _remove_tree(
        self,
        path: Path,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> None:
        if should_cancel and should_cancel():
            raise OperationCancelled("cancelled")
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            for child in list(path.iterdir()):
                self._remove_tree(child, should_cancel=should_cancel)
            path.rmdir()
        else:
            path.unlink()


def rename_no_replace(source: Path, target: Path) -> None:
    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            renameat2 = libc.renameat2
        except (AttributeError, OSError):
            renameat2 = None
        if renameat2 is not None:
            at_fdcwd = -100
            rename_noreplace = 1
            result = renameat2(
                at_fdcwd,
                os.fsencode(source),
                at_fdcwd,
                os.fsencode(target),
                rename_noreplace,
            )
            if result == 0:
                return
            error = ctypes.get_errno()
            if error not in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
                raise OSError(error, os.strerror(error), str(target))
    if os.path.lexists(target):
        raise FileExistsError(errno.EEXIST, "目标已存在", str(target))
    os.rename(source, target)
