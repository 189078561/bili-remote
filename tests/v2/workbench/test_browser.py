"""Browser checks distinguish synthetic V2 contract responses from real legacy tools."""
import json
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_browser_modules import browser_session
from test_browser_modules import live_server as shared_live_server

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def live_server(settings):
    yield from shared_live_server.__wrapped__(settings)


def mount(page):
    token = page.locator('meta[name="csrf-token"]').get_attribute("content")
    template = (ROOT / "bili_remote/templates/v2/workbench/workbench.html").read_text()
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' + '<meta name="csrf-token" content="' + token + '"><style>body{margin:0}</style>' + template + '</html>'
    page.route('**/__wb', lambda route: route.fulfill(content_type='text/html', body=document))
    page.goto(page.url.rstrip('/') + '/__wb')
    page.add_style_tag(path=str(ROOT / "bili_remote/static/v2/workbench/workbench.css"))
    page.add_script_tag(path=str(ROOT / "bili_remote/static/v2/workbench/api.js"))
    # Existing FileBrowser expects these host utilities, supplied by app.js in the real shell.
    page.evaluate('''() => {
      window.element = (tag, cls, text) => { const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n; };
      window.formatBytes = value => value + ' B';
      window.api = (path, options={}) => {const body=options.body ? JSON.parse(options.body) : undefined; return new BiliWorkbenchAPI().request(path, body, options.method || 'GET');};
    }''')
    page.add_script_tag(path=str(ROOT / "bili_remote/static/file-browser.js"))
    page.add_script_tag(path=str(ROOT / "bili_remote/static/v2/workbench/preview.js"))
    page.add_script_tag(path=str(ROOT / "bili_remote/static/v2/workbench/workbench.js"))
    page.evaluate("async () => {window.wb = await BiliWorkbench.mount(document.querySelector('[data-workbench]'), {pollInterval: 200});}")


def context(revision=1):
    return {"schema_version": 1, "context_id": "synthetic", "revision": revision,
            "resources": [{"root_key": "downloads", "relative_path": "Media/episode01.mp4", "kind": "file", "fingerprint": "fixture"}],
            "identity": {"media_type": "series"}, "selected_title": {"value": "合成测试作品"},
            "metadata": {"overview": {"value": "合成简介"}}, "files": [{
                "ref": {"root_key": "downloads", "relative_path": "Media/episode01.mp4", "kind": "file", "fingerprint": "fixture"},
                "probe": {"resolution": {"value": "1080p", "source": "ffprobe"}, "video_codec": {"value": "H.265", "source": "ffprobe"}, "audio_codec": {"value": "AAC", "source": "ffprobe"}},
            }], "warnings": [], "conflicts": [],
            "episode": {"scope": "single", "season": {"value": 1}, "episode": {"value": 1}},
            "effective_rules": {"naming_rule": {"templates": {"series": "{Title}.{Episode}"}}, "subtitle_rule": {"template_id": "tv_episode"}}}


def v2_fixture(page, requests):
    """Explicit test-only service boundary; production UI contains no sample responses."""
    def respond(route):
        request = route.request
        path = request.url.split('/api/v2/')[1].split('?')[0]
        payload = request.post_data_json if request.method == "POST" else {}
        requests.append((path, payload))
        rev = (payload.get("context") or {}).get("revision", 1)
        if path == "resources/resolve":
            resolved = context(rev + 1)
            resolved['resources'] = payload['resources']
            resolved['files'][0]['ref'] = payload['resources'][0]
            result = {"context": resolved, "changes": [], "capabilities": []}
        elif path in ("release-title/render", "subtitles/render"):
            result = {"text": "Generated title" if path.startswith("release-title") else "合成副标题", "context_revision": rev, "warnings": []}
        elif path == "publishing/preview":
            import html
            text = '\n'.join(payload[x] for x in ("release_title", "subtitle", "overview"))
            result = {"content": html.escape(text) if payload['format'] == 'html' else text,
                      "validation": {"ready": False, "errors": [], "warnings": [{"message": "缺少完整报告"}]}, "context_revision": rev}
        elif path == 'naming/preview':
            result = {"preview_token": "test-only-token", "expires_at": "2099-01-01T00:00:00Z", "context_revision": rev, "operations": [{"source": context()['resources'][0], "target_relative_path": "Media/Renamed.mp4"}], "can_apply": True, "conflicts": []}
        else:
            route.fulfill(status=404, json={"detail": "not implemented in fixture"})
            return
        route.fulfill(json=result)
    page.route('**/api/v2/**', respond)


