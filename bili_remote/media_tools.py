"""Shared defaults, cancellable tools and optical-disc source selection."""

from __future__ import annotations

import re
import struct
import subprocess
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .file_service import FileServiceError, OperationCancelled
from .settings_registry import DEFINITIONS_BY_KEY, deserialize_setting_value

tool_cancel = ContextVar("media_tool_cancel", default=None)


def defaults(database, settings, data: dict[str, Any], mapping: dict[str, str]):
    result = dict(data)
    stored = database.get_settings() if database else {}
    for field, key in mapping.items():
        if result.get(field) is None:
            definition = DEFINITIONS_BY_KEY[key]
            result[field] = (
                deserialize_setting_value(definition, stored[key], settings)
                if key in stored
                else definition.default
            )
    return result


def run_tool(command, *, timeout=300, should_cancel=lambda: False):
    """Drain pipes while polling cancellation; terminate the tool on every exit path."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    started = time.monotonic()
    try:
        while True:
            if should_cancel():
                raise OperationCancelled
            if time.monotonic() - started > timeout:
                raise FileServiceError(f"工具执行超过 {timeout} 秒")
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def disc_root(source: Path) -> tuple[str, Path] | None:
    if source.is_symlink():
        raise FileServiceError("光盘路径不能是符号链接")
    for kind, name in (("bluray", "BDMV"), ("dvd", "VIDEO_TS")):
        if source.is_dir() and source.name.upper() == name:
            return kind, source.parent
        if source.is_dir() and (source / name).is_dir():
            return kind, source
    return None


def _mpls(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if raw[:4] != b"MPLS" or len(raw) < 24:
        raise ValueError("无效 MPLS")
    offset = struct.unpack_from(">I", raw, 8)[0]
    count = struct.unpack_from(">H", raw, offset + 6)[0]
    cursor = offset + 10
    clips, duration = [], 0.0
    for _ in range(count):
        length = struct.unpack_from(">H", raw, cursor)[0]
        item = cursor + 2
        clip = raw[item : item + 5].decode("ascii")
        if not re.fullmatch(r"\d{5}", clip):
            raise ValueError("无效蓝光片段名")
        start, end = struct.unpack_from(">II", raw, item + 12)
        seconds = max(0, end - start) / 45000
        clips.append({"clip": clip, "start": start / 45000, "duration": seconds})
        duration += seconds
        cursor += length + 2
    return {"id": path.stem, "duration_seconds": duration, "clips": clips}


def optical_choices(source: Path) -> dict[str, Any]:
    disc = disc_root(source)
    if not disc:
        return {"kind": "video", "choices": [], "selected": None}
    kind, root = disc
    # Never allow an optical helper to escape the file-browser mount via links.
    if any(p.is_symlink() for p in root.rglob("*")):
        raise FileServiceError("光盘目录包含符号链接")
    choices = []
    if kind == "bluray":
        for p in sorted((root / "BDMV" / "PLAYLIST").glob("*.mpls")):
            try:
                entry = _mpls(p)
                if entry["duration_seconds"] > 0:
                    choices.append(entry)
            except (OSError, ValueError, struct.error, UnicodeError):
                continue
        choices.sort(key=lambda x: (-x["duration_seconds"], x["id"]))
    else:
        folder = root / "VIDEO_TS"
        groups: dict[str, list[Path]] = {}
        for p in sorted(folder.iterdir()):
            match = re.fullmatch(r"VTS_(\d{2})_([1-9])\.VOB", p.name, re.I)
            if match:
                groups.setdefault(match[1], []).append(p)
        for group, files in groups.items():
            choices.append(
                {
                    "id": group,
                    "size_bytes": sum(p.stat().st_size for p in files),
                    "files": [p.name for p in files],
                }
            )
        choices.sort(key=lambda x: (-x["size_bytes"], x["id"]))
    if not choices:
        raise FileServiceError("光盘目录中没有可用的播放列表或正片标题组")
    return {"kind": kind, "choices": choices, "selected": choices[0]["id"]}


def optical_input(source: Path, selection: str | None = None) -> dict[str, Any]:
    info = optical_choices(source)
    if info["kind"] == "video":
        return {
            **info,
            "input": str(source),
            "input_args": [],
            "probe": source,
            "reports": [source],
        }
    _, root = disc_root(source)
    chosen = next((x for x in info["choices"] if x["id"] == (selection or info["selected"])), None)
    if not chosen:
        raise FileServiceError("所选光盘播放列表不存在")
    if info["kind"] == "bluray":
        clip = root / "BDMV" / "STREAM" / (chosen["clips"][0]["clip"] + ".m2ts")
        return {
            **info,
            "selected": chosen["id"],
            "input": f"bluray:{root}",
            "input_args": ["-playlist", str(int(chosen["id"]))],
            "probe": clip,
            "duration_seconds": chosen["duration_seconds"],
            "reports": [],
        }
    folder = root / "VIDEO_TS"
    files = [folder / name for name in chosen["files"]]
    if any("|" in str(p) for p in files):
        raise FileServiceError("DVD 路径包含不支持的分隔符")
    ifo = next((p for p in folder.iterdir() if p.name.upper() == f"VTS_{chosen['id']}_0.IFO"), None)
    if not ifo:
        raise FileServiceError("所选 DVD 标题组缺少 IFO 文件")
    return {
        **info,
        "selected": chosen["id"],
        "input": "concat:" + "|".join(map(str, files)),
        "input_args": [],
        "probe": files[0],
        "reports": [ifo, files[0]],
    }
