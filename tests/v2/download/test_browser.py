from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_browser_modules import browser_session, live_server  # noqa: F401


ROOT = Path(__file__).resolve().parents[3]


def test_download_workbench_browser_interactions_and_responsive_widths():
    playwright_api = pytest.importorskip("playwright.sync_api")
    playwright = playwright_api.sync_playwright().start()
    try:
        try:
            browser = playwright.chromium.launch()
        except playwright_api.Error as exc:
            pytest.skip(str(exc))
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        fragment = (ROOT / "bili_remote/templates/v2/download/workbench.html").read_text()
        css = (ROOT / "bili_remote/static/v2/download/workbench.css").read_text()
        js = ROOT / "bili_remote/static/v2/download/workbench.js"
        page.set_content(f'<meta name="csrf-token" content="test"><main id="mount">{fragment}</main>')
        page.add_style_tag(content=css)
        page.add_script_tag(path=str(js))
        page.evaluate("""() => {
          window.calls = [];
          window.jobs = [{id: 9, title: '合成剧集', api_mode: 'web', status: 'queued', progress: 0, speed: null, eta: null}];
          window.loginState = {web:true, tv:false};
          window.BiliDownloadV2.mount(document.querySelector('#mount'), {
            pollInterval: 60000,
            outputTemplate: '{title}.{id}.mp4',
            onChooseDirectory: async () => 'Series/Season 1',
            onLogin: mode => { window.loginModes = [...(window.loginModes || []), mode]; },
            api: async (url, options={}) => {
              window.calls.push({url, options});
              if (url === '/api/bili/status') return {logged_in: window.loginState.web, tv_logged_in: window.loginState.tv};
              if (url === '/api/bili/logout') { window.loginState.web = false; return {ok:true}; }
              if (url === '/api/bili/tv/logout') { window.loginState.tv = false; return {ok:true}; }
              if (url === '/api/jobs?limit=500') return window.jobs;
              if (url === '/api/history?limit=200') return [];
              if (url === '/api/resolve') return {
                title: '合成剧集', uploader: '测试账号', api_mode: 'web', webpage_url: 'https://www.bilibili.com/bangumi/play/ss42',
                episodes: [
                  {id: 'ep-a', title: '第一集', group: '正片', group_index: 1},
                  {id: 'ep-b', title: '第二集', group: '正片', group_index: 2},
                  {id: 'ep-c', title: '花絮', group: '预告', group_index: 3}
                ], default_episode_id: 'ep-a',
                formats: [{selector:'137+30280',format_id:'137',height:1080,width:1920,fps:60,vcodec:'avc1',acodec:'none',tbr:5000,dynamic_range:'SDR'}]
              };
              if (url === '/api/episodes/formats') return {formats: [{selector:'137+30280',format_id:'137',height:1080,width:1920,fps:60,vcodec:'avc1',acodec:'none',tbr:5000,dynamic_range:'SDR'}]};
              if (url === '/api/episodes/formats/batch') return {selected_count:2,checked:2,sampled:false,failed:0,profiles:[{height:1080,fps_high:true,vcodec:'avc',dynamic_range:'SDR',coverage:2,sample:{width:1920,height:1080,fps:60,vcodec:'avc1'}}]};
              if (url === '/api/jobs/batch') return {count: 2, ids: [10, 11]};
              if (url.endsWith('/cancel')) { window.jobs[0].status = 'cancelled'; return {ok:true}; }
              if (url.endsWith('/retry')) { window.jobs[0].status = 'queued'; return {ok:true}; }
              if (url.includes('/log?')) return {content:'synthetic log line'};
              throw new Error('unexpected test request: ' + url);
            }
          });
        }""")
        page.locator("[data-account-status]", has_text="Web 已登录").wait_for()
        page.locator(".dv2-account > summary").click()
        assert page.get_by_role("button", name="断开 Web").is_visible()
        assert page.get_by_role("button", name="断开 TV").is_hidden()
        page.get_by_role("button", name="断开 Web").click()
        page.locator("[data-account-status]", has_text="Web 未登录").wait_for()
        page.get_by_role("button", name="Web 登录").click()
        page.get_by_role("button", name="TV 登录").click()
        assert page.evaluate("window.loginModes") == ["web", "tv"]
        page.locator(".dv2-account > summary").click()
        page.locator("#dv2-url").fill("https://www.bilibili.com/bangumi/play/ss42")
        page.get_by_role("button", name="解析链接").click()
        page.locator("[data-title]", has_text="合成剧集").wait_for()
        assert page.locator(".dv2-episode").count() == 3
        if page.locator(".dv2-select-actions details").get_attribute("open") is None:
            page.locator(".dv2-select-actions summary").click()
        page.get_by_role("button", name="全选正片").click()
        assert page.locator("[data-selected-count]").inner_text() == "已选 2 集"
        page.get_by_role("button", name="反选").click()
        assert page.locator("[data-selected-count]").inner_text() == "已选 1 集"
        if page.locator(".dv2-select-actions details").get_attribute("open") is None:
            page.locator(".dv2-select-actions summary").click()
        page.get_by_role("button", name="全选正片").click()
        page.get_by_role("button", name="检测所选画质").click()
        page.wait_for_function("window.calls.some(call => call.url === '/api/episodes/formats/batch')")
        page.locator("[data-format]").select_option("profile:0")
        resolve_calls_before = page.evaluate("window.calls.filter(call => call.url === '/api/resolve').length")
        assert page.locator(".dv2-episodes").is_visible()
        page.get_by_role("button", name="收起", exact=True).click()
        assert page.locator(".dv2-episodes").is_hidden()
        assert page.locator("[data-selected-count]").inner_text() == "已选 2 集"
        assert page.locator("[data-format]").input_value() == "profile:0"
        page.get_by_role("button", name="展开", exact=True).click()
        assert page.locator(".dv2-episodes").is_visible()
        assert page.evaluate("document.querySelectorAll('.dv2-episode input:checked').length") == 2
        assert page.evaluate("window.calls.filter(call => call.url === '/api/resolve').length") == resolve_calls_before
        page.locator(".dv2-advanced > summary").click()
        page.get_by_role("button", name="浏览").click()
        assert page.locator("[data-output]").input_value() == "Series/Season 1"
        page.locator("[data-download-form] button[type=submit]").click()
        page.wait_for_function("window.calls.some(call => call.url === '/api/jobs/batch')")
        payload = page.evaluate("JSON.parse(window.calls.find(call => call.url === '/api/jobs/batch').options.body)")
        assert payload["episode_ids"] == ["ep-a", "ep-b"]
        assert payload["format_profile"] == {"height": 1080, "fps_high": True, "vcodec": "avc", "dynamic_range": "SDR"}
        assert payload["api_mode"] == "web"
        assert page.locator(".dv2-episodes").is_hidden()
        assert page.locator("[data-selected-count]").inner_text() == "已选 2 集"
        assert page.locator("[data-format]").input_value() == "profile:0"
        assert page.locator(".dv2-job").count() == 1
        page.get_by_role("button", name="取消").click()
        page.locator(".dv2-badge", has_text="已取消").wait_for()
        page.get_by_role("button", name="重试").click()
        page.locator(".dv2-badge", has_text="排队中").wait_for()
        page.get_by_role("button", name="查看日志").click()
        assert page.locator("[data-log-content]").inner_text() == "synthetic log line"
        for width in (390, 768, 1440):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        assert not page_errors, page_errors
        browser.close()
    finally:
        playwright.stop()


