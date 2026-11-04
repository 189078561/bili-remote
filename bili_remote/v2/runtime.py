"""Composition root for V2, using the existing services and processing worker."""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import secrets
from collections import OrderedDict
from pathlib import Path

from .metadata.providers import TMDBProvider, BangumiProvider, PTGenProvider
from .metadata.resolver import MetadataResolver
from .naming.service import NamingService
from .naming.memory import NamingMemoryService
from .naming.probe import MediaProbeAdapter
from .naming.tasks import NamingTaskHandlers
from .naming.errors import NamingError
from .storage import NamingMemoryRepository, RenameAuditRepository, DownloadOriginRepository
from .subtitle.memory import SubtitleRuleMemory
from .task_center import TaskCenter
from .download.completion import DownloadCompletionAdapter
from ..file_service import current_mutation_task


class NamingTasks:
    def __init__(self, runtime): self.runtime=runtime

    def enqueue_naming(self,batch_id,stable_key,undo=False):
        task=self.runtime.enqueue('v2_rename_undo' if undo else 'v2_rename_apply', {'batch_id':batch_id}, stable_key)
        self.runtime.services.database.execute('UPDATE v2_rename_batches SET task_id=? WHERE id=?',(task['id'],batch_id))
        return task

    def enqueue_probe(self,resources,expected_fingerprints):
        return self.runtime.enqueue('v2_media_probe', {'resources':resources,'expected_fingerprints':expected_fingerprints})