def test_contract_edit_copy_formats_responsive_and_no_draft(live_server):
    base_url, _ = live_server
    playwright, browser, page, errors = browser_session(base_url)
    requests = []
    try:
        v2_fixture(page, requests)
        mount(page)
        page.evaluate("wb.select([{root_key:'downloads',relative_path:'Media/episode01.mp4',kind:'video',fingerprint:'fixture'}])")
        expect(page.locator('[data-edit="release_title"]')).to_have_value('Generated title')
        expect(page.locator('[data-wb="output"]')).to_contain_text('合成简介')
        page.locator('[data-edit="release_title"]').fill('人工主标题 <script>window.bad=true</script>')
        page.locator('[data-edit="subtitle"]').fill('人工副标题')
        page.locator('[data-edit="overview"]').fill('人工简介')
        expect(page.locator('[data-wb="output"]')).to_contain_text('人工简介')
        page.locator('[data-action="resolve"]').click()
        expect(page.locator('[data-wb="context-summary"]')).to_contain_text('修订 3')
        assert page.locator('[data-edit="release_title"]').input_value().startswith('人工主标题')
        assert page.locator('[data-edit="overview"]').input_value() == '人工简介'
        page.evaluate("navigator.clipboard.writeText = async value => {window.copied=value;}")
        for field in ('release_title', 'subtitle', 'overview', 'content'):
            page.locator(f'[data-copy="{field}"]').click()
            assert page.evaluate('window.copied')
        for output in ('bbcode', 'html', 'markdown'):
            page.locator('[data-wb="format"]').select_option(output)
            expect(page.locator('[data-wb="validation"]')).to_contain_text('内容尚有缺项')
            if output == 'html':
                expect(page.locator('[data-wb="html-preview"]')).to_be_visible()
                assert page.evaluate('window.bad') is None
        for width in (390, 768, 1440):
            page.set_viewport_size({"width": width, "height": 1000})
            for tab in ('content', 'images', 'report', 'torrent'):
                page.locator(f'[data-tab="{tab}"]').click()
                expect(page.locator(f'[data-panel="{tab}"]')).to_be_visible()
                expect(page.locator('[aria-label="识别与命名"]')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, tab)
            if width in (390, 1440):
                page.locator('[data-tab="content"]').click()
                page.screenshot(path=str(ROOT / '.dev' / f'workbench-{width}.png'), full_page=True)
        assert all('project_id' not in payload and 'session_id' not in payload for _, payload in requests)
        assert page.evaluate("Object.keys(localStorage).filter(k=>k.includes('workbench'))") == []
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_real_browser_unavailable_services_are_honest(live_server):
    base_url, _ = live_server
    playwright, browser, page, errors = browser_session(base_url)
    try:
        mount(page)
        page.locator('.file-row[data-path="Media"]').click()
        page.locator('.file-row[data-path="Media/episode01.mp4"] .file-check').check()
        expect(page.locator('[data-wb="issues"]')).to_contain_text('尚未接入')
        assert page.locator('[data-edit="release_title"]').input_value() == ''
        assert page.locator('[data-action="naming"]').is_disabled()
        expect(page.locator('[data-wb="task-status"]')).to_contain_text('现有工具队列')
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_real_tools_screenshot_collage_download_report_and_torrent(live_server):
    base_url, app = live_server
    from test_release_tools import FakeImageClient
    import httpx
    fake_upload = FakeImageClient()
    app.state.services.image_hosts._client_factory = lambda *_: fake_upload
    qbit_calls = []
    def qbit(request):
        qbit_calls.append(request.url.path)
        if request.url.path.endswith('/auth/login'):
            return httpx.Response(200, text='Ok.', headers={'set-cookie': 'SID=synthetic-session; path=/'})
        if request.url.path.endswith('/torrents/add'):
            return httpx.Response(200, text='Ok.')
        return httpx.Response(404)
    app.state.services.qbittorrent._client_factory = lambda url, timeout: httpx.Client(base_url=url, timeout=timeout, transport=httpx.MockTransport(qbit))
    app.state.services.database.update_settings({'qbittorrent_url': 'http://qbit.test', 'qbittorrent_username': 'test-admin'})
    path = app.state.services.settings.download_root / 'Media' / 'episode01.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24', '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', str(path)], check=True)
    playwright, browser, page, errors = browser_session(base_url)
    try:
        mount(page)
        page.locator('.file-row[data-path="Media"]').click()
        page.locator('.file-row[data-path="Media/episode01.mp4"] .file-check').check()
        page.locator('[data-tab="images"]').click()
        for mode, total in [('single', '1'), ('collage', '4')]:
            page.locator('[data-wb="image-mode"]').select_option(mode)
            page.locator('[data-wb="image-count"]').fill(total)
            previous = page.evaluate('wb.images[0]?.path')
            page.locator('[data-action="screenshots"]').click()
            try:
                page.wait_for_function('(previous) => wb.images.length > 0 && wb.images[0].path !== previous', arg=previous, timeout=30000)
            except Exception:
                print(page.locator('[data-wb="notices"]').inner_text())
                print(page.evaluate('JSON.stringify([...wb.tasks.values()])'))
                raise
            image = page.locator('[data-wb="images"] img').first
            expect(image).to_have_js_property('complete', True)
            with page.expect_download() as pending:
                page.get_by_role('link', name='下载缩略图').first.click()
            assert pending.value.suggested_filename.endswith(('.jpg', '.png'))
        page.locator('[data-action="upload"]').click()
        expect(page.locator('[data-wb="upload-links"]')).to_contain_text('https://images.example/', timeout=30000)
        assert fake_upload.calls >= 1
        page.locator('[data-tab="report"]').click()
        page.locator('[data-action="report"]').click()
        expect(page.locator('[data-wb="reports"]')).to_contain_text('MediaInfo', timeout=30000)
        page.locator('[data-tab="torrent"]').click()
        page.locator('[data-action="torrent"]').click()
        expect(page.get_by_role('link', name='下载种子')).to_be_visible(timeout=30000)
        with page.expect_download() as pending:
            page.get_by_role('link', name='下载种子').click()
        assert pending.value.suggested_filename.endswith('.torrent')
        page.get_by_role('button', name='添加到 qBittorrent', exact=True).click()
        expect(page.locator('[data-wb="notices"]')).to_contain_text('种子已提交到 qBittorrent')
        assert '/api/v2/torrents/add' in qbit_calls
        page.goto(base_url + '/static/v2/workbench/index.html')
        expect(page.locator('[data-workbench]')).to_be_visible()
        assert page.locator('[data-edit="release_title"]').input_value() == ''
        task_card = page.locator('.wb-task', has_text='torrent_build')
        task_card.get_by_role('button', name='查看产物').click()
        expect(task_card.get_by_role('link', name='下载种子')).to_be_visible()
        for task in app.state.services.database.list_processing_tasks():
            assert task['project_id'] is None
            assert not set(task['input']) & {'release_title', 'subtitle', 'overview', 'context', 'bbcode', 'html'}
        with app.state.services.database.connect() as connection:
            for table in ('release_projects', 'publish_sessions', 'media_metadata', 'release_documents'):
                assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
        assert not app.state.services.database.list_release_projects()
        assert not app.state.services.database.list_publish_sessions()
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_slow_responses_and_rename_confirmation(live_server):
    base_url, _ = live_server
    playwright, browser, page, errors = browser_session(base_url)
    requests, held = [], []
    try:
        v2_fixture(page, requests)
        mount(page)
        page.evaluate("wb.select([{root_key:'downloads',relative_path:'Media/episode01.mp4',kind:'video',fingerprint:'fixture'}])")
        expect(page.locator('[data-wb="output"]')).to_contain_text('合成简介')
        page.route('**/api/v2/release-title/render', lambda route: held.append(route))
        page.evaluate("void wb.generate('release_title')")
        page.locator('[data-edit="release_title"]').fill('人工锁定')
        assert held
        rev = page.evaluate('wb.context.revision')
        held.pop().fulfill(json={"text": "过期主标题", "context_revision": rev, "warnings": []})
        expect(page.locator('[data-edit="release_title"]')).to_have_value('人工锁定')
        page.unroute('**/api/v2/release-title/render')
        # A composition response from before the edit may never become copyable.
        page.route('**/api/v2/publishing/preview', lambda route: held.append(route))
        page.locator('[data-edit="overview"]').fill('旧简介')
        page.wait_for_timeout(300)
        assert held
        old = held.pop()
        page.locator('[data-edit="overview"]').fill('最新简介')
        old.fulfill(json={"content": "过期发布内容", "context_revision": rev, "validation": {"ready": True, "errors": [], "warnings": []}})
        assert page.locator('[data-wb="output"]').inner_text() != '过期发布内容'
        page.unroute('**/api/v2/publishing/preview')
        for route in held:
            route.fulfill(json={"content": "最新简介", "context_revision": rev, "validation": {"ready": False, "errors": [], "warnings": []}})
        held.clear()
        page.route('**/api/v2/naming/preview', lambda route: held.append(route))
        page.locator('[data-action="naming"]').click()
        page.locator('.wb-adjust > summary').click()
        page.locator('[data-wb="selected-title"]').fill('新的选名')
        assert held
        held.pop().fulfill(json={"preview_token": "stale", "expires_at": "2099-01-01T00:00:00Z", "context_revision": rev, "can_apply": True, "operations": [], "conflicts": []})
        expect(page.locator('[data-wb="rename"]')).to_be_hidden()
        page.unroute('**/api/v2/naming/preview')
        page.locator('[data-action="naming"]').click()
        expect(page.locator('[data-wb="rename"]')).to_be_visible()
        assert not any(path == 'naming/apply' for path, _ in requests)
        def apply(route):
            requests.append(('naming/apply', route.request.post_data_json))
            route.fulfill(json={"task": {"kind": "processing", "id": 999, "status": "queued", "task_type": "naming_apply"}})
        page.route('**/api/v2/naming/apply', apply)
        page.locator('[data-action="apply"]').click()
        page.wait_for_timeout(100)
        payload = [payload for path, payload in requests if path == 'naming/apply'][0]
        assert set(payload) == {'preview_token', 'expected_revision', 'confirm', 'idempotency_key'}
        assert payload['preview_token'] == 'test-only-token'
        assert payload['expected_revision'] == rev
        assert payload['confirm'] is True
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_task_identity_notifications_retry_cancel_and_refresh_without_content(live_server):
    base_url, _ = live_server
    playwright, browser, page, errors = browser_session(base_url)
    tasks = [
        {"kind": "download", "id": 1, "task_type": "download", "status": "running", "progress": None, "cancel_allowed": True, "retry_allowed": False},
        {"kind": "processing", "id": 1, "task_type": "image_upload", "status": "failed", "progress": 30, "cancel_allowed": False, "retry_allowed": True, "error": "合成上传错误"},
    ]
    actions = []
    try:
        page.route('**/api/v2/tasks?*', lambda route: route.fulfill(json={"items": tasks, "limit": 100, "offset": 0}))
        def action(route):
            actions.append(route.request.url)
            if route.request.url.endswith('/cancel'):
                tasks[0] = {**tasks[0], 'status': 'cancelled', 'cancel_allowed': False}
                route.fulfill(json={'task': tasks[0]})
            elif route.request.url.endswith('/retry'):
                tasks[1] = {**tasks[1], 'status': 'running', 'cancel_allowed': True, 'retry_allowed': False, 'error': None}
                route.fulfill(json={'task': tasks[1]})
            else:
                route.fulfill(status=404, json={})
        page.route('**/api/v2/tasks/*/*/*', action)
        mount(page)
        assert page.locator('.wb-task').count() == 2
        assert page.locator('.wb-task progress').first.get_attribute('value') is None
        assert page.locator('[data-wb="notices"]').inner_text() == ''
        page.locator('.wb-task').first.get_by_role('button', name='取消', exact=True).click()
        page.locator('.wb-task').nth(1).get_by_role('button', name='重试', exact=True).click()
        expect(page.locator('.wb-task').nth(1)).to_contain_text('运行中')
        tasks[1] = {**tasks[1], 'status': 'completed', 'finished_at': '2026-11-04T00:00:00Z', 'cancel_allowed': False}
        expect(page.locator('[data-wb="notices"]')).to_contain_text('已完成')
        page.wait_for_timeout(600)
        assert page.locator('.wb-notice', has_text='image_upload：已完成').count() == 1
        page.locator('[data-edit="release_title"]').fill('刷新后不得恢复的文案')
        mount(page)
        assert page.locator('[data-edit="release_title"]').input_value() == ''
        assert page.locator('.wb-task').count() == 2
        assert any('/download/1/cancel' in url for url in actions)
        assert any('/processing/1/retry' in url for url in actions)
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_real_standalone_entry_authentication_search_and_no_storage(live_server):
    base_url, _ = live_server
    playwright, browser, page, errors = browser_session(base_url)
    try:
        page.add_init_script("Storage.prototype.setItem = () => {throw Error('No frontend persistence allowed');}")
        page.goto(base_url + '/static/v2/workbench/index.html')
        expect(page.locator('[data-workbench]')).to_be_visible()
        expect(page.locator('.file-row[data-path="Media"]')).to_be_visible()
        page.locator('[data-wb="file-search"]').fill('no-such-file')
        expect(page.locator('[data-wb="browser"] .file-row')).to_have_count(0)
        page.locator('[data-wb="file-search"]').fill('Media')
        expect(page.locator('[data-wb="browser"] .file-row')).to_have_count(1)
        for width in (390, 768, 1440):
            page.set_viewport_size({'width': width, 'height': 1000})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('.file-row[data-path="Media"] .file-check').check()
        expect(page.locator('[data-wb="issues"]')).to_contain_text('尚未接入')
        assert page.evaluate('workbench.resources[0].kind') == 'directory'
        assert page.locator('[data-wb="scope"]').input_value() == 'season'
        assert not errors, errors
        anonymous = browser.new_context()
        visitor = anonymous.new_page()
        visitor.goto(base_url + '/static/v2/workbench/index.html')
        expect(visitor.locator('[data-entry-status]')).to_contain_text('请先登录')
        expect(visitor.locator('[data-workbench]')).to_be_hidden()
        anonymous.close()
    finally:
        browser.close()
        playwright.stop()


