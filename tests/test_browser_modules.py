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
    # Some hosts allocate ephemeral ports below 10000, including Chromium's
    # restricted ports (e.g. 3659). Keep isolated browser services above 20000.
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        if port >= 20000:
            break
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


def browser_session(base_url, *, legacy=False):
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
    try:
        page.goto(base_url)
        page.locator("#password").fill("test-password")
        page.locator("#login-form button[type=submit]").click()
        page.locator(".app-tabs [data-view=files]").wait_for()
    except Exception:
        browser.close()
        playwright.stop()
        raise
    return playwright, browser, page, errors
