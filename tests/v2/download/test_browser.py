from __future__ import annotations

import json
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_browser_modules import browser_session, live_server  # noqa: F401


ROOT = Path(__file__).resolve().parents[3]


def test_download_and_parse_history_delete_and_grouping(live_server, settings):
    base, app = live_server
    db = app.state.services.database
    bangumi = "https://www.bilibili.com/bangumi/play/"
    media = settings.download_root / "Media" / "episode01.mp4"
    db.execute(
        """
        INSERT INTO jobs (kind, source_url, title, status, output_template, result_path, created_at)
        VALUES ('manual', ?, '已下载视频', 'completed', '%(ext)s', ?, '2026-11-18T17:00:00+00:00')
        """,
        (bangumi + "ep41", str(media)),
    )
    job_id = db.list_jobs()[0]["id"]

    def save(source, mode, episodes, minute):
        db.save_history(
            source_url=bangumi + source, api_mode=mode, title="同名番剧", uploader="",
            duration=None, is_playlist=True, playlist_count=len(episodes),
            default_episode_id=source[2:].partition("?")[0] if source.startswith("ep") else None,
            formats=[{"selector": f"cached-{minute}", "height": 1080}],
            episodes=[{"id": str(number), "group": "正片", "title": f"第 {number} 集"} for number in episodes],
        )
        db.execute(
            "UPDATE parse_history SET updated_at=? WHERE source_url=? AND api_mode=?",
            (f"2026-11-18T17:{minute:02d}:00+00:00", bangumi + source, mode),
        )

    save("ss11", "web", [40, 41], 0)
    save("ep40?from=old", "web", [40, 41], 1)
    save("ep40?from=share", "web", [40, 41], 2)
    save("ep42", "web", [40, 41, 42], 3)
    save("ep41", "tv", [40, 41], 4)
    save("ss12", "web", [50, 51], 5)

    playwright, browser, page, errors = browser_session(base)
    try:
        page.locator("[data-view=download]").click()
        page.get_by_text("下载历史", exact=True).click()
        download = page.locator("[data-download-history]")
        expect(download).to_contain_text("已下载视频")
        page.once("dialog", lambda dialog: dialog.dismiss())
        download.get_by_role("button", name="删除记录").click()
        expect(download).to_contain_text("已下载视频")
        page.once("dialog", lambda dialog: dialog.accept())
        download.get_by_role("button", name="删除记录").click()
        expect(download).to_contain_text("暂无下载历史")
        assert db.get_job(job_id) is None
        assert media.read_bytes() == b"test-video"

        page.get_by_text("解析记录", exact=True).click()
        rows = page.locator("[data-history] .dv2-history-item")
        expect(rows).to_have_count(3)
        latest_web = rows.filter(has_text="Web · 2026/11/19 01:03:00")
        expect(latest_web).to_be_visible()
        page.route("**/api/episodes/formats/batch", lambda route: route.fulfill(json={"episode_formats": {}}))
        latest_web.get_by_role("button", name="复用解析").click()
        expect(page.locator("#dv2-url")).to_have_value(bangumi + "ep42")
        expect(page.locator("[data-mode]")).to_have_value("web")
        expect(page.locator("[data-format] option").last).to_have_attribute("value", "cached-3")
        page.once("dialog", lambda dialog: dialog.accept())
        latest_web.get_by_role("button", name="删除记录").click()
        expect(rows).to_have_count(2)
        assert [(r["source_url"], r["api_mode"]) for r in db.list_history()] == [
            (bangumi + "ss12", "web"), (bangumi + "ep41", "tv"),
        ]
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


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
          const formats = [
            {selector:'137+30280',format_id:'137',height:2160,fps:25,vcodec:'avc1',acodec:'none',tbr:15844,filesize:2289565696},
            {selector:'136+30280',format_id:'136',height:720,vcodec:'avc1',acodec:'none',tbr:1000,filesize:1000000000},
            {selector:'30280',format_id:'30280',acodec:'mp4a',tbr:191,filesize:27577549},
            {selector:'30232',format_id:'30232',acodec:'mp4a'}
          ];
          const detailed = [
            {...formats[0], dynamic_range:'SDR', locked_selector:'(137+30280)',size_with_audio:2317143245},
            {...formats[0], format_id:'138', selector:'138+30280', dynamic_range:'SDR', tbr:9200, locked_selector:'(138+30280)',size_with_audio:1400000000},
            {...formats[0], format_id:'139', selector:'139+30280', vcodec:'hev1', dynamic_range:'HDR10', tbr:9800, locked_selector:'(139+30280)',size_with_audio:1500000000},
            {...formats[1], fps:25, dynamic_range:'SDR', locked_selector:'(136+30280)',size_with_audio:1027577549},
            {...formats[2], locked_selector:'(30280)',size_with_audio:27577549},
            {...formats[3], locked_selector:'(30232)'}
          ];
          const episodeB = [{...detailed[1],size_with_audio:827577549}, detailed[3]];
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
              if (url === '/api/resolve' && JSON.parse(options.body).url.includes('/video/')) {
                return {title:'独立视频',api_mode:'web',episodes:[],formats};
              }
              if (url === '/api/resolve') return {
                title: '合成剧集', uploader: '测试账号', api_mode: 'web', webpage_url: 'https://www.bilibili.com/bangumi/play/ss42',
                episodes: [
                  {id: 'ep-a', title: '第一集', group: '正片', group_index: 1},
                  {id: 'ep-b', title: '第二集', group: '正片', group_index: 2},
                  {id: 'ep-c', title: '花絮', group: '预告', group_index: 3}
                ], default_episode_id: 'ep-a',
                formats
              };
              if (url === '/api/episodes/formats') {
                return {formats:JSON.parse(options.body).url.endsWith('epep-b') ? episodeB : detailed};
              }
              if (url === '/api/episodes/formats/batch') return {episode_formats:{
                'ep-a': detailed,
                'ep-b': episodeB
              }};
              if (url === '/api/jobs') return {id:12};
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
        assert page.locator(".dv2-episode").count() == 2
        expect(page.locator("[data-episode-categories] button")).to_have_text(["正片", "预告"])
        page.locator('[data-episode-id="ep-a"]').focus()
        page.locator(".dv2-episode-quality").focus()
        single_labels = [
            "2160P · 25fps · H.264 · 高码 · SDR · 15.84 Mbps · 2.32 GB",
            "2160P · 25fps · H.265 · HDR10 · 9.80 Mbps · 1.50 GB",
            "2160P · 25fps · H.264 · 标准 · SDR · 9.20 Mbps · 1.40 GB",
            "720P · 25fps · H.264 · SDR · 1.00 Mbps · 1.03 GB",
            "仅音频 · AAC · 高码 · 191 kbps · 27.6 MB",
            "仅音频 · AAC · 标准 · 码率未知 · 体积待确认",
        ]
        quality = page.locator("[data-batch-format]")
        expect(quality.locator("option")).to_have_text(["最高可用 · 最佳音轨 · 2.32 GB", *single_labels])
        expect(page.locator(".dv2-episode-quality option")).to_have_text(["跟随统一画质", *single_labels])
        quality.select_option(label=single_labels[0])
        confirmed_single = quality.input_value()
        expect(page.locator("[data-batch-size]")).to_have_text("2.32 GB")
        page.get_by_role("button", name="全选", exact=True).click()
        assert page.locator("[data-selected-count]").inner_text() == "已选 2 集"
        expect(quality).to_have_value(confirmed_single)
        page.locator(".dv2-select-actions summary").click()
        page.get_by_role("button", name="反选全部").click()
        assert page.locator("[data-selected-count]").inner_text() == "已选 1 集"
        page.get_by_role("button", name="清空全部").click()
        page.get_by_role("button", name="全选", exact=True).click()
        page.locator(".dv2-select-actions summary").click()
        page.get_by_role("button", name="检测所选画质").click()
        page.wait_for_function("window.calls.some(call => call.url === '/api/episodes/formats/batch')")
        quality = page.locator("[data-batch-format]")
        expect(quality.locator("option").nth(1)).to_contain_text("15.84 Mbps")
        quality.select_option(label=quality.locator("option").nth(1).inner_text())
        confirmed_tier = quality.input_value()
        expect(page.locator("[data-download-form] button[type=submit]")).to_be_disabled()
        page.locator("[data-batch-fallback]").check()
        expect(page.locator("[data-batch-size]")).to_have_text("3.14 GB")
        expect(page.locator("[data-batch-list]")).to_contain_text("缺档降一档")
        expect(page.locator("[data-batch-list]")).to_contain_text("9.20 Mbps")
        resolve_calls_before = page.evaluate("window.calls.filter(call => call.url === '/api/resolve').length")
        assert page.locator(".dv2-episodes").is_visible()
        page.locator("[data-episode-toggle]").click()
        assert page.locator(".dv2-episodes").is_hidden()
        assert page.locator("[data-selected-count]").inner_text() == "已选 2 集"
        assert quality.input_value() == confirmed_tier
        page.locator("[data-episode-toggle]").click()
        assert page.locator(".dv2-episodes").is_visible()
        assert page.evaluate("document.querySelectorAll('.dv2-episode[aria-pressed=true]').length") == 2
        assert page.evaluate("window.calls.filter(call => call.url === '/api/resolve').length") == resolve_calls_before
        page.locator(".dv2-advanced > summary").click()
        page.get_by_role("button", name="浏览").click()
        assert page.locator("[data-output]").input_value() == "Series/Season 1"
        page.locator("[data-download-form] button[type=submit]").click()
        page.wait_for_function("window.calls.some(call => call.url === '/api/jobs/batch')")
        payload = page.evaluate("JSON.parse(window.calls.find(call => call.url === '/api/jobs/batch').options.body)")
        assert payload["episode_ids"] == ["ep-a", "ep-b"]
        assert payload["episode_selectors"] == {"ep-a": "(137+30280)", "ep-b": "(138+30280)"}
        assert payload["api_mode"] == "web"
        assert page.locator(".dv2-episodes").is_hidden()
        assert page.locator("[data-selected-count]").inner_text() == "已选 2 集"
        assert quality.input_value() == confirmed_tier
        assert page.locator(".dv2-job").count() == 1
        page.get_by_role("button", name="取消").click()
        page.locator("[data-download-history]").locator("..").locator("summary").click()
        page.locator(".dv2-badge", has_text="已取消").wait_for()
        page.get_by_role("button", name="重试").click()
        page.locator(".dv2-badge", has_text="排队中").wait_for()
        page.get_by_role("button", name="查看日志").click()
        assert page.locator("[data-log-content]").inner_text() == "synthetic log line"
        page.locator("[data-log-close]").click()
        page.locator("#dv2-url").fill("https://www.bilibili.com/video/BV1TEST")
        page.get_by_role("button", name="解析链接").click()
        expect(quality.locator("option")).to_have_text(["最高可用 · 最佳音轨 · 2.32 GB", *single_labels])
        quality.select_option(label=single_labels[1])
        expect(page.locator("[data-batch-size]")).to_have_text("1.50 GB")
        page.locator("[data-download-form] button[type=submit]").click()
        request = page.evaluate("window.calls.find(call => call.url === '/api/episodes/formats')")
        assert json.loads(request["options"]["body"])["detailed"] is True
        request = page.evaluate("window.calls.find(call => call.url === '/api/jobs')")
        assert json.loads(request["options"]["body"])["format_selector"] == "(139+30280)"
        for width in (390, 768, 1440):
            page.set_viewport_size({"width": width, "height": 1000})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        assert not page_errors, page_errors
        browser.close()
    finally:
        playwright.stop()


