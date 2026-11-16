from playwright.sync_api import expect
from test_browser_modules import browser_session, live_server  # noqa: F401


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
