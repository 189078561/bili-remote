from __future__ import annotations

import json

import pytest
from playwright.sync_api import expect
from test_browser_modules import browser_session, live_server  # noqa: F401


def mount(page, *, adapter=False, page_size=20):
    page.set_default_timeout(7000)
    if page.viewport_size['width'] <= 768:
        page.locator('#nav-toggle').click()
    page.locator('[data-view="files"]').click()
    page.locator('#file-browser-host .file-row').first.wait_for()
    page.evaluate('mediaBrowser.destroy()')
    if adapter:
        page.add_script_tag(url='/static/v2/filebrowser/adapter.js')
    page.evaluate('''async ({adapter, pageSize}) => {
      const host = document.createElement('div'); host.id = 'fb-test';
      document.querySelector('#file-browser-host').replaceChildren(host);
      if (adapter) {
        window.sharedContext = null;
        window.fbAdapter = new FileBrowserAdapter({
          getContext: () => window.sharedContext,
          setContext: value => window.sharedContext = value,
          openWorkbench: value => window.workbenchContext = value,
          openNaming: value => window.namingContext = value
        });
        window.fb = fbAdapter.mount(host, {pageSize});
      } else window.fb = new FileBrowser(host, {allowMultiple: true, pageSize});
      await fb.init();
    }''', {"adapter": adapter, "pageSize": page_size})
    assert page.evaluate('fb.data !== null'), page.evaluate('({error: fb.el.listError.textContent, host: !!document.querySelector("#fb-test"), roots: fb.roots})')
    return page.locator('#fb-test')


def more_action(host, label):
    action = host.get_by_role('button', name=label, exact=True)
    if not action.is_visible():
        host.locator('.file-more-menu > summary').click()
    action.click()


def select(host, path):
    host.locator(f'.file-row[data-path="{path}"] .file-check').check()


def test_core_file_operations_and_editor_conflict(live_server):
    base, app = live_server
    root = app.state.services.settings.download_root
    (root / 'note.txt').write_text('original', encoding='utf-8')
    (root / 'Destination').mkdir()
    playwright, browser, page, errors = browser_session(base)
    try:
        host = mount(page)
        select(host, 'note.txt')
        host.get_by_role('button', name='预览/编辑', exact=True).click()
        editor = page.get_by_role('textbox', name='note.txt 文本内容')
        expect(editor).to_have_value('original')
        (root / 'note.txt').write_text('external edit', encoding='utf-8')
        editor.fill('my edit')
        page.locator('.text-editor-dialog').get_by_role('button', name='保存', exact=True).click()
        expect(page.locator('.text-editor-dialog .form-error')).to_contain_text('编辑内容仍保留')
        expect(editor).to_have_value('my edit')
        assert (root / 'note.txt').read_text() == 'external edit'
        page.locator('.text-editor-dialog').get_by_role('button', name='重新加载').click()
        expect(editor).to_have_value('external edit')
        editor.fill('saved content')
        page.locator('.text-editor-dialog').get_by_role('button', name='保存', exact=True).click()
        expect(editor).not_to_be_visible()
        assert (root / 'note.txt').read_text() == 'saved content'

        select(host, 'note.txt')
        host.get_by_role('button', name='重命名', exact=True).click()
        page.get_by_role('textbox', name='新文件名（不含扩展名）').fill('renamed')
        page.locator('#modal').get_by_role('button', name='保存', exact=True).click()
        expect(host.locator('.file-row[data-path="renamed.txt"]')).to_be_visible()
        select(host, 'renamed.txt')
        more_action(host, '复制')
        host.locator('.file-row[data-path="Destination"]').click()
        more_action(host, '粘贴')
        expect(page.get_by_role('heading', name='确认复制', exact=True)).to_be_visible()
        assert not (root / 'Destination/renamed.txt').exists()
        page.locator('#modal').get_by_role('button', name='确认复制', exact=True).click()
        expect(host.locator('.file-row[data-path="Destination/renamed.txt"]')).to_be_visible()
        assert (root / 'renamed.txt').exists()

        # Directory ordinary rename retains its contents.
        host.locator('.file-breadcrumbs').get_by_role('button', name='资源下载目录').click()
        select(host, 'Destination')
        host.get_by_role('button', name='重命名', exact=True).click()
        page.get_by_role('textbox', name='新名称', exact=True).fill('Archive')
        page.locator('#modal').get_by_role('button', name='保存', exact=True).click()
        expect(host.locator('.file-row[data-path="Archive"]')).to_be_visible()
        select(host, 'renamed.txt')
        more_action(host, '剪切')
        host.locator('.file-row[data-path="Media"]').click()
        more_action(host, '粘贴')
        expect(page.get_by_role('heading', name='确认移动', exact=True)).to_be_visible()
        assert (root / 'renamed.txt').exists()
        page.locator('#modal').get_by_role('button', name='取消', exact=True).click()
        assert (root / 'renamed.txt').exists()
        more_action(host, '粘贴')
        page.locator('#modal').get_by_role('button', name='确认移动', exact=True).click()
        expect(host.locator('.file-row[data-path="Media/renamed.txt"]')).to_be_visible()
        assert not (root / 'renamed.txt').exists()
        select(host, 'Media/renamed.txt')
        more_action(host, '删除')
        expect(page.get_by_role('heading', name='确认删除', exact=True)).to_be_visible()
        assert (root / 'Media/renamed.txt').exists()
        page.locator('#modal').get_by_role('button', name='确认删除', exact=True).click()
        expect(host.locator('.file-row[data-path="Media/renamed.txt"]')).to_have_count(0)
        assert (root / 'Archive/renamed.txt').read_text() == 'saved content'
        assert not errors
    finally:
        browser.close()
        playwright.stop()


