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