def test_tv_selection_separates_web_fallback_and_shows_retry_source(live_server):
    base, _ = live_server
    playwright, browser, page, errors = browser_session(base)
    submissions = []
    retries = []

    def route_api(route):
        path = route.request.url.split("?")[0].removeprefix(base)
        if path == "/api/resolve":
            route.fulfill(json={
                "title": "合成番剧", "api_mode": "tv", "default_episode_id": "41",
                "episodes": [
                    {"id": "41", "title": "第 1 集", "group": "正片", "group_index": 1},
                    {"id": "42", "title": "第 2 集", "group": "正片", "group_index": 2},
                ],
            })
        elif path == "/api/episodes/formats/batch":
            tracks = {
                "41": {"format_id": "tv125-hevc", "dynamic_range": "HDR10",
                       "vcodec": "hev1", "locked_selector": "(tv125-hevc+tva30280)"},
                "42": {"format_id": "100198", "dynamic_range": "SDR",
                       "vcodec": "avc1", "locked_selector": "(100198+30280)"},
            }
            ids = route.request.post_data_json["episode_ids"]
            route.fulfill(json={
                "episode_formats": {key: [{**tracks[key], "height": 2160, "tbr": 8000}] for key in ids},
                "episode_sources": {key: "tv" if key == "41" else "web" for key in ids},
            })
        elif path == "/api/jobs":
            route.fulfill(json=[{
                "id": 99, "title": "TV 任务", "api_mode": "tv",
                "status": "queued" if retries else "failed",
            }])
        elif path == "/api/jobs/batch":
            submissions.append(route.request.post_data_json)
            route.fulfill(json={"ids": [100], "count": 1})
        elif path == "/api/jobs/99/retry":
            retries.append(path)
            route.fulfill(json={"ok": True})
        else:
            route.continue_()

    try:
        page.route("**/api/**", route_api)
        page.locator("[data-view=download]").click()
        page.locator("[data-mode]").select_option("tv")
        page.locator("#dv2-url").fill("https://www.bilibili.com/bangumi/play/ep41")
        page.get_by_role("button", name="解析链接").click()
        expect(page.locator("[data-batch-format]")).to_contain_text("HDR10")
        expect(page.locator("[data-quality-source]")).to_have_text("云视听小电视")
        page.get_by_role("button", name="全选", exact=True).click()
        expect(page.locator("[data-batch-format] option")).to_have_text(["请先选择同一片源"])
        submit = page.locator("[data-download-form] button[type=submit]")
        expect(submit).to_be_disabled()
        page.get_by_role("button", name="仅选 Web").click()
        expect(page.locator("[data-source-note]")).to_contain_text("已切换到 Web")
        expect(page.locator("[data-batch-format]")).to_contain_text("H.264")
        expect(page.locator("[data-batch-format]")).not_to_contain_text("HDR10")
        submit.click()
        assert submissions[0]["api_mode"] == "web"
        assert submissions[0]["episode_selectors"] == {"42": "(100198+30280)"}

        page.get_by_text("下载历史", exact=True).click()
        retry = page.get_by_role("button", name="重试 · 云视听小电视")
        retry.click()
        expect(page.locator("[data-notice]")).to_contain_text("按 云视听小电视 片源重新排队")
        assert retries == ["/api/jobs/99/retry"]
        assert not errors
    finally:
        browser.close()
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
        page.route('**/api/episodes/formats', lambda route: route.fulfill(json={'formats': []}))
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
        page.locator('.dv2-job[data-job-id="2"]').wait_for(state="attached")
        merging = page.locator('.dv2-job[data-job-id="1"]')
        completed = page.locator('.dv2-job[data-job-id="2"]')
        expect(merging.locator(".dv2-badge")).to_have_text("Web · 合并中")
        assert "正在合并音视频" in merging.locator(".dv2-job-meta").inner_text()
        assert "剩余" not in merging.locator(".dv2-job-meta").inner_text()
        assert "/s" not in merging.locator(".dv2-job-meta").inner_text()
        expect(merging.locator(".dv2-progress")).to_be_visible()

        expect(page.locator("[data-download-history-count]")).to_have_text("1 条")
        expect(completed).to_be_hidden()
        page.get_by_text("下载历史", exact=True).click()
        expect(completed.locator(".dv2-badge")).to_have_text("Web · 已完成")
        completed_meta = " ".join(completed.locator(".dv2-job-meta").inner_text().split())
        assert "完成时间" in completed_meta
        assert "下载总用时 1 分 5 秒" in completed_meta
        assert "最终文件大小 117.7 MiB" in completed_meta
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

        log_button = completed.get_by_role("button", name="查看日志")
        log_button.scroll_into_view_if_needed()
        page.evaluate("(el) => window.scrollTo(0, window.scrollY + el.getBoundingClientRect().top - 180)",
                      log_button.element_handle())
        scroll_before_dialog = page.evaluate("window.scrollY")
        assert scroll_before_dialog > 0

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
        page.wait_for_function("y => Math.abs(window.scrollY - y) <= 2", arg=scroll_before_dialog)

        open_log()
        page.keyboard.press("Escape")
        expect(dialog).to_be_hidden()
        page.wait_for_function("y => Math.abs(window.scrollY - y) <= 2", arg=scroll_before_dialog)

        open_log()
        dialog_box = dialog.bounding_box()
        assert dialog_box
        page.mouse.click(max(2, dialog_box["x"] - 10), max(2, dialog_box["y"] - 10))
        expect(dialog).to_be_hidden()
        page.wait_for_function("y => Math.abs(window.scrollY - y) <= 2", arg=scroll_before_dialog)

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


