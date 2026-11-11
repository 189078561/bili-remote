from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import Database
from .file_service import (
    FileConflictError,
    FileService,
    FileServiceError,
    OperationCancelled,
    validate_filename,
)
from .media_tools import defaults

PIECE_MIN = 16 * 1024
PIECE_MAX = 16 * 1024 * 1024
MAX_FILES = 100_000


class TorrentError(FileServiceError):
    pass


def bencode(value: Any) -> bytes:
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii") + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode("ascii") + b":" + value
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return str(len(encoded)).encode("ascii") + b":" + encoded
    if isinstance(value, (list, tuple)):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        encoded_items: list[tuple[bytes, bytes]] = []
        for key, item in value.items():
            encoded_key = key.encode("utf-8") if isinstance(key, str) else bytes(key)
            encoded_items.append((encoded_key, bencode(item)))
        encoded_items.sort(key=lambda pair: pair[0])
        return b"d" + b"".join(bencode(key) + item for key, item in encoded_items) + b"e"
    raise TypeError(f"无法 bencode 的类型：{type(value).__name__}")


def bdecode(data: bytes) -> Any:
    def parse(offset: int) -> tuple[Any, int]:
        if offset >= len(data):
            raise ValueError("bencode 数据意外结束")
        marker = data[offset : offset + 1]
        if marker == b"i":
            end = data.find(b"e", offset + 1)
            if end < 0:
                raise ValueError("bencode 整数缺少结束符")
            return int(data[offset + 1 : end]), end + 1
        if marker == b"l":
            result: list[Any] = []
            cursor = offset + 1
            while cursor < len(data) and data[cursor : cursor + 1] != b"e":
                item, cursor = parse(cursor)
                result.append(item)
            if cursor >= len(data):
                raise ValueError("bencode 列表缺少结束符")
            return result, cursor + 1
        if marker == b"d":
            result_dict: dict[bytes, Any] = {}
            cursor = offset + 1
            while cursor < len(data) and data[cursor : cursor + 1] != b"e":
                key, cursor = parse(cursor)
                if not isinstance(key, bytes):
                    raise ValueError("bencode 字典键必须是字符串")
                item, cursor = parse(cursor)
                result_dict[key] = item
            if cursor >= len(data):
                raise ValueError("bencode 字典缺少结束符")
            return result_dict, cursor + 1
        if marker.isdigit():
            colon = data.find(b":", offset)
            if colon < 0:
                raise ValueError("bencode 字符串缺少长度")
            length = int(data[offset:colon])
            start = colon + 1
            end = start + length
            if end > len(data):
                raise ValueError("bencode 字符串长度越界")
            return data[start:end], end
        raise ValueError("无效的 bencode 数据")

    value, position = parse(0)
    if position != len(data):
        raise ValueError("bencode 数据包含多余内容")
    return value


@dataclass(frozen=True, slots=True)
class TorrentFile:
    relative_parts: tuple[str, ...]
    path: Path
    size: int
    mtime_ns: int


def _parse_piece_length(value: str, total_size: int) -> int:
    text = str(value or "auto").strip().lower()
    if text == "auto":
        if total_size <= 64 * 1024**2:
            return 256 * 1024
        if total_size <= 512 * 1024**2:
            return 1024**2
        if total_size <= 2 * 1024**3:
            return 2 * 1024**2
        if total_size <= 8 * 1024**3:
            return 4 * 1024**2
        return 8 * 1024**2
    match = re.fullmatch(r"(\d+)([km]?)", text)
    if not match:
        raise TorrentError("piece 大小格式无效")
    exponent = 1 if match.group(2) == "k" else 2 if match.group(2) == "m" else 0
    size = int(match.group(1)) * (1024**exponent)
    if size < PIECE_MIN or size > PIECE_MAX or size & (size - 1):
        raise TorrentError("piece 大小必须是 16 KiB 到 16 MiB 之间的 2 的幂")
    return size


