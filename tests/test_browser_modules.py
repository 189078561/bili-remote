from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from urllib.request import urlopen

import pytest
import uvicorn

from bili_remote.web import create_app


@pytest.fixture
def live_server(settings) -> Iterator[tuple[str, object]]:
    settings.ensure_directories()
    (settings.download_root / "Media").mkdir(exist_ok=True)
    (settings.download_root / "Media" / "episode01.mp4").write_bytes(b"test-video")
    (settings.images_root / "pixel.png").write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010804000000"
            "b51c0c020000000b49444154789c63f8cf00000301010018dd8db0"
            "0000000049454e44ae426082"
        )
    )
    app = create_app(settings)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with urlopen(f"{base_url}/healthz", timeout=0.5) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("测试服务器未能启动")
    try:
        yield base_url, app
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def browser_session(base_url):
    playwright_api = pytest.importorskip("playwright.sync_api")
    playwright = playwright_api.sync_playwright().start()
    try:
        browser = playwright.chromium.launch()
    except playwright_api.Error as exc:
        playwright.stop()
        pytest.skip(str(exc))
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(base_url)
    page.locator("#password").fill("test-password")
    page.locator("#login-form button[type=submit]").click()
    page.wait_for_url(base_url + "/")
    page.locator(".app-tabs [data-view=files]").wait_for()
    return playwright, browser, page, errors



def open_legacy_publish(page):
    page.locator('[data-view="publish"]').click()
    page.locator('.assistant-panel > summary', has_text="发布信息").click()
    page.locator('.assistant-advanced > summary', has_text="其他发布工具").click()
    page.locator('#assistant-legacy-open').click()