class V2Runtime:
    def __init__(self, services):
        self.services=services
        self.memory=NamingMemoryRepository(services.database)
        self.subtitle_memory=SubtitleRuleMemory(self.memory)
        self.audit=RenameAuditRepository(services.database)
        self.origins=DownloadOriginRepository(services.database)
        self.tasks=TaskCenter(services)
        self.sessions=OrderedDict()
        self.resolver=MetadataResolver({})
        self.probe=MediaProbeAdapter(services.settings)
        self.adapters={}

    def configure(self):
        # Reads only the database assigned to this service instance. Tests use tmp_path.
        stored=self.services.database.get_settings()
        metadata=self.services.metadata
        token=os.environ.get('V2_TMDB_TOKEN','') or metadata._secret('tmdb_api_key')
        ptgen=os.environ.get('V2_PTGEN_URL','') or stored.get('ptgen_url','')
        key=os.environ.get('V2_PTGEN_API_KEY','') or metadata._secret('ptgen_api_key')
        providers={'tmdb':TMDBProvider(token),'ptgen':PTGenProvider(ptgen,key)}
        enabled=os.environ.get('V2_BANGUMI_ENABLED','false').lower()=='true' or stored.get('v2_bangumi_enabled')=='true'
        providers['bangumi']=BangumiProvider(os.environ.get('V2_BANGUMI_TOKEN','') or metadata._secret('v2_bangumi_api_key'), configured=enabled)
        self.resolver=MetadataResolver(providers)
        self.sessions.clear()

    def naming(self,owner):
        if owner not in self.sessions:
            self.sessions[owner]=NamingService(self.services.files,self.probe,owner_session=owner,
                audit=self.audit,tasks=NamingTasks(self),resolver=self.resolver,
                memory=NamingMemoryService(self.memory),origins=self.origins)
            while len(self.sessions)>100:
                old,_=self.sessions.popitem(last=False); self.adapters.pop(old,None)
        self.sessions.move_to_end(owner)
        return self.sessions[owner]

    def enqueue(self,task_type,data,key=None):
        task,_=self.services.processing.enqueue(project_id=None,task_type=task_type,input_data=data,idempotency_key=key)
        return self.tasks.project('processing',task)

    async def dispatch(self,task,should_cancel):
        token=current_mutation_task.set(task['id'])
        try:
            if task['task_type']=='v2_media_report':
                return await asyncio.to_thread(self.report,task['input'],should_cancel)
            service=self.naming('background-executor')
            return await NamingTaskHandlers(service).handle(task['task_type'],task['input'],should_cancel=should_cancel)
        finally:
            current_mutation_task.reset(token)

    def recover(self):
        for row in self.services.database.fetch_all("SELECT id,task_id FROM v2_rename_batches WHERE status IN ('running','failed')"):
            token=current_mutation_task.set(row['task_id'])
            try: self.naming('background-executor').executor.recover(row['id'])
            finally: current_mutation_task.reset(token)

    def report(self,data,should_cancel):
        if set(data)!={'resource'}:
            raise NamingError('invalid_task','报告任务仅接受资源引用')
        resource=data['resource']
        files=self.naming('background-executor').files
        if files.fingerprint(resource)!=resource.get('fingerprint'):
            raise NamingError('stale_resource','报告源已变化',status=409)
        if should_cancel(): raise NamingError('cancelled','报告已取消')
        from ..media_tools import tool_cancel
        token=tool_cancel.set(should_cancel)
        try:
            result=self.services.release_info.analyze({'root_key':resource['root_key'],'relative_path':resource['relative_path']})
        finally:
            tool_cancel.reset(token)
        text='\n\n'.join(str(r.get('kind','媒体报告'))+'\n'+str(r.get('text','')) for r in result.get('reports',[]) if r.get('text'))
        if not text: raise NamingError('report_unavailable','媒体报告工具未返回内容')
        if should_cancel(): raise NamingError('cancelled','报告已取消')
        if files.fingerprint(resource)!=resource['fingerprint']:
            raise NamingError('stale_resource','生成报告时源已变化',status=409)
        identifier=secrets.token_hex(16)
        root=self.services.files.root('release_info').path
        path=root / f'media-report-{identifier}.txt'
        with path.open('x',encoding='utf-8') as output: output.write(text)
        ref=files.reference('release_info',path.name)
        return {'artifact_refs':[{'kind':'report','id':identifier,'resource':ref,'url':f'/api/v2/reports/{identifier}/file'}], 'warnings':result.get('warnings',[])}

    async def download_completed(self,job):
        root=self.services.files.root('downloads').path.resolve()
        # The runner supplies selected Bilibili fields from its structured response.
        facts=getattr(self.services.runner,'completion_origins',{}).pop(job['id'],{})
        item_origins=getattr(self.services.runner,'completion_item_origins',{}).pop(job['id'],{})
        paths=getattr(self.services.runner,'completion_paths',{}).pop(job['id'],[]) or [job.get('result_path') or '']
        origins=[]
        for value in dict.fromkeys(paths):
            path=Path(value)
            try: relative=path.resolve().relative_to(root).as_posix()
            except ValueError: continue
            if not path.is_file(): continue
            ref=self.naming('download-completion').files.reference('downloads',relative)
            # A playlist's shared metadata cannot establish each episode's identity.
            item_facts={**item_origins.get(value,{}), **({k:v for k,v in facts.items() if v is not None} if len(paths)==1 else {})}
            origin={k:item_facts.get(k) for k in ('bvid','season_id','ep_id','cid','original_title','episode_index')}
            origin.update(job_id=job['id'],original_resource=ref,api_mode=job.get('api_mode','web'))
            self.origins.put(origin)
            origins.append(origin)
        if not origins: return
        # Probe/recognition is best effort and never renames or changes download status.
        adapter=self.adapters.setdefault('download-completion',DownloadCompletionAdapter(self.naming('download-completion')))
        await adapter.on_completed(job['id'],origins)

    async def download_context(self,owner,job_id):
        job=self.services.database.get_job(job_id)
        if not job or job['status']!='completed':
            raise NamingError('download_not_completed','下载尚未完成',status=409)
        if not self.origins.list(job_id): await self.download_completed(job)
        origins=self.origins.list(job_id)
        if not origins: raise NamingError('resource_not_found','下载产物不在可访问资源根内',status=404)
        # Fresh context for this authenticated owner, not the background owner's token.
        service=self.naming(owner)
        context=await service.analyze([o['original_resource'] for o in origins],origins)
        preview=service.preview(context)
        context=service.contexts[context['context_id']]
        return {'context':context,'preview':preview}
