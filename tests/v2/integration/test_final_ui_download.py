from playwright.sync_api import expect
from test_browser_modules import browser_session
from tests.v2.integration.test_final_ui import live_server, ARTIFACTS

def test_final_ui_download_states_and_episode_quality(live_server):
    """Real local APIs/DB; B站 parsing is an explicitly offline upstream adapter."""
    import asyncio
    from bili_remote.downloader import ResolvedMedia, DownloadFailure
    base, app = live_server
    services = app.state.services
    formats = [dict(selector='137+30280',format_id='137',height=1080,fps=24,vcodec='avc1',acodec='none'),dict(selector='136+30280',format_id='136',height=720,fps=24,vcodec='avc1',acodec='none')]
    async def resolve(url, mode):
        await asyncio.sleep(.35)
        if 'BV1xx411c7mE' in url:
            raise DownloadFailure('离线合成测试：解析失败')
        return ResolvedMedia(title='合成自然观察 · 第一季',uploader='隔离测试',duration=240,webpage_url=url,is_playlist=True,playlist_count=6,formats=formats,episodes=[dict(id=str(i),title=f'合成视频 第 {i} 集',group='正片',group_index=i,duration=40,url=f'https://www.bilibili.com/bangumi/play/ep{i}') for i in range(1,7)],default_episode_id='1',api_mode=mode)
    async def episode_formats(url, mode):
        return {'formats': formats if not url.endswith('6') else formats[1:]}
    services.runner.resolve=resolve
    services.runner.episode_formats=episode_formats
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        page.locator('.app-tabs [data-view=download]').click()
        for width, height in ((1440,900),(390,844)):
            page.set_viewport_size({'width':width,'height':height})
            page.screenshot(path=str(ARTIFACTS / f'download-idle-{width}.png'))
        page.locator('#dv2-url').fill('https://www.bilibili.com/video/BV1xx411c7mD')
        page.locator('[data-parse]').click()
        assert not errors, errors
        expect(page.locator('.dv2')).to_have_attribute('data-stage','parsing')
        page.screenshot(path=str(ARTIFACTS / 'download-parsing-390.png'))
        expect(page.locator('.dv2-episode')).to_have_count(6)
        page.locator('[data-select-all]').click()
        quality=page.locator('.dv2-episode-quality').last
        quality.focus()
        expect(quality.locator('option')).to_have_count(2)
        quality.select_option('136+30280')
        expect(page.locator('[data-summary-quality]')).to_contain_text('1 集单独设置')
        for width,height in ((1440,900),(390,844)):
            page.set_viewport_size({'width':width,'height':height})
            page.evaluate('scrollTo(0,0)')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(ARTIFACTS / f'download-selected-{width}.png'))
        page.locator('[data-download-form] button[type=submit]').click()
        expect(page.locator('[data-notice]')).to_contain_text('任务已加入', timeout=15000)
        jobs = services.database.list_jobs(limit=100)
        assert len(jobs) == 6
        assert next(job for job in jobs if job['source_url'].endswith('ep6'))['format_selector'] == '136+30280'
        page.locator('#dv2-url').fill('https://www.bilibili.com/video/BV1xx411c7mE')
        page.locator('[data-parse]').click()
        expect(page.locator('[data-error]')).to_contain_text('离线合成测试：解析失败')
        page.screenshot(path=str(ARTIFACTS / 'download-parse-failed-390.png'))
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_final_ui_partial_episode_submission_does_not_duplicate(live_server):
    from bili_remote.downloader import ResolvedMedia
    base,app=live_server
    formats=[dict(selector='136+30280',height=720)]
    async def resolve(url,mode):
        return ResolvedMedia(title='离线部分提交验收',uploader='合成',duration=120,default_episode_id='1',webpage_url=url,is_playlist=True,playlist_count=3,formats=formats,episodes=[dict(id=str(i),title=f'合成选集 {i}',url=f'https://www.bilibili.com/bangumi/play/ep{i}') for i in range(1,4)],api_mode=mode)
    async def episode_formats(url,mode):return {'formats':formats}
    app.state.services.runner.resolve=resolve
    app.state.services.runner.episode_formats=episode_formats
    playwright,browser,page,errors=browser_session(base,legacy=False)
    calls=[]
    def fail_once(route):
        calls.append(route.request.post_data_json['episode_ids'])
        if len(calls)==2:route.fulfill(status=503,json={'detail':'合成故障：第二批暂不可用'})
        else:route.continue_()
    page.route('**/api/jobs/batch',fail_once)
    try:
        page.locator('.app-tabs [data-view=download]').click()
        page.locator('#dv2-url').fill('https://www.bilibili.com/video/BV1xx411c7mD')
        page.locator('[data-parse]').click()
        expect(page.locator('.dv2-episode')).to_have_count(3)
        page.locator('[data-select-all]').click()
        quality=page.locator('.dv2-episode-quality').last
        quality.focus();expect(quality.locator('option')).to_have_count(2)
        quality.select_option('136+30280')
        page.locator('[data-download-form] button[type=submit]').click()
        expect(page.locator('[data-error]')).to_contain_text('已提交 2 集')
        expect(page.locator('[data-episode-list] input:checked')).to_have_count(1)
        page.locator('[data-download-form] button[type=submit]').click()
        expect(page.locator('.dv2')).to_have_attribute('data-stage','submitted')
        assert calls==[['1','2'],['3'],['3']]
        assert len(app.state.services.database.list_jobs(limit=100))==3
        assert not errors,errors
    finally:browser.close();playwright.stop()


def test_final_ui_task_states_retry_and_long_content(live_server):
    from tests.v2.integration.test_final_product import seed
    base,app=live_server
    source=seed(app);db=app.state.services.database
    def job(title,status,**data):
        identifier=db.create_job(source_url='https://www.bilibili.com/video/BV1xx411c7mD',kind='manual',output_template='%(title)s.%(ext)s',output_dir='',title=title)
        db.update_job(identifier,status=status,**data)
        return identifier
    active=job('合成进行中任务 · 长标题与多语言文件名称用于布局验收'*3,'running',progress=43)
    failed=job('合成下载失败任务','failed',error='离线合成故障：连接中断，请重试')
    complete=job('合成已完成下载','completed',progress=100,result_path=str(source))
    playwright,browser,page,errors=browser_session(base,legacy=False)
    try:
        for theme in ('light','dark'):
            page.emulate_media(color_scheme=theme)
            for width,height in ((1440,900),(390,844)):
                page.set_viewport_size({'width':width,'height':height})
                for view in ('home','download'):
                    page.locator(f'.app-tabs [data-view={view}]').click()
                    if view=='home':expect(page.locator('#home-tasks')).to_contain_text('43%')
                    else:expect(page.locator(f'.dv2-job[data-job-id="{failed}"]')).to_contain_text('连接中断')
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path=str(ARTIFACTS/f'{view}-task-states-{width}-{theme}.png'),full_page=True)
        page.locator(f'.dv2-job[data-job-id="{failed}"]').get_by_role('button',name='重试',exact=True).click()
        expect(page.locator(f'.dv2-job[data-job-id="{failed}"]')).to_have_attribute('data-status','completed',timeout=30000)
        assert db.get_job(complete)['result_path']==str(source)
        assert not errors,errors
    finally:
        db.update_job(active,status='cancelled')
        browser.close();playwright.stop()