def _validate_trackers(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        if not text.startswith(("http://", "https://", "udp://")):
            raise TorrentError("Tracker 必须以 http://、https:// 或 udp:// 开头")
        seen.add(text)
        result.append(text)
    if len(result) > 20:
        raise TorrentError("Tracker 最多 20 个")
    return result


class TorrentService:
    def __init__(self, files: FileService, database: Database) -> None:
        self.files = files
        self.database = database

    def _collect_files(self, source: Path) -> tuple[str, list[TorrentFile], bool]:
        if source.is_symlink():
            raise TorrentError("不允许对符号链接制作种子")
        if source.is_file():
            info = source.stat()
            if not stat.S_ISREG(info.st_mode):
                raise TorrentError("源对象不是普通文件")
            return source.name, [TorrentFile((), source, info.st_size, info.st_mtime_ns)], True
        if not source.is_dir():
            raise TorrentError("源路径必须是文件或目录")
        files: list[TorrentFile] = []
        for current_root, dirnames, filenames in os.walk(source, followlinks=False):
            root_path = Path(current_root)
            dirnames.sort()
            filenames.sort()
            for dirname in list(dirnames):
                child = root_path / dirname
                if child.is_symlink():
                    raise TorrentError("源目录包含符号链接，已拒绝制作种子")
            for filename in filenames:
                path = root_path / filename
                if path.is_symlink():
                    raise TorrentError("源目录包含符号链接，已拒绝制作种子")
                try:
                    info = path.stat()
                except OSError as exc:
                    raise TorrentError(f"无法读取源文件：{filename}") from exc
                if not stat.S_ISREG(info.st_mode):
                    continue
                relative = path.relative_to(source)
                files.append(
                    TorrentFile(
                        tuple(relative.parts),
                        path,
                        info.st_size,
                        info.st_mtime_ns,
                    )
                )
                if len(files) > MAX_FILES:
                    raise TorrentError(f"源目录文件数超过 {MAX_FILES} 个限制")
        if not files:
            raise TorrentError("源目录中没有可制作种子的普通文件")
        files.sort(key=lambda item: item.relative_parts)
        return source.name, files, False

    @staticmethod
    def build_pieces(
        files: list[TorrentFile],
        piece_length: int,
        should_cancel=lambda: False,
        progress=lambda payload: None,
    ) -> tuple[bytes, int]:
        pieces = bytearray()
        current = bytearray()
        actual_total = 0
        for item in files:
            before = item.path.stat()
            if before.st_size != item.size or before.st_mtime_ns != item.mtime_ns:
                raise TorrentError(f"源文件在制作期间发生变化：{item.path.name}")
            try:
                with item.path.open("rb") as handle:
                    while True:
                        if should_cancel():
                            raise OperationCancelled
                        need = min(1024 * 1024, piece_length - len(current))
                        chunk = handle.read(need)
                        if not chunk:
                            break
                        current.extend(chunk)
                        actual_total += len(chunk)
                        progress({"bytes": actual_total, "file": item.path.name})
                        if len(current) == piece_length:
                            pieces.extend(hashlib.sha1(current).digest())
                            current.clear()
            except OSError as exc:
                raise TorrentError(f"读取源文件失败：{item.path.name}") from exc
            after = item.path.stat()
            if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
                raise TorrentError(f"源文件在制作期间发生变化：{item.path.name}")
        if current:
            pieces.extend(hashlib.sha1(current).digest())
        return bytes(pieces), actual_total

    def _output_directory(self, relative_path: str) -> Path:
        path, _ = self.files.resolve("torrents", relative_path, must_exist=False)
        path.mkdir(parents=True, exist_ok=True)
        path, _ = self.files.resolve("torrents", relative_path, must_exist=True)
        if path.is_symlink() or not path.is_dir():
            raise TorrentError("种子输出路径不是普通目录")
        return path

    @staticmethod
    def _write_atomic(target: Path, content: bytes, *, overwrite: bool) -> None:
        temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if overwrite:
                if target.exists() and target.is_dir():
                    raise TorrentError("目标路径是目录，不能覆盖")
                if target.is_symlink():
                    raise TorrentError("目标路径是符号链接，不能覆盖")
                os.replace(temporary, target)
            else:
                try:
                    os.link(temporary, target)
                except FileExistsError as exc:
                    raise FileConflictError("种子文件已存在") from exc
                temporary.unlink()
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def with_defaults(self, data):
        return defaults(
            self.database,
            self.files.settings,
            data,
            {
                "piece_length": "torrent_default_piece_length",
                "private": "torrent_default_private",
                "source": "torrent_default_source",
                "trackers": "torrent_default_trackers",
                "output_relative_path": "torrent_output_relative_path",
            },
        )

    def create(
        self, data: dict[str, Any], *, should_cancel=lambda: False, progress=lambda payload: None
    ) -> dict[str, Any]:
        data = self.with_defaults(data)
        source_root = str(data.get("source_root_key") or "downloads")
        source_relative = str(data.get("source_relative_path") or "")
        source, _ = self.files.resolve(
            source_root,
            source_relative,
            must_exist=True,
            allow_empty=False,
        )
        source_name, file_items, single_file = self._collect_files(source)
        total_size = sum(item.size for item in file_items)
        piece_length = _parse_piece_length(str(data.get("piece_length") or "auto"), total_size)
        pieces, actual_total = self.build_pieces(file_items, piece_length, should_cancel, progress)
        if actual_total != total_size:
            raise TorrentError("源文件总大小在制作期间发生变化")

        name = str(data.get("name") or source_name).strip() or source_name
        name = validate_filename(name)
        info: dict[str, Any] = {
            "name": name,
            "piece length": piece_length,
            "pieces": pieces,
        }
        if single_file:
            info["length"] = file_items[0].size
        else:
            info["files"] = [
                {
                    "length": item.size,
                    "path": list(item.relative_parts),
                }
                for item in file_items
            ]
        private = bool(data.get("private", True))
        if private:
            info["private"] = 1
        source_tag = str(data.get("source") or "").strip()
        if source_tag:
            if len(source_tag) > 100 or "\x00" in source_tag:
                raise TorrentError("source 标记无效")
            info["source"] = source_tag

        trackers = _validate_trackers(list(data.get("trackers") or []))
        info_hash = hashlib.sha1(bencode(info)).hexdigest()
        torrent: dict[str, Any] = {
            "info": info,
            "creation date": int(datetime.now(UTC).timestamp()),
            "created by": "Bili Remote 1.0.0",
            "encoding": "UTF-8",
        }
        if trackers:
            torrent["announce"] = trackers[0]
            torrent["announce-list"] = [[tracker] for tracker in trackers]
        payload = bencode(torrent)
        decoded = bdecode(payload)
        if (
            not isinstance(decoded, dict)
            or not isinstance(decoded.get(b"info"), dict)
            or bencode(decoded[b"info"]) != bencode(info)
        ):
            raise TorrentError("生成的种子结构校验失败")

        output_directory = self._output_directory(str(data.get("output_relative_path") or ""))
        filename = str(data.get("filename") or f"{name}.torrent").strip()
        filename = validate_filename(filename)
        if not filename.lower().endswith(".torrent"):
            filename = f"{filename}.torrent"
        target = output_directory / filename
        if source.is_dir() and target.resolve().is_relative_to(source.resolve()):
            raise TorrentError("种子输出文件不能写入源目录")
        if target.exists() and target.is_dir():
            raise TorrentError("目标路径是目录，不能写入种子")
        if target.is_symlink():
            raise TorrentError("目标路径是符号链接，不能写入种子")
        if target.exists() and not bool(data.get("overwrite")):
            raise FileConflictError("种子文件已存在，请确认覆盖或更换文件名")
        if should_cancel():
            raise OperationCancelled
        self._write_atomic(target, payload, overwrite=bool(data.get("overwrite")))

        output_relative = str(data.get("output_relative_path") or "").strip().strip("/")
        relative_output = f"{output_relative}/{filename}" if output_relative else filename
        build_id = self.database.create_torrent_build(
            name=name,
            source_root_key=source_root,
            source_relative_path=source_relative,
            output_root_key="torrents",
            output_relative_path=relative_output,
            info_hash=info_hash,
            piece_length=piece_length,
            total_size_bytes=total_size,
            file_count=len(file_items),
            private=private,
            trackers=trackers,
            source=source_tag,
        )
        record = self.database.get_torrent_build(build_id)
        if record is None:
            raise TorrentError("种子记录保存失败")
        record["download_url"] = f"/api/torrents/{build_id}/file"
        return record

    def _default_trackers(self) -> list[str]:
        raw = self.database.get_settings().get("torrent_default_trackers", "[]")
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return [str(item) for item in values] if isinstance(values, list) else []

    def list_builds(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        records = self.database.list_torrent_builds(limit=limit, offset=offset)
        for record in records:
            record["download_url"] = f"/api/torrents/{record['id']}/file"
        return records

    def _validate_source_snapshot(
        self, record: dict[str, Any], torrent_path: Path
    ) -> None:
        """Reject downloads when the payload no longer matches the torrent layout.

        A rename leaves the historical build record and .torrent file intact, but
        downloading that file after the source moved would produce a torrent that
        cannot seed. Validate the cheap filesystem snapshot and torrent file list
        at download/import time; content hashing remains part of torrent creation.
        """
        try:
            source, _ = self.files.resolve(
                str(record["source_root_key"]),
                str(record["source_relative_path"]),
                must_exist=True,
                allow_empty=False,
            )
            _, current_files, single_file = self._collect_files(source)
        except (FileServiceError, OSError) as exc:
            raise TorrentError("源资源已变化，请重新制种") from exc

        current_total = sum(item.size for item in current_files)
        if (
            len(current_files) != int(record.get("file_count") or 0)
            or current_total != int(record.get("total_size_bytes") or 0)
        ):
            raise TorrentError("源资源已变化，请重新制种")

        try:
            torrent = bdecode(torrent_path.read_bytes())
            info = torrent[b"info"]
            if not isinstance(info, dict):
                raise ValueError("种子缺少 info")
            if hashlib.sha1(bencode(info)).hexdigest() != str(record["info_hash"]):
                raise ValueError("种子 info hash 不一致")
            if single_file:
                if info.get(b"length") != current_total or b"files" in info:
                    raise ValueError("单文件结构不一致")
            else:
                raw_files = info.get(b"files")
                if not isinstance(raw_files, list):
                    raise ValueError("目录结构不一致")
                actual_layout: list[tuple[tuple[str, ...], int]] = []
                for item in raw_files:
                    if not isinstance(item, dict):
                        raise ValueError("文件条目无效")
                    parts = item.get(b"path")
                    length = item.get(b"length")
                    if (
                        not isinstance(parts, list)
                        or not parts
                        or not all(isinstance(part, bytes) for part in parts)
                        or not isinstance(length, int)
                    ):
                        raise ValueError("文件条目无效")
                    actual_layout.append(
                        (tuple(part.decode("utf-8") for part in parts), length)
                    )
                expected_layout = sorted(
                    (item.relative_parts, item.size) for item in current_files
                )
                if sorted(actual_layout) != expected_layout:
                    raise ValueError("文件路径或大小不一致")
        except (KeyError, OSError, UnicodeDecodeError, ValueError) as exc:
            raise TorrentError("源资源已变化，请重新制种") from exc

    def file_path(self, build_id: int) -> Path:
        record = self.database.get_torrent_build(build_id)
        if record is None:
            raise TorrentError("种子记录不存在")
        path, _ = self.files.resolve(
            str(record["output_root_key"]),
            str(record["output_relative_path"]),
            must_exist=True,
            allow_empty=False,
        )
        if path.is_symlink() or not path.is_file() or path.suffix.lower() != ".torrent":
            raise TorrentError("种子文件不存在或状态异常")
        self._validate_source_snapshot(record, path)
        return path