def test_number_and_title_preview_stay_inline_with_episode_selection(live_server):
    base, _ = live_server
    playwright, browser, page, errors = browser_session(base, legacy=False)
    try:
        url = "https://www.bilibili.com/bangumi/play/ep3854802"
        page.route("**/api/resolve", lambda route: route.fulfill(json={
            "title": "凡人修仙传", "source_url": url, "formats": [], "api_mode": "web",
            "episodes": [{"id": "3854802", "group": "正片", "title": "200 暮兰之战24"}],
            "default_episode_id": "3854802",
            "naming_items": {"3854802": {"identifier": "ep3854802", "number_kind": "episode", "number": 200, "chapter": "暮兰之战24"}},
        }))
        page.route("**/api/episodes/formats/batch", lambda route: route.fulfill(json={
            "episode_formats": {"3854802": [{"selector": "30112+30280", "format_id": "30112",
                "locked_selector": "(30112+30280)", "height": 1080, "vcodec": "avc1", "tbr": 2940,
                "size_with_audio": 188239248}]},
        }))
        page.route("**/api/jobs/batch", lambda route: route.fulfill(json={"ids": [101], "count": 1}))
        page.locator("[data-view=download]").click()
        page.locator("#dv2-url").fill(url)
        page.locator("[data-parse]").click()
        expect(page.locator("[data-naming-preview]")).to_have_text("凡人修仙传.E200.mp4")
        expect(page.locator("[data-naming-facts]")).to_contain_text("连续第 200 集 · 暮兰之战24")
        page.locator("[data-naming-name]").fill("凡人 修仙传")
        page.locator("[data-naming-number]").fill("178")
        expect(page.locator("[data-naming-preview]")).to_have_text("凡人.修仙传.E178.mp4")
        with page.expect_request("**/api/jobs/batch") as submitted:
            page.locator("[data-download-form] button[type=submit]").click()
        assert submitted.value.post_data_json["episode_selectors"] == {"3854802": "(30112+30280)"}
        options = submitted.value.post_data_json["naming"]
        assert options["mode"] == "smart" and options["title"] == "凡人 修仙传"
        assert options["episode_numbers"] == {"3854802": 178}
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()

