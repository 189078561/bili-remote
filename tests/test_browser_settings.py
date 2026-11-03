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
        assert page.locator(".settings-sidebar").is_visible()
        assert not page.locator("#settings-group-select").is_visible()

        save = page.locator("#settings-save")
        assert save.is_disabled()
        cursor = save.evaluate("element => getComputedStyle(element).cursor")
        assert cursor == "not-allowed"
        request_count = len(settings_requests)
        save.hover()
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

        page.locator("#settings-back").click()
        page.wait_for_url(f"{base_url}/")
        page.locator("#download-view").wait_for(state="visible")

        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#nav-toggle").click()
        page.locator("#settings-entry").click()
        page.wait_for_url(f"{base_url}/settings")
        page.locator("#settings-view").wait_for(state="visible")
        assert not page.locator(".settings-group-nav").is_visible()
        assert page.locator("#settings-group-select").is_visible()
        assert (
            page.locator(".settings-layout").evaluate("n => getComputedStyle(n).display") == "block"
        )
        assert page.locator("#nav-toggle").is_visible()
    finally:
        browser.close()
        playwright.stop()


def test_file_checkbox_selection_enables_available_actions(live_server) -> None:
    playwright_api = pytest.importorskip("playwright.sync_api")
    base_url, app = live_server
    app.state.services.database.update_settings(
        {
            "collage_default_layout_mode": "count",
            "collage_default_columns": "3",
            "collage_default_rows": "2",
            "collage_default_total": "7",
            "collage_default_width": "800",
            "collage_default_content_type": "custom",
            "collage_custom_skip_start": "12",
            "collage_custom_skip_end": "34",
            "screenshot_default_width": "1600",
            "screenshot_output_relative_path": "shots",
            "collage_output_relative_path": "exports/collage",
        }
    )
    try:
        playwright = playwright_api.sync_playwright().start()
        browser = playwright.chromium.launch()
    except playwright_api.Error as exc:
        pytest.skip(f"Playwright 浏览器不可用：{exc}")

    try:
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base_url)
        page.locator("#password").fill("test-password")
        page.locator("#login-form button[type=submit]").click()
        page.wait_for_url(f"{base_url}/")
        page.locator(".file-row.directory").filter(has_text="Media").wait_for(state="visible")
        page.locator(".file-row.directory").filter(has_text="Media").click()
        file_row = page.locator(".file-row.file").filter(has_text="episode01.mp4")
        file_row.wait_for(state="visible")

        def button_state(label: str) -> tuple[bool, str]:
            button = page.locator("#file-browser-host").get_by_role("button", name=label, exact=True, include_hidden=True)
            return (
                button.is_disabled(),
                button.evaluate("element => getComputedStyle(element).cursor"),
            )

        assert button_state("预览/编辑") == (True, "not-allowed")
        file_row.locator(".file-check").check()
        page.locator("#file-browser-host .selection-count").filter(has_text="已选择 1 项").wait_for(state="visible")
        for label in (
            "预览/编辑",
            "重命名",
            "复制",
            "剪切",
            "移动到",
            "删除",
            "批量重命名",
            "生成缩略图",
        ):
            assert button_state(label) == (False, "pointer"), label
        assert button_state("粘贴") == (True, "not-allowed")

        screenshot_requests: list[str] = []
        page.on(
            "request",
            lambda request: (
                screenshot_requests.append(request.url)
                if request.url.endswith("/api/files/screenshots")
                else None
            ),
        )
        captured: dict[str, object] = {}
        session_id = "a" * 32
        session_payload = {
            "id": session_id,
            "status": "completed",
            "task_id": 901,
            "output_root_key": "images",
            "output_relative_path": "shots/batch",
            "params": {
                "total": 7,
                "width": 1600,
                "content_type": "custom",
                "skip_start_seconds": 12,
                "skip_end_seconds": 34,
            },
            "videos": [
                {
                    "id": "video-1",
                    "source": {
                        "root_key": "downloads",
                        "relative_path": "Media/episode01.mp4",
                        "name": "episode01.mp4",
                    },
                    "status": "completed",
                    "media": {
                        "filename": "episode01.mp4",
                        "width": 1920,
                        "height": 1080,
                        "video_codec": "H264",
                        "audio_codec": "AAC",
                        "duration_seconds": 90,
                    },
                    "items": [
                        {
                            "id": "item-1",
                            "index": 0,
                            "status": "completed",
                            "available": True,
                            "timestamp": "00:00:10",
                            "actual_timestamp_seconds": 10,
                            "relative_path": "shots/batch/episode/00h00m10s-0001.jpg",
                            "fingerprint": "fingerprint-1",
                            "root_key": "images",
                            "upload": {"status": "unavailable"},
                        },
                        {
                            "id": "item-2",
                            "index": 1,
                            "status": "completed",
                            "available": True,
                            "timestamp": "00:00:20",
                            "actual_timestamp_seconds": 20,
                            "relative_path": "shots/batch/episode/00h00m20s-0002.jpg",
                            "fingerprint": "fingerprint-2",
                            "root_key": "images",
                            "upload": {"status": "unavailable"},
                        },
                    ],
                }
            ],
            "counts": {"videos": 1, "completed": 2, "failed": 0, "missing": 0, "total": 2},
        }

        def handle_screenshot_create(route) -> None:
            captured.update(json.loads(route.request.post_data or "{}"))
            route.fulfill(
                status=202,
                content_type="application/json",
                body=json.dumps(
                    {
                        "task": {"id": 901, "status": "queued"},
                        "created": True,
                        "session_id": session_id,
                        "output_relative_path": "shots/batch",
                    }
                ),
            )

        page.route("**/api/files/screenshots", handle_screenshot_create)
        page.route(
            f"**/api/files/screenshots/{session_id}",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(session_payload),
            ),
        )
        page.route(
            "**/api/tasks/901",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "id": 901,
                        "status": "completed",
                        "result": {
                            "status": "completed",
                            "session_id": session_id,
                            "total": 1,
                            "succeeded": 1,
                            "failed": 0,
                            "image_total": 2,
                            "image_succeeded": 2,
                            "image_failed": 0,
                            "current": "",
                        },
                    }
                ),
            ),
        )
        page.get_by_role("button", name="生成缩略图", exact=True).click()
        page.locator("#assistant-generate").wait_for()
        # Verify the retained legacy workbench independently of the new main workflow.
        page.evaluate('CollageTool.open([{relative_path:"Media/episode01.mp4",name:"episode01.mp4",kind:"video"}], "downloads")')
        page.locator(".collage-dialog").wait_for(state="visible")
        assert page.locator(".collage-source-list").filter(has_text="episode01.mp4").is_visible()
        page.locator(".collage-progress").filter(has_text="任务完成").wait_for(state="visible")
        assert "total" not in captured
        assert captured["output_mode"] == "single"
        # Server resolves the saved dimensions, skip ranges and output directory.
        assert "width" not in captured
        assert screenshot_requests
        page.locator(".screenshot-card").first.wait_for(state="visible")
        assert page.locator(".screenshot-card").count() == 2
        page.locator(".screenshot-card .screenshot-thumb").first.click()
        page.locator(".screenshot-preview-dialog").wait_for(state="visible")
        assert page.get_by_role("link", name="下载原图", exact=True).is_visible()
        page.locator("#modal-close").click()
        page.locator(".screenshot-preview-dialog").wait_for(state="hidden")
        assert not page.get_by_role("button", name="上传图床", exact=True).first.is_disabled()
        page.locator("#modal-close").click()
        page.locator('[data-view="files"]').click()

        file_row.locator(".file-check").uncheck()
        page.locator("#file-browser-host .selection-count").filter(has_text="未选择").wait_for(state="visible")
        assert button_state("预览/编辑") == (True, "not-allowed")

        file_row.locator(".file-name").click()
        page.locator("#file-browser-host .selection-count").filter(has_text="已选择 1 项").wait_for(state="visible")
        assert file_row.locator(".file-check").is_checked()
    finally:
        browser.close()
        playwright.stop()
