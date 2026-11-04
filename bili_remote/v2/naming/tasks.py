"""Handlers for registration in the existing processing manager, not a worker."""
import asyncio

from .errors import NamingError


class NamingTaskHandlers:
    task_types = {"v2_rename_apply", "v2_rename_undo", "v2_media_probe"}

    def __init__(self, service):
        self.service = service

    async def handle(self, task_type, input_data, *, should_cancel=lambda: False):
        if task_type not in self.task_types or not isinstance(input_data, dict):
            raise NamingError("invalid_task", "不支持的命名任务")
        if task_type == "v2_media_probe":
            if set(input_data) != {"resources", "expected_fingerprints"}:
                raise NamingError("invalid_task", "探测任务仅接受资源引用和指纹")
            return await self.service.probe_resources(input_data["resources"], input_data["expected_fingerprints"], should_cancel=should_cancel)
        if set(input_data) != {"batch_id"} or not self.service.executor:
            raise NamingError("invalid_task", "改名任务仅接受已持久化的批次引用")
        batch = await asyncio.to_thread(self.service.executor.execute, input_data["batch_id"], should_cancel=should_cancel)
        return {"batch_id": batch["id"], "status": batch["status"], "changed_count": len(batch["items"]), "artifact_refs": []}
