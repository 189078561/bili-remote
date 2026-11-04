"""Unregistered V2 router. Integration supplies authentication, CSRF and DI."""
from __future__ import annotations

from functools import wraps
import inspect

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from .errors import NamingError


class NamingRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handler(request):
            try:
                if request.method != "GET" and len(await request.body()) > 4 * 1024 * 1024:
                    raise NamingError("request_too_large", "请求超过大小限制")
                response = await original(request)
            except RequestValidationError as exc:
                response = JSONResponse(status_code=422, content={"detail": {"code": "invalid_request", "message": "请求参数无效", "fields": [".".join(str(x) for x in error["loc"]) for error in exc.errors()]}})
            except NamingError as exc:
                response = JSONResponse(status_code=exc.status, content={"detail": exc.detail()})
            except HTTPException as exc:
                response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)
            response.headers["Cache-Control"] = "no-store"
            return response
        return handler


async def _call(callback, *args):
    value = callback(*args)
    return await value if inspect.isawaitable(value) else value


def create_router(get_service, *, authenticate, csrf):
    """Callbacks accept Request. authenticate returns the opaque session owner.

    No permissive defaults: even preview requires a valid login and CSRF.
    get_service(Request) must return a session-scoped NamingService.
    """
    router = APIRouter(prefix="/api/v2", tags=["v2-naming"], route_class=NamingRoute)

    async def boundary(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        owner = await _call(authenticate, request)
        if not owner:
            raise HTTPException(401, detail={"code": "unauthenticated", "message": "请先登录", "fields": []})
        if request.method != "GET":
            if not await _call(csrf, request):
                raise HTTPException(403, detail={"code": "csrf_failed", "message": "请求验证失败", "fields": []})
            if len(await request.body()) > 4 * 1024 * 1024:
                raise HTTPException(422, detail={"code": "request_too_large", "message": "请求超过大小限制", "fields": []})
        service = await _call(get_service, request)
        if service.owner != owner:
            raise HTTPException(403, detail={"code": "session_mismatch", "message": "会话不匹配", "fields": []})
        return service

    def check(body, required, allowed):
        if not isinstance(body, dict) or set(body) - set(allowed) or set(required) - set(body):
            raise NamingError("invalid_request", "请求字段缺失或包含不支持字段")

    def endpoint(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except NamingError as exc:
                raise HTTPException(exc.status, detail=exc.detail(), headers={"Cache-Control": "no-store"}) from exc
            except (ValueError, TypeError, KeyError) as exc:
                # Never expose payloads, upstream URLs or credentials in errors.
                raise HTTPException(422, detail={"code": "invalid_request", "message": "参数或规则无效，请检查输入", "fields": []}, headers={"Cache-Control": "no-store"}) from exc
        return wrapped

    @router.post("/resources/resolve")
    @endpoint
    async def resolve(body: dict = Body(...), service=Depends(boundary)):
        check(body, ["resources"], ["resources", "origins", "selection", "context", "manual_overrides"])
        context = await service.analyze(body["resources"], body.get("origins"), body.get("context"), selection=body.get("selection"), manual_overrides=body.get("manual_overrides"))
        return {"context": context, "changes": service.last_changes, "capabilities": service.last_capabilities}

    @router.post("/resources/probe", status_code=202)
    @endpoint
    async def probe(body: dict = Body(...), service=Depends(boundary)):
        check(body, ["resources", "expected_fingerprints"], ["resources", "expected_fingerprints"])
        return {"task": await service.enqueue_probe(body["resources"], body["expected_fingerprints"])}

    @router.get("/metadata/search")
    @endpoint
    async def search(query: str = Query(..., min_length=1, max_length=200), media_type: str = "movie", providers: str = "tmdb,bangumi", year: int | None = None, page: int = Query(1, ge=1, le=1000), service=Depends(boundary)):
        if not service.resolver:
            raise NamingError("integration_unavailable", "资料适配器未接入", status=503)
        return await service.resolver.search(query, media_type, providers.split(","), year=year, page=page)

    @router.get("/metadata/capabilities")
    @endpoint
    async def capabilities(provider: str, service=Depends(boundary)):
        if not service.resolver:
            raise NamingError("integration_unavailable", "资料适配器未接入", status=503)
        return await service.resolver.capabilities(provider)

    @router.post("/naming/preview")
    @endpoint
    async def preview(body: dict = Body(...), service=Depends(boundary)):
        check(body, ["context"], ["context", "overrides", "rules"])
        return service.preview(body["context"], body.get("overrides"), rules=body.get("rules"))

    async def apply(body, service, undo):
        check(body, ["preview_token", "expected_revision", "confirm", "idempotency_key"], ["preview_token", "expected_revision", "confirm", "idempotency_key"])
        if body["confirm"] is not True or type(body["expected_revision"]) is not int:
            raise NamingError("confirmation_required", "请明确确认本次预览")
        return await service.apply(body["preview_token"], body["expected_revision"], body["idempotency_key"], undo=undo)

    @router.post("/naming/apply", status_code=202)
    @endpoint
    async def naming_apply(body: dict = Body(...), service=Depends(boundary)):
        return await apply(body, service, False)

    @router.post("/naming/undo/preview")
    @endpoint
    async def undo_preview(body: dict = Body(...), service=Depends(boundary)):
        check(body, ["batch_id"], ["batch_id"])
        return await service.undo_preview(body["batch_id"])

    @router.post("/naming/undo/apply", status_code=202)
    @endpoint
    async def undo_apply(body: dict = Body(...), service=Depends(boundary)):
        return await apply(body, service, True)

    @router.get("/naming/memory")
    @endpoint
    async def memory_get(identity_key: str | None = None, season_key: int | None = None, service=Depends(boundary)):
        if not service.memory:
            raise NamingError("integration_unavailable", "规则仓储未接入", status=503)
        return service.memory.get(identity_key, season_key)

    @router.put("/naming/memory")
    @endpoint
    async def memory_put(body: dict = Body(...), service=Depends(boundary)):
        check(body, ["scope", "expected_revision"], ["scope", "identity_key", "season_key", "expected_revision", "selected_title", "aliases", "naming_rule", "subtitle_rule", "episode_rules"])
        if not service.memory:
            raise NamingError("integration_unavailable", "规则仓储未接入", status=503)
        return service.memory.put(body["scope"], {"identity_key": body.get("identity_key"), "season_key": body.get("season_key")}, {k: v for k, v in body.items() if k in {"selected_title", "aliases", "naming_rule", "subtitle_rule", "episode_rules"}}, body["expected_revision"])

    return router
