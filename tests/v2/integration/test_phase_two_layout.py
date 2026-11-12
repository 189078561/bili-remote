"""Real application layouts, temporary files and real preview composition.

No UI data is injected: the matrix uses the shared browser, explicit resource
confirmation, real local resolve/compose endpoints and normal field editing.
"""
from pathlib import Path
import json
import subprocess
import shutil

import pytest
from playwright.sync_api import expect
from tests.v2.workbench.test_browser import show_panel
from test_browser_modules import browser_session, live_server  # noqa: F401

ARTIFACTS = Path(__file__).resolve().parents[3] / '.dev' / 'workbench-product-visual'
SIZES = [(1440, 900), (1280, 800), (1024, 768), (768, 1024), (390, 844)]


def navigate(page, view):
    show_panel(page)
    page.locator(f'[data-view="{view}"]').click()


def box(page, selector):
    return page.locator(selector).bounding_box()


def assert_no_overflow(page):
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.viewport_size


def seed(app):
    settings = app.state.services.settings
    media = settings.download_root / 'Media'
    (media / '阅读说明.txt').write_text('布局验收说明\n真实临时文本文件，可以预览与编辑。', encoding='utf-8')
    (media / 'Season 02').mkdir(exist_ok=True)
    video = media / 'Layout.Sample.S01E01.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=320x180:rate=24', '-t', '1', '-c:v', 'libx264',
                    '-pix_fmt', 'yuv420p', '-threads', '1', '-y', str(video)], check=True)
    for episode in range(2, 25):
        shutil.copyfile(video, media / f'Layout.Sample.S01E{episode:02}.mp4')
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', str(video),
                    '-frames:v', '1', '-threads', '1', '-y', str(settings.images_root / '验收截图.png')], check=True)