@pytest.mark.parametrize('width', [390, 768, 1440])
def test_mobile_tree_breadcrumb_search_paging_image_and_selection(live_server, width):
    base, app = live_server
    root = app.state.services.settings.download_root
    for index in range(45):
        (root / f'entry{index:02}.txt').write_text(str(index))
    playwright, browser, page, errors = browser_session(base)
    try:
        page.set_viewport_size({'width': width, 'height': 1000})
        host = mount(page)
        expect(host.locator('.root-shortcut')).to_have_count(4)
        expect(host.locator('.file-row')).to_have_count(20)
        host.get_by_role('button', name='下一页', exact=True).click()
        expect(host.locator('.file-pager')).to_contain_text('第 2 / 3 页')
        host.get_by_role('searchbox', name='搜索当前目录').fill('entry4')
        expect(host.locator('.file-row')).to_have_count(5)
        host.get_by_label('全选当前页').check()
        expect(host.locator('.selection-count')).to_have_text('已选择 5 项')
        host.get_by_role('searchbox').fill('entry00')
        expect(host.locator('.file-row')).to_have_count(1)
        expect(host.locator('.selection-count')).to_have_text('未选择')
        if width <= 768:
            host.get_by_role('button', name='目录树', exact=True).click()
        expect(host.locator('.file-tree-pane')).to_be_visible()
        host.locator('.tree-root-label').filter(has_text='图片目录').click()
        expect(host.get_by_role('searchbox')).to_have_value('')
        expect(host.locator('.file-row[data-path="pixel.png"]')).to_be_visible()
        select(host, 'pixel.png')
        host.get_by_role('button', name='预览/编辑', exact=True).click()
        image = page.locator('.image-preview-frame img')
        expect(image).to_be_visible()
        page.wait_for_function('document.querySelector(".image-preview-frame img").naturalWidth > 0')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if width <= 768:
            assert host.locator('.file-browser-shell').evaluate('el => getComputedStyle(el).gridTemplateColumns').count(' ') == 0
        assert not errors
    finally:
        browser.close()
        playwright.stop()


def test_shared_context_handoff_and_media_values(live_server):
    base, _app = live_server
    playwright, browser, page, errors = browser_session(base)
    calls = []
    def resolve(route):
        payload = route.request.post_data_json
        calls.append(payload)
        refs = payload['resources']
        context = {'schema_version': 1, 'context_id': 'shared', 'revision': 7, 'resources': refs,
                   'files': [{'ref': refs[0], 'probe': {'video_codec': {'value': 'H.265', 'source': 'ffprobe'},
                                                      'audio_codec': {'value': None, 'source': 'ffprobe'}}}], 'warnings': []}
        route.fulfill(json={'context': context, 'changes': [], 'capabilities': []})
    page.route('**/api/v2/resources/resolve', resolve)
    try:
        host = mount(page, adapter=True)
        host.locator('.file-row[data-path="Media"]').click()
        select(host, 'Media/episode01.mp4')
        host.get_by_role('button', name='进入发种工作台', exact=True).click()
        page.wait_for_function('window.workbenchContext?.revision === 7')
        more_action(host, '智能命名')
        page.wait_for_function('window.namingContext === window.workbenchContext')
        host.get_by_role('button', name='预览/编辑', exact=True).click()
        expect(page.locator('.fb-media-info')).to_contain_text('H.265（ffprobe）')
        expect(page.locator('.fb-media-info')).to_contain_text('未知')
        assert len(calls) == 1
        assert calls[0]['resources'][0]['kind'] == 'file'
        assert page.evaluate('sharedContext === workbenchContext && sharedContext === namingContext')
        assert not errors
    finally:
        browser.close()
        playwright.stop()
