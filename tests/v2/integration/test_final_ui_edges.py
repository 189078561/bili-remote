"""Isolated empty/error/long-content states at the approved viewports."""
from playwright.sync_api import expect
from test_browser_modules import browser_session, live_server
from tests.v2.integration.test_final_ui import ARTIFACTS
from tests.v2.integration.test_final_product import seed
from tests.v2.workbench.test_browser import show_panel
from bili_remote.downloader import ResolvedMedia


def test_final_ui_empty_and_long_content_matrix(live_server):
    base,app=live_server
    root=app.state.services.settings.download_root
    (root/'Media'/'episode01.mp4').unlink();(root/'Media').rmdir()  # only this fixture's synthetic seed
    async def resolve(url,mode):
        return ResolvedMedia(title='离线大量选集验收',uploader='合成数据',duration=None,webpage_url=url,is_playlist=True,playlist_count=120,formats=[],episodes=[dict(id=str(i),title=('较长的合成选集标题，用于验证折行与选择操作。'*4)+str(i),group='正片',group_index=i,url=f'https://www.bilibili.com/bangumi/play/ep{i}') for i in range(1,121)],default_episode_id='1',api_mode=mode)
    app.state.services.runner.resolve=resolve
    playwright,browser,page,errors=browser_session(base,legacy=False)
    try:
        page.wait_for_function('window.AppResources && window.V2Workbench')
        for theme in ('light','dark'):
            page.emulate_media(color_scheme=theme)
            for width,height in ((1440,900),(390,844)):
                page.set_viewport_size({'width':width,'height':height})
                for view in ('home','files','publish','download'):
                    page.locator(f'.app-tabs [data-view={view}]').click()
                    if view=='home':expect(page.locator('#home-resources')).to_contain_text('还没有')
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(theme,width,view)
                    page.screenshot(path=str(ARTIFACTS/f'{view}-empty-{width}-{theme}.png'))
        source=seed(app);target=source.with_name(('合成资源_长名称_多语言_'*6)+'.S01E01.mp4');source.rename(target)
        page.locator('.app-tabs [data-view=files]').click()
        page.locator('#file-browser-host .fb-tree-toggle').click()
        page.locator('#file-browser-host .file-tree-pane').get_by_role('button',name='刷新',exact=True).click()
        page.keyboard.press('Escape')
        page.locator(f'#file-browser-host .file-row[data-path="{target.name}"] .file-check').check()
        page.screenshot(path=str(ARTIFACTS/'files-long-name-390-dark.png'))
        page.locator('#file-browser-host').get_by_role('button',name='进入工作台',exact=True).click()
        page.wait_for_function('V2Workbench.context && V2Workbench.preview')
        long_text='人工长简介，用于验证换行、编辑保护和完整复制。\n'*120
        page.locator('[data-edit=overview]').fill(long_text)
        expect(page.locator('[data-wb=output]')).to_contain_text('人工长简介')
        show_panel(page,'preview')
        page.context.grant_permissions(['clipboard-read','clipboard-write'])
        page.locator('[data-copy=content]').click()
        assert page.evaluate('navigator.clipboard.readText()').count('人工长简介')==120
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.screenshot(path=str(ARTIFACTS/'preview-long-description-390-dark.png'))
        show_panel(page)
        page.locator('.app-tabs [data-view=download]').click()
        page.locator('#dv2-url').fill('https://www.bilibili.com/video/BV1xx411c7mD')
        page.locator('[data-parse]').click()
        expect(page.locator('.dv2-episode')).to_have_count(120)
        page.locator('[data-select-all]').click()
        expect(page.locator('[data-selected-count]')).to_contain_text('120')
        for width,height in ((1440,900),(390,844)):
            page.set_viewport_size({'width':width,'height':height})
            page.evaluate('scrollTo(0,0)')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(ARTIFACTS/f'download-many-episodes-{width}-dark.png'))
        expect(page.locator('[data-edit=overview]')).to_have_value(long_text)
        assert not errors,errors
    finally:browser.close();playwright.stop()
