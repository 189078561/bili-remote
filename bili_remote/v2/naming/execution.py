"""Two-phase in-place rename handler for the existing processing executor.

Audit is injected by integration. It must persist create/update calls before
returning. No module-local database, worker, task table or project is created.
"""
from __future__ import annotations

from copy import deepcopy
import secrets
from pathlib import PurePosixPath
import threading

from .errors import NamingError

_EXECUTION_LOCK = threading.RLock()


class RenameExecutor:
    def __init__(self, files, audit):
        self.files, self.audit = files, audit
        # All session-scoped service instances share one mutation lock. The
        # existing processing executor is single-worker; integration must keep
        # that guarantee or supply an interprocess operation lock.
        self.lock = _EXECUTION_LOCK

    def prepare(self, operations, *, owner, undo_of=None):
        with self.lock:
            if not operations:
                raise NamingError("no_changes", "没有需要执行的重命名")
            conflicts = self.files.validate_operations(operations)
            if conflicts:
                raise NamingError("rename_conflict", "改名预检未通过", status=409, fields=conflicts)
            batch_id = secrets.token_urlsafe(24)
            items = []
            for ordinal, operation in enumerate(operations):
                item = deepcopy(operation)
                source = PurePosixPath(item["source"]["relative_path"])
                item.update(ordinal=ordinal, temporary_path=str(source.with_name(f".bili-v2-{batch_id}-{ordinal}.tmp")), status="prepared", after_fingerprint=None, error=None)
                items.append(item)
            batch = {"id": batch_id, "owner": owner, "status": "prepared", "undo_of": undo_of, "items": items}
            self.audit.create(deepcopy(batch))
            return batch_id

    def execute(self, batch_id, *, should_cancel=lambda: False):
        with self.lock:
            batch = self.audit.get(batch_id)
            if not batch:
                raise NamingError("batch_not_found", "改名批次不存在", status=404)
            if batch["status"] == "completed":
                return batch
            if batch["status"] != "prepared":
                raise NamingError("recovery_required", "该批次需要恢复，不能重复执行", status=409)
            conflicts = self.files.validate_operations(batch["items"])
            if conflicts:
                raise NamingError("rename_conflict", "执行前资源已变化", status=409, fields=conflicts)
            batch["status"] = "running"
            self.audit.update(deepcopy(batch))
            try:
                for phase in ("staged", "applied"):
                    for item in batch["items"]:
                        if should_cancel():
                            raise NamingError("cancelled", "用户取消改名")
                        source_ref = deepcopy(item["source"])
                        if phase == "staged":
                            destination = item["temporary_path"]
                        else:
                            source_ref["relative_path"] = item["temporary_path"]
                            destination = item["target_relative_path"]
                        # Write intent before the filesystem operation; recovery
                        # locates the original inode after any interrupted step.
                        item["intent"] = phase
                        self.audit.update(deepcopy(batch))
                        self.files.move(source_ref, destination, item["source_fingerprint"])
                        item["status"] = phase
                        if phase == "applied":
                            item["after_fingerprint"] = self.files.fingerprint({**item["source"], "relative_path": destination})
                        self.audit.update(deepcopy(batch))
                batch["status"] = "completed"
                self.audit.update(deepcopy(batch))
                return deepcopy(batch)
            except Exception as exc:
                batch["status"] = "failed"
                batch["error"] = "改名中断，需要检查逐项恢复结果"
                # Avoid arbitrary exception strings containing server paths.
                try:
                    self.audit.update(deepcopy(batch))
                except Exception:
                    pass
                self._recover(batch)
                if isinstance(exc, NamingError) and exc.code == "cancelled" and batch["status"] == "rolled_back":
                    raise NamingError("cancelled", "改名已取消并恢复原文件", status=409) from exc
                raise NamingError("execution_failed", "改名未完成；已尝试恢复，详情见批次审计", status=409) from exc

    def _recover(self, batch):
        """First return applied items to staging, then restore all original names.

        If audit intent was persisted immediately before a crash, identify the
        original file using its fingerprint rather than trusting item status.
        Never remove a temporary file that could not be restored.
        """
        locations = {}
        for item in batch["items"]:
            found = []
            for path in {item["source"]["relative_path"], item["temporary_path"], item["target_relative_path"]}:
                ref = {**item["source"], "relative_path": path}
                try:
                    if self.files.fingerprint(ref) == item["source_fingerprint"]:
                        found.append(path)
                except (NamingError, OSError):
                    continue
            if len(found) == 1:
                locations[item["ordinal"]] = found[0]
            else:
                item["error"] = "无法唯一定位源文件；保留现场，需人工恢复"
        for item in batch["items"]:
            location = locations.get(item["ordinal"])
            if not location or location == item["source"]["relative_path"] or location == item["temporary_path"]:
                continue
            try:
                self.files.move({**item["source"], "relative_path": location}, item["temporary_path"], item["source_fingerprint"])
                locations[item["ordinal"]] = item["temporary_path"]
            except Exception:
                item["error"] = "无法退回临时名；保留现场"
        for item in reversed(batch["items"]):
            location = locations.get(item["ordinal"])
            original = item["source"]["relative_path"]
            try:
                if not location:
                    continue
                if location != original:
                    self.files.move({**item["source"], "relative_path": location}, original, item["source_fingerprint"])
                item["status"], item["error"] = "rolled_back", None
            except Exception:
                item["error"] = "原路径被占用或源已变化，未覆盖，临时文件已保留"
        batch["status"] = "rolled_back" if all(x["status"] == "rolled_back" for x in batch["items"]) else "failed"
        try:
            self.audit.update(deepcopy(batch))
        except Exception:
            pass

    def recover(self, batch_id):
        with self.lock:
            batch = self.audit.get(batch_id)
            if not batch or batch["status"] not in {"running", "failed"}:
                raise NamingError("recovery_not_allowed", "批次不存在或无需恢复", status=409)
            self._recover(batch)
            return deepcopy(batch)

    def undo_operations(self, batch_id, *, owner):
        batch = self.audit.get(batch_id)
        if not batch or batch.get("owner") != owner:
            raise NamingError("batch_not_found", "改名批次不存在", status=404)
        if batch["status"] != "completed":
            raise NamingError("undo_not_allowed", "仅可撤销已完成批次", status=409)
        return [{"source": {**x["source"], "relative_path": x["target_relative_path"], "fingerprint": x["after_fingerprint"]},
                 "target_relative_path": x["source"]["relative_path"], "source_fingerprint": x["after_fingerprint"],
                 "temporary_required": True, "sidecar_of": x.get("sidecar_of"), "warnings": []}
                for x in batch["items"]]
