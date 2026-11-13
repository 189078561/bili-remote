"""Authenticated public V2 API registration; legacy routes keep their contracts."""
import asyncio
import hashlib
import hmac
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from .contracts import PublishPreviewRequest, RenderRequest, ResourceRef
from .naming.router import create_router as naming_router, NamingRoute
from .subtitle.router import create_router as subtitle_router
from .naming.errors import NamingError
from .workbench.release_title import ReleaseTitleBuilder
from .workbench.publishing import PublishingService


def register(app,services):
    runtime=services.v2
    def authenticate(request:Request):
        cookie=request.cookies.get(services.sessions.cookie_name)
        session=services.sessions.parse(cookie)
        if not session:
            raise HTTPException(401,detail={'code':'unauthenticated','message':'请先登录','fields':[]})
        return hashlib.sha256(cookie.encode()).hexdigest()

    def csrf(request:Request):
        authenticate(request)
        session=services.sessions.parse(request.cookies.get(services.sessions.cookie_name))
        header=request.headers.get('X-CSRF-Token','')
        if not header or not hmac.compare_digest(header,session.csrf):
            raise HTTPException(403,detail={'code':'csrf_failed','message':'请求验证失败','fields':[]})
        return True

    def get_naming(request:Request): return runtime.naming(authenticate(request))
    app.include_router(naming_router(get_naming,authenticate=authenticate,csrf=csrf))
    app.include_router(subtitle_router(require_authenticated=authenticate,require_csrf=csrf))
    router=APIRouter(prefix='/api/v2',route_class=NamingRoute,dependencies=[Depends(authenticate)])
    title=ReleaseTitleBuilder(); publishing=PublishingService()

    @router.post('/release-title/render',dependencies=[Depends(csrf)])
    def render_title(body:RenderRequest):
        return title.build(body.context.model_dump(exclude_none=True),body.rule)

    @router.post('/publishing/preview',dependencies=[Depends(csrf)])
    def compose(body:PublishPreviewRequest):
        report_texts=[]
        for report in body.reports:
            if not report.resource or report.resource.root_key!='release_info' or not report.resource.relative_path.startswith('media-report-'):
                raise NamingError('invalid_artifact','仅接受真实媒体报告产物')
            ref=report.resource.model_dump()
            files=runtime.naming('artifact-reader').files
            path=files.path(ref)
            if ref['fingerprint']!=files.fingerprint(ref) or path.stat().st_size>4*1024*1024:
                raise NamingError('stale_artifact','媒体报告已变化或超过大小限制',status=409)
            report_texts.append(path.read_text(encoding='utf-8'))
        images=[x.model_dump() for x in body.screenshots]
        return publishing.compose(body.context.model_dump(exclude_none=True),body.release_title,body.subtitle,
            body.overview,images,body.format,links=body.links,report_texts=report_texts,
            poster=body.poster.model_dump() if body.poster else None)

    @router.get('/tasks')
    def tasks(kind:str|None=None,status:str|None=None,limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0)):
        return runtime.tasks.list(kind,status,limit,offset)

    @router.get('/tasks/{kind}/{task_id}')
    def task(kind:str,task_id:int): return runtime.tasks.get(kind,task_id)

    @router.post('/tasks/{kind}/{task_id}/{action}',dependencies=[Depends(csrf)])
    async def action(kind:str,task_id:int,action:str):
        if action not in {'cancel','retry'}: raise NamingError('unknown_action','操作不存在',status=404)
        return await runtime.tasks.action(kind,task_id,action)

    @router.post('/resources/reference',dependencies=[Depends(csrf)])
    def reference(body:ResourceRef):
        # Convert a locator into the V2 fingerprint after FileService permissions/limits.
        files=runtime.naming('reference-reader').files
        reference=files.reference(body.root_key,body.relative_path)
        if body.fingerprint:
            from ..file_service import _quick_fingerprint
            if body.fingerprint not in {reference['fingerprint'],_quick_fingerprint(files.path(reference))}:
                raise NamingError('stale_resource','源文件已变化，请重新选择',status=409)
        return reference

    @router.post('/reports',dependencies=[Depends(csrf)],status_code=202)
    def report(body:ResourceRef):
        files=runtime.naming('report-reader').files
        if body.fingerprint!=files.fingerprint(body.model_dump()):
            raise NamingError('stale_resource','源文件已变化',status=409)
        return {'task':runtime.enqueue('v2_media_report',{'resource':body.model_dump()})}

    @router.get('/reports/{artifact_id}/file')
    def report_file(artifact_id:str):
        import re
        from fastapi.responses import FileResponse
        if not re.fullmatch('[a-f0-9]{32}',artifact_id):
            raise NamingError('invalid_artifact','报告不存在',status=404)
        files=runtime.naming('artifact-reader').files
        path=files.path({'root_key':'release_info','relative_path':f'media-report-{artifact_id}.txt'})
        return FileResponse(path,media_type='text/plain',filename=path.name)

    @router.post('/downloads/{job_id}/recognize',dependencies=[Depends(csrf)])
    async def download_context(job_id:int,request:Request):
        return await runtime.download_context(authenticate(request),job_id)

    @router.get('/openapi.json',include_in_schema=False)
    def openapi_document(): return app.openapi()

    app.include_router(router)

    # Module routers validate dictionaries internally for standalone compatibility.
    # Publish their shared context types in the assembled application's OpenAPI.
    from fastapi.openapi.utils import get_openapi
    def openapi():
        if app.openapi_schema: return app.openapi_schema
        schema=get_openapi(title=app.title,version=app.version,routes=app.routes)
        context={'$ref':'#/components/schemas/ResourceContext'}
        resource={'$ref':'#/components/schemas/ResourceRef'}
        bodies={
            '/api/v2/resources/resolve': (['resources'], {'resources':{'type':'array','items':resource},'origins':{'type':'array','items':{'$ref':'#/components/schemas/DownloadOrigin'}},'context':context,'selection':{'type':'object'},'manual_overrides':{'type':'object'}}),
            '/api/v2/resources/probe': (['resources','expected_fingerprints'], {'resources':{'type':'array','items':resource},'expected_fingerprints':{'type':'object','additionalProperties':{'type':'string'}}}),
            '/api/v2/naming/preview': (['context'], {'context':context,'rules':{'type':'object'},'overrides':{'type':'object'}}),
            '/api/v2/subtitles/render': (['context','rule'], {'context':context,'rule':{'type':'object'}}),
        }
        for path,(required,properties) in bodies.items():
            schema['paths'][path]['post']['requestBody']={'required':True,'content':{'application/json':{'schema':{'type':'object','additionalProperties':False,'required':required,'properties':properties}}}}
        app.openapi_schema=schema
        return schema
    app.openapi=openapi

    @app.middleware('http')
    async def v2_boundary(request,call_next):
        if not request.url.path.startswith('/api/v2/'):
            return await call_next(request)
        # Bound streamed bodies before JSON parsing, including requests without Content-Length.
        if request.method not in {'GET','HEAD'}:
            from fastapi.responses import JSONResponse
            data=bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data)>4*1024*1024:
                    return JSONResponse({'detail':{'code':'request_too_large','message':'请求超过 4 MiB','fields':[]}},status_code=422,headers={'Cache-Control':'no-store'})
            request._body=bytes(data)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        return response
