from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .database import Database
from .file_service import (
    VIDEO_EXTENSIONS,
    FileConflictError,
    FileService,
    FileServiceError,
    validate_filename,
)
from .media_probe import probe_command, probe_json_sync, video_details_from_payload
from .media_tools import defaults, disc_root, optical_input, run_tool, tool_cancel

MAX_FILE_LIST = 20_000
MAX_PROBED_VIDEOS = 200
MAX_MEDIAINFO_CHARS = 1_000_000
BDINFO_TIMEOUT_SECONDS = 300
_INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class ReleaseInfoError(FileServiceError):
    pass


def _human_size(size: int) -> str:
    value = max(0, int(size))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    index = 0
    number = float(value)
    while number >= 1024 and index < len(units) - 1:
        number /= 1024
        index += 1
    return f"{number:.2f} {units[index]}" if index else f"{value} B"


def _safe_title(title: str) -> str:
    text = _INVALID_FILENAME.sub("_", title).strip(" .")
    return text[:160].rstrip(" .") or "release-info"


def _duration_text(seconds: Any) -> str:
    try:
        value = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return "未知"
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _sanitize_report_paths(text: str, path: Path) -> str:
    """Keep reports portable by removing server-side absolute source paths."""
    name = path.name
    result = str(text).replace(str(path), name).replace(str(path.parent), ".")
    result = re.sub(
        r"(?im)^(\s*Complete name\s*:\s*).*$",
        lambda match: f"{match.group(1)}{name}",
        result,
    )
    result = re.sub(
        r"(?im)^(\s*Folder name\s*:\s*).*$",
        lambda match: f"{match.group(1)}.",
        result,
    )
    return result


