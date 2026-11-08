"""Approved UI regression against the real isolated application, with synthetic storage."""
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_browser_modules import browser_session
from test_browser_modules import live_server as shared_live_server

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / '.dev/final-ui'


@pytest.fixture
def live_server(settings):
    yield from shared_live_server.__wrapped__(settings)


def test_final_ui_navigation_and_task_drawer(live_server):
    base, _ = live_server
    playwright, browser, page, errors = browser_session(base, legacy=False)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    try:
        for width, height in ((1440, 900), (390, 844), (768, 1024), (1024, 768), (1280, 800)):
            page.set_viewport_size({'width': width, 'height': height})
            nav = page.locator('#app-navigation')
            expect(nav).to_be_visible()
            assert page.locator('.app-sidebar').count() == 0
            box = nav.bounding_box()
            assert box['y'] > height - 90 if width == 390 else box['y'] < 68
            for view in ('home', 'files', 'publish', 'download', 'settings'):
                selector = '#settings-entry' if view == 'settings' else f'.app-tabs [data-view={view}]'
                page.locator(selector).click()
                expect(page.locator(f'#{view}-view')).to_be_visible()
                expect(page.locator(selector)).to_have_attribute('aria-current', 'page')
            page.locator('.app-tabs [data-view=home]').click()
            page.screenshot(path=str(ARTIFACTS / f'phase1-shell-{width}.png'))
            page.locator('#task-center-toggle').click()
            expect(page.locator('#task-center')).to_be_visible()
            page.keyboard.press('Escape')
            expect(page.locator('#task-center')).to_be_hidden()
            assert page.evaluate('!document.querySelector("#app-navigation").inert')
        page.locator('.account-menu summary').click()
        page.locator('#theme-toggle').click()
        page.locator('.app-tabs [data-view=files]').click()
        expect(page.locator('.account-menu')).not_to_have_attribute('open', '')
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_final_ui_files_selection_and_details(live_server):
    base, app = live_server
    folder = app.state.services.settings.download_root / 'Media'
    for index in range(2, 9):
        (folder / f'Synthetic.Series.S01E{index:02d}.1080p.WEB-DL.mkv').write_bytes(b'isolated visual fixture')
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        page.locator('.app-tabs [data-view=files]').click()
        page.locator('#file-browser-host .file-row[data-path="Media"]').click()
        expect(page.locator('#file-browser-host .file-row')).to_have_count(8)
        expect(page.locator('#file-browser-host .file-details-pane')).to_be_hidden()
        expect(page.locator('#file-browser-host .file-batch-actions')).to_be_hidden()
        for width, height in ((1440, 900), (390, 844)):
            page.set_viewport_size({'width': width, 'height': height})
            page.locator('#file-browser-host .file-check').nth(1).check()
            expect(page.locator('#file-browser-host .file-batch-actions')).to_be_visible()
            expect(page.locator('#file-browser-host .file-batch-actions')).to_contain_text('进入工作台')
            assert page.locator('#file-browser-host .file-batch-actions > button:disabled:visible').count() == 0
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            expect(page.locator('#app-navigation')).to_be_in_viewport()
            page.screenshot(path=str(ARTIFACTS / f'files-selected-{width}.png'))
            if width == 1440:
                import json
                (ARTIFACTS / 'file-metrics.json').write_text(json.dumps(page.evaluate('''() => Object.fromEntries(['.file-list-head','.file-row','.file-check'].map(s=> { const n=document.querySelector('#file-browser-host '+s), c=getComputedStyle(n);return [s,{height:c.height,minHeight:c.minHeight,padding:c.padding,grid:c.gridTemplateColumns}]}))''')))
            page.locator('#file-browser-host .file-clear-selection').click()
            expect(page.locator('#file-browser-host .file-batch-actions')).to_be_hidden()
            page.locator('#file-browser-host .file-row-more').first.click()
            page.get_by_role('menuitem', name='文件详情', exact=True).click()
            expect(page.locator('#file-browser-host .file-details-pane')).to_be_visible()
            page.keyboard.press('Escape')
            expect(page.locator('#file-browser-host .file-details-pane')).to_be_hidden()
            page.locator('#file-browser-host .file-clear-selection').click()
            page.screenshot(path=str(ARTIFACTS / f'files-browse-{width}.png'))
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_final_ui_workbench_resource_editing_and_readonly_preview(live_server):
    from tests.v2.integration.test_final_product import seed
    base, app = live_server
    source = seed(app)
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        page.locator('.app-tabs [data-view=files]').click()
        page.locator(f'#file-browser-host .file-row[data-path="{source.name}"] .file-check').check()
        page.locator('#file-browser-host').get_by_role('button', name='进入工作台', exact=True).click()
        page.wait_for_function('V2Workbench.context && V2Workbench.preview')
        expect(page.locator('[data-sheet=resources]')).to_be_hidden()
        expect(page.locator('[data-sheet=identity]')).to_be_hidden()
        page.locator('[data-wb=selected-title]').fill('Motion Study')
        expect(page.locator('[data-wb=rename-summary]')).to_contain_text('Motion.Study', timeout=15000)
        page.locator('[data-edit=release_title]').fill('Motion Study S01 · 合成媒体验收')
        page.locator('[data-edit=subtitle]').fill('合成短片 / 本地媒体参数')
        page.locator('[data-edit=overview]').fill('隔离环境生成的合成视频，用于验证真实工具流程。人工编辑保留。')
        expect(page.locator('[data-wb=output]')).to_contain_text('人工编辑保留')
        for width, height in ((1440, 900), (390, 844)):
            page.set_viewport_size({'width':width,'height':height})
            page.wait_for_function('V2Workbench.preview?.context_revision === V2Workbench.context?.revision && !V2Workbench.pendingAdjustmentEpoch')
            page.evaluate('scrollTo(0,0)')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(ARTIFACTS / f'workbench-{width}.png'))
            page.locator('[data-layout=identity]').click()
            expect(page.locator('[data-wb=query]')).to_be_visible()
            page.locator('[data-action=resolve]').click()
            expect(page.locator('[data-edit=release_title]')).to_have_value('Motion Study S01 · 合成媒体验收')
            page.locator('[data-sheet=identity] [data-layout=close]').click()
            page.locator('[data-layout=changes]').click()
            expect(page.locator('[data-wb=rename-list]')).to_contain_text('Motion.Study')
            page.screenshot(path=str(ARTIFACTS / f'workbench-changes-{width}.png'))
            page.locator('[data-sheet=changes] [data-layout=close]').click()
            assert source.exists()
        page.locator('[data-layout=preview]').click()
        page.context.grant_permissions(['clipboard-read','clipboard-write'])
        page.locator('[data-copy=content]').click()
        assert '人工编辑保留' in page.evaluate('navigator.clipboard.readText()')
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_final_ui_parent_child_selection_deduplicates_before_processing(live_server):
    base, _ = live_server
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        page.wait_for_function('window.V2Workbench')
        result = page.evaluate('''async () => {
          const api=V2Workbench.api;
          const refs=await Promise.all(['Media','Media/episode01.mp4'].map(relative_path=>api.request('/api/v2/resources/reference',{root_key:'downloads',relative_path})));
          await V2Workbench.select([refs[0],refs[1],refs[1]]);
          return {resources:V2Workbench.resources,files:V2Workbench.context.files};
        }''')
        assert len(result['resources']) == 1
        assert result['resources'][0]['relative_path'] == 'Media'
        paths = [item['ref']['relative_path'] for item in result['files']]
        assert paths == ['Media/episode01.mp4']
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_final_ui_home_and_settings_real_save(live_server):
    from tests.v2.integration.test_final_product import seed
    base,app=live_server
    source=seed(app)
    playwright,browser,page,errors=browser_session(base,legacy=False)
    try:
        page.locator('.app-tabs [data-view=home]').click()
        expect(page.locator('#home-resources')).to_contain_text(source.name)
        assert page.locator('.home-entry').count()==0
        for width,height in ((1440,900),(390,844)):
            page.set_viewport_size({'width':width,'height':height})
            page.screenshot(path=str(ARTIFACTS/f'home-{width}.png'))
            page.locator('#settings-entry').click()
            expect(page.locator('#settings-group-nav button')).to_have_count(10)
            if width==390:page.locator('#settings-group-select').select_option('downloads')
            else:page.locator('[data-group=downloads]').click()
            expect(page.locator('#settings-savebar')).to_be_hidden()
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(ARTIFACTS/f'settings-{width}.png'))
            if width==390:page.locator('#settings-group-select').select_option('collage')
            else:page.locator('[data-group=collage]').click()
            field=page.locator('[data-setting-key=screenshot_output_mode] select')
            current=field.input_value();next_value='collage' if current=='single' else 'single'
            field.select_option(next_value)
            expect(page.locator('#settings-savebar')).to_be_visible()
            page.locator('#settings-save').click()
            expect(page.locator('#settings-dirty-indicator')).to_have_text('已保存')
            assert app.state.services.database.get_settings()['screenshot_output_mode']==next_value
            page.locator('.app-tabs [data-view=home]').click()
        assert not errors,errors
    finally:
        browser.close();playwright.stop()


