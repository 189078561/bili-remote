import asyncio
import json
import subprocess
import time
import pytest
from fastapi.testclient import TestClient
from bili_remote.web import create_app
from bili_remote.database import Database
from bili_remote.v2.storage import NamingMemoryRepository
from bili_remote.v2.subtitle.memory import SubtitleRuleMemory
from bili_remote.v2.naming.errors import NamingError


def login(client):
    result=client.post('/api/auth/login',json={'username':'admin','password':'test-password'})
    assert result.status_code==200, result.text
    client.headers['X-CSRF-Token']=result.json()['csrf']


def video(settings):
    settings.ensure_directories()
    path=settings.download_root/'Sample.S01E01.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x180:rate=10:duration=3',
                    '-c:v','mpeg4',str(path)],check=True)
    return path


def wait(client,task):
    for _ in range(200):
        result=client.get(f"/api/v2/tasks/{task['kind']}/{task['id']}")
        assert result.status_code==200,result.text
        result=result.json()
        if result['status'] in {'completed','failed','cancelled'}: return result
        time.sleep(.05)
    pytest.fail('task did not finish')


def test_real_api_rename_undo_and_no_drafts(settings):
    path=video(settings); app=create_app(settings)
    with TestClient(app) as client:
        login(client)
        services=app.state.services
        ref=client.post('/api/v2/resources/reference',json={'root_key':'downloads','relative_path':path.name}).json()
        baseline={t:services.database.fetch_one(f'SELECT count(*) n FROM {t}')['n'] for t in ['release_projects','publish_sessions','media_metadata','release_documents']}
        r=client.post('/api/v2/resources/resolve',json={'resources':[ref],'manual_overrides':{'selected_title':'Synthetic','year':2025,'episode':{'season':1,'episode':1}}})
        assert r.status_code==200,r.text
        context=r.json()['context']
        assert context['files'][0]['probe']['video_codec']['source']=='ffprobe'
        assert 'naming_rule' in context['effective_rules']
        for endpoint in ['release-title','subtitles']:
            r=client.post(f'/api/v2/{endpoint}/render',json={'context':context,'rule':{}})
            assert r.status_code==200,r.text
            assert r.json()['text']
        r=client.post('/api/v2/publishing/preview',json={'context':context,'release_title':'Synthetic','subtitle':'副标题','overview':'<script>bad()</script>','format':'html'})
        assert r.status_code==200,r.text
        assert '<script>' not in r.json()['content']
        r=client.post('/api/v2/naming/preview',json={'context':context,'rules':{'custom_title':'Synthetic'}})
        assert r.status_code==200,r.text
        preview=r.json(); assert preview['can_apply'],preview
        assert path.exists()
        payload={'preview_token':preview['preview_token'],'expected_revision':preview['context_revision'],'confirm':True,'idempotency_key':'integration-rename'}
        r=client.post('/api/v2/naming/apply',json=payload)
        assert r.status_code==202,r.text
        submitted=r.json(); task=wait(client,submitted['task']); assert task['status']=='completed',task
        assert client.post('/api/v2/naming/apply',json=payload).json()==submitted
        target=settings.download_root/preview['operations'][0]['target_relative_path']
        assert target.is_file() and not path.exists()
        assert services.database.get_processing_task(task['id'])['project_id'] is None
        assert client.post(f"/api/v2/tasks/processing/{task['id']}/retry",json={}).status_code==409
        r=client.post('/api/v2/naming/undo/preview',json={'batch_id':submitted['batch_id']})
        assert r.status_code==200,r.text
        undo=r.json(); assert undo['can_apply'],undo
        r=client.post('/api/v2/naming/undo/apply',json={'preview_token':undo['preview_token'],'expected_revision':undo['context_revision'],'confirm':True,'idempotency_key':'undo'})
        assert r.status_code==202,r.text
        assert wait(client,r.json()['task'])['status']=='completed'
        assert path.exists() and not target.exists()
        for table,count in baseline.items(): assert services.database.fetch_one(f'SELECT count(*) n FROM {table}')['n']==count
        for row in services.database.fetch_all('SELECT input_json,result_json FROM processing_tasks'):
            assert 'bad()' not in json.dumps(row) and '副标题' not in json.dumps(row,ensure_ascii=False)


