import subprocess
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect
from test_browser_modules import browser_session
from test_browser_modules import live_server as shared_live_server


@pytest.fixture
def live_server(settings):
    yield from shared_live_server.__wrapped__(settings)


ARTIFACTS = Path("/tmp/bili-assistant-validation")


def prepare_video(app):
    services = app.state.services
    path = services.settings.download_root / "Media" / "测试.Sample.S01E01.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            "8",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-y",
            str(path),
        ],
        check=True,
    )
    return path


def test_assistant_real_generation_download_mobile_and_draft(live_server):
    base_url, app = live_server
    prepare_video(app)
    playwright, browser, page, errors = browser_session(base_url)
    ARTIFACTS.mkdir(exist_ok=True)
    try:
        page.locator('[data-view="publish"]').click()
        assert page.locator(".app-tabs > button").count() == 5
        page.locator('#assistant-browser .file-row[data-path="Media"]').click()
        page.locator(
            '#assistant-browser .file-row[data-path="Media/测试.Sample.S01E01.mp4"] .file-check'
        ).check()
        expect(page.locator("#assistant-media")).to_contain_text("H264", timeout=20000)
        page.locator("#assistant-count").fill("4")
        page.locator("#assistant-layout").select_option("2")
        page.locator("#assistant-generate").click()
        expect(page.locator("#assistant-images img")).to_be_visible(timeout=60000)
        expect(page.locator("#assistant-images img")).to_have_js_property("complete", True)
        assert page.locator("#modal").is_hidden()
        with page.expect_download() as download_info:
            page.get_by_role("link", name="↓ 下载缩略图").click()
        download = download_info.value
        assert download.suggested_filename.endswith(".jpg")
        download.save_as(str(ARTIFACTS / "generated-grid.jpg"))
        page.screenshot(path=str(ARTIFACTS / "desktop-results.png"), full_page=True)
        page.locator('[data-result="preview"]').click()
        page.locator("#assistant-title").fill("My edited title")
        page.locator("#assistant-description").fill("手动编辑内容，不能被覆盖。")
        for width in [320, 375, 390, 768, 900, 1024, 1440, 1920]:
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            if width <= 900:
                for step in ["files", "config", "results"]:
                    page.locator(f'.assistant-steps [data-step="{step}"]').click()
                    assert page.locator(f'[data-pane="{step}"]').is_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
                        width,
                        step,
                    )
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator('.assistant-steps [data-step="config"]').click()
        page.locator("#assistant-mode").select_option("single")
        page.locator("#assistant-time").fill("00:00:03")
        page.locator("#assistant-generate").click()
        page.wait_for_function("SeedAssistant.state.images[0]?.columns===1", timeout=60000)
        page.locator('.assistant-steps [data-step="results"]').click()
        page.screenshot(path=str(ARTIFACTS / "mobile-single.png"), full_page=True)
        with page.expect_download() as single_download:
            page.get_by_role("link", name="↓ 下载缩略图").click()
        single_download.value.save_as(str(ARTIFACTS / "generated-single.jpg"))
        page.reload()
        page.locator("#nav-toggle").click()
        page.locator('[data-view="publish"]').click()
        page.locator('.assistant-steps [data-step="results"]').click()
        page.locator('[data-result="preview"]').click()
        assert page.locator("#assistant-title").input_value() == "My edited title"
        assert page.locator("#assistant-description").input_value() == "手动编辑内容，不能被覆盖。"
        assert not errors, errors
    finally:
        browser.close()
        playwright.stop()


