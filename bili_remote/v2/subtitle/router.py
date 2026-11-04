"""Dependency-injected HTTP boundary for on-demand subtitle rendering."""

from __future__ import annotations

import inspect
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from .renderer import SubtitleService
from .rules import SubtitleRuleError

MAX_REQUEST_BYTES = 4 * 1024 * 1024


def create_router(*, service=None, require_authenticated, require_csrf):
    """Create the module router with explicit authentication and CSRF guards.

    The host application owns guard implementation and registration. Requiring
    both callbacks prevents a module router from accidentally exposing an
    unprotected POST endpoint when mounted by the integration session.
    """
    if not callable(require_authenticated) or not callable(require_csrf):
        raise TypeError("authentication and CSRF dependencies are required")
    subtitle_service = service or SubtitleService()
    router = APIRouter()

    @router.post("/api/v2/subtitles/render", dependencies=[
        Depends(require_authenticated), Depends(require_csrf),
    ], openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": {
                "type": "object", "additionalProperties": False,
                "required": ["context", "rule"],
                "properties": {
                    "context": {"type": "object", "description": "Shared V2 ResourceContext"},
                    "rule": {"type": "object", "description": "Strict SubtitleRule; platform is not supported"},
                },
            }}},
        },
        "responses": {
            "200": {"description": "SubtitleResult; generated text is not persisted"},
            "422": {"description": "Invalid SubtitleRule or request"},
        },
    })
    async def render_subtitle(request: Request):
        headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
        length = request.headers.get("content-length")
        if length is not None:
            try:
                content_length = int(length)
            except ValueError:
                return JSONResponse({"detail": {"code": "invalid_request", "message": "Invalid content length.", "fields": ["request"]}},
                                    status_code=422, headers=headers)
            if content_length > MAX_REQUEST_BYTES:
                return JSONResponse({"detail": {"code": "invalid_request", "message": "Request exceeds the 4 MiB limit.", "fields": ["request"]}},
                                    status_code=422, headers=headers)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_REQUEST_BYTES:
                return JSONResponse({"detail": {"code": "invalid_request", "message": "Request exceeds the 4 MiB limit.", "fields": ["request"]}},
                                    status_code=422, headers=headers)
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return JSONResponse({"detail": {"code": "invalid_request", "message": "Request body must be valid JSON.", "fields": ["request"]}},
                                status_code=422, headers=headers)
        if not isinstance(payload, dict) or set(payload) != {"context", "rule"}:
            return JSONResponse({"detail": {"code": "invalid_request", "message": "Request must contain only context and rule.", "fields": ["request"]}},
                                status_code=422, headers=headers)
        if not isinstance(payload["context"], dict) or not isinstance(payload["rule"], dict):
            return JSONResponse({"detail": {"code": "invalid_request", "message": "context and rule must be objects.", "fields": ["context", "rule"]}},
                                status_code=422, headers=headers)
        try:
            result = subtitle_service.render(payload["context"], payload["rule"])
            if inspect.isawaitable(result):
                result = await result
            if not isinstance(result, dict):
                raise TypeError("SubtitleService.render must return an object")
            return JSONResponse(result, headers=headers)
        except SubtitleRuleError as error:
            return JSONResponse({
                "detail": {"code": error.code, "message": str(error), "fields": error.fields},
            }, status_code=422, headers=headers)

    return router
