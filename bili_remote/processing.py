from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .collage import CollageService
from .config import Settings
from .database import Database
from .file_service import FileService, OperationCancelled
from .media_probe import probe_executable, probe_json
from .naming import (
    NamingError,
    PreviewStore,
    apply_preview,
    rollback_latest,
)
from .screenshots import ScreenshotService
from .workspace import ScanSummary, WorkspaceError, resolve_workspace_path, scan_project

TASK_TYPES = {
    "torrent_build",
    "release_report",
    "image_upload",
    "scan",
    "import",
    "rename_apply",
    "rename_rollback",
    "media_check",
    "file_operation",
    "collage",
    "screenshot",
    "screenshot_collage",
}


class ProcessingTaskManager:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        previews: PreviewStore,
        files: FileService | None = None,
        collage: CollageService | None = None,
        screenshots: ScreenshotService | None = None,
    ) -> None:
        self.tool_jobs = None
        self.settings = settings
        self.database = database
        self.previews = previews
        self.files = files
        self.collage = collage
        self.screenshots = screenshots
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._worker and not self._worker.done():
            return
        self._stop.clear()
        self._worker = asyncio.create_task(self._run_worker(), name="processing-worker")
        self._wake.set()

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._worker:
            try:
                await asyncio.wait_for(self._worker, timeout=15)
            except TimeoutError:
                self._worker.cancel()
                await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    def wake(self) -> None:
        self._wake.set()

    def enqueue(
        self,
        *,
        project_id: int | None,
        task_type: str,
        input_data: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if task_type not in TASK_TYPES:
            raise ValueError("不支持的后台任务类型")
        key = idempotency_key or f"{task_type}:{secrets.token_hex(12)}"
        if idempotency_key:
            active = self.database.find_active_processing_task(task_type, idempotency_key)
            if active:
                return active, False
        log_path = self.settings.logs_dir / (
            f"task-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(5)}.log"
        )
        try:
            task_id = self.database.create_processing_task(
                project_id=project_id,
                task_type=task_type,
                input_data=input_data,
                log_path=str(log_path),
                idempotency_key=key,
            )
        except sqlite3.IntegrityError:
            active = self.database.find_active_processing_task(task_type, key)
            if active:
                return active, False
            raise
        if project_id is not None:
            self.database.transition_release_project(project_id, "processing")
        task = self.database.get_processing_task(task_id)
        if not task:
            raise RuntimeError("后台任务创建失败")
        self.wake()
        return task, True

    def cancel(self, task_id: int) -> bool:
        task = self.database.get_processing_task(task_id)
        cancelled = self.database.cancel_processing_task(task_id)
        if cancelled:
            latest = self.database.get_processing_task(task_id)
            if task and latest and latest["status"] == "cancelled" and task.get("project_id"):
                project_id = int(task["project_id"])
                self._settle_project(project_id, "ready")
                self._event(project_id, "task_cancelled", f"{task['task_type']} 任务已取消")
            self.wake()
        return cancelled

    def retry(self, task_id: int) -> bool:
        task = self.database.get_processing_task(task_id)
        if not task or not self.database.retry_processing_task(task_id):
            return False
        if task.get("project_id"):
            self.database.transition_release_project(int(task["project_id"]), "processing")
        self.wake()
        return True

    async def _run_worker(self) -> None:
        while not self._stop.is_set():
            task = self.database.next_queued_processing_task()
            if not task:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=2.0)
                except TimeoutError:
                    pass
                continue
            if not self.database.claim_processing_task(int(task["id"])):
                continue
            task = self.database.get_processing_task(int(task["id"]))
            if not task:
                continue
            if task.get("project_id"):
                self.database.transition_release_project(int(task["project_id"]), "processing")
            await self._execute(task)

    async def _execute(self, task: dict[str, Any]) -> None:
        task_id = int(task["id"])
        log_file = Path(task.get("log_path") or self.settings.logs_dir / f"task-{task_id}.log")
        log_file.parent.mkdir(parents=True, exist_ok=True)

        def log(message: str) -> None:
            stamp = datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            with log_file.open("a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}] {message}\n")

        def should_cancel() -> bool:
            latest = self.database.get_processing_task(task_id)
            return bool(latest and latest.get("cancel_requested"))

        project_id = int(task["project_id"]) if task.get("project_id") else None
        log(f"任务 #{task_id} 开始：{task['task_type']}")
        try:
            result = await self._dispatch(task, log=log, should_cancel=should_cancel)
            if should_cancel():
                raise asyncio.CancelledError
            tool_failed = (
                task["task_type"] in {"torrent_build", "release_report", "image_upload"}
                and int(result.get("failed") or 0) > 0
            )
            workflow_failed = result.get("output_mode") and int(result.get("failed") or 0) > 0
            failed_operation = (
                task["task_type"] == "file_operation" and int(result.get("failed") or 0) > 0
            )
            collage_failed = (
                task["task_type"] == "collage" and int(result.get("succeeded") or 0) == 0
            )
            collage_partial = (
                task["task_type"] == "collage"
                and int(result.get("failed") or 0) > 0
                and int(result.get("succeeded") or 0) > 0
            )
            screenshot_failed = (
                task["task_type"] in {"screenshot", "screenshot_collage"}
                and int(result.get("succeeded") or 0) == 0
            )
            screenshot_partial = (
                task["task_type"] in {"screenshot", "screenshot_collage"}
                and int(result.get("failed") or 0) > 0
                and int(result.get("succeeded") or 0) > 0
            )
            self.database.update_processing_task(
                task_id,
                status=(
                    "failed"
                    if failed_operation
                    or collage_failed
                    or screenshot_failed
                    or tool_failed
                    or workflow_failed
                    else "completed"
                ),
                result_json=json.dumps(result, ensure_ascii=False),
                finished_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
                error=(
                    f"{result.get('failed', 0)} 个工具项目失败"
                    if tool_failed
                    else f"{result.get('failed', 0)} 个文件操作失败"
                    if failed_operation
                    else f"{result.get('failed', 0)} 个视频拼接失败"
                    if collage_failed or collage_partial
                    else f"{result.get('failed', 0)} 个截图任务失败"
                    if screenshot_failed or screenshot_partial
                    else None
                ),
            )
            if project_id is not None:
                next_status = "ready" if task["task_type"] in {"scan", "import"} else "review"
                self._settle_project(project_id, next_status)
                self._event(
                    project_id,
                    "task_completed",
                    f"{task['task_type']} 任务完成",
                    result,
                )
            log(f"任务 #{task_id} 完成")
        except (asyncio.CancelledError, OperationCancelled):
            self._mark_cancelled(task_id, project_id, task["task_type"], log)
        except Exception as exc:
            if should_cancel():
                self._mark_cancelled(task_id, project_id, task["task_type"], log)
            else:
                message = str(exc) or exc.__class__.__name__
                self.database.update_processing_task(
                    task_id,
                    status="failed",
                    error=message,
                    finished_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
                )
                if project_id is not None:
                    self._settle_project(project_id, "failed")
                    self._event(project_id, "task_failed", message)
                log(f"任务 #{task_id} 失败：{message}")

    def _mark_cancelled(
        self,
        task_id: int,
        project_id: int | None,
        task_type: str,
        log,
    ) -> None:
        self.database.update_processing_task(
            task_id,
            status="cancelled",
            finished_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
            error="任务已取消",
        )
        if project_id is not None:
            self._settle_project(project_id, "ready")
            self._event(project_id, "task_cancelled", f"{task_type} 任务已取消")
        log(f"任务 #{task_id} 已取消")

    def _settle_project(self, project_id: int, terminal_status: str) -> None:
        status = (
            "processing" if self.database.active_project_task_count(project_id) else terminal_status
        )
        self.database.transition_release_project(project_id, status)

    async def _dispatch(
        self,
        task: dict[str, Any],
        *,
        log,
        should_cancel,
    ) -> dict[str, Any]:
        task_type = task["task_type"]
        data = task.get("input") or {}
        project_id = int(task["project_id"]) if task.get("project_id") else None
        if task_type in {"torrent_build", "release_report", "image_upload"}:
            return await asyncio.to_thread(self.tool_jobs.run, task, log, should_cancel)
        if task_type in {"scan", "import"}:
            if project_id is None:
                raise WorkspaceError("扫描任务缺少项目")
            summary = await asyncio.to_thread(
                scan_project,
                self.settings,
                self.database,
                project_id,
                root_key=str(data.get("root_key") or ""),
                relative_path=str(data.get("relative_path") or ""),
                season_hint=(int(data["season"]) if data.get("season") is not None else None),
                log=log,
                should_cancel=should_cancel,
            )
            return self._summary_payload(summary)
        if task_type == "rename_apply":
            if project_id is None:
                raise NamingError("重命名任务缺少项目")
            preview = self.previews.get(str(data.get("preview_token") or ""), project_id)
            result = await asyncio.to_thread(
                apply_preview,
                self.settings,
                self.database,
                preview,
                task_id=int(task["id"]),
                log=log,
                should_cancel=should_cancel,
            )
            self.previews.remove(preview.token)
            return result
        if task_type == "rename_rollback":
            if project_id is None:
                raise NamingError("回滚任务缺少项目")
            return await asyncio.to_thread(
                rollback_latest,
                self.settings,
                self.database,
                project_id,
                log=log,
                should_cancel=should_cancel,
            )
        if task_type == "media_check":
            if project_id is None:
                raise NamingError("检查任务缺少项目")
            return await self._media_check(
                project_id,
                task_id=int(task["id"]),
                log=log,
                should_cancel=should_cancel,
            )
        if task_type == "file_operation":
            if self.files is None:
                raise RuntimeError("文件服务未初始化")

            def progress(payload: dict[str, Any]) -> None:
                self.database.update_processing_task(
                    int(task["id"]),
                    result_json=json.dumps(payload, ensure_ascii=False),
                )

            return await asyncio.to_thread(
                self.files.execute_operation,
                data,
                task_id=int(task["id"]),
                log=log,
                should_cancel=should_cancel,
                progress=progress,
            )
        if task_type == "collage":
            if self.collage is None:
                raise RuntimeError("缩略图拼接服务未初始化")

            def progress(payload: dict[str, Any]) -> None:
                self.database.update_processing_task(
                    int(task["id"]),
                    result_json=json.dumps(payload, ensure_ascii=False),
                )

            return await asyncio.to_thread(
                self.collage.run,
                data,
                task_id=int(task["id"]),
                log=log,
                should_cancel=should_cancel,
                progress=progress,
            )
        if task_type == "screenshot":
            if self.screenshots is None:
                raise RuntimeError("截图服务未初始化")

            def screenshot_progress(payload: dict[str, Any]) -> None:
                self.database.update_processing_task(
                    int(task["id"]),
                    result_json=json.dumps(payload, ensure_ascii=False),
                )

            result = await asyncio.to_thread(
                self.screenshots.run,
                data,
                task_id=int(task["id"]),
                log=log,
                should_cancel=should_cancel,
                progress=screenshot_progress,
            )
            final = await asyncio.to_thread(
                self.screenshots.finish_output,
                data["session_id"],
                task_id=int(task["id"]),
                log=log,
                should_cancel=should_cancel,
                progress=screenshot_progress,
            )
            if final:
                result = {**result, **final}
            session = self.screenshots.get_session(data["session_id"])
            if session.get("params", {}).get("output_mode"):
                result["output_mode"] = session["params"]["output_mode"]
                if result.get("image_failed"):
                    result.update(status="partial", failed=max(1, result.get("failed", 0)))
            if self.tool_jobs:
                uploads = await asyncio.to_thread(
                    self.tool_jobs.auto_upload,
                    data["session_id"],
                    log,
                    should_cancel,
                    screenshot_progress if result.get("output_mode") else None,
                )
                if uploads:
                    result["uploads"] = uploads
                    if any(item["status"] != "completed" for item in uploads):
                        result.update(status="partial", failed=max(1, result.get("failed", 0)))
            return result
        if task_type == "screenshot_collage":
            if self.screenshots is None:
                raise RuntimeError("截图服务未初始化")

            def screenshot_collage_progress(payload: dict[str, Any]) -> None:
                self.database.update_processing_task(
                    int(task["id"]),
                    result_json=json.dumps(payload, ensure_ascii=False),
                )

            return await asyncio.to_thread(
                self.screenshots.run_collage,
                data,
                task_id=int(task["id"]),
                log=log,
                should_cancel=should_cancel,
                progress=screenshot_collage_progress,
            )
        raise ValueError("不支持的后台任务类型")

    @staticmethod
    def _summary_payload(summary: ScanSummary) -> dict[str, Any]:
        return {
            "scanned": summary.scanned,
            "supported": summary.supported,
            "unsupported": summary.unsupported,
            "missing": summary.missing,
            "counts": summary.counts,
            "warnings": summary.warnings,
        }

    def _event(
        self,
        project_id: int,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.database.create_release_event(
                project_id=project_id,
                event_type=event_type,
                message=message,
                payload=payload,
            )
        except sqlite3.Error:
            pass

    def _probe_executable(self) -> str | None:
        return probe_executable(self.settings)

    async def _probe(self, path: Path) -> dict[str, Any]:
        return await probe_json(self.settings, path)

    async def _media_check(
        self,
        project_id: int,
        *,
        task_id: int,
        log,
        should_cancel,
    ) -> dict[str, Any]:
        files = [
            item
            for item in self.database.list_release_files(project_id, include_missing=False)
            if item["file_role"] in {"video", "iso", "bdmv"}
        ]
        if not files:
            result = {
                "status": "failed",
                "tool": self._probe_executable(),
                "files": [],
                "error": "没有可检查的视频、ISO 或 BDMV 资源",
            }
            self.database.create_release_check(
                project_id=project_id,
                task_id=task_id,
                status="failed",
                result=result,
                error=result["error"],
            )
            raise NamingError(result["error"])
        executable = self._probe_executable()
        if not executable:
            result = {
                "status": "unavailable",
                "tool": None,
                "files": [],
                "error": "ffprobe 不可用，请确认容器内已安装 ffmpeg",
            }
            self.database.create_release_check(
                project_id=project_id,
                task_id=task_id,
                status="unavailable",
                result=result,
                error=result["error"],
            )
            log(result["error"])
            return result
        results: list[dict[str, Any]] = []
        failed = 0
        for item in files:
            if should_cancel():
                raise asyncio.CancelledError
            try:
                file_path, relative = resolve_workspace_path(
                    self.settings,
                    item["root_key"],
                    item["relative_path"],
                    must_exist=True,
                    allow_empty=False,
                )
                probe_path = file_path
                if item["file_role"] == "bdmv":
                    streams = sorted(
                        (file_path / "STREAM").glob("*"),
                        key=lambda path: path.stat().st_size if path.is_file() else 0,
                        reverse=True,
                    )
                    if not streams:
                        raise RuntimeError("BDMV 中没有可探测的 STREAM 文件")
                    probe_path = streams[0]
                payload = await self._probe(probe_path)
                streams = payload.get("streams") or []
                video_stream = next(
                    (stream for stream in streams if stream.get("codec_type") == "video"),
                    {},
                )
                audio_codecs = [
                    stream.get("codec_name")
                    for stream in streams
                    if stream.get("codec_type") == "audio"
                ]
                subtitle_streams = [
                    {
                        "index": stream.get("index"),
                        "codec": stream.get("codec_name"),
                        "language": (stream.get("tags") or {}).get("language"),
                        "title": (stream.get("tags") or {}).get("title"),
                    }
                    for stream in streams
                    if stream.get("codec_type") == "subtitle"
                ]
                format_data = payload.get("format") or {}
                scan_result = dict(item.get("scan_result") or {})
                scan_result.update(
                    {
                        "container": format_data.get("format_name"),
                        "duration": float(format_data.get("duration") or 0) or None,
                        "resolution": (
                            f"{video_stream.get('height')}p" if video_stream.get("height") else None
                        ),
                        "video_codec": video_stream.get("codec_name"),
                        "audio_codec": ",".join(codec for codec in audio_codecs if codec),
                        "internal_subtitles": subtitle_streams,
                        "probe_path": relative,
                    }
                )
                self.database.update_release_file(
                    int(item["id"]),
                    scan_result_json=json.dumps(scan_result, ensure_ascii=False),
                )
                results.append(
                    {
                        "file_id": item["id"],
                        "relative_path": item["relative_path"],
                        "status": "completed",
                        "container": format_data.get("format_name"),
                        "duration": float(format_data.get("duration") or 0) or None,
                        "audio_tracks": audio_codecs,
                        "internal_subtitles": subtitle_streams,
                    }
                )
                log(f"检查完成：{item['relative_path']}")
            except Exception as exc:
                failed += 1
                results.append(
                    {
                        "file_id": item["id"],
                        "relative_path": item["relative_path"],
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                log(f"检查失败：{item['relative_path']}：{exc}")
        status = "failed" if failed else "completed"
        result = {"status": status, "tool": executable, "files": results}
        self.database.create_release_check(
            project_id=project_id,
            task_id=task_id,
            status=status,
            result=result,
            error=f"{failed} 个文件探测失败" if failed else None,
        )
        if failed:
            raise NamingError(f"{failed} 个文件 ffprobe 检查失败")
        return result


def task_log_content(path: str | None, *, lines: int = 200) -> str:
    if not path:
        return ""
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-max(1, min(lines, 500)) :])
