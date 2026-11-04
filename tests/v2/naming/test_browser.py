from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect

from bili_remote.database import Database
from bili_remote.file_service import FileService
from bili_remote.v2.naming import NamingService, create_router
from tests.v2.naming.test_service_api import Audit, Tasks, Probe


def test_naming_browser_preview_confirm_undo_responsive_no_storage(settings):
    settings.ensure_directories()
    path = settings.download_root / "a.mkv"
    path.write_bytes(b"synthetic-browser-fixture")
    database = Database(settings.database_path)
    database.initialize()
    service = NamingService(FileService(settings, database), Probe(), owner_session="session", audit=Audit(), tasks=Tasks())
    ref = service.files.reference("downloads", "a.mkv")
    app = FastAPI()
    app.include_router(create_router(lambda request: service, authenticate=lambda request: "session", csrf=lambda request: request.headers.get("X-CSRF-Token") == "csrf"))
    static = Path(__file__).resolve().parents[3] / "bili_remote/static/v2/naming"
    @app.get("/")
    def index():
        import json
        return HTMLResponse('<!doctype html><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/naming.css"><div id="app"></div><script type="module">import {mount} from "/naming.js";window.panel=mount(document.querySelector("#app"),{selectResources:async()=>'+json.dumps([ref])+', request:(url,options={})=>fetch(url,{...options,headers:{...options.headers,"X-CSRF-Token":"csrf"}}), onTask:task=>window.lastTask=task});</script>')
    @app.get("/naming.js")
    def javascript(): return FileResponse(static / "naming.js", media_type="text/javascript")
    @app.get("/naming.css")
    def css(): return FileResponse(static / "naming.css", media_type="text/css")
    client = TestClient(app)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            def fulfill(route):
                request = route.request
                url = request.url.replace("http://127.0.0.1:18121", "")
                response = client.request(request.method, url, content=request.post_data_buffer, headers=dict(request.headers))
                route.fulfill(status=response.status_code, headers=dict(response.headers), body=response.content)
            page.route("http://127.0.0.1:18121/**", fulfill)
            page.goto("http://127.0.0.1:18121/")
            page.get_by_role("button", name="选择视频 / 目录", exact=True).click()
            page.get_by_label("自定义名称", exact=True).fill("Browser Film")
            page.get_by_label("年份", exact=True).fill("2023")
            page.get_by_role("button", name="探测与识别", exact=True).click()
            expect(page.locator(".v2-naming__files legend")).to_have_text("a.mkv")
            page.get_by_role("button", name="生成命名预览", exact=True).click()
            expect(page.locator(".v2-naming__preview")).to_contain_text("Browser.Film.2023.1080p.WEB-DL.H.265.AAC-HDVWEB.mkv")
            assert path.exists() and not service.tasks.calls
            for width in (390, 768, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            page.get_by_role("button", name="确认执行本次预览", exact=True).click()
            page.wait_for_function("window.lastTask?.status === 'queued'")
            batch = next(iter(service.executor.audit.records))
            service.executor.execute(batch)
            assert not path.exists()
            page.get_by_role("button", name="预览撤销", exact=True).click()
            expect(page.locator(".v2-naming__preview")).to_contain_text("→ a.mkv")
            page.get_by_role("button", name="确认执行本次预览", exact=True).click()
            expect(page.get_by_role("button", name="确认执行本次预览", exact=True)).to_be_disabled()
            undo = service.tasks.calls[-1]["batch_id"]
            service.executor.execute(undo)
            assert path.read_bytes() == b"synthetic-browser-fixture"
            assert page.evaluate("localStorage.length") == 0
            assert not errors, errors
        finally:
            browser.close()