def test_persistent_rule_layers_subtitle_and_conflicts(settings):
    settings.ensure_directories(); db=Database(settings.database_path); db.initialize()
    repo=NamingMemoryRepository(db)
    first=repo.put('global',{'identity_key':'global'}, {'naming_rule':{'group':'TEST'}},0)
    assert first['revision']==1
    asyncio.run(SubtitleRuleMemory(repo).save(scope='global',rule={'template_id':'custom','custom_template':'{Title} {Episode:00}'},expected_revision=1))
    again=NamingMemoryRepository(Database(settings.database_path)).get()
    assert again['layers'][0]['naming_rule']['group']=='TEST'
    assert again['layers'][0]['subtitle_rule']['custom_template']=='{Title} {Episode:00}'
    with pytest.raises(NamingError): repo.put('global',{}, {'overview':'draft'},2)
    with pytest.raises(NamingError): repo.put('global',{}, {'naming_rule':{}},1)
    with pytest.raises(NamingError): repo.put('global',{}, {'subtitle_rule':{'custom_template':'{platform}'}},2)


def test_api_security_and_paths(settings):
    app=create_app(settings)
    with TestClient(app) as client:
        assert client.get('/api/v2/tasks').status_code==401
        login(client)
        token=client.headers.pop('X-CSRF-Token')
        assert client.post('/api/v2/resources/reference',json={'root_key':'downloads','relative_path':'x'}).status_code==403
        client.headers['X-CSRF-Token']=token
        for path in ['../x','/etc/passwd','a/../b','a\x00b']:
            r=client.post('/api/v2/resources/reference',json={'root_key':'downloads','relative_path':path})
            assert r.status_code==422,r.text
            assert r.headers['cache-control']=='no-store'
        r=client.put('/api/v2/naming/memory',json={'scope':'global','expected_revision':0,'overview':'draft'})
        assert r.status_code==422
        r=client.get('/api/v2/metadata/search',params={'query':'Synthetic','media_type':'movie'})
        assert r.status_code==200,r.text
        assert r.json()['items']==[]
        assert {w['ref'] for w in r.json()['warnings']}=={'tmdb','bangumi'}
        assert all(w['code']=='unconfigured' for w in r.json()['warnings'])
        schema=client.get('/api/v2/openapi.json').json()
        assert 'MediaFile' in schema['components']['schemas']
        assert schema['paths']['/api/v2/subtitles/render']['post']['requestBody']['content']['application/json']['schema']['properties']['context']=={'$ref':'#/components/schemas/ResourceContext'}


def test_report_artifact_and_download_completion(settings):
    path=video(settings); app=create_app(settings)
    with TestClient(app) as client:
        login(client); services=app.state.services
        ref=client.post('/api/v2/resources/reference',json={'root_key':'downloads','relative_path':path.name}).json()
        result=client.post('/api/v2/reports',json=ref)
        assert result.status_code==202,result.text
        task=wait(client,result.json()['task']); assert task['status']=='completed',task
        assert task['artifact_refs'][0]['kind']=='report'
        assert services.database.fetch_one('SELECT count(*) n FROM release_documents')['n']==0
        # Synthetic downloaded file and structured server facts exercise the real completion hook.
        job_id=services.database.create_job(kind='manual',source_url='https://www.bilibili.com/video/BVsynthetic',title='Synthetic',format_selector='best',output_template='%(title)s.%(ext)s',all_parts=False,output_dir='')
        services.database.update_job(job_id,status='completed',result_path=str(path))
        services.runner.completion_origins[job_id]={'bvid':'BVsynthetic','season_id':'42','ep_id':'8','cid':'90','original_title':'Synthetic','episode_index':1}
        before=path.read_bytes()
        response=client.post(f'/api/v2/downloads/{job_id}/recognize',json={})
        assert response.status_code==200,response.text
        assert response.json()['context']['origins'][0]['cid']=='90'
        assert 'operations' in response.json()['preview']
        assert path.exists() and path.read_bytes()==before
        assert services.database.get_job(job_id)['status']=='completed'
        assert services.v2.origins.list(job_id)[0]['original_resource']['fingerprint']==ref['fingerprint']
        # Report references compose actual report content without persisting publication text.
        context=response.json()['context']
        result=client.post('/api/v2/publishing/preview',json={'context':context,'release_title':'Synthetic','subtitle':'副标题','overview':'简介','reports':task['artifact_refs'],'format':'bbcode'})
        assert result.status_code==200,result.text
        assert len(result.json()['content'])>100