def test_new_module_workspaces_navigation_and_responsive_layout(live_server):
    base_url, _app = live_server
    playwright, browser, page, errors = browser_session(base_url)
    try:
        assert page.locator(".app-tabs [data-view]").count() == 4
        page.locator('[data-view="home"]').click()
        page.get_by_role("heading", name="欢迎使用 Bili Remote").wait_for()
        from pathlib import Path

        artifacts = Path("/tmp/bili-ui-validation")
        artifacts.mkdir(exist_ok=True)
        page.screenshot(path=str(artifacts / "home-desktop.png"), full_page=True)
        for kind, selector in (
            ("uploads", "#uploads-select"),
            ("release-info", "#release-source-select"),
            ("torrents", "#torrent-source-select"),
        ):
            page.locator('[data-view="files"]').click()
            page.locator(".file-history-menu summary").click()
            page.locator(f'[data-file-tool="{kind}"]').click()
            page.locator("#assistant-extra-tools").wait_for()
            page.locator(selector).click()
            page.locator("#modal .unified-file-browser").wait_for()
            assert page.locator(f"#{kind}-view").evaluate("n=>n.isConnected")
            page.locator("#modal-close").click()
            page.locator("#assistant-extra-tools").wait_for()
            page.locator("#assistant-tools-close").click()
            assert page.locator("#file-tool-dock").evaluate("n=>n.children.length") == 4
        # Legacy stored default/history identifiers still reach real tools.
        page.evaluate('openBusinessView("torrents")')
        page.locator("#assistant-extra-tools #torrent-create").wait_for()
        page.locator("#assistant-tools-close").click()
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(artifacts / "files-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#nav-toggle").click()
        assert page.locator("#settings-entry").is_visible()
        page.locator('[data-view="publish"]').click()
        assert page.locator("#publish-view").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_screenshot_workbench_sampling_mode_browser_flow(live_server):
    from test_screenshots import make_video

    base_url, app = live_server
    services = app.state.services
    make_video(services.settings.download_root / "Media" / "episode01.mp4")
    services.database.update_settings(
        {
            "screenshot_output_mode": "collage",
            "collage_default_columns": "3",
            "collage_default_rows": "3",
            "collage_default_content_type": "custom",
            "collage_custom_skip_start": "0",
            "collage_custom_skip_end": "0",
            "screenshot_default_width": "320",
            "collage_default_width": "240",
            "screenshot_default_sampling": "uniform",
        }
    )
    playwright, browser, page, errors = browser_session(base_url)
    try:
        page.locator('[data-view="files"]').click()
        page.locator('.file-row[data-path="Media"]').click()
        video = page.locator('.file-row[data-path="Media/episode01.mp4"]')
        video.wait_for()
        video.click()
        from pathlib import Path

        artifacts = Path("/tmp/bili-ui-validation")
        artifacts.mkdir(exist_ok=True)
        page.screenshot(path=str(artifacts / "files-desktop.png"), full_page=True)
        page.get_by_role("button", name="生成缩略图", exact=True).click()
        page.locator("#assistant-generate").wait_for()
        # Existing advanced screenshot sessions remain compatible for older callers.
        page.evaluate('CollageTool.open([{relative_path:"Media/episode01.mp4",name:"episode01.mp4",kind:"video"}], "downloads")')
        page.locator(".screenshot-workbench").wait_for()
        page.locator(".collage-result-image").wait_for(timeout=30000)
        assert page.locator(".screenshot-card").count() == 9
        assert not page.get_by_label("截图方式", exact=True).count()
        assert page.get_by_role("link", name="下载拼图", exact=True).count() == 1
        page.screenshot(path=str(artifacts / "thumbnail-result.png"), full_page=True)
        page.locator(".result-secondary summary").click()
        page.get_by_role("button", name="重新合成所选图片", exact=True).click()
        page.locator('a[href*="path=episode01_collage.png"]').wait_for(timeout=15000)
        page.locator("#modal-close").click()
        page.locator('[data-view="files"]').click()
        assert video.locator(".file-check").is_checked()
        # Output remains available after refresh, from the server session history.
        page.reload()
        page.locator("#screenshot-sessions-button").click()
        page.get_by_role("button", name="打开会话", exact=True).first.click()
        page.locator(".collage-result-image").wait_for()
        page.locator(".screenshot-list-section > summary").click()
        page.locator(".screenshot-more > summary").first.click()
        page.get_by_role("button", name="预览纯画面", exact=True).first.click()
        page.locator(".screenshot-preview-dialog").wait_for()
        page.locator("#modal-close").click()
        page.locator("#modal-close").click()
        page.locator("#settings-entry").click()
        page.locator('[data-group="collage"]').click()
        mode = page.locator('[data-setting-key="screenshot_output_mode"] select')
        page.screenshot(path=str(artifacts / "settings-thumbnails.png"), full_page=True)
        mode.select_option("single")
        assert not page.locator('[data-setting-key="collage_default_rows"]').is_visible()
        page.locator("#settings-save").click()
        page.locator("#settings-dirty-indicator", has_text="已保存").wait_for()
        page.locator("#settings-back").click()
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_catalog_proposals_pter_draft_and_tools_responsive(live_server):
    from pathlib import Path

    import httpx

    base_url, app = live_server
    services = app.state.services
    services.database.update_settings(
        {
            "tmdb_api_key": services.secrets.encrypt_json("tmdb-test"),
            "ptgen_url": "https://ptgen.example/",
        }
    )
    requests = []
    fail_ptgen = [False]

    def mock(request):
        requests.append(str(request.url))
        if request.url.host == "ptgen.example":
            if request.url.params.get("search"):
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "data": [
                            {
                                "title": "测试作品",
                                "year": "2024",
                                "link": "https://movie.douban.com/subject/42/",
                            },
                        ],
                    },
                )
            if fail_ptgen[0]:
                return httpx.Response(503, text="Unavailable")
            return httpx.Response(200, json={"success": True, "format": "[b]测试简介[/b]"})
        if "search" in request.url.path:
            return httpx.Response(
                200,
                json={"results": [{"id": 42, "name": "测试作品", "first_air_date": "2024-01-01"}]},
            )
        return httpx.Response(
            200,
            json={
                "id": 42,
                "name": "Test Show"
                if request.url.params.get("language") == "en-US"
                else "测试作品",
                "first_air_date": "2024-01-01",
                "external_ids": {"imdb_id": "tt42"},
                "overview": "TMDB 简介",
            },
        )

    services.metadata.transport = httpx.MockTransport(mock)
    playwright, browser, page, errors = browser_session(base_url)
    try:
        open_legacy_publish(page)
        page.locator("#publish-metadata").get_by_role("button", name="搜索作品", exact=True).click()
        page.get_by_label("资料来源", exact=True).select_option("tmdb")
        page.get_by_label("搜索作品", exact=True).fill("测试作品")
        page.locator(".metadata-picker").get_by_role("button", name="搜索", exact=True).click()
        page.get_by_role("button", name="选择作品", exact=True).click()
        page.locator("#publish-metadata .tool-error", has_text="TMDB 资料已载入").wait_for()
        assert page.locator("#publish-description").input_value() == "TMDB 简介"
        page.get_by_role("button", name="补充 / 重试简介", exact=True).click()
        page.locator("#publish-metadata .tool-error", has_text="PT-Gen 简介已补充").wait_for()
        assert page.locator("#publish-title_en").input_value() == "Test Show"
        assert "测试简介" in page.locator("#publish-description").input_value()
        assert any("ptgen.example" in url for url in requests)
        page.locator("#publish-title_en").fill("My Edited Title")
        fail_ptgen[0] = True
        page.locator("#publish-metadata").get_by_role(
            "button", name="补充 / 重试简介", exact=True
        ).click()
        page.locator("#publish-metadata .tool-error", has_text="已有资料已保留").wait_for()
        assert page.locator("#publish-title_en").input_value() == "My Edited Title"
        fail_ptgen[0] = False
        page.get_by_role("button", name="补充 / 重试简介", exact=True).click()
        page.locator("#publish-metadata .tool-error", has_text="PT-Gen 简介已补充").wait_for()
        assert page.locator("#publish-title_en").input_value() == "My Edited Title"
        page.locator("#publish-create").click()
        page.locator("#publish-output-title", has_text="草稿").wait_for()
        assert "完整 MediaInfo" in page.locator("#publish-warnings").inner_text()
        assert "测试简介" in page.locator("#publish-output").input_value()
        artifacts = Path("/tmp/bili-ui-validation")
        artifacts.mkdir(exist_ok=True)
        page.evaluate("scrollTo(0,0)")
        page.screenshot(path=str(artifacts / "publish-desktop.png"), full_page=True)
        page.reload()
        open_legacy_publish(page)
        page.locator("#publish-output-title", has_text="草稿").wait_for()
        assert page.locator("#publish-title_en").input_value() == "My Edited Title"
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(artifacts / "publish-mobile.png"), full_page=True)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_publish_associates_only_current_resource_and_restores_saved_draft(live_server):
    base_url, app = live_server
    services = app.state.services
    (services.settings.download_root / "MediaExtra").mkdir()
    (services.settings.download_root / "MediaExtra" / "other.mp4").write_bytes(b"video")
    report_ids = []
    for title, path in (("报告 A", "Media"), ("报告 B", "Media"), ("其他资源报告", "MediaExtra")):
        report_ids.append(
            services.database.create_release_document(
                title=title,
                source_root_key="downloads",
                source_relative_path=path,
                output_root_key="release_info",
                format="bbcode",
                file_list=[],
                generated_text="",
                media={
                    "reports": [
                        {
                            "id": title,
                            "kind": "MediaInfo",
                            "valid": True,
                            "text": "General\n" + title,
                        }
                    ]
                },
            )
        )
    upload_ids = []
    for path in ("Media/episode01.mp4", "MediaExtra/other.mp4"):
        prepared = services.screenshots.prepare_create(
            {"sources": [{"root_key": "downloads", "relative_path": path}], "total": 1}
        )
        upload_id = services.database.create_image_upload(
            provider="pixhost",
            source_kind="screenshot",
            source_root_key="images",
            source_relative_path="pixel.png",
            source_name=path,
            source_fingerprint="test",
            source_size_bytes=1,
            session_id=prepared["session_id"],
            session_item_id="frame",
        )
        services.database.update_image_upload(
            upload_id, status="completed", remote_url="https://images.example/shot.png"
        )
        upload_ids.append(upload_id)
    poster = services.database.create_image_upload(
        provider="pixhost",
        source_kind="file",
        source_root_key="images",
        source_relative_path="pixel.png",
        source_name="poster",
        source_fingerprint="poster",
        source_size_bytes=1,
    )
    services.database.update_image_upload(
        poster, status="completed", remote_url="https://images.example/poster.png"
    )
    playwright, browser, page, errors = browser_session(base_url)
    try:
        open_legacy_publish(page)
        page.locator("#publish-source-select").click()
        page.locator('#modal .file-row[data-path="Media"] .file-check').check()
        page.get_by_role("button", name="使用此资源", exact=True).click()
        page.wait_for_function(
            'document.querySelector("#publish-release-select").options.length === 3'
        )
        assert not page.locator("#publish-release-select").input_value()
        assert "其他资源报告" not in page.locator("#publish-release-select").inner_text()
        page.wait_for_function(
            '(id)=>Boolean(document.querySelector(`#publish-upload-list input[value="${id}"]`))',
            arg=upload_ids[0],
        )
        assert page.locator(f'#publish-upload-list input[value="{upload_ids[1]}"]').count() == 0
        page.locator("#publish-release-select").select_option(str(report_ids[0]))
        page.locator(".publish-artifacts > summary", has_text="截图选择与顺序").click()
        page.locator(f'#publish-upload-list input[value="{upload_ids[0]}"]').check()
        page.locator("#publish-show-all-images").check()
        page.locator(".publish-attributes > summary").click()
        page.locator("#publish-poster_upload_id").select_option(str(poster))
        page.locator("#publish-show-all-images").uncheck()
        page.wait_for_function(
            '(id)=>document.querySelector("#publish-poster_upload_id").value === String(id)',
            arg=poster,
        )
        page.locator("#publish-title_en").fill("Resource A")
        page.locator("#publish-create").click()
        page.locator("#publish-output-title", has_text="草稿").wait_for()
        # Saved links survive records falling outside the latest 200 items.
        for number in range(201):
            services.database.create_release_document(
                title=f"较新报告 {number}",
                source_root_key="downloads",
                source_relative_path="MediaExtra",
                output_root_key="release_info",
                format="bbcode",
                file_list=[],
                generated_text="",
                media={},
            )
            newer = services.database.create_image_upload(
                provider="pixhost",
                source_kind="file",
                source_root_key="images",
                source_relative_path="pixel.png",
                source_name=f"newer {number}",
                source_fingerprint="newer",
                source_size_bytes=1,
            )
            services.database.update_image_upload(
                newer, status="completed", remote_url="https://images.example/newer.png"
            )
        page.reload()
        open_legacy_publish(page)
        page.locator("#publish-output-title", has_text="草稿").wait_for()
        assert page.locator(f'#publish-upload-list input[value="{upload_ids[0]}"]').is_checked()
        assert page.locator("#publish-release-select").input_value() == str(report_ids[0])
        assert page.locator("#publish-poster_upload_id").input_value() == str(poster)
        assert "Media" in page.locator("#publish-resource-label").inner_text()
        # The path prefix Media must never include its sibling MediaExtra.
        page.evaluate(
            'BiliModules.openPublishResource({root_key:"downloads",relative_path:"MediaExtra",is_dir:true})'
        )
        page.wait_for_function(
            'document.querySelector("#publish-release-select").options.length > 2'
        )
        assert not page.locator("#publish-release-select").input_value()
        assert page.locator(f'#publish-upload-list input[value="{upload_ids[0]}"]').count() == 0
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_publish_tmdb_selection_and_link_supplement_preserves_manual_edits(live_server):
    import httpx

    base_url, app = live_server
    services = app.state.services
    services.database.update_settings({"ptgen_url": "https://ptgen.example/api/v1/info", "tmdb_api_key":services.secrets.encrypt_json("test-key")})
    calls = []
    changed = [False]

    def respond(request):
        calls.append(str(request.url))
        if request.url.host == "api.themoviedb.org":
            if "/search/" in request.url.path:
                return httpx.Response(200, json={"results":[{"id":300,"name":"测试剧 第三季","first_air_date":"2026-01-01"},{"id":100,"name":"测试剧 第一季","first_air_date":"2020-01-01"}]})
            return httpx.Response(200, json={"id":100,"name":"Test Show Season 1" if request.url.params.get("language")=="en-US" else "测试剧 第一季","first_air_date":"2020-01-01","overview":"第一季故事"})
        assert request.url.params["url"] == "https://movie.douban.com/subject/100/"
        return httpx.Response(
            200,
            json={
                "success": True,
                "chinese_title": "测试剧 第一季",
                "foreign_title": "New Name" if changed[0] else "Test Show Season 1",
                "year": "2020",
                "format": "[b]第一季简介[/b]",
                "introduction": "第一季故事",
            },
        )

    services.metadata.transport = httpx.MockTransport(respond)
    playwright, browser, page, errors = browser_session(base_url)
    try:
        open_legacy_publish(page)
        page.locator("#publish-metadata").get_by_role("button", name="搜索作品", exact=True).click()
        assert page.get_by_label("资料来源").input_value() == "tmdb"
        page.get_by_label("搜索作品", exact=True).fill("测试剧")
        page.get_by_label("搜索作品", exact=True).press("Enter")
        page.locator(".metadata-candidate").nth(1).wait_for()
        assert len(calls) == 1  # No automatic first-result adoption.
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        from pathlib import Path

        artifacts = Path("/tmp/bili-ui-validation")
        artifacts.mkdir(exist_ok=True)
        page.screenshot(path=str(artifacts / "ptgen-picker-mobile.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.screenshot(path=str(artifacts / "ptgen-picker-desktop.png"), full_page=True)
        page.locator(".metadata-candidate", has_text="2020").get_by_role(
            "button", name="选择作品", exact=True
        ).click()
        page.locator("#publish-metadata .tool-error", has_text="TMDB 资料已载入").wait_for()
        assert page.locator("#publish-year").input_value() == "2020"
        assert page.locator("#publish-title_en").input_value() == "Test Show Season 1"
        assert page.locator("#publish-description").input_value() == "第一季故事"
        assert len(calls) == 3
        page.locator("#publish-title_en").fill("My Manual Title")
        page.locator("#publish-description").fill("我的人工简介")
        page.locator("#publish-metadata .metadata-extra summary").click()
        page.locator("#publish-douban_url").fill("https://movie.douban.com/subject/100/")
        page.locator("#publish-poster_url").fill("https://images.example/manual-poster.png")
        changed[0] = True
        page.get_by_role("button", name="补充 / 重试简介", exact=True).click()
        page.get_by_role("heading", name="预览资料变更").wait_for()
        assert page.locator(".proposal-row", has_text="英文名").count() == 1
        page.get_by_role("button", name="采用所选字段", exact=True).click()
        assert page.locator("#publish-title_en").input_value() == "My Manual Title"
        assert page.locator("#publish-description").input_value() == "我的人工简介"
        assert page.locator("#publish-poster_url").input_value().endswith("manual-poster.png")
        # The shared picker retains TMDB as the default for batch rename callers.
        page.evaluate("window.MediaCatalog.open(() => {})")
        assert page.get_by_label("资料来源").input_value() == "tmdb"
        page.locator("#modal-close").click()
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_tmdb_picker_discards_old_search_and_failed_detail_preserves_edits(live_server):
    import httpx

    base_url, app = live_server
    services = app.state.services
    services.database.update_settings({"tmdb_api_key": services.secrets.encrypt_json("test-key")})
    started, release = threading.Event(), threading.Event()

    def respond(request):
        if "/search/" in request.url.path:
            name = request.url.params["query"]
            if name == "旧作品":
                started.set()
                release.wait(timeout=10)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 100, "name": name, "first_air_date": "2020-01-01",
                        },
                    ],
                },
            )
        return httpx.Response(503, json={"error":"TMDB unavailable"})

    services.metadata.transport = httpx.MockTransport(respond)
    playwright, browser, page, errors = browser_session(base_url)
    try:
        open_legacy_publish(page)
        page.locator("#publish-title_zh").fill("已有资料")
        page.locator("#publish-metadata").get_by_role("button", name="搜索作品", exact=True).click()
        page.get_by_label("搜索作品", exact=True).fill("旧作品")
        page.locator(".metadata-picker").get_by_role("button", name="搜索", exact=True).click()
        assert started.wait(timeout=5)
        page.get_by_label("搜索作品", exact=True).fill("新作品")
        release.set()
        page.locator(".metadata-picker").get_by_role("button", name="搜索", exact=True).wait_for(state="visible")
        page.wait_for_function("!document.querySelector('.metadata-picker .is-busy')")
        assert page.locator(".metadata-candidate").count() == 0
        page.locator(".metadata-picker").get_by_role("button", name="搜索", exact=True).click()
        page.locator(".metadata-candidate", has_text="新作品").wait_for()
        assert page.locator(".metadata-candidate", has_text="旧作品").count() == 0
        page.get_by_role("button", name="选择作品", exact=True).click()
        page.locator(".metadata-picker .tool-error", has_text="已有资料已保留").wait_for()
        page.locator("#modal-close").click()
        assert page.locator("#publish-title_zh").input_value() == "已有资料"
        assert not errors, errors
    finally:
        release.set()
        browser.close()
        playwright.stop()
