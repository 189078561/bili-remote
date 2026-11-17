"""Publish completed downloads without changing yt-dlp's working filenames."""
from __future__ import annotations

import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

from ...file_service import _path_fingerprint
from ..naming.filesystem import SUBTITLES, VIDEO_EXTENSIONS
from ..naming.probe import MediaProbeAdapter
from .naming import final_extension, initial_filename, positive_number, safe_identifier, url_facts

_FINAL_MEDIA = VIDEO_EXTENSIONS | {".aac", ".ac3", ".eac3", ".flac", ".m4a", ".m4v", ".mka", ".mp3", ".oga", ".ogg", ".ogv", ".opus", ".wav"}
_AUXILIARIES = SUBTITLES | {".nfo", ".jpg", ".jpeg", ".png", ".webp", ".json"}


@contextmanager
def open_directory(path, *, create=False):
    """Open components without following symlinks and pin the final directory."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("下载目录无效")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            if create:
                try:
                    os.mkdir(part, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def staging_directory(root, job_id):
    directory = root / f".bili-remote-job-{int(job_id)}"
    with open_directory(root) as parent:
        try:
            os.mkdir(directory.name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
        with open_directory(directory) as stage:
            info = os.fstat(stage)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise OSError("下载暂存目录权限无效")
    return directory


def finished_paths(stage, paths):
    result = []
    for raw in dict.fromkeys(paths):
        path = Path(raw)
        if not path.is_absolute():
            path = stage / path
        try:
            path.relative_to(stage)
            with open_directory(path.parent) as directory:
                info = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and path.suffix.lower() in _FINAL_MEDIA:
                    result.append(path)
        except (OSError, ValueError):
            continue
    return list(dict.fromkeys(result))


def associated_files(source, siblings):
    for path in sorted(source.parent.iterdir()):
        if path.suffix.lower() not in _AUXILIARIES or path.is_symlink() or not path.is_file():
            continue
        matches = [video for video in siblings if video.parent == path.parent
                   and (path.stem == video.stem or path.stem.startswith(video.stem + "."))]
        if matches and max(matches, key=lambda video: len(video.stem)) == source:
            extra = path.name[len(source.stem):]
            # Retain valid language/format extensions. Do not promote fragments.
            if len(extra.encode()) <= 45 and re.fullmatch(r"[.a-zA-Z0-9_-]+", extra):
                yield path, extra


def _same_inode(left, right):
    return stat.S_ISREG(right.st_mode) and (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _link_bundle(source, parent, bindings):
    with open_directory(source.parent) as stage, open_directory(parent, create=True) as output:
        created = []
        try:
            taken = {name.casefold(): name for name in os.listdir(output)}
            for original, target in bindings:
                if len(os.fsencode(target)) > 255 or target in {".", ".."}:
                    raise OSError("完工文件名过长")
                source_info = os.stat(original.name, dir_fd=stage, follow_symlinks=False)
                if not stat.S_ISREG(source_info.st_mode):
                    raise OSError("仅整理普通下载文件")
                existing = taken.get(target.casefold())
                if existing:
                    current = os.stat(existing, dir_fd=output, follow_symlinks=False)
                    if existing == target and _same_inode(source_info, current):
                        continue  # A restart can reuse an already published link.
                    raise FileExistsError(target)
                os.link(original.name, target, src_dir_fd=stage, dst_dir_fd=output, follow_symlinks=False)
                created.append((target, source_info))
                taken[target.casefold()] = target
            os.fsync(output)
        except OSError:
            for name, original in reversed(created):
                try:
                    if _same_inode(original, os.stat(name, dir_fd=output, follow_symlinks=False)):
                        os.unlink(name, dir_fd=output)
                except FileNotFoundError:
                    pass
            raise


def _publish(source, siblings, stage, root, plan, extension, job_id, position):
    parent = root / source.relative_to(stage).parent
    sidecars = list(associated_files(source, siblings))
    identifier = safe_identifier(plan.get("identifier"))
    alternatives = (None, identifier, f"job{job_id}-{position}") if identifier else (None, f"job{job_id}-{position}")
    for suffix in dict.fromkeys(alternatives):
        name = initial_filename(plan, extension, collision=suffix)
        stem = Path(name).stem
        bindings = [(source, name), *((auxiliary, stem + trailing) for auxiliary, trailing in sidecars)]
        try:
            _link_bundle(source, parent, bindings)
            return parent / name, [(str(item), str(parent / target)) for item, target in bindings]
        except FileExistsError:
            continue
    raise FileExistsError("初步命名的目标位置已被占用")


def result_plan(plan, origin):
    result = dict(plan)
    result["title"] = plan.get("title") or origin.get("original_title") or ""
    origin_id = safe_identifier(origin.get("bvid")) or safe_identifier("cid" + str(origin.get("cid") or ""))
    part = None
    if not (origin.get("ep_id") or origin.get("season_id")):
        part = url_facts(origin.get("webpage_url")).number or positive_number(origin.get("part_number"))
    if result.get("all_parts") or not result.get("number") and part:
        result.update(number_kind="part" if part else None, number=part,
                      identifier=origin_id or result.get("identifier"))
    elif not result.get("identifier"):
        result["identifier"] = origin_id or safe_identifier("ep" + str(origin.get("ep_id") or ""))
    return result


async def finalize_download(settings, root, stage, job_id, plan, paths, facts, item_facts):
    sources = finished_paths(stage, paths)
    probe = MediaProbeAdapter(settings, timeout=8)
    records, cleanup = [], []
    for index, source in enumerate(sources, 1):
        origin = dict(item_facts.get(str(source), {}))
        if len(sources) == 1:
            origin.update({key: val for key, val in facts.items() if val is not None})
        chosen = result_plan(plan, origin)
        evidence = await probe.probe(source)
        warning = None
        try:
            target, completed = _publish(source, sources, stage, root, chosen, final_extension(source, evidence), job_id, index)
            cleanup.extend(completed)
        except OSError:
            target = source
            warning = "初步命名未能安全完成，已保留下载文件"
        origin["episode_index"] = chosen.get("number")
        identifier = chosen.get("identifier") or ""
        if identifier.lower().startswith("bv") and not origin.get("bvid"):
            origin["bvid"] = identifier
        if identifier.lower().startswith("ep") and not origin.get("ep_id"):
            origin["ep_id"] = identifier[2:]
        if chosen.get("title"):
            origin["original_title"] = chosen["title"]
        records.append({"path": str(target), "fingerprint": _path_fingerprint(target), "probe": evidence,
                        "origin": {key: origin.get(key) for key in ("bvid", "season_id", "ep_id", "cid", "original_title", "episode_index")},
                        "warning": warning})
    return records, cleanup


def cleanup_staging(stage, links):
    """After the DB commits, unlink only staged names still backed by final files."""
    directories = set()
    for old_name, new_name in links:
        old, new = Path(old_name), Path(new_name)
        try:
            with open_directory(old.parent) as source, open_directory(new.parent) as target:
                before = os.stat(old.name, dir_fd=source, follow_symlinks=False)
                after = os.stat(new.name, dir_fd=target, follow_symlinks=False)
                if _same_inode(before, after):
                    os.unlink(old.name, dir_fd=source)
                    parent = old.parent
                    while parent != stage.parent:
                        directories.add(parent)
                        parent = parent.parent
        except OSError:
            continue
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        try:
            with open_directory(directory.parent) as parent:
                os.rmdir(directory.name, dir_fd=parent)
        except OSError:
            pass