def test_playlist_completion_keeps_every_real_file(settings):
    path=video(settings)
    second=path.with_name('Sample.S01E02.mp4'); second.write_bytes(path.read_bytes())
    app=create_app(settings)
    with TestClient(app) as client:
        login(client); services=app.state.services
        job_id=services.database.create_job(kind='manual',source_url='https://www.bilibili.com/video/BVsynthetic',title='Synthetic',format_selector='best',output_template='%(title)s.%(ext)s',all_parts=True,output_dir='')
        services.database.update_job(job_id,status='completed',result_path=str(second))
        services.runner.completion_paths[job_id]=[str(path),str(second)]
        services.runner.completion_origins[job_id]={'cid':'must-not-be-shared'}
        from yt_dlp import YoutubeDL
        with YoutubeDL({'quiet':True}) as downloader:
            payload=downloader.evaluate_outtmpl('%(.{filepath,bvid,cid,season_id,episode_id,series,title,episode_number,playlist_index})j',
                {'filepath':str(path),'bvid':'BVsynthetic','cid':101,'playlist_index':1,'title':'Synthetic','url':'https://example.invalid/private-signature'})
        services.runner.remember_completion_item(job_id,payload)
        assert 'private-signature' not in json.dumps(services.runner.completion_item_origins)
        result=client.post(f'/api/v2/downloads/{job_id}/recognize',json={})
        assert result.status_code==200,result.text
        context=result.json()['context']
        assert len(context['resources'])==len(context['files'])==2
        assert [o['cid'] for o in context['origins']]==['101',None]
        assert context['episode']['scope']=='season'
        assert context['effective_rules']['subtitle_rule']['template_id']=='tv_season'
        assert path.exists() and second.exists()


def test_durable_rename_swap_recovery_and_session_guard(settings):
    from bili_remote.v2.storage import RenameAuditRepository
    from bili_remote.v2.naming.filesystem import NamingFiles
    from bili_remote.v2.naming.execution import RenameExecutor
    from bili_remote.file_service import FileService
    from tests.v2.naming.test_execution import plan
    settings.ensure_directories(); db=Database(settings.database_path); db.initialize()
    files=NamingFiles(FileService(settings,db))
    executor=RenameExecutor(files,RenameAuditRepository(db))
    root=settings.download_root
    (root/'a.mkv').write_bytes(b'A'); (root/'b.mkv').write_bytes(b'B')
    batch_id=executor.prepare(plan(executor,[('a.mkv','b.mkv'),('b.mkv','a.mkv')]),owner='owner')
    executor.execute(batch_id)
    restarted=RenameExecutor(files,RenameAuditRepository(Database(settings.database_path)))
    with pytest.raises(NamingError): restarted.undo_operations(batch_id,owner='another')
    undo=restarted.prepare(restarted.undo_operations(batch_id,owner='owner'),owner='owner',undo_of=batch_id)
    restarted.execute(undo)
    assert (root/'a.mkv').read_bytes()==b'A' and (root/'b.mkv').read_bytes()==b'B'
    interrupted=restarted.prepare(plan(restarted,[('a.mkv','c.mkv')]),owner='owner')
    batch=restarted.audit.get(interrupted); item=batch['items'][0]
    batch['status']='running'; item['intent']='staged'; restarted.audit.update(batch)
    files.move(item['source'],item['temporary_path'],item['source_fingerprint'])
    # Crash after filesystem move, before the next audit update.
    task_id=db.create_processing_task(project_id=None,task_type='v2_rename_apply',input_data={'batch_id':interrupted},log_path=str(settings.logs_dir/'synthetic.log'),idempotency_key='restart')
    db.update_processing_task(task_id,status='running')
    db.execute('UPDATE v2_rename_batches SET task_id=? WHERE id=?',(task_id,interrupted))
    app=create_app(settings)
    app.state.services.v2.recover()
    recovered=RenameExecutor(files,RenameAuditRepository(Database(settings.database_path)))
    assert recovered.audit.get(interrupted)['status']=='rolled_back'
    assert (root/'a.mkv').read_bytes()==b'A'
    assert not list(root.glob('.bili-v2-*'))


def test_task_center_retry_resets_legacy_download_progress(settings):
    settings.ensure_directories(); services=create_app(settings).state.services
    services.database.initialize()
    job_id=services.database.create_job(kind='manual',source_url='https://www.bilibili.com/video/BVsynthetic',title='Synthetic',format_selector='best',output_template='%(title)s.%(ext)s',all_parts=False,output_dir='')
    services.database.update_job(job_id,status='failed',progress=70,downloaded_bytes=4096,total_bytes=8192,speed=10,eta=2,error='synthetic failure')
    result=asyncio.run(services.v2.tasks.action('download',job_id,'retry'))
    assert result['task']['status']=='queued' and result['task']['progress']==0
    job=services.database.get_job(job_id)
    assert job['retry_count']==1 and job['downloaded_bytes']==0
    assert all(job[key] is None for key in ['total_bytes','speed','eta','error','started_at','finished_at'])
    with pytest.raises(NamingError): asyncio.run(services.v2.tasks.action('download',job_id,'retry'))
