"""Projection of the two existing queues; no third executor or task table."""
from ..naming.errors import NamingError

SAFE_RETRY = {'screenshot','screenshot_collage','image_upload','torrent_build','v2_media_probe','v2_media_report'}
TERMINAL = {'completed','failed','cancelled','auth_required'}

class TaskCenter:
    def __init__(self, services):
        self.services = services
        self.db = services.database

    def project(self, kind, row):
        status = row['status']
        task_type = row.get('task_type','download')
        result = row.get('result') or {}
        refs = list(result.get('artifact_refs') or [])
        if status == 'completed' and kind == 'processing':
            if task_type == 'image_upload':
                for upload in result.get('uploads', []):
                    if upload.get('status') == 'completed':
                        refs.append({'kind':'upload','id':str(upload['id']),'resource':None,'url':upload.get('remote_url')})
            if task_type == 'torrent_build' and result.get('record', {}).get('id'):
                record=result['record']
                refs.append({'kind':'torrent','id':str(record['id']),'resource':None,
                             'url':f"/api/torrents/{record['id']}/file"})
            if task_type in {'screenshot','screenshot_collage'} and row.get('input',{}).get('session_id'):
                from urllib.parse import urlencode
                from ..naming.filesystem import NamingFiles
                try:
                    session=self.services.screenshots.get_session(row['input']['session_id'])
                    outputs=list((session.get('final_outputs') or {}).values()) or [item for video in session.get('videos',[]) for item in video.get('items',[])]
                    for output in outputs:
                        path=output.get('output_relative_path') or output.get('relative_path')
                        if output.get('status')!='completed' or not path: continue
                        resource=NamingFiles(self.services.files).reference('images',path)
                        refs.append({'kind':'collage' if session.get('params',{}).get('output_mode')=='collage' else 'screenshot',
                            'id':str(output.get('id') or output.get('video_id') or path),'resource':resource,
                            'url':'/api/files/image?'+urlencode({'root_key':'images','path':path,'download':'true'})})
                except (ValueError,OSError):
                    # Deleted artifacts are absent; retain the actual completed task state.
                    pass
        return {'kind':kind,'id':row['id'],'task_type':task_type,'status':status,
            'progress':row.get('progress') if kind == 'download' else None,
            'created_at':row.get('created_at'),'finished_at':row.get('finished_at'),
            'artifact_refs':refs,'error':row.get('error'),
            'retry_allowed':status in {'failed','cancelled','auth_required'} and (kind == 'download' or task_type in SAFE_RETRY),
            'cancel_allowed':status in {'queued','running'}}

    def get(self, kind, task_id):
        if kind not in {'download','processing'}:
            raise NamingError('invalid_task_kind','无效的任务类型')
        row = self.db.get_job(task_id) if kind == 'download' else self.db.get_processing_task(task_id)
        if not row:
            raise NamingError('task_not_found','任务不存在',status=404)
        return self.project(kind,row)

    def list(self, kind=None, status=None, limit=100, offset=0):
        if kind not in {None,'download','processing'}:
            raise NamingError('invalid_task_kind','无效的任务类型')
        items=[]
        # Filter before pagination; do not silently miss matching older tasks.
        for current, table in [('download','jobs'),('processing','processing_tasks')]:
            if kind and kind != current:
                continue
            query = f'SELECT * FROM {table}'
            args=()
            if status:
                query+=' WHERE status=?'; args=(status,)
            rows=self.db.fetch_all(query+' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET 0', args+(limit+offset,))
            for row in rows:
                if current=='processing': row=self.db._decode_task(row)
                items.append(self.project(current,row))
        items.sort(key=lambda x:(x['created_at'] or '',x['kind'],x['id']),reverse=True)
        return {'items':items[offset:offset+limit],'limit':limit,'offset':offset}

    async def action(self,kind,task_id,action):
        ref=self.get(kind,task_id)
        if not ref[action+'_allowed']:
            raise NamingError('task_action_conflict','任务状态不允许该操作；文件变更必须重新预览',status=409)
        if kind=='download':
            if action=='cancel':
                success=await self.services.manager.cancel_job(task_id)
            else:
                with self.db.connect() as connection:
                    success=connection.execute("UPDATE jobs SET status='queued',error=NULL,progress=0,downloaded_bytes=0,total_bytes=NULL,speed=NULL,eta=NULL,started_at=NULL,finished_at=NULL,retry_count=retry_count+1 WHERE id=? AND status IN ('failed','cancelled','auth_required')",(task_id,)).rowcount==1
                if success: self.services.manager.wake()
        else:
            success=getattr(self.services.processing,action)(task_id)
        if not success:
            raise NamingError('task_action_conflict','任务状态已变化',status=409)
        return {'task':self.get(kind,task_id)}
