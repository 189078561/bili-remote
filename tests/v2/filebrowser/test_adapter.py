from __future__ import annotations

from playwright.sync_api import expect
from test_browser_modules import browser_session, live_server  # noqa: F401
from tests.v2.filebrowser.test_interactions import mount


def test_adapter_rejects_stale_sources_and_late_context(live_server):
    base, _app = live_server
    playwright, browser, page, errors = browser_session(base)
    try:
        mount(page, adapter=True)
        result = page.evaluate('''async () => {
          const item = fb.data.items.find(item => item.kind === 'directory');
          const stale = {...item, fingerprint: 'stale'};
          let staleError = '';
          try { await fbAdapter.select([stale], fb.rootKey); } catch (error) { staleError = error.message; }
          let context = null, release;
          const adapter = new FileBrowserAdapter({
            getContext: () => context,
            setContext: value => context = value,
            request: async (url, options) => {
              if (url.startsWith('/api/files/meta')) return {...item};
              if (url === '/api/v2/resources/reference') return JSON.parse(options.body);
              const refs = JSON.parse(options.body).resources;
              return new Promise(resolve => release = () => resolve({context: {
                schema_version: 1, context_id: 'late', revision: 1, resources: refs}}));
            }
          });
          const pending = adapter.contextFor([item], 'downloads').catch(error => error.message);
          while (!release) await new Promise(resolve => setTimeout(resolve, 0));
          context = {context_id: 'newer', revision: 9};
          release();
          return {staleError, lateError: await pending, context};
        }''')
        assert '资源已变化' in result['staleError']
        assert '资源选择已更新' in result['lateError']
        assert result['context']['context_id'] == 'newer'
        assert not errors
    finally:
        browser.close()
        playwright.stop()


def test_partial_move_preserves_only_unfinished_clipboard_and_nested_picker(live_server):
    base, _app = live_server
    playwright, browser, page, errors = browser_session(base)
    try:
        host = mount(page)
        page.route('**/api/tasks/98765', lambda route: route.fulfill(json={
            'status': 'failed', 'result': {'total': 2, 'processed': 2, 'succeeded': 1, 'failed': 1,
            'items': [
                {'source': {'root_key': 'downloads', 'relative_path': 'done.txt'}, 'status': 'completed'},
                {'source': {'root_key': 'downloads', 'relative_path': 'retry.txt'}, 'status': 'failed', 'message': '权限不足'}]}}))
        page.evaluate('''async () => {
          fb.clipboard = {action: 'cut', items: [
            {root_key: 'downloads', relative_path: 'done.txt'},
            {root_key: 'downloads', relative_path: 'retry.txt'}]};
          await fb.showTaskProgress(98765, {operation: 'move', clipboard: fb.clipboard});
        }''')
        assert page.evaluate('fb.clipboard.items.map(item => item.relative_path)') == ['retry.txt']
        expect(host.locator('.file-inline-task')).to_contain_text('权限不足')
        page.evaluate('''() => {
          const panel = element('div', 'outer-test'); panel.textContent = 'parent content'; showModal(panel);
          window.pickerResult = 'pending';
          FileBrowser.openPicker({mode: 'picker', selectionType: 'directory'})
            .then(result => window.pickerResult = result);
        }''')
        expect(page.locator('.file-picker-dialog')).to_be_visible()
        page.locator('.file-picker-dialog').get_by_role('button', name='取消', exact=True).click()
        expect(page.locator('.outer-test')).to_be_visible()
        page.wait_for_function('pickerResult === null')
        assert not errors
    finally:
        browser.close()
        playwright.stop()


def test_open_editor_keeps_original_root_after_background_navigation(live_server):
    base, app = live_server
    settings = app.state.services.settings
    (settings.download_root / 'same.txt').write_text('download')
    (settings.release_info_root / 'same.txt').write_text('release')
    playwright, browser, page, errors = browser_session(base)
    try:
        host = mount(page)
        host.locator('.file-row[data-path="same.txt"] .file-check').check()
        host.get_by_role('button', name='预览/编辑', exact=True).click()
        editor = page.get_by_role('textbox', name='same.txt 文本内容')
        expect(editor).to_have_value('download')
        page.evaluate('fb.navigate("release_info", "")')
        editor.fill('download edited')
        page.locator('.text-editor-dialog').get_by_role('button', name='保存', exact=True).click()
        expect(editor).not_to_be_visible()
        assert (settings.download_root / 'same.txt').read_text() == 'download edited'
        assert (settings.release_info_root / 'same.txt').read_text() == 'release'
        assert not errors
    finally:
        browser.close()
        playwright.stop()
