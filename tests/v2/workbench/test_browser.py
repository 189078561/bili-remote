"""Browser checks distinguish synthetic V2 contract responses from real legacy tools."""
import json
import re
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


def show_panel(page, name=None):
    """Navigate inline sections without opening any sheet."""
    page.wait_for_function('!(window.wb || window.V2Workbench || window.workbench)?.selecting')
    if name == 'resources':
        expect(page.locator('[data-wb="resource-browser"]')).to_be_visible()
        return
    if name is None:
        overview = page.locator('[data-collapse="overview"]')
        if overview.get_attribute('aria-expanded') == 'false':
            overview.click()
    if name in ('naming', 'identity'):
        toggle = page.locator('[data-collapse="naming"]')
        if toggle.get_attribute('aria-expanded') == 'false':
            toggle.click()
        if name == 'naming':
            details = page.locator('#wb-naming-rules')
            if details.get_attribute('open') is None:
                details.locator(':scope > summary').click()
    elif name == 'changes':
        show_panel(page, 'identity')
        details = page.locator('#wb-naming-files')
        if details.get_attribute('open') is None:
            details.locator(':scope > summary').click()
    target = {
        'images': '[data-action="screenshots"]', 'report': '[data-action="report"]',
        'torrent': '[data-action="torrent"]', 'preview': '#wb-publish-preview',
        'content': '#wb-panel-content', None: '#wb-panel-content',
    }.get(name)
    if target and page.locator(target).is_visible():
        page.locator(target).scroll_into_view_if_needed()


def mount(page):
    token = page.locator('meta[name="csrf-token"]').get_attribute("content")
    template = (ROOT / "bili_remote/templates/v2/workbench/workbench.html").read_text()
    document = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' + '<meta name="csrf-token" content="' + token + '"><style>body{margin:0}</style>' + template + '</html>'
    page.route('**/__wb', lambda route: route.fulfill(content_type='text/html', body=document))
    page.goto(page.url.rstrip('/') + '/__wb')
    page.add_style_tag(url="/static/v2/workbench/workbench.css")
    page.add_script_tag(path=str(ROOT / "bili_remote/static/v2/workbench/api.js"))
    page.add_script_tag(path=str(ROOT / "bili_remote/static/v2/workbench/preview.js"))
    page.add_script_tag(path=str(ROOT / "bili_remote/static/v2/workbench/workbench.js"))
    page.evaluate("async () => {window.wb = await BiliWorkbench.mount(document.querySelector('[data-workbench]'), {pollInterval: 200});}")


