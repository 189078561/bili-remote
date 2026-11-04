"""Reuse FileService permissions and roots; never create a second file browser."""
from __future__ import annotations

import ctypes
import os
import stat
from pathlib import Path, PurePosixPath

from ...file_service import FileServiceError, _path_fingerprint, validate_filename
from ...workspace import VIDEO_EXTENSIONS, SUBTITLE_EXTENSIONS
from .errors import NamingError

ROOTS = {"downloads", "torrents", "images", "release_info"}
AUDIO = {".mka", ".aac", ".ac3", ".eac3", ".flac", ".dts", ".wav", ".mp3"}
SUBTITLES = SUBTITLE_EXTENSIONS | {".idx"}


class NamingFiles:
    def __init__(self, file_service):
        self.service = file_service

    def path(self, ref, *, exists=True, mutation=False):
        if not isinstance(ref, dict) or set(ref) - {"root_key", "relative_path", "kind", "fingerprint"} or not isinstance(ref.get("root_key"), str) or ref.get("root_key") not in ROOTS:
            raise NamingError("invalid_resource", "无效的资源根目录")
        if ref.get("kind", "file") not in {"file", "directory"} or ref.get("fingerprint") is not None and (not isinstance(ref["fingerprint"], str) or len(ref["fingerprint"]) != 64):
            raise NamingError("invalid_resource", "资源类型或指纹无效")
        raw = ref.get("relative_path")
        if not isinstance(raw, str) or "\\" in raw or not raw or raw.startswith("/") or any(x in {"", ".", ".."} for x in raw.split("/")):
            raise NamingError("invalid_path", "仅允许正规化的根内相对路径")
        try:
            # Reject every symlink component, including links within the root.
            root = self.service.root(ref["root_key"]).path
            current = root
            for part in PurePosixPath(raw).parts:
                current = current / part
                if current.is_symlink():
                    raise NamingError("symlink", "命名操作不允许符号链接")
            path, relative = self.service.resolve(ref["root_key"], raw, must_exist=exists, allow_empty=False)
            if mutation:
                reason = self.service._mutation_protection(path, relative_path=relative)
                if reason:
                    raise NamingError("protected_resource", reason, status=409)
                if not os.access(path.parent, os.W_OK | os.X_OK) or not path.parent.stat().st_mode & 0o222:
                    raise NamingError("permission_denied", "目录不可写", status=403)
            return path
        except NamingError:
            raise
        except (FileServiceError, ValueError, OSError) as exc:
            raise NamingError("invalid_resource", "资源不存在、越界或不可访问", status=404) from exc

    def fingerprint(self, ref):
        return _path_fingerprint(self.path(ref))

    def reference(self, root_key, relative_path):
        ref = {"root_key": root_key, "relative_path": relative_path, "kind": "file"}
        path = self.path(ref)
        ref["kind"] = "directory" if path.is_dir() else "file"
        ref["fingerprint"] = _path_fingerprint(path)
        return ref

    def expand(self, resources, *, limit=1000, max_depth=None):
        max_depth = max_depth or self.service.settings.workspace_scan_max_depth
        scan_limit = self.service.settings.workspace_scan_max_files
        if not isinstance(resources, list) or not resources or len(resources) > limit:
            raise NamingError("resource_limit", "请选择 1 至 1000 个资源")
        output, seen, scanned = [], set(), 0
        for ref in resources:
            path = self.path(ref)
            if ref.get("fingerprint") and ref["fingerprint"] != _path_fingerprint(path):
                raise NamingError("stale_resource", "源文件已变化，请重新选择", status=409)
            stack = [(path, 0)]
            while stack:
                item, depth = stack.pop()
                scanned += 1
                if scanned > scan_limit:
                    raise NamingError("scan_limit", "超过配置的目录扫描数量限制")
                if depth > max_depth:
                    raise NamingError("scan_limit", "目录超过扫描深度限制")
                relative = str(PurePosixPath(ref["relative_path"]) / item.relative_to(path)) if item != path else ref["relative_path"]
                child = self.reference(ref["root_key"], relative)
                if item.is_dir():
                    stack.extend((p, depth + 1) for p in sorted(item.iterdir(), reverse=True))
                elif stat.S_ISREG(item.stat().st_mode) and item.suffix.lower() in VIDEO_EXTENSIONS:
                    key = (child["root_key"], child["relative_path"])
                    if key not in seen:
                        seen.add(key)
                        output.append(child)
                if len(seen) > limit:
                    raise NamingError("resource_limit", "目录包含超过 1000 个视频")
        return output

    def sidecars(self, video_ref):
        video = self.path(video_ref)
        result = []
        # A sidecar belongs to the longest exact video stem in this directory.
        videos = [p for p in video.parent.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS and not p.is_symlink()]
        for path in sorted(video.parent.iterdir()):
            if path.suffix.lower() not in SUBTITLES:
                continue
            matches = [p for p in videos if path.stem == p.stem or path.stem.startswith(p.stem + ".")]
            if not matches or max(matches, key=lambda p: len(p.stem)) != video:
                continue
            tokens = path.stem[len(video.stem):].lstrip(".").split(".") if path.stem != video.stem else []
            # Arbitrary prefixes/suffixes are not evidence of association.
            allowed = {"chs", "cht", "eng", "en", "zh", "zh-cn", "zh-tw", "zh-hans", "zh-hant", "ja", "jpn", "forced", "sdh", "cc", "default"}
            if any(token.casefold() not in allowed for token in tokens):
                continue
            relative = str(PurePosixPath(video_ref["relative_path"]).with_name(path.name))
            result.append({"ref": self.reference(video_ref["root_key"], relative), "video_ref": video_ref,
                           "kind": "subtitle", "language": next((x for x in tokens if x.lower() not in {"forced", "sdh", "cc", "default"}), None),
                           "suffix_tokens": tokens, "association": "exact_stem", "confirmed": True})
        return result

    def validate_operations(self, operations, *, fingerprints=True):
        sources, targets, conflicts = {}, {}, []
        for op in operations:
            ref = op["source"]
            try:
                source = self.path(ref, mutation=True)
            except NamingError as exc:
                if exc.status == 404 or exc.code == "symlink":
                    conflicts.append({"code": "stale_resource", "ref": ref, "message": "源文件不存在或路径已变化"})
                    continue
                raise
            source_key = (ref["root_key"], ref["relative_path"])
            target_ref = {**ref, "relative_path": op["target_relative_path"]}
            try:
                target = self.path(target_ref, exists=False)
            except NamingError as exc:
                if exc.code == "symlink":
                    conflicts.append({"code": "target_exists", "ref": ref, "message": "目标为符号链接，禁止覆盖"})
                    continue
                raise
            try:
                validate_filename(target.name)
                if len(os.fsencode(target.name)) > 255:
                    raise ValueError("文件名过长")
            except (ValueError, FileServiceError) as exc:
                raise NamingError("invalid_filename", str(exc)) from exc
            if source.parent != target.parent:
                raise NamingError("not_in_place", "仅允许原目录内重命名")
            if source_key in sources:
                conflicts.append({"code": "duplicate_source", "ref": ref, "message": "源文件重复"})
            sources[source_key] = source
            target_key = (ref["root_key"], str(target.parent), target.name.casefold())
            if target_key in targets:
                conflicts.append({"code": "duplicate_target", "ref": ref, "message": "多个文件生成同一目标"})
            targets[target_key] = target
            if fingerprints and _path_fingerprint(source) != op["source_fingerprint"]:
                conflicts.append({"code": "stale_resource", "ref": ref, "message": "源文件指纹已变化"})
        source_paths = set(sources.values())
        for source in source_paths:
            if source.is_dir() and any(other != source and other.is_relative_to(source) for other in source_paths):
                conflicts.append({"code": "overlapping_sources", "message": "目录与内部文件必须分开预览和改名"})
        for op in operations:
            ref = op["source"]
            try:
                target = self.path({**ref, "relative_path": op["target_relative_path"]}, exists=False)
            except NamingError as exc:
                if exc.code == "symlink":
                    continue
                raise
            for sibling in target.parent.iterdir():
                if sibling.name.casefold() == target.name.casefold() and sibling not in source_paths:
                    conflicts.append({"code": "target_exists", "ref": ref, "message": "目标已存在（包含大小写冲突）"})
            if self.service._target_protection(target):
                conflicts.append({"code": "protected_target", "ref": ref, "message": "目标被活动任务保护"})
        return conflicts

    def move(self, source_ref, target_relative, expected_fingerprint):
        """Pin the directory while renaming: an ancestor swap cannot escape roots.

        Linux renameat2(RENAME_NOREPLACE) is deliberately required. The legacy
        portable existence-check/rename fallback cannot guarantee no overwrite.
        """
        source = self.path(source_ref, mutation=True)
        target = self.path({**source_ref, "relative_path": target_relative}, exists=False)
        if source.parent != target.parent:
            raise NamingError("not_in_place", "仅允许同目录改名")
        root = self.service.root(source_ref["root_key"]).path.resolve()
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in PurePosixPath(source_ref["relative_path"]).parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            info = os.stat(source.name, dir_fd=descriptor, follow_symlinks=False)
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                raise NamingError("unsupported_kind", "仅接受普通文件或目录")
            pinned_source = Path(f"/proc/self/fd/{descriptor}") / source.name
            if stat.S_ISDIR(info.st_mode) and self.service._tree_has_symlink(pinned_source):
                raise NamingError("symlink", "目录内包含符号链接，不能改名")
            if _path_fingerprint(pinned_source) != expected_fingerprint:
                raise NamingError("stale_resource", "源文件已变化", status=409)
            for name in os.listdir(descriptor):
                if name.casefold() == target.name.casefold() and name != source.name:
                    raise NamingError("target_exists", "目标或大小写等价文件已存在", status=409)
            libc = ctypes.CDLL(None, use_errno=True)
            rename = getattr(libc, "renameat2", None)
            if rename is None:
                raise NamingError("unsupported_filesystem", "系统不支持安全无覆盖重命名")
            result = rename(descriptor, os.fsencode(source.name), descriptor, os.fsencode(target.name), 1)
            if result:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def strong_fingerprint(path):
    """After/before rename stable content evidence, including inode and mtime."""
    return _path_fingerprint(path)
