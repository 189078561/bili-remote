"""Real synthetic media and isolated service validation for product additions."""
import asyncio
import httpx
import pytest
from test_screenshots import make_video, service_for, create_data, run_session
from bili_remote.screenshots import ScreenshotError
from bili_remote.v2.workbench.publishing import PublishingService
from bili_remote.v2.metadata.providers import TMDBProvider


def test_percentage_range_uses_each_video_duration(settings):
    service,_=service_for(settings)
    make_video(settings.download_root/'short.mp4',duration=2)
    make_video(settings.download_root/'long.mp4',duration=5)
    data=create_data('short.mp4','long.mp4',total=2)
    data.update(start_percent=25,end_percent=75,sampling_mode='uniform')
    session,_=run_session(service,data)
    for video in session['videos']:
        duration=video['media']['duration_seconds']
        assert video['planned_times']==pytest.approx([duration*.375,duration*.625])
        assert all(item['status']=='completed' for item in video['items'])
    assert len(session['videos'])==2
    for start,end in [(90,20),(-1,90),(0,101),(30,None)]:
        with pytest.raises(ScreenshotError):
            service.prepare_create({**data,'start_percent':start,'end_percent':end})


def test_description_markup_and_unsafe_links():
    service=PublishingService()
    body='[b]人工简介[/b]\n[img]https://example.test/image.png[/img]\n[url=javascript:alert(1)]bad[/url]<script>bad()</script>'
    bb=service.compose({'revision':1},'标题','副标题',body,format='bbcode')['content']
    assert '[b]人工简介[/b]' in bb
    assert '[img]https://example.test/image.png[/img]' in bb
    assert '[url=javascript:' not in bb
    html=service.compose({'revision':1},'标题','副标题',body,format='html')['content']
    assert '<strong>人工简介</strong>' in html
    assert '<script>' not in html and 'href="javascript:' not in html
    assert html.count('人工简介')==1


def test_explicit_tmdb_id_uses_detail_endpoint_offline_transport():
    calls=[]
    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(200,json={'id':42,'title':'合成电影','original_title':'Synthetic','release_date':'2025-01-01','poster_path':'/fixture.jpg'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider=TMDBProvider('development-fixture',client=client)
            result=await provider.search('tmdb:movie:42','series')
            assert result['items'][0]['media_type']=='movie'
            assert result['items'][0]['id']=='42'
            assert result['items'][0]['poster_url'].endswith('/fixture.jpg')
    asyncio.run(run())
    assert calls==['/3/movie/42']