def context(revision=1):
    return {"schema_version": 1, "context_id": "synthetic", "revision": revision,
            "resources": [{"root_key": "downloads", "relative_path": "Media/episode01.mp4", "kind": "file", "fingerprint": "fixture"}],
            "identity": {"media_type": "series"}, "selected_title": {"value": "合成测试作品"},
            "metadata": {"overview": {"value": "合成简介"}}, "files": [{
                "ref": {"root_key": "downloads", "relative_path": "Media/episode01.mp4", "kind": "file", "fingerprint": "fixture"},
                "probe": {"resolution": {"value": "1080p", "source": "ffprobe"}, "video_codec": {"value": "H265", "source": "ffprobe"}, "audio_codec": {"value": "AAC", "source": "ffprobe"}},
            }], "warnings": [], "conflicts": [],
            "episode": {"scope": "single", "season": {"value": 1}, "episode": {"value": 1}},
            "effective_rules": {"naming_rule": {"template_id": "series", "templates": {"series": "{Title}.{Episode}"}}, "subtitle_rule": {"template_id": "tv_episode"},
                                "sources": {"naming_rule.template_id": {"scope": "default", "reason": "filename_episode", "source_ref": "Media/episode01.mp4"}}}}


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
        expect(page.locator('[data-wb="template-source"]')).to_contain_text('规则来源：文件名 episode01.mp4')
        expect(page.locator('[data-wb="output"]')).to_contain_text('合成简介')
        show_panel(page, None)
        page.locator('[data-edit="release_title"]').fill('人工主标题 <script>window.bad=true</script>')
        show_panel(page, None)
        page.locator('[data-edit="subtitle"]').fill('人工副标题')
        show_panel(page, None)
        page.locator('[data-edit="overview"]').fill('人工简介')
        expect(page.locator('[data-wb="output"]')).to_contain_text('人工简介')
        show_panel(page, None)
        show_panel(page, 'identity')
        page.locator('[data-action="resolve"]').click()
        page.wait_for_function('wb.context.revision === 3')
        assert page.locator('[data-edit="release_title"]').input_value().startswith('人工主标题')
        assert page.locator('[data-edit="overview"]').input_value() == '人工简介'
        page.evaluate("navigator.clipboard.writeText = async value => {window.copied=value;}")
        for field in ('release_title', 'subtitle', 'overview', 'content'):
            show_panel(page, 'preview' if field == 'content' else None)
            page.locator(f'[data-copy="{field}"]').click()
            assert page.evaluate('window.copied')
            expect(page.locator(f'[data-copy="{field}"]')).to_have_text('已复制')
        page.evaluate("""() => {
            navigator.clipboard.writeText = async () => { throw Error('剪贴板访问被拒绝'); };
            document.execCommand = () => false;
        }""")
        page.locator('[data-copy="content"]').click()
        expect(page.locator('[data-copy="content"]')).to_have_text('复制失败')
        expect(page.locator('[data-wb="notices"]')).to_contain_text('剪贴板访问被拒绝')
        for output in ('bbcode', 'html', 'markdown'):
            show_panel(page, 'preview')
            page.locator('[data-wb="format"]').select_option(output)
            expect(page.locator('[data-wb="validation"]')).to_contain_text('内容尚有缺项')
            if output == 'html':
                page.locator('[data-preview-mode="effect"]').click()
                expect(page.locator('[data-wb="html-preview"]')).to_be_visible()
                assert page.evaluate('window.bad') is None
        for width in (390, 768, 1440):
            page.set_viewport_size({"width": width, "height": 1000})
            for tab in ('content', 'images', 'report', 'torrent'):
                show_panel(page, tab)
                expect(page.locator('#wb-operations')).to_be_visible()
                expect(page.locator('#wb-tool-output')).to_be_visible()
                expect(page.locator('#wb-naming')).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, tab)
            if width in (390, 1440):
                show_panel(page, 'content')
                page.screenshot(path=str(ROOT / '.dev' / f'workbench-{width}.png'), full_page=True)
        assert all('project_id' not in payload and 'session_id' not in payload for _, payload in requests)
        assert page.evaluate("Object.keys(localStorage).filter(k=>k.includes('workbench'))") == []
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_real_browser_unconfigured_metadata_is_honest(live_server):
    base_url, _ = live_server
    playwright, browser, page, errors = browser_session(base_url)
    try:
        mount(page)
        show_panel(page, 'resources')
        page.locator('.file-row[data-path="Media"]').dblclick()
        show_panel(page, 'resources')
        page.locator('.file-row[data-path="Media/episode01.mp4"] .file-check').check()
        page.get_by_role('button', name='使用所选资源', exact=True).click()
        expect(page.locator('[data-wb="issues"]')).to_contain_text('未配置 TMDB')
        assert page.evaluate('wb.context.identity') is None
        assert page.locator('[data-action="naming"]').is_enabled()
        show_panel(page, None)
        show_panel(page, 'naming')
        page.locator('[data-action="naming"]').click()
        expect(page.locator('[data-wb="rename-warning"]')).to_contain_text('缺少')
        assert page.locator('.wb-naming-result [data-action="apply"]').is_disabled()
        expect(page.locator('[data-wb="task-status"]')).to_contain_text('刷新仅恢复任务')
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_real_tools_screenshot_collage_download_report_and_torrent(live_server):
    base_url, app = live_server
    from test_release_tools import FakeImageClient
    fake_upload = FakeImageClient()
    app.state.services.image_hosts._client_factory = lambda *_: fake_upload
    path = app.state.services.settings.download_root / 'Media' / 'episode01.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24', '-t', '3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', str(path)], check=True)
    playwright, browser, page, errors = browser_session(base_url, legacy=False)
    try:
        page.locator('.app-tabs [data-view=publish]').click()
        page.wait_for_function('window.V2Workbench')
        page.evaluate('window.wb = V2Workbench')
        show_panel(page, 'resources')
        page.locator('[data-workbench] .file-row[data-path="Media"]').dblclick()
        show_panel(page, 'resources')
        page.locator('[data-workbench] .file-row[data-path="Media/episode01.mp4"] .file-check').check()
        page.get_by_role('button', name='使用所选资源', exact=True).click()
        expect(page.locator('[data-wb="resource-media"]')).to_contain_text('1 个媒体文件')
        show_panel(page, 'identity')
        page.locator('[data-wb="selected-title"]').fill('Synthetic Tool Flow')
        show_panel(page, None)
        show_panel(page, 'naming')
        page.locator('[data-action="overrides"]').click()
        expect(page.locator('[data-edit="release_title"]')).to_have_value(re.compile('Synthetic Tool Flow'))
        show_panel(page, None)
        page.locator('[data-edit="overview"]').fill('Synthetic overview for complete publishing flow')
        show_panel(page, 'images')
        database = app.state.services.database
        for mode, total in [('single', '1'), ('single', '2'), ('collage', '4')]:
            database.update_settings({'screenshot_output_mode': mode, 'collage_default_total': total,
                                      'collage_default_layout_mode': 'count', 'collage_default_content_type': 'custom',
                                      'collage_custom_skip_start': '0', 'collage_custom_skip_end': '0'})
            previous = page.evaluate('wb.images[0]?.path')
            show_panel(page, 'images')
            page.locator('[data-action="screenshots"]').click()
            try:
                page.wait_for_function('(previous) => wb.images.length > 0 && wb.images[0].path !== previous', arg=previous, timeout=30000)
            except Exception:
                print(page.locator('[data-wb="notices"]').inner_text())
                print(page.evaluate('JSON.stringify([...wb.tasks.values()])'))
                raise
            image = page.locator('[data-wb="images"] img').first
            expect(image).to_have_js_property('complete', True)
            preview = page.locator('[data-wb="images"] .wb-image-preview').first
            preview.focus()
            page.keyboard.press('Enter')
            dialog = page.locator('[data-wb="image-dialog"]')
            expect(dialog.locator('img')).to_be_visible()
            expect(dialog.get_by_role('button', name='关闭')).to_be_visible()
            page.keyboard.press('Escape')
            expect(dialog).not_to_be_visible()
            expect(preview).to_be_focused()
            with page.expect_download() as pending:
                page.get_by_role('link', name='下载缩略图').first.click()
            assert pending.value.suggested_filename.endswith(('.jpg', '.png'))
        show_panel(page, 'images')
        page.screenshot(path=str(ROOT / '.dev/workbench-product-visual/tool-real-screenshots.png'))
        page.locator('[data-action="upload"]').click()
        expect(page.locator('[data-wb="upload-links"]')).to_contain_text('https://images.example/', timeout=30000)
        expect(page.get_by_role('button', name='复制全部 BBCode')).to_be_visible()
        assert fake_upload.calls >= 1
        page.route('**/api/uploads/jobs', lambda route: route.fulfill(status=422, json={'detail': '合成图床失败'}))
        page.locator('[data-action=upload]').click()
        expect(page.locator('[data-wb=tool-progress-upload]')).to_contain_text('合成图床失败')
        expect(page.locator('[data-wb=images] img')).to_have_count(1)
        expect(page.locator('[data-wb=upload-links]')).to_contain_text('https://images.example/')
        page.unroute('**/api/uploads/jobs')
        show_panel(page, 'report')
        show_panel(page, 'report')
        page.locator('[data-action="report"]').click()
        expect(page.locator('[data-wb="reports"]')).to_contain_text('MediaInfo', timeout=30000)
        page.screenshot(path=str(ROOT / '.dev/workbench-product-visual/tool-real-report.png'))
        show_panel(page, 'torrent')
        show_panel(page, 'torrent')
        page.locator('[data-action="torrent"]').click()
        expect(page.get_by_role('link', name='下载种子')).to_be_visible(timeout=30000)
        page.screenshot(path=str(ROOT / '.dev/workbench-product-visual/tool-real-torrent.png'))
        with page.expect_download() as pending:
            page.get_by_role('link', name='下载种子').click()
        assert pending.value.suggested_filename.endswith('.torrent')
        assert page.get_by_role('button', name='添加到 qBittorrent', exact=True).count() == 0
        show_panel(page, 'content')
        assert page.locator('.wb-sheet, [data-sheet=tools]').count() == 0
        expect(page.locator('[data-wb="torrents"]')).to_contain_text(' B')
        expect(page.locator('[data-wb="output"]')).to_contain_text('Synthetic overview')
        expect(page.locator('[data-wb="output"]')).to_contain_text('https://images.example/')
        expect(page.locator('[data-wb="output"]')).to_contain_text('MediaInfo')
        page.goto(base_url + '/static/v2/workbench/index.html')
        expect(page.locator('[data-workbench]')).to_be_visible()
        assert page.locator('[data-edit="release_title"]').input_value() == ''
        expect(page.locator('[data-wb="resource-browser"]')).to_be_visible()
        show_panel(page, 'resources')
        page.locator('[data-workbench] .file-row[data-path="Media"]').dblclick()
        show_panel(page, 'resources')
        page.locator('[data-workbench] .file-row[data-path="Media/episode01.mp4"] .file-check').check()
        page.get_by_role('button', name='使用所选资源', exact=True).click()
        expect(page.locator('[data-wb="studio"]')).to_be_visible()
        output = page.locator('#wb-tool-output')
        expect(output.get_by_role('link', name='下载种子')).to_be_visible()
        expect(output.locator('[data-wb=reports]')).to_contain_text('MediaInfo')
        expect(output.locator('[data-wb=reports]')).not_to_contain_text(str(path.parent))
        expect(output.locator('[data-wb=images] img')).to_have_count(1)
        expect(output.locator('[data-wb=upload-links]')).to_contain_text('https://images.example/')
        assert page.locator('.wb-tasks').get_attribute('open') is None
        assert page.locator('[data-edit=overview]').input_value() != 'Synthetic overview for complete publishing flow'
        output.get_by_role('button', name='清空', exact=True).click()
        expect(output.locator('[data-wb=reports]')).to_be_empty()
        expect(page.locator('[data-wb=output]')).not_to_contain_text('Complete name')
        page.locator('[data-action=report]').click()
        expect(output.locator('[data-wb=reports]')).to_contain_text('MediaInfo')
        assert not page.locator('.wb-sheet').count()
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
        show_panel(page, None)
        page.locator('[data-edit="release_title"]').fill('人工锁定')
        assert held
        rev = page.evaluate('wb.context.revision')
        held.pop().fulfill(json={"text": "过期主标题", "context_revision": rev, "warnings": []})
        expect(page.locator('[data-edit="release_title"]')).to_have_value('人工锁定')
        page.unroute('**/api/v2/release-title/render')
        # A composition response from before the edit may never become copyable.
        page.route('**/api/v2/publishing/preview', lambda route: held.append(route))
        show_panel(page, None)
        page.locator('[data-edit="overview"]').fill('旧简介')
        page.wait_for_timeout(300)
        assert held
        old = held.pop()
        show_panel(page, None)
        page.locator('[data-edit="overview"]').fill('最新简介')
        old.fulfill(json={"content": "过期发布内容", "context_revision": rev, "validation": {"ready": True, "errors": [], "warnings": []}})
        assert page.locator('[data-wb="output"]').inner_text() != '过期发布内容'
        page.unroute('**/api/v2/publishing/preview')
        for route in held:
            route.fulfill(json={"content": "最新简介", "context_revision": rev, "validation": {"ready": False, "errors": [], "warnings": []}})
        held.clear()
        page.route('**/api/v2/naming/preview', lambda route: held.append(route))
        show_panel(page, None)
        show_panel(page, 'naming')
        page.locator('[data-action="naming"]').click()
        show_panel(page, None)
        page.locator('[data-wb="selected-title"]').fill('新的选名')
        assert held
        held.pop().fulfill(json={"preview_token": "stale", "expires_at": "2099-01-01T00:00:00Z", "context_revision": rev, "can_apply": True, "operations": [], "conflicts": []})
        expect(page.locator('[data-wb="rename"]')).to_be_hidden()
        page.unroute('**/api/v2/naming/preview')
        show_panel(page, None)
        show_panel(page, 'naming')
        page.locator('[data-action="naming"]').click()
        expect(page.locator('[data-wb="rename"]')).to_be_visible()
        accepted_revision = page.evaluate('wb.preview.context_revision')
        assert accepted_revision > rev  # The latest name was resolved before preview.
        assert not any(path == 'naming/apply' for path, _ in requests)
        def apply(route):
            requests.append(('naming/apply', route.request.post_data_json))
            route.fulfill(json={"task": {"kind": "processing", "id": 999, "status": "queued", "task_type": "naming_apply"}})
        page.route('**/api/v2/naming/apply', apply)
        show_panel(page, None)
        show_panel(page, 'changes')
        page.locator('[data-action="apply"]').click()
        page.wait_for_timeout(100)
        payload = [payload for path, payload in requests if path == 'naming/apply'][0]
        assert set(payload) == {'preview_token', 'expected_revision', 'confirm', 'idempotency_key'}
        assert payload['preview_token'] == 'test-only-token'
        assert payload['expected_revision'] == accepted_revision
        assert payload['confirm'] is True
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_task_identity_notifications_retry_cancel_and_refresh_without_content(live_server):
    base_url, _ = live_server
    playwright, browser, page, errors = browser_session(base_url)
    requests = []
    tasks = [
        {"kind": "download", "id": 1, "task_type": "download", "status": "running", "progress": None, "cancel_allowed": True, "retry_allowed": False},
        {"kind": "processing", "id": 1, "task_type": "image_upload", "status": "failed", "progress": 30, "cancel_allowed": False, "retry_allowed": True, "error": "合成上传错误"},
    ]
    actions = []
    try:
        v2_fixture(page, requests)
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
        page.evaluate("wb.select([{root_key:'downloads',relative_path:'Media/episode01.mp4',kind:'video',fingerprint:'fixture'}])")
        assert page.locator('.wb-task').count() == 2
        assert page.locator('.wb-task progress').first.get_attribute('value') is None
        assert page.locator('[data-wb="notices"]').inner_text() == ''
        page.locator('.wb-tasks > summary').click()
        page.locator('.wb-task').first.get_by_role('button', name='取消', exact=True).click()
        page.locator('.wb-task').nth(1).get_by_role('button', name='重试', exact=True).click()
        expect(page.locator('.wb-task').nth(1)).to_contain_text('运行中')
        tasks[1] = {**tasks[1], 'status': 'completed', 'finished_at': '2026-11-04T00:00:00Z', 'cancel_allowed': False}
        expect(page.locator('[data-wb="notices"]')).to_contain_text('已完成')
        page.wait_for_timeout(600)
        assert page.locator('.wb-notice', has_text='image_upload：已完成').count() == 1
        show_panel(page, None)
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
        show_panel(page, 'resources')
        expect(page.locator('.file-row[data-path="Media"]')).to_be_visible()
        search = page.locator('[data-wb="browser"]').get_by_role('searchbox', name='搜索当前目录')
        expect(page.get_by_role('searchbox')).to_have_count(1)
        search.fill('no-such-file')
        expect(page.locator('[data-wb="browser"] .file-row')).to_have_count(0)
        search.fill('Media')
        expect(page.locator('[data-wb="browser"] .file-row')).to_have_count(1)
        for width in (390, 768, 1440):
            page.set_viewport_size({'width': width, 'height': 1000})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        show_panel(page, 'resources')
        page.locator('.file-row[data-path="Media"] .file-check').check()
        page.get_by_role('button', name='使用所选资源', exact=True).click()
        expect(page.locator('[data-wb="issues"]')).to_contain_text('未配置 TMDB')
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