def test_resource_switch_discards_old_identity_and_preview_markup_is_inert(live_server):
    base_url, _ = live_server
    playwright, browser, page, errors = browser_session(base_url)
    requests, held = [], []
    try:
        v2_fixture(page, requests)
        mount(page)
        page.route('**/api/v2/resources/resolve', lambda route: held.append(route))
        page.evaluate("void wb.select([{root_key:'downloads',relative_path:'A.mp4',kind:'video',fingerprint:'A'}])")
        page.wait_for_timeout(100)
        page.evaluate("void wb.select([{root_key:'downloads',relative_path:'B.mp4',kind:'video',fingerprint:'B'}])")
        page.wait_for_timeout(100)
        assert len(held) == 2
        newest = context(8)
        newest['resources'] = [{'root_key': 'downloads', 'relative_path': 'B.mp4', 'kind': 'file', 'fingerprint': 'B'}]
        newest['selected_title']['value'] = '最新资源 B'
        held[1].fulfill(json={'context': newest, 'changes': [], 'capabilities': []})
        expect(page.locator('[data-wb="context-summary"]')).to_contain_text('最新资源 B')
        held[0].fulfill(json={'context': context(3), 'changes': [], 'capabilities': []})
        page.wait_for_timeout(100)
        assert page.evaluate('wb.context.revision') == 8
        assert page.evaluate('wb.resources[0].relative_path') == 'B.mp4'
        result = page.evaluate('''() => {
            const host=document.createElement('div');
            BiliWorkbenchPreview.render(host, '[url=javascript:alert(1)]unsafe[/url] <img src=x onerror=alert(1)>', 'bbcode');
            return {anchors:host.querySelectorAll('a').length, images:host.querySelectorAll('img').length, text:host.textContent};
        }''')
        assert result['anchors'] == result['images'] == 0
        assert '<img' in result['text']
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_completed_rename_reads_authoritative_paths_and_preserves_edits(live_server):
    base_url, app = live_server
    playwright, browser, page, errors = browser_session(base_url)
    requests = []
    tasks = []
    try:
        v2_fixture(page, requests)
        page.route('**/api/v2/tasks?*', lambda route: route.fulfill(json={'items': tasks}))
        mount(page)
        page.locator('.file-row[data-path="Media"]').click()
        page.locator('.file-row[data-path="Media/episode01.mp4"] .file-check').check()
        expect(page.locator('[data-edit="release_title"]')).to_have_value('Generated title')
        page.locator('[data-edit="overview"]').fill('必须保留的人工简介')
        page.locator('[data-action="naming"]').click()
        expect(page.locator('[data-wb="rename"]')).to_be_visible()
        # Test-only execution adapter: rename only a synthetic file in tmp_path.
        def execute(route):
            path = app.state.services.settings.download_root / 'Media' / 'episode01.mp4'
            path.rename(path.with_name('Renamed.mp4'))
            task = {'kind': 'processing', 'id': 909, 'task_type': 'naming_apply', 'status': 'queued', 'progress': None, 'cancel_allowed': False, 'retry_allowed': False}
            tasks.append(task)
            route.fulfill(json={'task': task, 'batch_id': 'synthetic-batch'})
        page.route('**/api/v2/naming/apply', execute)
        page.locator('[data-action="apply"]').click()
        expect(page.locator('[data-wb="rename"]')).to_be_hidden()
        tasks[0] = {**tasks[0], 'status': 'completed', 'finished_at': '2026-11-04T00:00:00Z'}
        expect(page.locator('[data-wb="resource"]')).to_contain_text('Renamed.mp4')
        expect(page.locator('[data-edit="release_title"]')).to_have_value('Generated title')
        assert page.locator('[data-edit="overview"]').input_value() == '必须保留的人工简介'
        assert page.evaluate('wb.resources[0].fingerprint')
        assert page.evaluate('wb.resources[0].relative_path') == 'Media/Renamed.mp4'
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_v2_task_artifact_refs_feed_upload_links_and_composition(live_server):
    base_url, _ = live_server
    playwright, browser, page, errors = browser_session(base_url)
    requests, tasks = [], []
    artifact = {'kind': 'upload', 'id': '31', 'resource': None, 'url': 'https://images.example/synthetic-actual-reference.jpg'}
    try:
        v2_fixture(page, requests)
        page.route('**/api/v2/tasks?*', lambda route: route.fulfill(json={'items': tasks}))
        page.route('**/api/v2/tasks/processing/31', lambda route: route.fulfill(json=tasks[0]))
        mount(page)
        page.evaluate("wb.select([{root_key:'downloads',relative_path:'Media/episode01.mp4',kind:'video',fingerprint:'fixture'}])")
        expect(page.locator('[data-wb="output"]')).to_contain_text('合成简介')
        queued = {'kind': 'processing', 'id': 31, 'task_type': 'image_upload', 'status': 'queued', 'progress': None, 'artifact_refs': []}
        tasks.append(queued)
        page.evaluate('(task) => wb.track(task, {type:"upload", epoch:wb.epoch})', queued)
        tasks[0] = {**queued, 'status': 'completed', 'artifact_refs': [artifact], 'finished_at': '2026-11-04T00:00:00Z'}
        page.locator('[data-tab="images"]').click()
        expect(page.locator('[data-wb="upload-links"] a')).to_have_attribute('href', artifact['url'])
        page.wait_for_timeout(350)
        compositions = [payload for path, payload in requests if path == 'publishing/preview']
        assert artifact in compositions[-1]['screenshots']
        assert artifact['url'] in compositions[-1]['links']
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()
