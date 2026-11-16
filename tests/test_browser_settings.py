from __future__ import annotations

import json
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
    sample_directory = settings.download_root / "Media"
    sample_directory.mkdir(exist_ok=True)
    (sample_directory / "episode01.mp4").write_bytes(b"test-video")
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


def test_settings_center_desktop_and_mobile_browser_flow(live_server) -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")
    base_url, app = live_server
    try:
        playwright = playwright_api.sync_playwright().start()
        browser = playwright.chromium.launch()
    except playwright_api.Error as exc:
        pytest.skip(f"Playwright 浏览器不可用：{exc}")

    try:
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        settings_requests: list[str] = []
        page.on(
            "request",
            lambda request: (
                settings_requests.append(request.url) if "/api/settings" in request.url else None
            ),
        )

        page.goto(f"{base_url}/settings")
        page.locator("#login-form").wait_for(state="visible")
        page.locator("#password").fill("test-password")
        page.locator("#login-form button[type=submit]").click()
        page.wait_for_url(f"{base_url}/")

        page.locator('[data-view="download"]').click()
        page.locator("#download-view").wait_for(state="visible")
        page.locator("#settings-entry").click()
        page.wait_for_url(f"{base_url}/settings")
        page.locator("#settings-view").wait_for(state="visible")
        page.locator(".settings-group-button").first.wait_for(state="visible")
        assert page.locator(".settings-sidebar").is_visible()
        assert not page.locator("#settings-group-select").is_visible()

        save = page.locator("#settings-save")
        assert save.is_disabled()
        assert not page.locator("#settings-savebar").is_visible()
        request_count = len(settings_requests)
        page.wait_for_timeout(350)
        assert len(settings_requests) == request_count

        page.locator('.settings-group-button[data-group="files"]').click()
        directory_row = page.locator('[data-setting-key="directories.downloads"]')
        directory_row.get_by_role("button", name="浏览选择").click()
        page.locator("#modal .unified-file-browser").wait_for(state="visible")
        page.locator("#modal-close").click()
        page.locator("#modal").wait_for(state="hidden")

        page_size = page.locator('[data-setting-key="file_browser_page_size"] input')
        page_size.fill("140")
        page.locator("#settings-dirty-indicator").filter(has_text="项未保存").wait_for(
            state="visible"
        )
        assert not save.is_disabled()
        save.click()
        page.locator("#settings-save-status").filter(has_text="设置已保存").wait_for(
            state="visible"
        )
        assert app.state.services.database.get_settings()["file_browser_page_size"] == "140"

        page.locator('.app-tabs [data-view="download"]').click()
        page.wait_for_url(f"{base_url}/")
        page.locator("#download-view").wait_for(state="visible")

        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#settings-entry").click()
        page.wait_for_url(f"{base_url}/settings")
        page.locator("#settings-view").wait_for(state="visible")
        assert not page.locator(".settings-group-nav").is_visible()
        assert page.locator("#settings-group-select").is_visible()
        assert (
            page.locator(".settings-layout").evaluate("n => getComputedStyle(n).display") == "block"
        )
        assert page.locator("#app-navigation").is_visible()
    finally:
        browser.close()
        playwright.stop()