class ReleaseInfoService:
    def __init__(self, settings: Settings, files: FileService, database: Database) -> None:
        self.settings = settings
        self.files = files
        self.database = database

    @staticmethod
    def _command(command, **kwargs):
        callback = tool_cancel.get()
        if callback is None:
            return subprocess.run(command, **kwargs)
        result = run_tool(command, timeout=kwargs.get("timeout", 300), should_cancel=callback)
        result.stdout = result.stdout.decode("utf-8", errors="replace")
        result.stderr = result.stderr.decode("utf-8", errors="replace")
        return result

    def with_defaults(self, data):
        return defaults(
            self.database,
            self.settings,
            data,
            {
                "format": "release_info_default_format",
                "output_relative_path": "release_info_output_relative_path",
                "bdinfo_timeout": "bdinfo_timeout_seconds",
                "include_mediainfo": "release_info_include_mediainfo",
            },
        )

    def status(self) -> dict[str, Any]:
        mediainfo = self._find_executable("mediainfo")
        bdinfo = self._find_executable("bdinfo", "BDInfo")
        return {
            "mediainfo": mediainfo,
            "bdinfo": bdinfo,
            "ffprobe": self._find_executable(self.settings.ffprobe_command[0]),
        }

    @staticmethod
    def _find_executable(*names: str) -> str | None:
        for name in names:
            executable = shutil.which(name)
            if executable:
                return executable
        return None

    def _iter_files(self, source: Path) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if source.is_file():
            candidates = [(source.name, source)]
        else:
            candidates = []
            for root, dirnames, filenames in os.walk(source, followlinks=False):
                root_path = Path(root)
                dirnames.sort()
                filenames.sort()
                if any((root_path / name).is_symlink() for name in dirnames):
                    raise ReleaseInfoError("源目录包含符号链接，已拒绝分析")
                for filename in filenames:
                    path = root_path / filename
                    if path.is_symlink():
                        raise ReleaseInfoError("源目录包含符号链接，已拒绝分析")
                    candidates.append((path.relative_to(source).as_posix(), path))
                    if len(candidates) > MAX_FILE_LIST:
                        raise ReleaseInfoError(f"源目录文件数超过 {MAX_FILE_LIST} 个限制")
        for relative, path in candidates:
            try:
                info = path.stat()
            except OSError as exc:
                raise ReleaseInfoError(f"无法读取源文件：{relative}") from exc
            if not path.is_file() or path.is_symlink():
                continue
            result.append(
                {
                    "relative_path": relative,
                    "name": path.name,
                    "size_bytes": int(info.st_size),
                    "size_human": _human_size(info.st_size),
                    "mtime_ns": int(info.st_mtime_ns),
                    "extension": path.suffix.lower(),
                    "is_video": path.suffix.lower() in VIDEO_EXTENSIONS,
                }
            )
        if not result:
            raise ReleaseInfoError("源路径中没有可分析的文件")
        return result

    def _mediainfo_text(self, path: Path) -> str | None:
        executable = self._find_executable("mediainfo")
        if not executable:
            return None
        try:
            completed = self._command(
                [executable, "--Output=Text", str(path)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0 or not completed.stdout.strip():
            return None
        if len(completed.stdout) > MAX_MEDIAINFO_CHARS:
            raise ReleaseInfoError("MediaInfo 报告超过存储限制，已拒绝截断")
        return _sanitize_report_paths(completed.stdout, path)

    @staticmethod
    def _bluray_root(source: Path) -> Path | None:
        if source.is_symlink():
            return None
        if source.is_file():
            return source if source.suffix.lower() == ".iso" else None
        if source.name.upper() == "BDMV" and source.is_dir() and not source.is_symlink():
            return source.parent
        bdmv = source / "BDMV"
        if bdmv.is_dir() and not bdmv.is_symlink():
            return source
        return None

    def _bdinfo_text(
        self, source: Path, selection=None, timeout=None
    ) -> tuple[str | None, str | None]:
        disc_root = self._bluray_root(source)
        if disc_root is None:
            return None, None
        executable = self._find_executable("bdinfo", "BDInfo")
        if not executable:
            return None, "检测到蓝光结构，但未安装 BDInfo，已跳过蓝光报告"
        self.settings.runtime_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(
                prefix="bdinfo-", dir=self.settings.runtime_dir
            ) as report_directory:
                completed = self._command(
                    [
                        executable,
                        *(["-m", str(selection) + ".MPLS"] if selection else ["-w"]),
                        str(disc_root),
                        report_directory,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=timeout or BDINFO_TIMEOUT_SECONDS,
                    check=False,
                )
                reports = sorted(
                    (
                        path
                        for path in Path(report_directory).rglob("*.txt")
                        if path.is_file() and not path.is_symlink()
                    ),
                    key=lambda path: path.stat().st_mtime_ns,
                    reverse=True,
                )
                content = ""
                if reports:
                    try:
                        content = reports[0].read_text(encoding="utf-8", errors="replace")
                    except OSError as exc:
                        return None, f"无法读取 BDInfo 报告：{exc}"
                elif completed.stdout.strip():
                    content = completed.stdout
                if not content.strip():
                    detail = completed.stderr.strip() or completed.stdout.strip()
                    if completed.returncode != 0:
                        message = detail[:300] or f"退出码 {completed.returncode}"
                        return None, f"BDInfo 报告生成失败：{message}"
                    return None, "BDInfo 未生成可读取的报告"
                if completed.returncode != 0:
                    return None, "BDInfo 扫描失败，返回的内容不能作为完整报告"
                if len(content) > MAX_MEDIAINFO_CHARS:
                    return None, "BDInfo 报告超过存储限制，已拒绝截断"
                return content, None
        except subprocess.TimeoutExpired:
            return None, f"BDInfo 扫描超过 {BDINFO_TIMEOUT_SECONDS} 秒，已停止"
        except OSError as exc:
            return None, f"无法执行 BDInfo：{exc}"

    @staticmethod
    def _ffprobe_text(details: dict[str, Any], payload: dict[str, Any]) -> str:
        format_data = payload.get("format") or {}
        display_name = str(details.get("path") or "未知").replace("\\", "/").rsplit("/", 1)[-1] or "未知"
        lines = [
            f"Complete name: {display_name}",
            "Format: "
            f"{format_data.get('format_long_name') or format_data.get('format_name') or '未知'}",
            f"Duration: {_duration_text(details.get('duration_seconds'))}",
            f"Overall bit rate: {format_data.get('bit_rate') or '未知'}",
            f"File size: {_human_size(int(details.get('size_bytes') or 0))}",
            "",
            f"Width: {details.get('width') or '未知'}",
            f"Height: {details.get('height') or '未知'}",
            f"Display aspect ratio: {details.get('aspect_ratio') or '未知'}",
            f"Frame rate: {details.get('fps') or '未知'}",
            f"Video codec: {details.get('video_codec') or '未知'}",
            f"Dynamic range: {details.get('hdr') or 'SDR/未知'}",
            "",
        ]
        for stream in payload.get("streams") or []:
            if stream.get("codec_type") != "audio":
                continue
            tags = stream.get("tags") or {}
            lines.append(
                "Audio: "
                f"{stream.get('codec_name') or '未知'} "
                f"{stream.get('channels') or '?'}ch "
                f"{stream.get('language') or tags.get('language') or '未知'}"
            )
        return "\n".join(lines)

    def _probe_videos(
        self,
        source: Path,
        files: list[dict[str, Any]],
        representatives=None,
        include_mediainfo=None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        videos: list[dict[str, Any]] = []
        warnings: list[str] = []
        if include_mediainfo is None:
            include_mediainfo = (
                self.database.get_settings().get("release_info_include_mediainfo", "true") == "true"
            )
        for item in [entry for entry in files if entry["is_video"]][:MAX_PROBED_VIDEOS]:
            callback = tool_cancel.get()
            if callback and callback():
                from .file_service import OperationCancelled

                raise OperationCancelled
            relative = str(item["relative_path"])
            path = source if source.is_file() else source / relative
            try:
                before = path.stat()
                if callback:
                    scanned = run_tool(
                        probe_command(self.settings, path), timeout=60, should_cancel=callback
                    )
                    if scanned.returncode:
                        raise RuntimeError("ffprobe 探测失败")
                    payload = json.loads(scanned.stdout)
                else:
                    payload = probe_json_sync(self.settings, path)
                details = video_details_from_payload(payload)
            except (RuntimeError, OSError, ValueError) as exc:
                videos.append(
                    {
                        "relative_path": relative,
                        "status": "failed",
                        "error": str(exc) or exc.__class__.__name__,
                        "details": None,
                        "media_info": None,
                        "media_info_tool": None,
                    }
                )
                warnings.append(f"{relative} 的媒体信息探测失败")
                continue
            info = (
                self._mediainfo_text(path)
                if include_mediainfo and (not representatives or relative in representatives)
                else None
            )
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ReleaseInfoError(f"源文件在报告生成期间发生变化：{relative}")
            tool = "mediainfo" if info else "ffprobe"
            if not info:
                info = self._ffprobe_text(
                    {**details, "path": relative, "size_bytes": item["size_bytes"]},
                    payload,
                )
            videos.append(
                {
                    "relative_path": relative,
                    "status": "completed",
                    "error": None,
                    "details": {
                        **details,
                        "path": relative,
                        "size_bytes": item["size_bytes"],
                        "size_human": item["size_human"],
                        "container": (payload.get("format") or {}).get("format_name"),
                        "bit_rate": (payload.get("format") or {}).get("bit_rate"),
                        "audio_codecs": [
                            stream.get("codec_name")
                            for stream in payload.get("streams") or []
                            if stream.get("codec_type") == "audio" and stream.get("codec_name")
                        ],
                    },
                    "media_info": info,
                    "media_info_tool": tool,
                }
            )
        if len([entry for entry in files if entry["is_video"]]) > MAX_PROBED_VIDEOS:
            warnings.append(f"视频数量超过 {MAX_PROBED_VIDEOS}，仅探测前 {MAX_PROBED_VIDEOS} 个")
        return videos, warnings

    @staticmethod
    def _file_list_text(files: list[dict[str, Any]], output_format: str) -> str:
        if output_format == "markdown":
            rows = [f"- `{item['relative_path']}` — {item['size_human']}" for item in files]
            return "\n".join(rows)
        if output_format == "bbcode":
            rows = [f"[*]{item['relative_path']} ({item['size_human']})" for item in files]
            return "[list]\n" + "\n".join(rows) + "\n[/list]"
        return "\n".join(f"{item['relative_path']} ({item['size_human']})" for item in files)

    @staticmethod
    def _media_text(videos: list[dict[str, Any]], output_format: str) -> str:
        sections: list[str] = []
        for video in videos:
            if video["status"] != "completed" or not video.get("media_info"):
                message = f"媒体信息探测失败：{video.get('error') or '未知错误'}"
                sections.append(message)
                continue
            text = str(video["media_info"]).strip()
            if output_format == "bbcode":
                sections.append(f"[quote]\n{text}\n[/quote]")
            elif output_format == "markdown":
                sections.append(f"```text\n{text}\n```")
            else:
                sections.append(text)
        return "\n\n".join(sections)

    def _generate_text(
        self,
        *,
        title: str,
        files: list[dict[str, Any]],
        videos: list[dict[str, Any]],
        output_format: str,
        disc_info: str | None = None,
    ) -> str:
        file_list = self._file_list_text(files, output_format)
        media = self._media_text(videos, output_format)
        disc = ""
        if disc_info:
            if output_format == "markdown":
                disc = f"\n\n## 蓝光信息\n\n```text\n{disc_info.strip()}\n```"
            elif output_format == "bbcode":
                disc = f"\n\n[quote]蓝光信息\n{disc_info.strip()}[/quote]"
            else:
                disc = f"\n\n蓝光信息\n{disc_info.strip()}"
        if output_format == "markdown":
            return f"# {title}\n\n## 文件清单\n\n{file_list}\n\n## 媒体信息\n\n{media}{disc}\n"
        if output_format == "bbcode":
            return f"[b]{title}[/b]\n\n[quote]文件清单\n{file_list}[/quote]\n\n{media}{disc}\n"
        return f"{title}\n\n文件清单\n{file_list}\n\n媒体信息\n{media}{disc}\n"

    def analyze(self, data: dict[str, Any]) -> dict[str, Any]:
        data = self.with_defaults(data)
        root_key = str(data.get("root_key") or "downloads")
        relative_path = str(data.get("relative_path") or "")
        source, relative = self.files.resolve(
            root_key,
            relative_path,
            must_exist=True,
            allow_empty=False,
        )
        if source.is_symlink():
            raise ReleaseInfoError("不允许分析符号链接")
        if source.suffix.lower() == ".iso":
            raise ReleaseInfoError("本次支持视频与光盘目录，ISO 支持尚未启用")
        files = self._iter_files(source)
        selected_files = data.get("representative_files")
        if selected_files:
            available = {entry["relative_path"] for entry in files}
            if not set(selected_files) <= available:
                raise ReleaseInfoError("所选代表文件不存在")
        optical = None
        if disc_root(source):
            try:
                optical = optical_input(source, data.get("disc_selection"))
            except FileServiceError as exc:
                if (
                    "没有可用" not in str(exc)
                    or disc_root(source)[0] != "bluray"
                    or data.get("disc_selection")
                ):
                    raise
                optical = {"kind": "bluray", "selected": None}
        videos, warnings = (
            ([], [])
            if optical
            else self._probe_videos(source, files, selected_files, data["include_mediainfo"])
        )
        disc_info = None
        reports = []
        if optical and optical["kind"] == "bluray":
            disc_info, warning = self._bdinfo_text(
                source, optical["selected"], data["bdinfo_timeout"]
            )
            if warning:
                warnings.append(warning)
            if disc_info:
                reports.append(
                    {
                        "id": "bdinfo:" + (optical["selected"] or "whole"),
                        "kind": "BDInfo",
                        "text": disc_info,
                        "publish_text": bdinfo_excerpt(disc_info),
                        "valid": "DISC INFO:" in disc_info and "PLAYLIST REPORT:" in disc_info,
                        "source": (optical["selected"] or "whole") + ".mpls",
                    }
                )
        elif optical:
            for path in optical["reports"]:
                text = self._mediainfo_text(path)
                reports.append(
                    {
                        "id": path.name,
                        "kind": "MediaInfo",
                        "text": text or "",
                        "valid": bool(text),
                        "source": path.name,
                    }
                )
                if not text:
                    warnings.append(f"{path.name} 的完整 MediaInfo 不可用")
        else:
            for video in videos:
                valid = video.get("media_info_tool") == "mediainfo" and bool(
                    video.get("media_info")
                )
                reports.append(
                    {
                        "id": video["relative_path"],
                        "kind": "MediaInfo" if valid else "ffprobe",
                        "text": video.get("media_info") or "",
                        "valid": valid,
                        "source": video["relative_path"],
                        "details": video.get("details") or {},
                    }
                )
                if not valid and (not selected_files or video["relative_path"] in selected_files):
                    warnings.append(
                        f"{video['relative_path']} 缺少完整 MediaInfo；"
                        "ffprobe 摘要不能用于发布就绪检查"
                    )
        title = str(data.get("title") or source.name).strip()
        if not title or len(title) > 240:
            raise ReleaseInfoError("发布标题不能为空且不能超过 240 个字符")
        output_format = str(data.get("format") or "markdown")
        if output_format not in {"markdown", "bbcode", "text"}:
            raise ReleaseInfoError("发布资料格式无效")
        text = self._generate_text(
            title=title,
            files=files,
            videos=videos,
            output_format=output_format,
            disc_info=disc_info,
        )
        if optical and optical["kind"] == "dvd":
            for report in reports:
                if report["valid"]:
                    text += "\n\n" + report["text"]
        return {
            "title": title,
            "root_key": root_key,
            "relative_path": relative,
            "format": output_format,
            "files": files,
            "videos": videos,
            "reports": reports,
            "disc_kind": optical["kind"] if optical else "video",
            "disc_selection": optical["selected"] if optical else None,
            "metadata": data.get("metadata") or {},
            "disc_info": disc_info,
            "disc_info_tool": "bdinfo" if disc_info else None,
            "generated_text": text,
            "warnings": warnings,
            "tools": self.status(),
        }

    def _output_target(
        self,
        title: str,
        output_relative_path: str,
        output_format: str,
        *,
        filename: str | None = None,
    ) -> Path:
        directory, _ = self.files.resolve("release_info", output_relative_path, must_exist=False)
        directory.mkdir(parents=True, exist_ok=True)
        directory, _ = self.files.resolve("release_info", output_relative_path, must_exist=True)
        if directory.is_symlink() or not directory.is_dir():
            raise ReleaseInfoError("发布信息输出路径不是普通目录")
        extension = "md" if output_format == "markdown" else "txt"
        name = filename or f"{_safe_title(title)}.{extension}"
        name = validate_filename(name)
        return directory / name

    @staticmethod
    def _write_atomic(target: Path, content: str, *, overwrite: bool) -> None:
        temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if target.exists() and target.is_dir():
                raise ReleaseInfoError("目标路径是目录，不能覆盖")
            if target.is_symlink():
                raise ReleaseInfoError("目标路径是符号链接，不能覆盖")
            if target.exists() and not overwrite:
                raise FileConflictError("发布信息文件已存在")
            if overwrite:
                os.replace(temporary, target)
            else:
                try:
                    os.link(temporary, target)
                except FileExistsError as exc:
                    raise FileConflictError("发布信息文件已存在") from exc
                temporary.unlink()
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _save_document_file(
        self,
        *,
        title: str,
        output_relative_path: str,
        output_format: str,
        content: str,
        filename: str | None,
        overwrite: bool,
        source: Path | None,
    ) -> str:
        target = self._output_target(
            title,
            output_relative_path,
            output_format,
            filename=filename,
        )
        if source and source.is_dir() and target.resolve().is_relative_to(source.resolve()):
            raise ReleaseInfoError("发布信息文件不能写入源目录")
        self._write_atomic(target, content, overwrite=overwrite)
        relative = target.relative_to(self.files.category_root("release_info").path)
        return relative.as_posix()

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        data = self.with_defaults(data)
        analysis = self.analyze(data)
        output_format = analysis["format"]
        user_text = str(data.get("description") or "").strip()
        content = user_text or analysis["generated_text"]
        source, _ = self.files.resolve(
            analysis["root_key"],
            analysis["relative_path"],
            must_exist=True,
            allow_empty=False,
        )
        output_path = self._save_document_file(
            title=analysis["title"],
            output_relative_path=str(data.get("output_relative_path") or ""),
            output_format=output_format,
            content=content,
            filename=str(data.get("filename") or "").strip() or None,
            overwrite=bool(data.get("overwrite")),
            source=source,
        )
        document_id = self.database.create_release_document(
            title=analysis["title"],
            source_root_key=analysis["root_key"],
            source_relative_path=analysis["relative_path"],
            output_root_key="release_info",
            output_relative_path=output_path,
            format=output_format,
            media={
                "videos": analysis["videos"],
                "reports": analysis["reports"],
                "disc_kind": analysis["disc_kind"],
                "disc_selection": analysis["disc_selection"],
                "metadata": analysis["metadata"],
                "disc_info": analysis.get("disc_info"),
                "disc_info_tool": analysis.get("disc_info_tool"),
                "warnings": analysis["warnings"],
            },
            file_list=analysis["files"],
            generated_text=analysis["generated_text"],
            user_text=user_text,
            status="saved",
        )
        record = self.database.get_release_document(document_id)
        if record is None:
            raise ReleaseInfoError("发布信息记录保存失败")
        record["tools"] = analysis["tools"]
        return record

    def update(self, document_id: int, data: dict[str, Any]) -> dict[str, Any]:
        record = self.database.get_release_document(document_id)
        if record is None:
            raise ReleaseInfoError("发布信息记录不存在")
        content = str(data.get("description", record.get("user_text") or ""))
        if len(content) > 2_000_000:
            raise ReleaseInfoError("发布描述超过 2 MB 限制")
        output_relative_path = str(
            data.get("output_relative_path")
            or str(record.get("output_relative_path") or "").rsplit("/", 1)[0]
        )
        output_path = record.get("output_relative_path")
        if data.get("write_file", True):
            source, _ = self.files.resolve(
                str(record["source_root_key"]),
                str(record["source_relative_path"]),
                must_exist=False,
            )
            filename = Path(str(record.get("output_relative_path") or "")).name or None
            output_path = self._save_document_file(
                title=str(record["title"]),
                output_relative_path=output_relative_path,
                output_format=str(record["format"]),
                content=content or str(record.get("generated_text") or ""),
                filename=filename,
                overwrite=bool(data.get("overwrite", True)),
                source=source if source.exists() else None,
            )
        media = dict(record.get("media") or {})
        if data.get("metadata") is not None:
            media["metadata"] = data["metadata"]
        self.database.update_release_document(
            document_id,
            media_json=json.dumps(media, ensure_ascii=False),
            user_text=content,
            output_relative_path=output_path,
            status="saved",
            error=None,
        )
        updated = self.database.get_release_document(document_id)
        if updated is None:
            raise ReleaseInfoError("发布信息记录不存在")
        return updated

    def get(self, document_id: int) -> dict[str, Any]:
        record = self.database.get_release_document(document_id)
        if record is None:
            raise ReleaseInfoError("发布信息记录不存在")
        record["tools"] = self.status()
        return record

    def list_documents(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        return self.database.list_release_documents(limit=limit, offset=offset)


def format_media_summary(details: dict[str, Any]) -> str:
    return json.dumps(details, ensure_ascii=False, indent=2)


def bdinfo_excerpt(text: str) -> str:
    """Only remove whole sections; preserve lines inside the standard report verbatim."""
    start = text.find("DISC INFO:")
    if start < 0:
        return text
    ends = [
        pos
        for marker in ("FILES:", "CHAPTERS:", "STREAM DIAGNOSTICS:", "QUICK SUMMARY:")
        if (pos := text.find(marker, start)) >= 0
    ]
    return text[start : min(ends) if ends else len(text)]
