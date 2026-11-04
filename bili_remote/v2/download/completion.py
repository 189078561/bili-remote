"""Narrow, best-effort handoff from completed legacy downloads to NamingService."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

_ALLOWED_ROOTS = {"downloads", "torrents", "images", "release_info"}
_ALLOWED_MODES = {"web", "tv"}
_ORIGIN_FIELDS = (
    "bvid",
    "season_id",
    "ep_id",
    "cid",
    "original_title",
    "episode_index",
)


class NamingAnalyzer(Protocol):
    async def analyze(
        self,
        resources: list[dict[str, Any]],
        origins: list[dict[str, Any]],
        context: Any,
        rules: Any,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class RecognitionProposal:
    job_id: int
    resources: tuple[dict[str, Any], ...]
    origins: tuple[dict[str, Any], ...]
    context: Any = None
    warnings: tuple[dict[str, str], ...] = ()
    naming_available: bool = True


@dataclass(slots=True)
class DownloadCompletionAdapter:
    """Call NamingService once per job; recognition errors never alter job status."""

    naming_service: NamingAnalyzer
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _completed: dict[int, asyncio.Task[RecognitionProposal]] = field(
        default_factory=dict, init=False, repr=False
    )

    async def on_completed(
        self, job_id: int, origin_refs: list[dict[str, Any]]
    ) -> RecognitionProposal:
        if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id < 1:
            raise ValueError("job_id must be a positive integer")
        async with self._lock:
            task = self._completed.get(job_id)
            if task is None:
                task = asyncio.create_task(self._recognize(job_id, origin_refs))
                self._completed[job_id] = task
        return await asyncio.shield(task)

    async def _recognize(
        self, job_id: int, origin_refs: list[dict[str, Any]]
    ) -> RecognitionProposal:
        warnings: list[dict[str, str]] = []
        try:
            origins, resources = _normalize_origins(job_id, origin_refs)
        except (TypeError, ValueError) as exc:
            return RecognitionProposal(
                job_id=job_id,
                resources=(),
                origins=(),
                warnings=({"code": "invalid_download_origin", "message": str(exc)},),
                naming_available=False,
            )
        try:
            result = await self.naming_service.analyze(
                resources=resources,
                origins=origins,
                context=None,
                rules=None,
            )
            context = result.get("context") if isinstance(result, dict) else result
            result_warnings = result.get("warnings", []) if isinstance(result, dict) else []
            warnings.extend(_safe_warnings(result_warnings))
            return RecognitionProposal(
                job_id, tuple(resources), tuple(origins), context, tuple(warnings), True
            )
        except Exception:
            # Do not expose upstream exception text: it may contain credentials or URLs.
            warnings.append({
                "code": "recognition_unavailable",
                "message": "下载已完成，资源识别暂不可用；可稍后从文件管理重新识别。",
            })
            return RecognitionProposal(
                job_id, tuple(resources), tuple(origins), None, tuple(warnings), False
            )


def _normalize_origins(
    job_id: int, origin_refs: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(origin_refs, list):
        raise TypeError("origin_refs must be a list")
    if not origin_refs:
        raise ValueError("at least one completed resource reference is required")
    origins: list[dict[str, Any]] = []
    resources: list[dict[str, Any]] = []
    for entry in origin_refs:
        if not isinstance(entry, dict):
            raise TypeError("each download origin must be an object")
        ref = entry.get("original_resource")
        if not isinstance(ref, dict):
            raise ValueError("original_resource is required")
        root_key = ref.get("root_key")
        relative_path = ref.get("relative_path")
        fingerprint = ref.get("fingerprint")
        if root_key not in _ALLOWED_ROOTS:
            raise ValueError("original_resource has an invalid root_key")
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or relative_path.startswith("/")
            or "\\" in relative_path
        ):
            raise ValueError("original_resource must use a relative path")
        path_parts = relative_path.split("/")
        if any(part in {"", ".", ".."} for part in path_parts) or "\x00" in relative_path:
            raise ValueError("original_resource path is not normalized")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError("original_resource fingerprint is required")
        kind = ref.get("kind")
        if kind not in {"file", "directory"}:
            raise ValueError("original_resource kind must be file or directory")
        normalized_ref = {
            "root_key": root_key,
            "relative_path": relative_path,
            "kind": kind,
            "fingerprint": fingerprint,
        }
        resources.append(normalized_ref)
        origin: dict[str, Any] = {name: _nullable(entry.get(name)) for name in _ORIGIN_FIELDS}
        raw_title = entry.get("original_title")
        origin["original_title"] = (
            raw_title.strip() or None if isinstance(raw_title, str) else None
        )
        raw_index = entry.get("episode_index")
        origin["episode_index"] = raw_index if isinstance(raw_index, int) and not isinstance(raw_index, bool) else None
        mode = entry.get("api_mode")
        if mode not in _ALLOWED_MODES:
            raise ValueError("api_mode must be web or tv")
        origin.update({
            "job_id": job_id,
            "api_mode": mode,
            "original_resource": normalized_ref,
        })
        origins.append(origin)
    return origins, resources


def _nullable(value: Any) -> str | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def _safe_warnings(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    safe: list[dict[str, str]] = []
    for item in values:
        if isinstance(item, dict):
            code = item.get("code")
            message = item.get("message")
            if isinstance(code, str) and isinstance(message, str):
                safe.append({"code": code[:80], "message": message[:300]})
    return safe
