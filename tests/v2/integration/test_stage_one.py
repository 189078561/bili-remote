from __future__ import annotations

from playwright.sync_api import expect
from tests.v2.workbench.test_browser import show_panel
from test_browser_modules import browser_session, live_server  # noqa: F401
from test_release_tools import FakeImageClient


def ready(page):
    page.set_default_timeout(15000)
    page.wait_for_function('window.V2Workbench?.browser && typeof mediaBrowser !== "undefined" && mediaBrowser?.data && document.querySelector(".v2-naming")')


def select_resource(page, path):
    return page.evaluate('''async path => {
      const item = await api('/api/files/meta?' + new URLSearchParams({root_key:'downloads',path}));
      return V2Workbench.select([{...item, root_key:'downloads'}]);
    }''', path)


def naming_panel(page):
    page.locator('[data-view="publish"]').click()
    show_panel(page, 'naming')
    return page.locator('[data-wb="naming-panel"]')


def test_file_tools_report_upload_and_cancelled_handoff_keep_business_state(live_server):
    base, app = live_server
    (app.state.services.settings.download_root / 'Media' / 'episode02.mp4').write_bytes(b'synthetic-video-2')
    uploader = FakeImageClient()
    app.state.services.image_hosts._client_factory = lambda *_: uploader
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        ready(page)
        show_panel(page)
        page.locator('[data-view="files"]').click()
        files = page.locator('#file-browser-host')
        files.locator('.file-row[data-path="Media"]').click()
        files.locator('.file-row[data-path="Media/episode01.mp4"] .file-check').check()
        files.locator('.file-more-menu > summary').click()
        files.get_by_role('button', name='媒体报告', exact=True).click()
        expect(page.locator('[data-panel="report"]')).to_be_visible()
        assert page.evaluate('V2Workbench.resources[0].relative_path') == 'Media/episode01.mp4'
        page.locator('[data-tab="content"]').click()
        page.locator('[data-edit="overview"]').fill('必须保留的人工简介')
        original = page.evaluate('({epoch:V2Workbench.epoch,context:V2Workbench.context.context_id})')

        show_panel(page)
        page.locator('[data-view="files"]').click()
        files.locator('.file-row[data-path="Media/episode01.mp4"] .file-check').uncheck()
        files.locator('.file-row[data-path="Media/episode02.mp4"] .file-check').check()
        files.locator('.file-more-menu > summary').click()
        with page.expect_event('dialog') as confirmation:
            files.get_by_role('button', name='媒体报告', exact=True).click(no_wait_after=True)
        confirmation.value.dismiss()
        expect(page.locator('#files-view')).to_be_visible()
        assert page.evaluate('({epoch:V2Workbench.epoch,context:V2Workbench.context.context_id})') == original
        expect(page.locator('[data-edit="overview"]')).to_have_value('必须保留的人工简介')

        page.evaluate('mediaBrowser.navigate("images", "")')
        files.locator('.file-row[data-path="pixel.png"] .file-check').check()
        files.locator('.file-more-menu > summary').click()
        with page.expect_request('**/api/uploads/jobs') as upload:
            files.get_by_role('button', name='上传图片', exact=True).click()
        assert upload.value.post_data_json['items'] == [{'root_key': 'images', 'relative_path': 'pixel.png'}]
        expect(page.locator('#uploads-view')).to_be_visible()
        expect(page.locator('#uploads-list').get_by_role('link', name='打开图片')).to_have_attribute('href', 'https://images.example/pixel.png', timeout=30000)
        assert uploader.calls == 1
        assert page.evaluate('({epoch:V2Workbench.epoch,context:V2Workbench.context.context_id})') == original
        expect(page.locator('[data-edit="overview"]')).to_have_value('必须保留的人工简介')
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_download_completed_entry_and_late_recognition_preserve_new_resource(live_server):
    base, app = live_server
    services = app.state.services
    first = services.settings.download_root / 'Media' / 'episode01.mp4'
    (first.parent / 'episode02.mp4').write_bytes(b'synthetic-video-2')
    job_id = services.database.create_job(kind='manual', source_url='https://www.bilibili.com/video/BVsynthetic',
        title='阶段一合成下载', format_selector='best', output_template='%(title)s.%(ext)s', all_parts=False, output_dir='')
    services.database.update_job(job_id, status='completed', result_path=str(first))
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        ready(page)
        page.locator('[data-view="download"]').click()
        job = page.locator(f'.dv2-job[data-job-id="{job_id}"]')
        expect(job.get_by_role('button', name='进入工作台', exact=True)).to_be_visible()
        expect(job.get_by_role('button', name='文件管理', exact=True)).to_have_count(0)
        page.evaluate('''() => {
          const original = window.fetch.bind(window);
          window.fetch = async (url, options) => {
            const response = await original(url, options);
            if (String(url).endsWith('/recognize') && !window.downloadHeld) {
              window.downloadHeld = true;
              return new Promise(resolve => {window.releaseDownload = () => resolve(response);});
            }
            return response;
          };
        }''')
        job.get_by_role('button', name='进入工作台', exact=True).click()
        page.wait_for_function('typeof releaseDownload === "function"')
        assert select_resource(page, 'Media/episode02.mp4')
        page.evaluate('releaseDownload()')
        # Drain the delayed response and the UI callback before checking no replacement occurred.
        page.wait_for_timeout(150)
        assert page.evaluate('V2Workbench.resources[0].relative_path') == 'Media/episode02.mp4'
        expect(page.locator('#download-view')).to_be_visible()

        job.get_by_role('button', name='进入工作台', exact=True).click()
        expect(page.locator('#publish-view')).to_be_visible()
        assert page.evaluate('V2Workbench.resources[0].relative_path') == 'Media/episode01.mp4'
        assert services.database.get_job(job_id)['status'] == 'completed'
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_advanced_recognition_cannot_restore_previous_resource(live_server):
    base, app = live_server
    (app.state.services.settings.download_root / 'Media' / 'episode02.mp4').write_bytes(b'synthetic-video-2')
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        ready(page)
        assert select_resource(page, 'Media/episode01.mp4')
        panel = naming_panel(page)
        page.evaluate('''() => {
          const original = window.fetch.bind(window);
          window.fetch = async (url, options) => {
            const response = await original(url, options);
            if (String(url) === '/api/v2/resources/resolve' && !window.namingHeld) {
              window.namingHeld = true;
              return new Promise(resolve => {window.releaseNaming = () => resolve(response);});
            }
            return response;
          };
        }''')
        panel.get_by_role('button', name='探测与识别', exact=True).click()
        page.wait_for_function('typeof releaseNaming === "function"')
        assert select_resource(page, 'Media/episode02.mp4')
        show_panel(page)
        page.locator('[data-edit="release_title"]').fill('新资源的人工标题')
        page.evaluate('releaseNaming()')
        page.wait_for_timeout(150)
        expect(panel.locator('.v2-naming__files legend')).to_have_text('Media/episode02.mp4')
        expect(page.locator('[data-edit="release_title"]')).to_have_value('新资源的人工标题')
        assert page.evaluate('V2Workbench.context.resources[0].relative_path') == 'Media/episode02.mp4'
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_same_resource_refresh_retains_unsubmitted_advanced_file_values(live_server):
    base, app = live_server
    (app.state.services.settings.download_root / 'Media' / 'episode02.mp4').write_bytes(b'synthetic-video-2')
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        ready(page)
        assert select_resource(page, 'Media/episode01.mp4')
        panel = naming_panel(page)
        resolution = panel.locator('[data-file="Media/episode01.mp4"] [data-field="resolution"]')
        resolution.fill('2160p')
        advanced_title = panel.get_by_role('textbox', name='自定义名称', exact=True)
        advanced_title.fill('Unsubmitted advanced name')
        panel.get_by_role('combobox', name='资源类型', exact=True).select_option('anime')
        page.evaluate('V2Workbench.resolve()')
        expect(resolution).to_have_value('2160p')
        expect(resolution).to_have_attribute('data-dirty', 'true')
        expect(advanced_title).to_have_value('Unsubmitted advanced name')
        expect(panel.get_by_role('combobox', name='资源类型', exact=True)).to_have_value('anime')
        with page.expect_request('**/api/v2/naming/preview') as preview:
            panel.get_by_role('button', name='生成命名预览', exact=True).click()
        assert preview.value.post_data_json['overrides']['file_values']['Media/episode01.mp4']['resolution'] == '2160p'
        assert select_resource(page, 'Media/episode02.mp4')
        other = panel.locator('[data-file="Media/episode02.mp4"] [data-field="resolution"]')
        expect(other).not_to_have_value('2160p')
        assert other.get_attribute('data-dirty') is None
        expect(advanced_title).not_to_have_value('Unsubmitted advanced name')
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_external_handoff_cannot_replace_resource_that_is_still_resolving(live_server):
    base, app = live_server
    (app.state.services.settings.download_root / 'Media' / 'episode02.mp4').write_bytes(b'synthetic-video-2')
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        ready(page)
        page.evaluate('''async () => {
          const first = await api('/api/files/meta?root_key=downloads&path=Media/episode01.mp4');
          const second = await api('/api/files/meta?root_key=downloads&path=Media/episode02.mp4');
          window.releaseContexts = {};
          const original = window.fetch.bind(window);
          window.fetch = async (url, options) => {
            const response = await original(url, options);
            if (String(url) === '/api/v2/resources/resolve') {
              const path = JSON.parse(options.body).resources[0].relative_path;
              return new Promise(resolve => {releaseContexts[path] = () => resolve(response);});
            }
            return response;
          };
          V2Workbench.api.transport = window.fetch.bind(window);
          window.handoffDone = false;
          window.handoffPending = mediaBrowser.onTool('report', [first], 'downloads')
            .catch(error => {window.handoffError = error.message;})
            .finally(() => {window.handoffDone = true;});
          window.nextResource = {...second,root_key:'downloads'};
        }''')
        page.wait_for_function('typeof releaseContexts["Media/episode01.mp4"] === "function"')
        page.evaluate('void (window.nextSelection = V2Workbench.select([nextResource]))')
        page.wait_for_function('typeof releaseContexts["Media/episode02.mp4"] === "function"')
        assert page.evaluate('V2Workbench.context') is None
        page.evaluate('releaseContexts["Media/episode01.mp4"]()')
        page.wait_for_function('handoffDone')
        assert '资源选择已更新' in page.evaluate('handoffError')
        assert page.evaluate('V2Workbench.context') is None
        assert page.evaluate('V2Workbench.resources[0].relative_path') == 'Media/episode02.mp4'
        page.evaluate('releaseContexts["Media/episode02.mp4"]()')
        assert page.evaluate('nextSelection') is True
        assert page.evaluate('V2Workbench.context.resources[0].relative_path') == 'Media/episode02.mp4'
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_advanced_rename_keeps_undo_after_completed_task(live_server):
    base, app = live_server
    original = app.state.services.settings.download_root / 'Media' / 'episode01.mp4'
    content = original.read_bytes()
    target = original.with_name('Stage.One.mp4')
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        ready(page)
        assert select_resource(page, 'Media/episode01.mp4')
        panel = naming_panel(page)
        panel.get_by_label('自定义名称', exact=True).fill('Stage One')
        panel.get_by_label('自定义模板（留空采用内置模板）', exact=True).fill('{Title}.{Extension}')
        panel.get_by_role('button', name='生成命名预览', exact=True).click()
        expect(panel.locator('.v2-naming__preview')).to_contain_text('Stage.One.mp4')
        expect(panel.get_by_role('button', name='确认执行本次预览', exact=True)).to_be_enabled()
        assert original.exists() and not target.exists()
        page.evaluate('''() => {
          const original = window.fetch.bind(window);
          window.fetch = async (url, options) => {
            const response = await original(url, options);
            if (String(url) === '/api/v2/naming/apply') {
              return new Promise(resolve => {window.releaseApply = () => resolve(response);});
            }
            return response;
          };
        }''')
        panel.get_by_role('button', name='确认执行本次预览', exact=True).click()
        page.wait_for_function('typeof releaseApply === "function"')
        # Editing another rule invalidates the preview, but the accepted batch must remain undoable.
        panel.get_by_label('制作组', exact=True).fill('LOCAL')
        page.evaluate('releaseApply()')
        page.wait_for_function('V2Workbench.context?.resources[0].relative_path === "Media/Stage.One.mp4"', timeout=30000)
        assert target.read_bytes() == content and not original.exists()
        panel.get_by_role('button', name='预览撤销', exact=True).click()
        expect(panel.locator('.v2-naming__preview')).to_contain_text('→ Media/episode01.mp4')
        panel.get_by_role('button', name='确认执行本次预览', exact=True).click()
        page.wait_for_function('V2Workbench.context?.resources[0].relative_path === "Media/episode01.mp4"', timeout=30000)
        assert original.read_bytes() == content and not target.exists()
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_advanced_apply_response_keeps_original_task_resource_scope(live_server):
    base, app = live_server
    root = app.state.services.settings.download_root
    (root / 'Media' / 'episode02.mp4').write_bytes(b'synthetic-video-2')
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        ready(page)
        assert select_resource(page, 'Media/episode01.mp4')
        original_epoch = page.evaluate('V2Workbench.epoch')
        panel = naming_panel(page)
        panel.get_by_label('自定义名称', exact=True).fill('Queued Source')
        panel.get_by_label('自定义模板（留空采用内置模板）', exact=True).fill('{Title}.{Extension}')
        panel.get_by_role('button', name='生成命名预览', exact=True).click()
        expect(panel.get_by_role('button', name='确认执行本次预览', exact=True)).to_be_enabled()
        page.evaluate('''() => {
          const original = window.fetch.bind(window);
          window.fetch = async (url, options) => {
            const response = await original(url, options);
            if (String(url) === '/api/v2/naming/apply') {
              return new Promise(resolve => {window.releaseApply = () => resolve(response);});
            }
            return response;
          };
        }''')
        panel.get_by_role('button', name='确认执行本次预览', exact=True).click()
        page.wait_for_function('typeof releaseApply === "function"')
        assert select_resource(page, 'Media/episode02.mp4')
        show_panel(page)
        page.locator('[data-edit="overview"]').fill('新资源保留的简介')
        page.evaluate('releaseApply()')
        page.wait_for_function('[...V2Workbench.jobs.values()].some(job => job.type === "rename")')
        plan = page.evaluate('[...V2Workbench.jobs.values()].find(job => job.type === "rename")')
        assert plan['epoch'] == original_epoch
        assert plan['resources'][0]['relative_path'] == 'Media/episode01.mp4'
        assert plan['operations'][0]['target_relative_path'] == 'Media/Queued.Source.mp4'
        page.wait_for_function('[...V2Workbench.tasks.values()].some(task => task.task_type === "v2_rename_apply" && task.status === "completed")', timeout=30000)
        assert (root / 'Media' / 'Queued.Source.mp4').exists()
        assert page.evaluate('V2Workbench.resources[0].relative_path') == 'Media/episode02.mp4'
        expect(page.locator('[data-edit="overview"]')).to_have_value('新资源保留的简介')
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()
