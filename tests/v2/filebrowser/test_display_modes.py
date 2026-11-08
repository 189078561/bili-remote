from __future__ import annotations

import pytest
from playwright.sync_api import expect
from test_browser_modules import browser_session, live_server  # noqa: F401
from tests.v2.filebrowser.test_interactions import mount, select


def mount_mode(page, display_mode, *, multiple=True):
    page.evaluate('''async ({displayMode, multiple}) => {
      fb.destroy();
      window.confirmedSelection = null;
      window.fb = new FileBrowser(document.querySelector('#fb-test'), {
        mode: displayMode === 'full' ? 'full' : 'select-file',
        displayMode, selectionType: 'both', allowMultiple: multiple, pageSize: 20,
        confirmLabel: '使用所选资源',
        onConfirm: items => { window.confirmedSelection = items; }
      });
      await fb.init();
    }''', {'displayMode': display_mode, 'multiple': multiple})
    return page.locator('#fb-test')


@pytest.mark.parametrize('display_mode', ['full', 'compact', 'picker'])
def test_display_modes_share_navigation_search_paging_and_selection(live_server, display_mode):
    base, app = live_server
    root = app.state.services.settings.download_root
    for index in range(45):
        (root / f'entry{index:02}.txt').write_text(str(index))
    (root / 'Media' / 'nested-only.txt').write_text('current-directory search boundary')
    playwright, browser, page, errors = browser_session(base)
    try:
        mount(page)
        host = mount_mode(page, display_mode)
        expect(host.locator('.unified-file-browser')).to_have_attribute('data-display-mode', display_mode)
        expect(host.get_by_role('searchbox', name='搜索当前目录')).to_have_count(1)
        expect(host.locator('.root-shortcut')).to_have_count(4)
        expect(host.locator('.file-row')).to_have_count(20)
        expect(host.locator('.file-breadcrumbs')).to_contain_text('资源下载目录')
        host.get_by_role('button', name='下一页', exact=True).click()
        expect(host.locator('.file-pager')).to_contain_text('第 2 / 3 页')

        # Search is performed before pagination, within the displayed directory.
        search = host.get_by_role('searchbox', name='搜索当前目录')
        search.fill('entry4')
        expect(host.locator('.file-row')).to_have_count(5)
        host.get_by_label('全选当前页').check()
        expect(host.locator('.selection-count')).to_have_text('已选择 5 项')
        search.fill('nested-only')
        expect(host.locator('.file-row')).to_have_count(0)
        expect(host.locator('.selection-count')).to_have_text('已选择 0 项' if display_mode == 'compact' else '未选择')

        if not host.locator('.file-tree-pane').is_visible():
            if display_mode == 'compact':
                host.locator('.file-tree-disclosure > summary').click()
            else:
                host.get_by_role('button', name='目录树', exact=True).click()
        expect(host.locator('.file-tree-pane')).to_be_visible()
        host.locator('.tree-root-label').filter(has_text='资源下载目录').click()
        expect(search).to_have_value('')
        expect(host.locator('.file-row[data-path="Media"]')).to_be_visible()
        host.locator('.file-row[data-path="Media"]').click()
        expect(host.locator('.file-breadcrumbs')).to_contain_text('Media')
        search.fill('nested-only')
        expect(host.locator('.file-row[data-path="Media/nested-only.txt"]')).to_be_visible()
        host.locator('.file-breadcrumbs').get_by_role('button', name='资源下载目录').click()
        expect(search).to_have_value('')

        search.fill('entry4')
        expect(host.locator('.file-row')).to_have_count(5)
        host.get_by_role('button', name='切换排序方向').click()
        expect(host.locator('.file-row').first).to_have_attribute('data-path', 'entry44.txt')
        host.get_by_role('combobox', name='文件排序').select_option('name')
        expect(host.locator('.file-row').first).to_have_attribute('data-path', 'entry44.txt')

        host.locator('.file-breadcrumbs').get_by_role('button', name='资源下载目录').click()
        expect(host.locator('.file-row[data-path="Media"]')).to_be_visible()
        select(host, 'Media')
        select(host, 'entry44.txt')
        expect(host.locator('.selection-count')).to_have_text('已选择 2 项')
        selected = page.evaluate('fb.selectedItems().map(item => ({path:item.relative_path, directory:item.is_dir}))')
        assert selected == [{'path': 'Media', 'directory': True}, {'path': 'entry44.txt', 'directory': False}]
        if display_mode != 'full':
            host.get_by_role('button', name='使用所选资源', exact=True).click()
            page.wait_for_function('confirmedSelection?.length === 2')
            assert page.evaluate('confirmedSelection.every(item => item.root_key === "downloads")')
        else:
            # Full management retains its original actions and confirmation flows.
            expect(host.get_by_role('button', name='预览/编辑', exact=True)).to_have_count(1)
            expect(host.locator('.file-more-menu > summary')).to_be_visible()

        # The same modes also honor callers that allow only one item.
        host = mount_mode(page, display_mode, multiple=False)
        select(host, 'Media')
        select(host, 'entry00.txt')
        expect(host.locator('.selection-count')).to_have_text('已选择 1 项')
        assert page.evaluate('fb.selectedItems().map(item => item.relative_path)') == ['entry00.txt']
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_legacy_picker_mode_infers_picker_display_and_returns_selected_directory(live_server):
    base, _app = live_server
    playwright, browser, page, errors = browser_session(base)
    try:
        mount(page)
        page.evaluate('''() => {
          window.pickerSelection = null;
          FileBrowser.openPicker({mode:'select-directory', selectionType:'directory',
            rootKeys:['downloads'], title:'选择目录', confirmLabel:'使用此目录'})
            .then(value => { window.pickerSelection = value; });
        }''')
        picker = page.locator('.file-picker-dialog')
        expect(picker.locator('.unified-file-browser')).to_have_attribute('data-display-mode', 'picker')
        picker.locator('.file-row[data-path="Media"]').click()
        expect(picker.locator('.file-breadcrumbs')).to_contain_text('Media')
        picker.get_by_role('button', name='使用此目录', exact=True).click()
        expect(picker).not_to_be_visible()
        page.wait_for_function('pickerSelection !== null')
        assert page.evaluate('pickerSelection') == {'root_key': 'downloads', 'relative_path': 'Media'}
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()