def test_module03_signed_in_main_page_inline_media_and_layout(live_server):
    base_url, _ = live_server
    playwright, browser, page, errors = browser_session(base_url, legacy=False)
    try:
        page.locator('.app-tabs [data-view=publish]').click()
        page.wait_for_function('window.V2Workbench')
        host = page.locator('#publish-view [data-workbench]')
        assert host.locator('.unified-file-browser,.wb-sheet').count() == 0
        order = host.evaluate("""el => [...el.querySelectorAll(
            '[data-wb=resource-browser], #wb-naming, #wb-operations, #wb-tool-output, #wb-panel-content, #wb-publish-preview'
        )].map(section => section.id || section.className)""")
        assert order[0] == 'wb-resources'
        assert order[1:] == ['wb-operations', 'wb-tool-output', 'wb-naming', 'wb-panel-content', 'wb-publish-preview']
        expect(host.locator('[data-action=report]')).to_be_disabled()
        picker = host.locator('[data-wb=browser]')
        picker.locator('.file-row[data-path="Media"]').dblclick()
        picker.locator('.file-row[data-path="Media/episode01.mp4"]').dblclick()
        expect(host.locator('[data-wb=resource-browser]')).to_be_visible()
        expect(host.locator('[data-wb=context-summary]')).to_contain_text('Media/episode01.mp4')
        expect(host.locator('[data-action=report]')).to_be_enabled()
        title = host.locator('[data-wb=title-zh]')
        naming = host.locator('[data-collapse=naming]')
        overview = host.locator('[data-collapse=overview]')
        expect(naming).to_have_attribute('aria-expanded', 'false')
        expect(overview).to_have_attribute('aria-expanded', 'false')
        title.fill('合成片名')
        expect(title).to_be_visible()
        expect(host.locator('[data-edit=release_title]')).to_be_visible()
        v2_fixture(page, [])
        page.route('**/api/v2/metadata/search?*', lambda route: route.fulfill(json={'items': [
            {'provider': 'tmdb', 'id': '42', 'title': '合成剧集候选', 'media_type': 'series'}]}))
        host.locator('[data-action=search]').click()
        candidate = host.get_by_role('button', name=re.compile('合成剧集候选'))
        expect(candidate).to_be_visible()
        expect(naming).to_have_attribute('aria-expanded', 'false')
        candidate.click()
        expect(candidate).to_have_count(0)
        expect(naming).to_have_attribute('aria-expanded', 'true')
        expect(overview).to_have_attribute('aria-expanded', 'true')
        expect(title).to_have_value('合成片名')
        host.locator('[data-action=search]').click()
        expect(candidate).to_be_visible()
        expect(overview).to_have_attribute('aria-expanded', 'false')
        candidate.click()
        expect(overview).to_have_attribute('aria-expanded', 'true')
        naming.click()
        expect(overview).to_have_attribute('aria-expanded', 'true')
        expect(title).to_be_visible()
        output = host.locator('[data-collapse=output]')
        output.click()
        expect(output).to_have_attribute('aria-expanded', 'false')
        assert host.locator('[data-wb=reports]').evaluate('el => !!el.closest("[inert]")')
        output.click()
        expect(output).to_have_attribute('aria-expanded', 'true')
        expect(host.locator('[data-wb=trackers], [data-wb=image-mode]')).to_have_count(0)
        host.locator('[data-tool-settings]').click()
        expect(page.locator('#settings-view')).to_be_visible()
        page.locator('.app-tabs [data-view=publish]').click()
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_first_level_resource_picker_stays_visible_with_downloads_only(live_server):
    base_url, app = live_server
    media = app.state.services.settings.download_root / 'Media'
    (media / 'episode02.mp4').write_bytes(b'synthetic-second-episode')
    playwright, browser, page, errors = browser_session(base_url)
    requests = []
    try:
        v2_fixture(page, requests)
        # Let the real resource-reference service compute the recursive
        # directory fingerprint while metadata/render calls remain synthetic.
        page.route('**/api/v2/resources/reference', lambda route: route.continue_())
        mount(page)
        picker = page.locator('[data-wb="resource-browser"]')
        studio = page.locator('[data-wb="studio"]')
        expect(picker).to_be_visible()
        expect(studio).to_be_visible()
        expect(picker.get_by_role('navigation', name='当前位置')).to_have_text('资源下载目录')
        expect(picker.get_by_role('button', name=re.compile('种子目录|图片目录|发布信息目录'))).to_have_count(0)
        assert page.evaluate("getComputedStyle(document.body).overflowY") != 'hidden'

        directory = page.locator('.file-row[data-path="Media"]')
        directory.click()
        expect(directory).to_have_class(re.compile('wb-file-active'))
        assert page.evaluate('wb.browser.path') == ''
        directory.dblclick()
        expect(page.get_by_role('navigation', name='当前位置')).to_contain_text('Media')
        up = page.locator('[data-resource-action="up"]')
        expect(up).to_be_enabled()
        search = page.locator('[data-wb="browser"]').get_by_role('searchbox', name='搜索当前目录')
        refresh = page.get_by_role('button', name='刷新', exact=True)
        with page.expect_request(lambda request: '/api/files/browse?' in request.url and 'path=Media' in request.url):
            refresh.click()
        expect(page.locator('[data-wb="browser"] .file-row')).to_have_count(2)
        search.fill('ISODE02')
        expect(page.locator('[data-wb="browser"] .file-row')).to_have_count(1)
        expect(page.locator('.file-row[data-path="Media/episode02.mp4"]')).to_be_visible()
        search.fill('')
        expect(page.locator('[data-wb="browser"] .file-row')).to_have_count(2)

        for name in ('episode01.mp4', 'episode02.mp4'):
            page.locator(f'.file-row[data-path="Media/{name}"] .file-check').check()
        expect(page.locator('[data-wb="selection-count"]')).to_have_text('已勾选 2 项')
        expect(page.locator('[data-wb="selection-list"] .wb-selection-item')).to_have_count(2)
        assert page.evaluate('wb.resources.length') == 0
        assert page.evaluate('wb.pendingSelection.length') == 2

        up.click()
        expect(page.get_by_role('navigation', name='当前位置')).to_have_text('资源下载目录')
        expect(page.locator('[data-wb="selection-count"]')).to_be_hidden()
        page.locator('.file-row[data-path="Media"]').dblclick()
        page.get_by_role('button', name='使用当前目录', exact=True).click()
        expect(studio).to_be_visible()
        expect(picker).to_be_visible()
        page.wait_for_function("wb.context?.resources[0]?.relative_path === 'Media' && wb.resources[0].kind === 'directory'")
        assert len([path for path, _ in requests if path == 'resources/resolve']) == 1
        expect(picker.get_by_role('navigation', name='当前位置')).to_contain_text('Media')

        mount(page)
        expect(picker).to_be_visible()
        handoff = context(10)
        resource = handoff['resources'][0]
        assert page.evaluate('async ({resource, context}) => wb.select([resource], {context})',
                             {'resource': resource, 'context': handoff}) is True
        expect(studio).to_be_visible()
        expect(picker).to_be_visible()
        assert page.evaluate('wb.resources[0].relative_path') == resource['relative_path']
        assert len([path for path, _ in requests if path == 'resources/resolve']) == 1
        assert not errors, errors
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
        expect(page.locator('[data-wb="resource"]')).to_have_text('B.mp4')
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
    held_resolves, held_reports = [], []
    try:
        v2_fixture(page, requests)
        page.route('**/api/v2/tasks?*', lambda route: route.fulfill(json={'items': tasks}))
        mount(page)
        show_panel(page, 'resources')
        page.locator('.file-row[data-path="Media"]').dblclick()
        show_panel(page, 'resources')
        page.locator('.file-row[data-path="Media/episode01.mp4"] .file-check').check()
        page.get_by_role('button', name='使用所选资源', exact=True).click()
        expect(page.locator('[data-edit="release_title"]')).to_have_value('Generated title')
        show_panel(page, None)
        page.locator('[data-edit="overview"]').fill('必须保留的人工简介')
        original_epoch = page.evaluate('wb.epoch')
        def hold_old_resolution(route):
            if route.request.post_data_json['resources'][0]['relative_path'] == 'Media/episode01.mp4':
                held_resolves.append(route)
            else:
                route.fallback()
        page.route('**/api/v2/resources/resolve', hold_old_resolution)
        page.route('**/api/v2/reports', lambda route: held_reports.append(route))
        with page.expect_request('**/api/v2/resources/resolve'), page.expect_request('**/api/v2/reports'):
            page.evaluate('''() => {
                window.beforeRenameResolve = wb.resolve();
                window.beforeRenameReport = wb.report();
            }''')
        for _ in range(100):
            if held_resolves and held_reports:
                break
            page.wait_for_timeout(20)
        assert held_resolves and held_reports
        show_panel(page, None)
        show_panel(page, 'naming')
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
        show_panel(page, None)
        show_panel(page, 'changes')
        page.locator('[data-action="apply"]').click()
        expect(page.locator('[data-wb="rename"]')).to_be_hidden()
        tasks[0] = {**tasks[0], 'status': 'completed', 'finished_at': '2026-11-04T00:00:00Z'}
        expect(page.locator('[data-wb="context-summary"]')).to_contain_text('Renamed.mp4')
        expect(page.locator('[data-edit="release_title"]')).to_have_value('Generated title')
        assert page.locator('[data-edit="overview"]').input_value() == '必须保留的人工简介'
        assert page.evaluate('wb.resources[0].fingerprint')
        assert page.evaluate('wb.resources[0].relative_path') == 'Media/Renamed.mp4'
        page.wait_for_function("wb.context?.resources[0]?.relative_path === 'Media/Renamed.mp4'")
        renamed_context = page.evaluate('wb.context')
        held_resolves.pop().fulfill(json={'context': context(50), 'changes': [], 'capabilities': []})
        page.evaluate('beforeRenameResolve')
        assert page.evaluate('wb.context') == renamed_context
        assert page.evaluate('wb.epoch') == original_epoch

        # An already submitted tool still belongs to the same logical resource
        # after an in-place rename, including a submission response arriving late.
        report_path = app.state.services.settings.release_info_root / 'rename-associated-report.txt'
        report_path.write_text('同资源改名后仍保留的真实报告产物', encoding='utf-8')
        report_task = {'kind': 'processing', 'id': 910, 'task_type': 'media_report', 'status': 'queued',
                       'progress': None, 'cancel_allowed': False, 'retry_allowed': False}
        tasks.append(report_task)
        held_reports.pop().fulfill(json={'task': report_task})
        page.evaluate('beforeRenameReport')
        assert page.evaluate("wb.jobs.get('processing:910').epoch") == original_epoch
        tasks[1] = {**report_task, 'status': 'completed', 'finished_at': '2026-11-05T00:00:00Z', 'result': {},
                    'artifact_refs': [{'kind': 'report', 'id': '910', 'url': None,
                                       'resource': {'root_key': 'release_info', 'relative_path': report_path.name,
                                                    'kind': 'file', 'fingerprint': 'synthetic-report-reference'}}]}
        show_panel(page, 'report')
        expect(page.locator('[data-wb="reports"]')).to_contain_text('同资源改名后仍保留的真实报告产物')
        assert page.evaluate("wb.artifacts.some(item => item.kind === 'report' && item.id === '910')")
        assert page.evaluate('wb.resources[0].relative_path') == 'Media/Renamed.mp4'
        expect(page.locator('[data-edit="overview"]')).to_have_value('必须保留的人工简介')
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
        page.route('**/api/tasks/31', lambda route: route.fulfill(json=tasks[0]))
        mount(page)
        page.evaluate("wb.select([{root_key:'downloads',relative_path:'Media/episode01.mp4',kind:'video',fingerprint:'fixture'}])")
        expect(page.locator('[data-wb="output"]')).to_contain_text('合成简介')
        queued = {'kind': 'processing', 'id': 31, 'task_type': 'image_upload', 'status': 'queued', 'progress': None, 'artifact_refs': []}
        tasks.append(queued)
        page.evaluate('(task) => wb.track(task, {type:"upload", epoch:wb.epoch})', queued)
        tasks[0] = {**queued, 'status': 'completed', 'artifact_refs': [artifact], 'finished_at': '2026-11-04T00:00:00Z'}
        show_panel(page, 'images')
        expect(page.locator('[data-wb="upload-links"] a')).to_have_attribute('href', artifact['url'])
        page.wait_for_timeout(350)
        compositions = [payload for path, payload in requests if path == 'publishing/preview']
        assert artifact in compositions[-1]['screenshots']
        assert artifact['url'] in compositions[-1]['links']
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_file_checks_stage_without_reset_and_resource_switch_requires_confirmation(live_server):
    base_url, app = live_server
    directory = app.state.services.settings.download_root / 'Media'
    for name in ('episode02.mp4', 'episode03.mp4'):
        (directory / name).write_bytes(b'synthetic-selection-only-video')
    playwright, browser, page, errors = browser_session(base_url)
    requests = []
    snapshot = '''() => ({resources: wb.resources, context: wb.context, epoch: wb.epoch,
        fields: wb.fields, dirty: [...wb.dirty], preview: wb.preview,
        artifacts: wb.artifacts, links: wb.links, content: wb.content})'''
    try:
        v2_fixture(page, requests)
        mount(page)
        show_panel(page, 'resources')
        page.locator('.file-row[data-path="Media"]').dblclick()
        show_panel(page, 'resources')
        page.locator('.file-row[data-path="Media/episode01.mp4"] .file-check').check()
        assert page.evaluate('wb.resources.length') == 0
        assert not any(path == 'resources/resolve' for path, _ in requests)
        use_selection = page.get_by_role('button', name='使用所选资源', exact=True)
        use_selection.click()
        expect(page.locator('[data-edit="release_title"]')).to_have_value('Generated title')
        for field, text in [('release_title', '保留人工主标题'), ('subtitle', '保留人工副标题'), ('overview', '保留人工简介')]:
            show_panel(page, None)
            page.locator(f'[data-edit="{field}"]').fill(text)
        # Apply a synthetic task response through the actual artifact association path.
        page.evaluate('''async () => {
            await wb.applyTask({kind:'processing',id:771,status:'completed',result:{},
                artifact_refs:[{kind:'upload',id:'771',resource:null,url:'https://images.example/keep.jpg'}]},
                {type:'upload',epoch:wb.epoch});
        }''')
        show_panel(page, None)
        show_panel(page, 'naming')
        page.locator('[data-action="naming"]').click()
        page.wait_for_function('!wb.busy("naming") && !wb.timers.has(wb.composeTimer)')
        expect(page.locator('[data-wb="rename"]')).to_be_visible()
        expect(page.locator('[data-wb="output"]')).to_contain_text('保留人工简介')
        before = page.evaluate(snapshot)
        assert before['artifacts'] and before['preview']
        for name in ('episode01.mp4', 'episode02.mp4', 'episode03.mp4'):
            show_panel(page, 'resources')
            page.locator(f'.file-row[data-path="Media/{name}"] .file-check').check()
            assert page.evaluate(snapshot) == before
        assert len([path for path, _ in requests if path == 'resources/resolve']) == 1

        dialogs = []
        def cancel_switch(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()
        page.once('dialog', cancel_switch)
        use_selection.click()
        assert dialogs
        assert page.evaluate(snapshot) == before
        assert len([path for path, _ in requests if path == 'resources/resolve']) == 1

        page.once('dialog', lambda dialog: dialog.accept())
        use_selection.click()
        expect(page.locator('[data-edit="release_title"]')).to_have_value('Generated title')
        assert page.evaluate('wb.resources.map(item => item.relative_path).sort()') == [
            'Media/episode01.mp4', 'Media/episode02.mp4', 'Media/episode03.mp4']
        assert page.evaluate('[...wb.dirty]') == []
        assert page.evaluate('wb.artifacts') == []
        page.wait_for_function('wb.preview?.context_revision === wb.context?.revision')
        assert page.evaluate('wb.epoch') > before['epoch']
        assert len([path for path, _ in requests if path == 'resources/resolve']) == 2
        expect(page.locator('[data-wb="output"]')).to_contain_text('合成简介')

        # Reusing the same resources, even reordered, must preserve current edits.
        show_panel(page, None)
        page.locator('[data-edit="overview"]').fill('同资源保留')
        expect(page.locator('[data-wb="output"]')).to_contain_text('同资源保留')
        same = page.evaluate(snapshot)
        unexpected_dialogs = []
        def reject_unexpected_switch(dialog):
            unexpected_dialogs.append(dialog.message)
            dialog.dismiss()
        page.on('dialog', reject_unexpected_switch)
        page.evaluate('wb.select([...wb.resources].reverse())')
        assert page.evaluate(snapshot) == same
        assert len([path for path, _ in requests if path == 'resources/resolve']) == 2
        assert page.evaluate("wb.select([{root_key:'downloads',relative_path:'../escape.mp4',kind:'file',fingerprint:'invalid'}])") is False
        assert page.evaluate(snapshot) == same
        assert not unexpected_dialogs
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_old_resource_resolve_title_and_composition_cannot_overwrite_new_edits(live_server):
    base_url, _ = live_server
    playwright, browser, page, errors = browser_session(base_url)
    requests, held = [], {}
    try:
        v2_fixture(page, requests)
        mount(page)
        page.evaluate("wb.select([{root_key:'downloads',relative_path:'A.mp4',kind:'file',fingerprint:'A'}])")
        expect(page.locator('[data-wb="output"]')).to_contain_text('合成简介')
        revision = page.evaluate('wb.context.revision')
        def hold_old_resource(route):
            payload = route.request.post_data_json
            resources = payload.get('resources') or payload.get('context', {}).get('resources', [])
            if resources and resources[0]['relative_path'] == 'A.mp4':
                held[route.request.url.split('/api/v2/')[1]] = route
            else:
                route.fallback()
        for endpoint in ('resources/resolve', 'release-title/render', 'publishing/preview'):
            page.route('**/api/v2/' + endpoint, hold_old_resource)
        with page.expect_request('**/api/v2/resources/resolve'), page.expect_request('**/api/v2/release-title/render'), page.expect_request('**/api/v2/publishing/preview'):
            page.evaluate('''() => {
                window.oldWork = Promise.allSettled([wb.resolve(), wb.generate('release_title'), wb.compose()]);
            }''')
        # A request event can precede its Python route callback; pump events until
        # every deliberately suspended response is available, with a bounded wait.
        for _ in range(100):
            if len(held) == 3:
                break
            page.wait_for_timeout(20)
        assert set(held) == {'resources/resolve', 'release-title/render', 'publishing/preview'}
        assert page.evaluate("wb.select([{root_key:'downloads',relative_path:'B.mp4',kind:'file',fingerprint:'B'}])") is True
        for field, text in [('release_title', 'B 人工主标题'), ('subtitle', 'B 人工副标题'), ('overview', 'B 人工简介')]:
            show_panel(page, None)
            page.locator(f'[data-edit="{field}"]').fill(text)
        expect(page.locator('[data-wb="output"]')).to_contain_text('B 人工简介')
        new_context = page.evaluate('wb.context')

        old_context = context(revision + 1)
        old_context['resources'] = [{'root_key': 'downloads', 'relative_path': 'A.mp4', 'kind': 'file', 'fingerprint': 'A'}]
        old_context['selected_title']['value'] = '迟到的 A 识别'
        held['resources/resolve'].fulfill(json={'context': old_context, 'changes': [], 'capabilities': []})
        held['release-title/render'].fulfill(json={'text': '迟到的 A 主标题', 'context_revision': revision, 'warnings': []})
        held['publishing/preview'].fulfill(json={'content': '迟到的 A 完整内容', 'context_revision': revision,
                                                'validation': {'ready': True, 'errors': [], 'warnings': []}})
        page.evaluate('oldWork')
        assert page.evaluate('wb.context') == new_context
        assert page.evaluate('wb.resources[0].relative_path') == 'B.mp4'
        for field, text in [('release_title', 'B 人工主标题'), ('subtitle', 'B 人工副标题'), ('overview', 'B 人工简介')]:
            expect(page.locator(f'[data-edit="{field}"]')).to_have_value(text)
        expect(page.locator('[data-wb="output"]')).to_contain_text('B 人工简介')
        assert '迟到的 A' not in page.locator('[data-wb="output"]').inner_text()
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_same_resource_from_other_entry_normalizes_fingerprint_without_reset(live_server):
    base_url, app = live_server
    playwright, browser, page, errors = browser_session(base_url)
    requests, reference_calls, dialogs = [], [], []
    canonical = {'root_key': 'downloads', 'relative_path': 'Media/episode01.mp4',
                 'kind': 'file', 'fingerprint': 'canonical-before-change'}
    reference = dict(canonical)
    snapshot = '''() => ({resources: wb.resources, context: wb.context, epoch: wb.epoch,
        fields: wb.fields, dirty: [...wb.dirty], artifacts: wb.artifacts, preview: wb.preview})'''
    def resolve_reference(route):
        reference_calls.append(route.request.post_data_json)
        route.fulfill(json=reference)
    def dismiss_switch(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()
    try:
        v2_fixture(page, requests)
        page.route('**/api/v2/resources/reference', resolve_reference)
        mount(page)
        supplied_context = context(7)
        supplied_context['resources'] = [canonical]
        supplied_context['files'][0]['ref'] = canonical
        # File-manager/download handoffs supply canonical V2 fingerprints, while
        # the shared browser still lists the original quick fingerprints.
        assert page.evaluate('({resource, context}) => wb.select([resource], {context})',
                             {'resource': canonical, 'context': supplied_context}) is True
        show_panel(page, None)
        page.locator('[data-edit="overview"]').fill('跨入口保留的人工简介')
        expect(page.locator('[data-wb="output"]')).to_contain_text('跨入口保留的人工简介')
        before = page.evaluate(snapshot)
        page.on('dialog', dismiss_switch)
        show_panel(page, 'resources')
        page.locator('.file-row[data-path="Media"]').dblclick()
        show_panel(page, 'resources')
        page.locator('.file-row[data-path="Media/episode01.mp4"] .file-check').check()
        raw = page.evaluate('wb.browser.selectedItems()[0].fingerprint')
        assert raw != canonical['fingerprint']
        with page.expect_response('**/api/v2/resources/reference'):
            page.get_by_role('button', name='使用所选资源', exact=True).click()
        assert reference_calls
        assert not dialogs
        assert page.evaluate(snapshot) == before
        assert not any(path == 'resources/resolve' for path, _ in requests)

        # A refreshed listing of a changed file must not be treated as the same
        # resource merely because its path is unchanged.
        (app.state.services.settings.download_root / canonical['relative_path']).write_bytes(b'changed-synthetic-video-content')
        reference = {**canonical, 'fingerprint': 'canonical-after-change'}
        page.evaluate('wb.browser.loadDirectory({clearSelection:true})')
        show_panel(page, 'resources')
        page.locator('.file-row[data-path="Media/episode01.mp4"] .file-check').check()
        assert page.evaluate('wb.browser.selectedItems()[0].fingerprint') != raw
        with page.expect_response('**/api/v2/resources/reference'), page.expect_event('dialog'):
            page.get_by_role('button', name='使用所选资源', exact=True).click()
        assert len(dialogs) == 1
        assert page.evaluate(snapshot) == before
        assert not any(path == 'resources/resolve' for path, _ in requests)
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_season_reselection_detects_changed_child_with_unchanged_directory_fingerprint(live_server):
    base_url, app = live_server
    directory = app.state.services.settings.download_root / 'Media'
    playwright, browser, page, errors = browser_session(base_url)
    requests, dialogs = [], []
    def dismiss_switch(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()
    try:
        v2_fixture(page, requests)
        # Exercise the real reference service and its recursive directory
        # fingerprint; only metadata/text generation uses the contract fixture.
        page.route('**/api/v2/resources/reference', lambda route: route.continue_())
        mount(page)
        show_panel(page, 'resources')
        page.locator('.file-row[data-path="Media"] .file-check').check()
        quick = page.evaluate('wb.browser.selectedItems()[0].fingerprint')
        use_selection = page.get_by_role('button', name='使用所选资源', exact=True)
        use_selection.click()
        expect(page.locator('[data-edit="release_title"]')).to_have_value('Generated title')
        strong = page.evaluate('wb.resources[0].fingerprint')
        assert strong != quick
        show_panel(page, None)
        page.locator('[data-edit="overview"]').fill('整季人工简介需要保留')
        expect(page.locator('[data-wb="output"]')).to_contain_text('整季人工简介需要保留')
        snapshot = '''() => ({resources: wb.resources, context: wb.context,
            epoch: wb.epoch, fields: wb.fields, dirty: [...wb.dirty]})'''
        before = page.evaluate(snapshot)
        directory_mtime = directory.stat().st_mtime_ns
        (directory / 'episode01.mp4').write_bytes(b'changed-existing-child-without-changing-directory-entry')
        assert directory.stat().st_mtime_ns == directory_mtime
        page.evaluate('wb.browser.loadDirectory({clearSelection:true})')
        show_panel(page, 'resources')
        page.locator('.file-row[data-path="Media"] .file-check').check()
        assert page.evaluate('wb.browser.selectedItems()[0].fingerprint') == quick

        page.on('dialog', dismiss_switch)
        with page.expect_response('**/api/v2/resources/reference') as checked, page.expect_event('dialog'):
            use_selection.click()
        assert checked.value.json()['fingerprint'] != strong
        assert len(dialogs) == 1
        assert page.evaluate(snapshot) == before
        assert len([path for path, _ in requests if path == 'resources/resolve']) == 1

        page.remove_listener('dialog', dismiss_switch)
        page.once('dialog', lambda dialog: dialog.accept())
        use_selection.click()
        expect(page.locator('[data-edit="overview"]')).to_have_value('合成简介')
        assert page.evaluate('wb.resources[0].fingerprint') != strong
        assert page.evaluate('wb.resources[0].kind') == 'directory'
        assert len([path for path, _ in requests if path == 'resources/resolve']) == 2
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_late_report_submission_tracks_old_task_without_changing_current_resource(live_server):
    base_url, _ = live_server
    playwright, browser, page, errors = browser_session(base_url)
    requests, tasks, held = [], [], []
    try:
        v2_fixture(page, requests)
        page.route('**/api/v2/tasks?*', lambda route: route.fulfill(json={'items': tasks}))
        page.route('**/api/v2/reports', lambda route: held.append(route))
        mount(page)
        page.evaluate("wb.select([{root_key:'downloads',relative_path:'A.mp4',kind:'file',fingerprint:'A'}])")
        with page.expect_request('**/api/v2/reports'):
            page.evaluate('() => { window.oldReport = wb.report(); }')
        old_epoch = page.evaluate('wb.epoch')
        page.evaluate("wb.select([{root_key:'downloads',relative_path:'B.mp4',kind:'file',fingerprint:'B'}])")
        show_panel(page, None)
        show_panel(page, None)
        page.locator('[data-edit="overview"]').fill('B 的人工简介')
        task = {'kind': 'processing', 'id': 881, 'task_type': 'media_report', 'status': 'queued',
                'progress': None, 'artifact_refs': [], 'cancel_allowed': False, 'retry_allowed': False}
        tasks.append(task)
        held.pop().fulfill(json={'task': task})
        page.evaluate('oldReport')
        assert page.evaluate("wb.jobs.get('processing:881').epoch") == old_epoch
        assert page.evaluate('wb.epoch') != old_epoch
        show_panel(page, 'report')
        expect(page.locator('[data-wb="reports"]')).to_be_empty()
        tasks[0] = {**task, 'status': 'completed', 'finished_at': '2026-11-05T00:00:00Z',
                    'artifact_refs': [{'kind': 'report', 'id': '881', 'url': None,
                                       'resource': {'root_key': 'release_info', 'relative_path': 'old-report.txt', 'kind': 'file', 'fingerprint': 'old'}}]}
        page.wait_for_function("wb.tasks.get('processing:881')?.status === 'completed'")
        expect(page.locator('[data-wb="reports"]')).to_be_empty()
        assert page.evaluate('wb.artifacts') == []
        assert page.evaluate('wb.resources[0].relative_path') == 'B.mp4'
        expect(page.locator('[data-edit="overview"]')).to_have_value('B 的人工简介')
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_workbench_inline_progress_and_logs_preserve_mobile_form(live_server):
    base_url, _ = live_server
    playwright, browser, page, errors = browser_session(base_url)
    requests, submissions, held_logs = [], [], []
    task = {'kind': 'processing', 'id': 81, 'task_type': 'torrent_build', 'status': 'queued',
            'progress': None, 'cancel_allowed': False, 'retry_allowed': False}
    current, stop_calls = [], []
    try:
        page.set_viewport_size({'width': 390, 'height': 720})
        page.emulate_media(reduced_motion='reduce', color_scheme='dark')
        v2_fixture(page, requests)
        page.route('**/api/v2/tasks?*', lambda route: route.fulfill(json={'items': current}))
        page.route('**/api/torrents/jobs', lambda route: submissions.append(route))
        page.route('**/api/tasks/81/logs?*', lambda route: held_logs.append(route))
        def stop(route):
            stop_calls.append(route.request.method)
            route.fulfill(json={'task': current[0]})  # running until worker acknowledges
        page.route('**/api/v2/tasks/processing/81/cancel', stop)
        mount(page)
        page.locator('.file-row[data-path="Media"]').click()
        page.locator('[data-resource-action="open"]').click()
        video = page.locator('.file-row[data-path="Media/episode01.mp4"]')
        video.click()
        page.locator('[data-resource-action="select"]').click()
        expect(page.locator('[data-edit="release_title"]')).to_have_value('Generated title')

        expect(page.locator('[data-wb="trackers"]')).to_have_count(0)
        collapse = page.locator('[data-collapse="overview"]')
        collapse.click()
        overview = page.locator('[data-edit="overview"]')
        overview.fill('手机端简介')
        collapse.click()
        expect(collapse).to_have_attribute('aria-expanded', 'false')
        assert overview.evaluate('el => !!el.closest("[inert]")')
        collapse.click()
        expect(overview).to_have_value('手机端简介')
        assert len([path for path, _ in requests if path == 'resources/resolve']) == 1
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')

        button = page.locator('[data-action="torrent"]')
        button.click()
        expect(button).to_have_text('处理中…')
        expect(button).to_have_attribute('aria-busy', 'true')
        progress = page.locator('[data-wb="tool-progress-torrent"]')
        expect(progress).to_contain_text('正在提交')
        page.evaluate('wb.run("torrent", wb.button("torrent"))')
        assert len(submissions) == 1
        current[:] = [task]
        submissions[0].fulfill(json={'task': task})
        bar = progress.get_by_role('progressbar', include_hidden=True)
        expect(progress).to_contain_text('排队中')
        assert bar.get_attribute('aria-valuenow') is None
        bar.evaluate('el => { window.originalBar = el; }')
        output_toggle = page.locator('[data-collapse="output"]')
        output_toggle.click()
        current[0] = {**task, 'status': 'running', 'progress': 42, 'cancel_allowed': True}
        expect(bar).to_have_attribute('aria-valuenow', '42')
        expect(output_toggle).to_have_attribute('aria-expanded', 'false')
        output_toggle.click()
        assert bar.evaluate('el => el === window.originalBar')
        expect(button).to_be_disabled()

        cancel = progress.get_by_role('button', name='停止 · 制作种子', exact=True)
        cancel.click()
        expect(progress).to_contain_text('正在停止，等待任务结束')
        assert stop_calls == ['POST']
        expect(progress.get_by_role('button', name='正在停止…')).to_be_disabled()
        current[0] = {**task, 'status': 'cancelled'}
        expect(progress).to_contain_text('已取消')
        expect(button).to_have_text('制作种子')
        expect(button).to_be_enabled()
        expect(page.get_by_role('link', name='下载种子')).to_have_count(0)

        page.locator('.wb-tasks > summary').click()
        logs = page.get_by_role('button', name='日志', exact=True)
        logs.scroll_into_view_if_needed()
        before = page.evaluate('scrollY')
        logs.click()
        dialog = page.locator('[data-wb="task-log-dialog"]')
        expect(dialog).to_be_visible()
        page.keyboard.press('Escape')
        expect(dialog).not_to_be_visible()
        assert page.evaluate('scrollY') == before
        expect(logs).to_be_focused()

        logs.click()
        expect(dialog).to_be_visible()
        held_logs[0].fulfill(json={'lines': ['旧响应不可覆盖']})
        held_logs[1].fulfill(json={'lines': [f'合成日志 {i}' for i in range(100)]})
        expect(dialog.locator('pre')).to_contain_text('合成日志 99')
        expect(dialog.locator('pre')).not_to_contain_text('旧响应不可覆盖')
        dialog.locator('.wb-task-log-body').evaluate('el => { el.scrollTop = el.scrollHeight; }')
        assert dialog.locator('.wb-task-log-body').evaluate('el => el.scrollTop > 0')
        expect(dialog.get_by_role('button', name='关闭')).to_be_in_viewport()
        page.mouse.click(2, 2)
        expect(dialog).not_to_be_visible()
        expect(logs).to_be_focused()
        assert page.locator('.wb-task pre').count() == 0
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()