@pytest.mark.parametrize('width,height', SIZES)
@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_real_workspace_visual_matrix(live_server, width, height, theme):
    base, app = live_server
    seed(app)
    playwright, browser, page, errors = browser_session(base, legacy=False)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    prefix = f'{width}x{height}-{theme}'
    geometry = {'size': [width, height], 'theme': theme}
    try:
        page.set_viewport_size({'width': width, 'height': height})
        page.emulate_media(color_scheme=theme, reduced_motion='reduce')
        page.wait_for_function('window.V2Workbench?.browser?.data && mediaBrowser?.data')
        # Enter directly from the real file manager: no second search or selection.
        navigate(page, 'files')
        files = page.locator('#file-browser-host')
        files.locator('.file-row[data-path="Media"]').click()
        files.locator('.file-row[data-path="Media/Layout.Sample.S01E01.mp4"] .file-check').check()
        files.get_by_role('button', name='进入工作台', exact=True).click()
        page.wait_for_function('V2Workbench.context && V2Workbench.preview')
        expect(page.locator('[data-sheet=resources]')).to_be_hidden()
        expect(page.locator('[data-sheet=preview]')).to_be_hidden()
        expect(page.locator('[data-wb=rename]')).to_be_visible()
        assert page.evaluate('V2Workbench.resources[0].relative_path') == 'Media/Layout.Sample.S01E01.mp4'
        source = app.state.services.settings.download_root / 'Media' / 'Layout.Sample.S01E01.mp4'
        page.locator('[data-wb=selected-title]').fill('Color Study')
        page.locator('[data-wb=season]').fill('1')
        show_panel(page, 'naming')
        page.locator('[data-wb=episode]').fill('2')
        expect(page.locator('[data-wb=rename-list]')).to_contain_text('Color.Study', timeout=15000)
        assert page.evaluate('V2Workbench.context.episode.episode.value') == 2
        page.locator('[data-wb=naming-type]').select_option('series')
        expect(page.locator('[data-wb=rename-list]')).to_contain_text('S01E02')
        show_panel(page)
        assert source.exists()  # Automatic preview is read-only.
        assert not list(source.parent.glob('Color.Study*'))
        expect(page.locator('[data-edit=release_title]')).not_to_have_value('')
        expect(page.locator('[data-edit=subtitle]')).not_to_have_value('')
        page.locator('[data-edit=release_title]').fill('Color Study S01E02 · 色彩与运动')
        page.locator('[data-edit=subtitle]').fill('本地合成短片 / 320×180 / H264')
        overview = '这段短片展示色彩图形的运动，用于验证真实媒体制作流程。\n\n画面由本地工具生成，不对应真实影视作品。媒体参数来自实际文件，影视身份尚未匹配。\n\n发布信息可以独立编辑；截图、报告和种子使用同一份工作资源。'
        page.locator('[data-edit=overview]').fill(overview)
        expect(page.locator('[data-wb=output]')).to_contain_text(overview)
        page.evaluate('window.layoutIdentity = {wb:V2Workbench, browser:V2Workbench.browser, title:document.querySelector("[data-edit=release_title]"), epoch:V2Workbench.epoch}')
        for notice in page.locator('.wb-notice button').all():
            notice.click()
        page.evaluate('scrollTo(0,0)')
        assert_no_overflow(page)
        assert page.locator('.wb-layout,.wb-files,.wb-main,.wb-output,.wb-settings').count() == 0
        geometry['studio'] = box(page, '.wb-studio')
        page.screenshot(path=str(ARTIFACTS / f'workbench-{prefix}.png'))
        page.screenshot(path=str(ARTIFACTS / f'workbench-full-{prefix}.png'), full_page=True)
        page.locator('.wb-publishing').scroll_into_view_if_needed()
        page.screenshot(path=str(ARTIFACTS / f'workbench-editor-{prefix}.png'))
        # Same nodes and edited fields through every tool, details, preview and picker.
        for tab in ['images','report','torrent','content']:
            show_panel(page, tab)
            expect(page.locator(f'[data-panel="{tab}"]')).to_be_visible()
        show_panel(page, 'naming')
        expect(page.locator('[data-wb=naming-panel]')).to_be_visible()
        page.screenshot(path=str(ARTIFACTS / f'workbench-naming-{prefix}.png'))
        show_panel(page, 'preview')
        for paragraph in overview.split('\n\n'):
            expect(page.locator('[data-wb=rich-preview]')).to_contain_text(paragraph)
        assert page.locator('[data-wb=rich-preview]').inner_text().count('这段短片展示色彩图形的运动') == 1
        geometry['preview'] = box(page, '[data-sheet=preview]')
        assert abs(geometry['preview']['width'] - (width if width == 390 else min(860, width * .92))) < 1
        page.screenshot(path=str(ARTIFACTS / f'workbench-preview-{prefix}.png'))
        page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
        page.locator('[data-copy=content]').click()
        assert page.evaluate('navigator.clipboard.readText()') == page.evaluate('V2Workbench.content')
        page.locator('[data-preview-mode=source]').click()
        expect(page.locator('[data-wb=output]')).to_be_visible()
        page.locator('[data-preview-mode=effect]').click()
        before = page.evaluate('JSON.stringify({epoch:V2Workbench.epoch,fields:V2Workbench.fields,resources:V2Workbench.resources})')
        show_panel(page, 'resources')
        resources = page.locator('[data-wb=browser]')
        resources.locator('.file-row[data-path="Media"]').click()
        resources.locator('.file-row[data-path="Media/Layout.Sample.S01E02.mp4"] .file-check').check()
        page.screenshot(path=str(ARTIFACTS / f'workbench-resources-{prefix}.png'))
        show_panel(page)
        assert page.evaluate('JSON.stringify({epoch:V2Workbench.epoch,fields:V2Workbench.fields,resources:V2Workbench.resources})') == before
        show_panel(page, 'resources')
        dialogs = []
        def cancel_switch(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()
        page.once('dialog', cancel_switch)
        resources.get_by_role('button', name='使用所选资源', exact=True).click()
        assert dialogs
        show_panel(page)
        assert page.evaluate('JSON.stringify({epoch:V2Workbench.epoch,fields:V2Workbench.fields,resources:V2Workbench.resources})') == before
        expect(page.locator('[data-edit=overview]')).to_have_value(overview)
        assert page.evaluate('layoutIdentity.wb===V2Workbench && layoutIdentity.browser===V2Workbench.browser && layoutIdentity.title===document.querySelector("[data-edit=release_title]")')
        navigate(page, 'files')
        files = page.locator('#file-browser-host')
        if width <= 600:
            files.get_by_role('button', name='目录树', exact=True).click()
            page.screenshot(path=str(ARTIFACTS / f'files-tree-{prefix}.png'))
        files.locator('.root-shortcut').filter(has_text='图片目录').click()
        files.locator('.file-row[data-path="验收截图.png"] .file-check').check()
        files.locator('.file-batch-actions summary').click()
        if not files.get_by_role('button', name='文件详情', exact=True).is_visible():
            files.locator('.file-batch-actions summary').click()
        files.get_by_role('button', name='文件详情', exact=True).click()
        expect(files.locator('.file-details-pane')).to_have_attribute('data-selection', 'single')
        expect(files.locator('.file-detail-image')).to_be_visible()
        page.wait_for_function('document.querySelector(".file-detail-image").naturalWidth === 320')
        if width > 600:
            a, b = [box(page, '#file-browser-host ' + selector) for selector in ['.file-tree-pane', '.file-list-pane']]
            assert a['x'] + a['width'] <= b['x']
            assert page.locator('#file-browser-host .file-details-pane').evaluate('n => getComputedStyle(n).position') == 'fixed'
            geometry['files'] = [a, b]
        else:
            assert box(page, '.file-details-pane')['width'] == 390
        assert_no_overflow(page)
        page.screenshot(path=str(ARTIFACTS / f'files-details-{prefix}.png'))
        before = box(page, '#file-browser-host .file-list-pane')['width']
        files.locator('.file-details-pane').get_by_role('button', name='收起', exact=True).click()
        assert abs(box(page, '#file-browser-host .file-list-pane')['width'] - before) < 2
        page.screenshot(path=str(ARTIFACTS / f'files-{prefix}.png'))
        files.locator('.file-row[data-path="pixel.png"] .file-check').check()
        if not files.get_by_role('button', name='文件详情', exact=True).is_visible():
            files.locator('.file-batch-actions summary').click()
        files.get_by_role('button', name='文件详情', exact=True).click()
        expect(files.locator('.file-details-pane')).to_have_attribute('data-selection', 'multiple')
        expect(files.locator('.file-detail-image')).to_have_count(0)
        expect(files.locator('.file-details-body')).to_contain_text('已选择 2 项')
        files.locator('.file-details-pane').get_by_role('button', name='收起', exact=True).click()
        if width <= 600:
            files.get_by_role('button', name='目录树', exact=True).click()
        files.locator('.root-shortcut').filter(has_text='资源下载目录').click()
        files.locator('.file-row[data-path="Media"] .file-check').check()
        if not files.get_by_role('button', name='文件详情', exact=True).is_visible():
            files.locator('.file-batch-actions summary').click()
        files.get_by_role('button', name='文件详情', exact=True).click()
        expect(files.locator('.file-details-pane')).to_have_attribute('data-selection', 'directory')
        expect(files.locator('.file-details-body')).to_contain_text('目录内容未统计')
        files.locator('.file-details-pane').get_by_role('button', name='收起', exact=True).click()
        page.set_viewport_size({'width': 390 if width >= 1180 else 1440, 'height': 900})
        navigate(page, 'publish')
        assert page.evaluate('layoutIdentity.wb===V2Workbench && layoutIdentity.browser===V2Workbench.browser && layoutIdentity.title===document.querySelector("[data-edit=release_title]") && layoutIdentity.epoch===V2Workbench.epoch')
        expect(page.locator('[data-edit=overview]')).to_have_value(overview)
        assert_no_overflow(page)
        assert not errors, errors
        (ARTIFACTS / f'geometry-{prefix}.json').write_text(json.dumps(geometry, ensure_ascii=False, indent=2))
    finally:
        browser.close()
        playwright.stop()


def test_details_discard_late_text_and_reuse_real_media_artifacts(live_server):
    base, app = live_server
    seed(app)
    root = app.state.services.settings.download_root
    (root / 'first.txt').write_text('first document')
    (root / 'second.txt').write_text('second document')
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        page.wait_for_function('window.V2Workbench?.browser?.data && mediaBrowser?.data')
        navigate(page, 'files')
        files = page.locator('#file-browser-host')
        page.evaluate('''() => {
          const fetch = window.fetch.bind(window);
          window.fetch = async (url, options) => {
            const response = await fetch(url, options);
            if (String(url).includes('/api/files/text?') && String(url).includes('first.txt')) {
              await new Promise(resolve => window.releaseFirstText = resolve);
            }
            return response;
          };
        }''')
        files.locator('.file-row[data-path="first.txt"] .file-check').check()
        files.locator('.file-batch-actions summary').click()
        files.get_by_role('button', name='文件详情', exact=True).click()
        page.wait_for_function('typeof window.releaseFirstText === "function"')
        page.keyboard.press('Escape')
        files.locator('.file-row[data-path="first.txt"] .file-check').uncheck()
        files.locator('.file-row[data-path="second.txt"] .file-check').check()
        files.locator('.file-batch-actions summary').click()
        files.get_by_role('button', name='文件详情', exact=True).click()
        expect(files.locator('.file-detail-text')).to_have_text('second document')
        page.evaluate('releaseFirstText()')
        page.wait_for_timeout(100)
        expect(files.locator('.file-detail-text')).to_have_text('second document')
        page.keyboard.press('Escape')
        files.locator('.file-row[data-path="Media"]').click()
        files.locator('.file-row[data-path="Media/Layout.Sample.S01E01.mp4"] .file-check').check()
        files.get_by_role('button', name='进入工作台', exact=True).click()
        page.wait_for_function('V2Workbench.context?.files?.[0]?.probe')
        show_panel(page, 'images')
        page.locator('[data-action="screenshots"]').click()
        expect(page.locator('.wb-image img').first).to_be_visible(timeout=30000)
        page.evaluate('window.contextBeforeDetail = JSON.stringify(V2Workbench.context)')
        navigate(page, 'files')
        files.locator('.file-batch-actions summary').click()
        files.get_by_role('button', name='文件详情', exact=True).click()
        files.get_by_role('button', name='读取媒体信息', exact=True).click()
        expect(files.locator('.file-detail-media')).to_contain_text('320')
        files.get_by_role('button', name='查看已有截图', exact=True).click()
        expect(files.locator('.file-detail-shots img').first).to_be_visible()
        page.wait_for_function('document.querySelector(".file-detail-shots img").naturalWidth > 0')
        assert page.evaluate('JSON.stringify(V2Workbench.context) === contextBeforeDetail')
        assert page.locator('video').count() == 0  # No unverified video streaming/player API.
        files.locator('.file-detail-shots img').first.scroll_into_view_if_needed()
        page.screenshot(path=str(ARTIFACTS / 'files-real-media-and-screenshot.png'))
        # The original editor and its save/conflict workflow remain reachable
        # from the new full-screen details panel on a phone.
        page.keyboard.press('Escape')
        page.set_viewport_size({'width': 390, 'height': 844})
        page.evaluate('mediaBrowser.navigate("downloads", "")')
        files.locator('.file-row[data-path="second.txt"] .file-check').check()
        if not files.get_by_role('button', name='文件详情', exact=True).is_visible():
            files.locator('.file-batch-actions summary').click()
        files.get_by_role('button', name='文件详情', exact=True).click()
        files.get_by_role('button', name='预览/编辑（详情）', exact=True).click()
        editor = page.get_by_role('textbox', name='second.txt 文本内容')
        expect(editor).to_be_visible()
        editor.fill('saved through original editor')
        page.locator('.text-editor-dialog').get_by_role('button', name='保存', exact=True).click()
        expect(editor).not_to_be_visible()
        assert (root / 'second.txt').read_text() == 'saved through original editor'
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_live_adjustments_discard_late_response_and_preserve_manual_rules(live_server):
    """Real resolve/preview responses, with only delivery of the old read delayed."""
    base, app = live_server
    seed(app)
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        page.wait_for_function('window.V2Workbench?.browser?.data && mediaBrowser?.data')
        navigate(page, 'files')
        files = page.locator('#file-browser-host')
        files.locator('.file-row[data-path="Media"]').click()
        files.locator('.file-row[data-path="Media/Layout.Sample.S01E01.mp4"] .file-check').check()
        files.get_by_role('button', name='进入工作台', exact=True).click()
        page.wait_for_function('V2Workbench.context && V2Workbench.preview')
        show_panel(page, 'naming')
        page.locator('[data-wb=naming-type]').select_option('series')
        show_panel(page)
        page.wait_for_function('V2Workbench.preview && V2Workbench.context.effective_rules.naming_rule.template_id === "series"')
        page.evaluate('''() => {
          const fetch = V2Workbench.api.transport;
          V2Workbench.api.transport = async (url, options) => {
            const response = await fetch(url, options);
            if (response.status === 409) window.adjustmentConflict = true;
            if (String(url) === '/api/v2/resources/resolve' && !window.adjustmentHeld) {
              window.adjustmentHeld = true;
              await new Promise(resolve => window.releaseAdjustment = resolve);
            }
            return response;
          };
        }''')
        page.locator('[data-wb=selected-title]').fill('Earlier Name')
        page.wait_for_function('typeof releaseAdjustment === "function"')
        show_panel(page, 'naming')
        page.locator('[data-wb=naming-type]').select_option('movie')
        page.locator('[data-wb=naming-type]').select_option('anime')
        show_panel(page)
        page.locator('[data-wb=selected-title]').fill('Latest Name')
        show_panel(page, 'naming')
        page.locator('[data-wb=episode]').fill('3')
        # Explicit preview must flush changes even before the debounce expires.
        page.locator('[data-action=naming]').click()
        show_panel(page)
        page.locator('[data-edit=release_title]').fill('人工标题保持不变')
        page.locator('[data-edit=overview]').fill('人工简介不接受迟到响应覆盖')
        # The older resolve has advanced the server revision but its response
        # is in transit. It may supply a retry base, never visible editor data.
        page.wait_for_function('window.adjustmentConflict === true')
        expect(page.locator('[data-edit=release_title]')).to_have_value('人工标题保持不变')
        page.evaluate('releaseAdjustment()')
        expect(page.locator('[data-wb=rename-list]')).to_contain_text('Latest.Name.S01E03')
        page.wait_for_timeout(200)
        expect(page.locator('[data-wb=selected-title]')).to_have_value('Latest Name')
        expect(page.locator('[data-wb=episode]')).to_have_value('3')
        expect(page.locator('[data-wb=naming-type]')).to_have_value('anime')
        expect(page.locator('[data-edit=release_title]')).to_have_value('人工标题保持不变')
        expect(page.locator('[data-edit=overview]')).to_have_value('人工简介不接受迟到响应覆盖')
        expect(page.locator('[data-wb=output]')).to_contain_text('人工简介不接受迟到响应覆盖')
        show_panel(page, 'naming')
        advanced = page.locator('[data-wb=naming-panel]')
        expect(advanced.get_by_role('combobox', name='资源类型', exact=True)).to_have_value('anime')
        expect(advanced.get_by_role('textbox', name='自定义名称', exact=True)).to_have_value('Latest Name')
        advanced.get_by_role('button', name='生成命名预览', exact=True).click()
        expect(advanced.locator('.v2-naming__preview')).to_contain_text('Latest.Name.S01E03')
        show_panel(page)
        expect(page.locator('[data-wb=rename-list]')).to_contain_text('Latest.Name.S01E03')
        expect(page.locator('[data-action=apply]')).to_be_disabled()  # Advanced token stays with its owner.
        page.locator('[data-wb=selected-title]').fill('New Main Name')
        expect(page.locator('[data-wb=rename-list]')).to_contain_text('New.Main.Name.S01E03')
        expect(page.locator('[data-edit=release_title]')).to_have_value('人工标题保持不变')
        assert (app.state.services.settings.download_root / 'Media/Layout.Sample.S01E01.mp4').exists()
        assert not list((app.state.services.settings.download_root / 'Media').glob('Latest.Name*'))
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()