def test_changed_link_cannot_restore_old_parse_result(live_server):
    """Offline upstream responses; verifies client race protection, not B站 access."""
    base,_=live_server
    playwright,browser,page,errors=browser_session(base,legacy=False)
    pending=[]
    try:
        page.wait_for_function('window.AppResources')
        page.locator('[data-view=download]').click()
        def respond(route):
            data=route.request.post_data_json
            if data['url'].endswith('old'):
                pending.append(route)
            else:
                route.fulfill(json={'title':'新的合成解析','formats':[],'episodes':[],'api_mode':'web'})
        page.route('**/api/resolve',respond)
        page.locator('#dv2-url').fill('https://www.bilibili.com/video/old')
        page.locator('[data-parse]').click()
        page.wait_for_timeout(100)
        assert pending
        page.locator('#dv2-url').fill('https://www.bilibili.com/video/new')
        page.locator('[data-parse]').click()
        expect(page.locator('[data-title]')).to_have_text('新的合成解析')
        pending.pop().fulfill(json={'title':'旧的迟到解析','formats':[],'episodes':[],'api_mode':'web'})
        page.wait_for_timeout(100)
        expect(page.locator('[data-title]')).to_have_text('新的合成解析')
        assert not errors,errors
    finally:
        browser.close();playwright.stop()