def test_assistant_tmdb_upload_retry_pipeline_reload_and_torrent(live_server):
    import threading

    from bili_remote.image_hosts import ImageHostError
    from bili_remote.torrents import bdecode

    base_url, app = live_server
    prepare_video(app)
    services = app.state.services
    services.database.update_settings({"tmdb_api_key": services.secrets.encrypt_json("test-key")})
    tmdb_calls = []

    def tmdb(request):
        tmdb_calls.append(request.url.path)
        if "/search/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": 21,
                            "name": "测试作品",
                            "title": "测试电影",
                            "first_air_date": "2024-01-01",
                            "release_date": "2024-01-01",
                        }
                    ]
                },
            )
        english = request.url.params.get("language") == "en-US"
        return httpx.Response(
            200,
            json={
                "id": 21,
                "name": "Test Show" if english else "测试作品",
                "title": "Test Movie" if english else "测试电影",
                "first_air_date": "2024-01-01",
                "release_date": "2024-01-01",
                "overview": "真实接口结构的测试简介",
                "genres": [{"name": "剧情"}],
            },
        )

    services.metadata.transport = httpx.MockTransport(tmdb)
    upload_calls = []
    release = threading.Event()

    class Host:
        def upload(self, path, media_type, timeout):
            upload_calls.append(path.name)
            if len(upload_calls) == 1:
                raise ImageHostError("测试图床：暂时不可用，请重试")
            if len(upload_calls) > 2:
                release.wait(timeout=10)
            return {
                "remote_url": "https://images.example/" + path.name,
                "remote_id": path.name,
                "thumbnail_url": "https://images.example/thumb/" + path.name,
            }

    services.image_hosts._client_factory = lambda *_: Host()
    playwright, browser, page, errors = browser_session(base_url)
    try:
        page.locator('[data-view="publish"]').click()
        page.locator('#assistant-browser .file-row[data-path="Media"]').click()
        page.locator(
            '#assistant-browser .file-row[data-path="Media/测试.Sample.S01E01.mp4"] .file-check'
        ).check()
        expect(page.locator("#assistant-media")).to_contain_text("H264", timeout=20000)
        page.locator("#assistant-query").fill("测试作品")
        page.locator("#assistant-search button").click()
        expect(page.locator("#assistant-matches button")).to_have_count(1)
        assert page.evaluate("SeedAssistant.state.metadata.tmdb_id || null") is None
        page.locator("#assistant-matches button").click()
        expect(page.locator("#assistant-title")).to_have_value(
            "Test Show 2024 S01E01 360p H264 AAC"
        )
        page.locator("#assistant-title").fill("人工标题")
        page.locator("#assistant-description").fill("保留我的简介")
        page.locator("#assistant-type").select_option("movie")
        page.locator("#assistant-search button").click()
        page.locator("#assistant-matches button").click()
        expect(page.locator("#assistant-metadata")).to_contain_text("测试电影")
        assert page.locator("#assistant-title").input_value() == "人工标题"
        assert page.locator("#assistant-description").input_value() == "保留我的简介"
        assert "/3/search/tv" in tmdb_calls and "/3/search/movie" in tmdb_calls
        page.locator("#assistant-mode").select_option("single")
        page.locator("#assistant-generate").click()
        expect(page.locator("#assistant-images img")).to_be_visible(timeout=60000)
        page.get_by_role("button", name="♧ 生成图床链接", exact=True).click()
        expect(page.locator("#assistant-jobs")).to_contain_text(
            "测试图床：暂时不可用，请重试", timeout=20000
        )
        assert page.locator("#modal").is_hidden()
        page.locator("#assistant-jobs").get_by_role("button", name="重试", exact=True).click()
        expect(page.locator('#assistant-links input[aria-label="URL"]')).to_have_count(
            1, timeout=20000
        )
        assert (
            page.locator('#assistant-links input[aria-label="URL"]')
            .input_value()
            .startswith("https://images.example/")
        )
        page.locator(".assistant-panel > summary", has_text="组合执行").click()
        page.locator("#assistant-batch-upload").check()
        page.locator("#assistant-batch-torrent").check()
        page.locator("#assistant-run").click()
        page.wait_for_function(
            "SeedAssistant.state.pipeline?.steps[0]==='upload' && "
            "SeedAssistant.state.pipeline?.active",
            timeout=30000,
        )
        page.reload()
        page.locator('[data-view="publish"]').click()
        release.set()
        page.wait_for_function("SeedAssistant.state.pipeline === null", timeout=30000)
        page.locator('[data-result="torrent"]').click()
        expect(page.locator("#assistant-torrents a")).to_be_visible()
        with page.expect_download() as info:
            page.locator("#assistant-torrents a").click()
        info.value.save_as(str(ARTIFACTS / "generated.torrent"))
        decoded = bdecode((ARTIFACTS / "generated.torrent").read_bytes())
        assert decoded[b"info"][b"private"] == 1
        assert len(upload_calls) == 3
        page.locator(".assistant-panel > summary", has_text="发布信息").click()
        page.locator("#assistant-save").click()
        page.wait_for_function("Boolean(SeedAssistant.state.publishId)")
        saved = services.database.get_publish_session(
            page.evaluate("SeedAssistant.state.publishId")
        )
        assert saved["title"] == "人工标题"
        assert saved["description"] == "保留我的简介"
        page.locator(".assistant-advanced > summary", has_text="其他发布工具").click()
        page.locator("#assistant-rename").click()
        expect(page.locator(".rename-workbench")).to_be_visible()
        page.locator("#modal-close").click()
        page.locator("#assistant-legacy-open").click()
        expect(page.locator("#assistant-legacy")).to_be_visible()
        expect(page.locator("#publish-title")).to_have_value("人工标题")
        page.locator("#publish-description").fill("高级编辑器中的修改")
        page.locator("#assistant-legacy-close").click()
        page.locator('[data-result="preview"]').click()
        expect(page.locator("#assistant-description")).to_have_value("高级编辑器中的修改")
        assert not errors, errors
    finally:
        release.set()
        browser.close()
        playwright.stop()


def test_file_operations_continue_inline_across_navigation_and_reload(live_server):
    import threading

    base_url, app = live_server
    services = app.state.services
    (services.settings.download_root / "note.txt").write_text("文件操作测试", encoding="utf-8")
    (services.settings.download_root / "Destination").mkdir()
    release = threading.Event()
    original = services.files.execute_operation

    def delayed(*args, **kwargs):
        release.wait(timeout=15)
        return original(*args, **kwargs)

    services.files.execute_operation = delayed
    playwright, browser, page, errors = browser_session(base_url)
    try:
        page.locator('[data-view="files"]').click()
        page.locator('#file-browser-host .file-row[data-path="note.txt"] .file-check').check()
        page.locator("#file-browser-host .file-more-menu > summary").click()
        page.locator("#file-browser-host").get_by_role("button", name="复制", exact=True).click()
        page.locator('#file-browser-host .file-row[data-path="Destination"]').click()
        page.locator("#file-browser-host .file-more-menu > summary").click()
        page.locator("#file-browser-host").get_by_role("button", name="粘贴", exact=True).click()
        page.get_by_role("button", name="确认复制", exact=True).click()
        expect(page.locator(".file-inline-task")).to_be_visible()
        assert page.locator("#modal").is_hidden()
        page.locator('[data-view="home"]').click()
        page.reload()
        page.locator('[data-view="files"]').click()
        expect(page.locator(".file-inline-task")).to_be_visible()
        release.set()
        expect(page.locator(".file-inline-task")).to_contain_text("操作完成", timeout=20000)
        assert (
            services.settings.download_root / "Destination" / "note.txt"
        ).read_text() == "文件操作测试"
        for width in [320, 390, 768, 1024, 1440]:
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        assert not errors, errors
    finally:
        release.set()
        browser.close()
        playwright.stop()