def test_final_ui_settings_search_and_categories(live_server):
    base,app=live_server
    playwright,browser,page,errors=browser_session(base,legacy=False)
    try:
        page.locator('#settings-entry').click()
        expect(page.locator('#settings-group-nav button')).to_have_count(10)
        for width,height in ((1440,900),(390,844),(768,1024)):
            page.set_viewport_size({'width':width,'height':height})
            keys=page.locator('#settings-group-select option').evaluate_all('(nodes)=>nodes.map(n=>n.value)')
            for key in keys:
                if width<=600:page.locator('#settings-group-select').select_option(key)
                else:page.locator(f'[data-group={key}]').click()
                expect(page.locator('.settings-group-heading h2')).not_to_be_empty()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,key)
        page.locator('#settings-search').click()
        page.get_by_role('searchbox',name='查找设置').fill('aria2_min_split_size')
        page.locator('.settings-search-result').click()
        expect(page.locator('[data-setting-key=aria2_min_split_size]')).to_be_visible()
        page.locator('#settings-search').click()
        page.get_by_role('searchbox',name='查找设置').fill('副标题')
        page.locator('.settings-search-result').first.click()
        expect(page.locator('.settings-group-heading h2')).to_have_text('副标题模板')
        expect(page.locator('#settings-savebar')).to_be_hidden()
        assert not errors,errors
    finally:browser.close();playwright.stop()