def test_download_workbench_stability_and_log_controls():
    playwright_api = pytest.importorskip("playwright.sync_api")
    playwright = playwright_api.sync_playwright().start()
    try:
        try:
            browser = playwright.chromium.launch()
        except playwright_api.Error as exc:
            pytest.skip(str(exc))
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        fragment = (ROOT / "bili_remote/templates/v2/download/workbench.html").read_text()
        css = (ROOT / "bili_remote/static/v2/download/workbench.css").read_text()
        js = ROOT / "bili_remote/static/v2/download/workbench.js"
        page.set_content(
            '<meta name="csrf-token" content="test">'
            '<style>body{min-height:2600px}</style>'
            f'<main id="mount">{fragment}</main>'
        )
        page.add_style_tag(content=css)
        page.add_script_tag(path=str(js))
        page.evaluate(
            """() => {
              window.calls = [];
              window.activeJobCalls = 0;
              window.maxActiveJobCalls = 0;
              window.jobs = [
                {
                  id: 1, title: '合成合并任务', api_mode: 'web', status: 'running',
                  progress: 100, speed: 1048576, eta: 30
                },
                {
                  id: 2, title: '合成完成任务', api_mode: 'web', status: 'completed',
                  progress: 100, speed: 2097152, eta: 2,
                  started_at: '2026-11-15T01:00:00+00:00',
                  finished_at: '2026-11-15T01:01:05+00:00',
                  download_duration_seconds: 65,
                  final_size_bytes: 123456789
                }
              ];
              window.logContent = Array.from(
                {length: 240}, (_, index) => `synthetic log line ${index + 1}`
              ).join('\\n');
              window.wb = window.BiliDownloadV2.mount(document.querySelector('#mount'), {
                pollInterval: 60000,
                api: async (url, options = {}) => {
                  window.calls.push({url, options});
                  if (url === '/api/bili/status') {
                    return {logged_in: true, tv_logged_in: false};
                  }
                  if (url === '/api/history?limit=200') return [];
                  if (url.includes('/log?')) return {content: window.logContent};
                  if (url !== '/api/jobs?limit=500') {
                    throw new Error(`unexpected test request: ${url}`);
                  }
                  window.activeJobCalls += 1;
                  window.maxActiveJobCalls = Math.max(
                    window.maxActiveJobCalls, window.activeJobCalls
                  );
                  try {
                    await new Promise(resolve => setTimeout(resolve, 60));
                    return window.jobs;
                  } finally {
                    window.activeJobCalls -= 1;
                  }
                }
              });
            }"""
        )
        page.locator('.dv2-job[data-job-id="2"]').wait_for()
        merging = page.locator('.dv2-job[data-job-id="1"]')
        completed = page.locator('.dv2-job[data-job-id="2"]')
        expect(merging.locator(".dv2-badge")).to_have_text("Web · 合并中")
        assert "正在合并音视频" in merging.locator(".dv2-job-meta").inner_text()
        assert "剩余" not in merging.locator(".dv2-job-meta").inner_text()
        assert "/s" not in merging.locator(".dv2-job-meta").inner_text()
        expect(merging.locator(".dv2-progress")).to_be_visible()

        expect(completed.locator(".dv2-badge")).to_have_text("Web · 已完成")
        completed_meta = completed.locator(".dv2-job-meta").inner_text()
        assert "完成于" in completed_meta
        assert "下载总用时 1 分 5 秒" in completed_meta
        assert "最终大小 117.7 MiB" in completed_meta
        assert "剩余" not in completed_meta
        assert "/s" not in completed_meta
        expect(completed.locator(".dv2-progress")).to_be_hidden()

        page.evaluate(
            """() => {
              window.stableCard = document.querySelector('.dv2-job[data-job-id="1"]');
              window.jobs[0].progress = 50;
            }"""
        )
        page.evaluate("window.wb.refreshJobs({force: true})")
        assert page.evaluate(
            """() => window.stableCard === document.querySelector(
              '.dv2-job[data-job-id="1"]'
            ) && window.stableCard.dataset.status === 'downloading'"""
        )
        assert "50%" in merging.locator(".dv2-job-meta").inner_text()

        page.wait_for_function("window.activeJobCalls === 0")
        page.evaluate("window.maxActiveJobCalls = 0; window.activeJobCalls = 0")
        concurrency = page.evaluate(
            """async () => {
              await Promise.all([
                window.wb.refreshJobs({force: true}),
                window.wb.refreshJobs({force: true}),
                window.wb.refreshJobs({force: true})
              ]);
              await new Promise(resolve => setTimeout(resolve, 180));
              return window.maxActiveJobCalls;
            }"""
        )
        assert concurrency == 1

        page.evaluate("window.scrollTo(0, 700)")
        assert page.evaluate("window.scrollY") == 700

        def open_log():
            completed.get_by_role("button", name="查看日志").click()
            expect(page.locator("[data-log-dialog]")).to_be_visible()
            page.wait_for_function(
                "document.activeElement === document.querySelector('[data-log-close]')"
            )

        open_log()
        dialog = page.locator("[data-log-dialog]")
        expect(page.locator("[data-log-close]")).to_be_visible()
        assert page.evaluate(
            "document.querySelector('[data-log-content]').scrollHeight > "
            "document.querySelector('[data-log-content]').clientHeight"
        )
        page.evaluate(
            "document.querySelector('[data-log-content]').scrollTop = 100000"
        )
        assert page.evaluate("document.querySelector('[data-log-content]').scrollTop > 0")
        page.get_by_role("button", name="关闭日志").click()
        expect(dialog).to_be_hidden()
        page.wait_for_function("Math.abs(window.scrollY - 700) <= 2")

        open_log()
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        page.wait_for_function("Math.abs(window.scrollY - 700) <= 2")

        open_log()
        dialog_box = dialog.bounding_box()
        assert dialog_box
        page.mouse.click(max(2, dialog_box["x"] - 10), max(2, dialog_box["y"] - 10))
        expect(dialog).to_be_hidden()
        page.wait_for_function("Math.abs(window.scrollY - 700) <= 2")

        page.evaluate("window.wb.destroy()")
        assert page.locator(".dv2-job").count() == 0
        assert page.evaluate(
            "document.querySelector('#mount').dataset.mounted === undefined"
        )
        page.evaluate("window.calls.length = 0")
        page.evaluate(
            "document.dispatchEvent(new CustomEvent('app:view', {detail: {view: 'download'}}))"
        )
        page.wait_for_timeout(100)
        assert page.evaluate("window.calls.length") == 0
        assert not page_errors, page_errors
        browser.close()
    finally:
        playwright.stop()