@pytest.mark.parametrize("legacy_snapshot", [False, True], ids=["structured-number", "legacy-title"])
def test_saved_parse_names_main_episode_and_bonus_by_their_own_metadata(live_server, legacy_snapshot):
    base, app = live_server
    source = "https://www.bilibili.com/bangumi/play/ep4507157"
    services = app.state.services
    episodes, _ = services.runner._build_episodes({}, {
        "main_section": {"episodes": [
            {"id": 3065290, "title": "1", "long_title": "来世，你可以找我报仇"},
            {"id": 3065291, "title": "200", "long_title": "暮兰之战24"},
            {"id": 3065292, "title": "新章", "long_title": "暮兰之战25"},
        ]},
        "section": [{"title": "PV花絮", "episodes": [{"id": 4507157, "title": "兢慎篇 预热PV"}]}],
    })
    if legacy_snapshot:
        for episode in episodes:
            episode.pop("display_episode_number")
            episode.pop("chapter_title")
    services.database.save_history(
        source_url=source, api_mode="web", title="盗妖行", uploader="",
        duration=None, is_playlist=True, playlist_count=1, default_episode_id="4507157",
        formats=[], episodes=episodes,
    )
    playwright, browser, page, errors = browser_session(base)
    try:
        page.route("**/api/episodes/formats/batch", lambda route: route.fulfill(json={"episode_formats": {}}))
        page.locator("[data-view=download]").click()
        page.get_by_text("解析记录", exact=True).click()
        page.get_by_role("button", name="复用解析").click()
        preview = page.locator("[data-naming-preview]")
        expect(preview).to_have_text("兢慎篇.预热PV.mp4")
        page.get_by_role("button", name="正片", exact=True).click()
        page.locator('[data-episode-id="3065290"]').click()
        expect(page.locator("[data-naming-episode]")).to_have_value("3065290")
        expect(page.locator("[data-naming-facts]")).to_contain_text("连续第 1 集")
        expect(preview.locator("div")).to_have_text(["盗妖行.E01.mp4", "兢慎篇.预热PV.mp4"])

        page.locator("[data-naming-number]").fill("7")
        expect(preview.locator("div")).to_have_text(["盗妖行.E07.mp4", "兢慎篇.预热PV.mp4"])
        page.locator('[data-episode-id="3065291"]').click()
        page.locator('[data-episode-id="3065292"]').click()
        expect(preview.locator("div")).to_have_text([
            "盗妖行.E07.mp4", "盗妖行.E200.mp4", "盗妖行.Biliep3065292.mp4", "兢慎篇.预热PV.mp4",
        ])
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_grid_hides_empty_categories_and_reuses_saved_parse_without_resolving(live_server):
    base, _ = live_server
    playwright, browser, page, errors = browser_session(base, legacy=False)
    source = "https://www.bilibili.com/bangumi/play/ss42"
    calls, submitted = [], []
    saved = {
        "source_url": source, "title": "缓存剧集", "api_mode": "tv", "is_playlist": True,
        "default_episode_id": "51",
        "episodes": [{"id": str(i), "group": "正片", "group_index": i, "title": f"第 {i} 集"}
                     for i in range(1, 221)],
        "formats": [{"selector": "cached-format", "format_id": "cached", "height": 720}],
    }
    try:
        page.route("**/api/history?limit=200", lambda route: route.fulfill(json=[{**saved, "id": 7}]))
        page.route("**/api/history/7", lambda route: route.fulfill(json=saved))

        def fresh(route):
            calls.append(route.request.post_data_json)
            route.fulfill(json={
                **saved, "source_url": "https://www.bilibili.com/bangumi/play/ss43", "api_mode": "web",
                "title": "含花絮和预告", "episodes": [
                    {"id": "1", "group": "正片", "title": "第一集"},
                    {"id": "7", "group": "特别花絮", "title": "幕后制作"},
                    {"id": "8", "group": "定档预告", "title": "首支预告"},
                ], "default_episode_id": "1",
            })

        page.route("**/api/resolve", fresh)
        page.route("**/api/episodes/formats", lambda route: route.fulfill(json={
            "formats": [{"selector": "episode-only", "format_id": "ep", "height": 1080}]
        }))

        def detailed(route):
            ids = route.request.post_data_json["episode_ids"]
            route.fulfill(json={"episode_formats": {item: [
                {"selector": "default+30280", "format_id": "default", "locked_selector": "(default+30280)", "height": 1080,
                 "vcodec": "avc1", "fps": 25, "dynamic_range": "SDR", "tbr": 2940, "size_with_audio": 177800000},
                {"selector": "episode-only", "format_id": "ep", "locked_selector": "(ep)", "height": 720,
                 "vcodec": "avc1", "fps": 25, "dynamic_range": "SDR", "tbr": 683, "size_with_audio": 41300000},
            ] for item in ids}})

        page.route("**/api/episodes/formats/batch", detailed)

        def batch(route):
            submitted.append(route.request.post_data_json)
            route.fulfill(json={"ids": [2], "count": 1})

        page.route("**/api/jobs/batch", batch)
        page.locator("[data-view=download]").click()
        page.get_by_text("解析记录", exact=True).click()
        page.get_by_role("button", name="复用解析").click()
        expect(page.locator("[data-title]")).to_have_text("缓存剧集")
        expect(page.locator("[data-mode]")).to_have_value("tv")
        expect(page.locator("[data-format] option").last).to_have_attribute("value", "cached-format")
        expect(page.locator("[data-episode-categories] button")).to_have_text(["正片"])
        expect(page.locator(".dv2-episode")).to_have_count(50)
        expect(page.locator(".dv2-episode").first).to_have_text("100")
        expect(page.locator('[data-episode-id="51"]')).to_have_attribute("aria-pressed", "true")
        controls = page.locator(".dv2-picker-actions")
        expect(controls.get_by_role("button")).to_have_text(["正序 ↑", "倒序 ↓", "全选"])
        page.get_by_role("button", name="201–220", exact=True).click()
        controls.get_by_role("button", name="全选", exact=True).click()
        expect(page.locator(".dv2-episode[aria-pressed=true]")).to_have_count(20)
        expect(page.locator("[data-selected-count]")).to_have_text("已选 21 集")
        expect(page.locator('[data-batch-group="same"] b')).to_have_text("21")
        batch_list = page.locator("[data-batch-list]")
        assert batch_list.evaluate("e => e.clientHeight === 4 * e.firstElementChild.offsetHeight && e.scrollHeight > e.clientHeight")
        batch_list.evaluate("e => e.scrollTop = e.scrollHeight")
        expect(page.locator("[data-batch-window]")).to_contain_text("21 / 21 集")
        page.get_by_role("button", name="101–150", exact=True).click()
        expect(page.locator(".dv2-episode[aria-pressed=true]")).to_have_count(0)
        expect(controls.get_by_role("button", name="全选", exact=True)).to_be_visible()
        page.get_by_role("button", name="201–220", exact=True).click()
        controls.get_by_role("button", name="取消全选", exact=True).click()
        expect(page.locator(".dv2-episode[aria-pressed=true]")).to_have_count(0)
        expect(page.locator("[data-selected-count]")).to_have_text("已选 1 集")
        page.get_by_role("button", name="正序 ↑").click()
        page.locator('[data-episode-id="1"]').click()
        expect(page.locator("[data-selected-count]")).to_have_text("已选 2 集")
        expect(page.locator(".dv2-episode").first).to_have_text("1")
        page.locator("[data-episode-toggle]").click()
        expect(page.locator("[data-episodes]")).to_be_hidden()
        page.locator("[data-episode-toggle]").click()
        expect(page.locator('[data-episode-id="1"]')).to_have_attribute("aria-pressed", "true")
        assert not calls

        page.locator("#dv2-url").fill("https://www.bilibili.com/bangumi/play/ss43")
        page.locator("[data-parse]").click()
        expect(page.locator("[data-episode-categories] button")).to_have_text(["正片", "花絮", "预告"])
        page.get_by_role("button", name="花絮", exact=True).click()
        controls.get_by_role("button", name="全选", exact=True).click()
        expect(page.locator('[data-episode-id="7"]')).to_have_attribute("aria-pressed", "true")
        expect(page.locator("[data-selected-count]")).to_have_text("已选 2 集")
        page.get_by_role("button", name="预告", exact=True).click()
        expect(controls.get_by_role("button", name="全选", exact=True)).to_be_visible()
        page.locator('[data-episode-id="8"]').click()
        expect(page.locator("[data-selected-count]")).to_have_text("已选 3 集")
        quality = page.locator(".dv2-episode-quality")
        quality.focus()
        expect(quality.locator("option").last).to_have_attribute("value", "episode-only")
        quality.select_option("episode-only")
        page.get_by_role("button", name="花絮", exact=True).click()
        for width in (390, 768, 1440):
            page.set_viewport_size({"width": width, "height": 960})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("[data-download-form] button[type=submit]").click()
        expect(page.locator(".dv2")).to_have_attribute("data-stage", "submitted")
        assert [job["episode_ids"] for job in submitted] == [["1", "7", "8"]]
        assert submitted[0]["episode_selectors"] == {"1": "(default+30280)", "7": "(default+30280)", "8": "(ep)"}
        page.get_by_role("button", name="复用解析").click()
        expect(page.locator("[data-selected-count]")).to_have_text("已选 2 集")
        expect(page.locator("[data-episode-categories] button")).to_have_text(["正片"])
        assert len(calls) == 1 and not errors, errors
    finally:
        browser.close()
        playwright.stop()
