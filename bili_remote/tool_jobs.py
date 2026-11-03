"""Adapt existing independent tools to the durable processing queue."""

from __future__ import annotations

import json

from .file_service import OperationCancelled
from .image_hosts import _stat_fingerprint
from .media_tools import tool_cancel


class ToolJobs:
    def __init__(self, database, reports, torrents, uploads, screenshots):
        self.database, self.reports, self.torrents = database, reports, torrents
        self.uploads, self.screenshots = uploads, screenshots

    def run(self, task, log, should_cancel):
        task_id, data = task["id"], task["input"]
        result = dict(task.get("result") or {})

        def checkpoint(payload):
            result.update(payload)
            self.database.update_processing_task(
                task_id, result_json=json.dumps(result, ensure_ascii=False)
            )

        if should_cancel():
            raise OperationCancelled
        token = tool_cancel.set(should_cancel)
        try:
            if task["task_type"] == "torrent_build":
                if result.get("record"):
                    return result
                record = self.torrents.create(
                    data, should_cancel=should_cancel, progress=checkpoint
                )
                checkpoint({"record": record, "succeeded": 1, "failed": 0})
            elif task["task_type"] == "release_report":
                if result.get("record"):
                    return result
                log("读取完整媒体报告")
                record = self.reports.create(data)
                checkpoint({"record": record, "succeeded": 1, "failed": 0})
            elif task["task_type"] == "image_upload":
                records = list(result.get("uploads") or [])
                by_source = {(r["source_root_key"], r["source_relative_path"]): r for r in records}
                for source in data["items"]:
                    if should_cancel():
                        raise OperationCancelled
                    key = source["root_key"], source["relative_path"]
                    previous = by_source.get(key)
                    if previous and previous["status"] == "completed":
                        continue
                    if previous:
                        record = self.uploads.retry(previous["id"])
                        records[records.index(previous)] = record
                    else:
                        record = self.uploads.upload_file(
                            **source,
                            provider=data.get("provider"),
                            session_id=data.get("session_id"),
                        )
                        records.append(record)
                    by_source[key] = record
                    checkpoint(
                        {
                            "uploads": records,
                            "succeeded": sum(r["status"] == "completed" for r in records),
                            "failed": sum(r["status"] == "failed" for r in records),
                        }
                    )
                    log(f"图片上传：{source['relative_path']} — {record['status']}")
            return result
        finally:
            tool_cancel.reset(token)

    def auto_upload(self, session_id, log, should_cancel, progress=None):
        session = self.screenshots.get_session(session_id)
        params = session.get("params") or {}
        if not params.get("auto_upload"):
            return
        records = []
        total = (
            sum(
                item.get("status") == "completed"
                for item in session.get("final_outputs", {}).values()
            )
            if params.get("output_mode") == "collage"
            else sum(
                item.get("status") == "completed"
                for video in session.get("videos", [])
                for item in video.get("items", [])
            )
        )

        def checkpoint():
            if progress:
                progress(
                    {
                        "stage": "upload",
                        "total": total,
                        "succeeded": sum(r["status"] == "completed" for r in records),
                        "failed": sum(r["status"] != "completed" for r in records),
                    }
                )

        checkpoint()
        if params.get("output_mode") == "collage":
            for item in (session.get("final_outputs") or {}).values():
                if should_cancel():
                    raise OperationCancelled
                if item.get("status") != "completed":
                    continue
                item_id = "collage:" + item["video_id"]
                path = item["output_relative_path"]
                local, _ = self.uploads.files.resolve("images", path, must_exist=True)
                previous = self.database.latest_uploads_for_session_items(session_id).get(item_id)
                if (
                    previous
                    and previous["status"] == "completed"
                    and previous["source_relative_path"] == path
                    and previous.get("source_fingerprint") == _stat_fingerprint(local)
                ):
                    records.append(previous)
                    checkpoint()
                    continue
                records.append(
                    self.uploads.upload_file(
                        root_key="images",
                        relative_path=path,
                        session_id=session_id,
                        provider=params.get("upload_provider"),
                        session_item_id=item_id,
                    )
                )
                log(f"自动上传拼图：{records[-1]['status']}")
                checkpoint()
            return records
        for video in session.get("videos") or []:
            for item in video.get("items") or []:
                if should_cancel():
                    raise OperationCancelled
                if item["status"] != "completed":
                    continue
                path = (
                    item.get("raw_relative_path")
                    if params.get("upload_variant") == "raw"
                    else item["relative_path"]
                )
                previous = self.database.latest_uploads_for_session_items(session_id).get(
                    item["id"]
                )
                local, _ = self.uploads.files.resolve(
                    "images", path or item["relative_path"], must_exist=True
                )
                if (
                    previous
                    and previous["status"] == "completed"
                    and previous["source_relative_path"] == path
                    and previous.get("source_fingerprint") == _stat_fingerprint(local)
                ):
                    records.append(previous)
                    checkpoint()
                    continue
                record = self.uploads.upload_file(
                    root_key="images",
                    relative_path=path or item["relative_path"],
                    session_id=session_id,
                    provider=params.get("upload_provider"),
                    session_item_id=item["id"],
                )
                log(f"自动上传：{record['status']}")
                records.append(record)
                checkpoint()
        return records
