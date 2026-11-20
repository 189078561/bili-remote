import json
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from bili_remote.web import create_app
from test_browser_modules import live_server  # noqa: F401


EPISODE = {
    "url": "https://www.bilibili.com/bangumi/play/ep21",
    "number": 1, "title": "第一集", "duration": 1200,
    "video": [{"url": "https://edge.bilivideo.com/upgcxcode/video.m4s?a=example",
               "quality": 80, "codec": "avc1.640028", "bandwidth": 800000, "height": 1080}],
    "audio": [{"url": "https://edge.bilivideo.com/upgcxcode/audio.m4s?a=example",
               "quality": 30280, "codec": "mp4a.40.2", "bandwidth": 120000}],
}


def test_handoff_enforces_auth_and_only_queues_exact_tracks(settings, monkeypatch):
    app = create_app(settings)
    service = app.state.services
    monkeypatch.setattr(service.manager, "start", lambda: None)
    with TestClient(app) as client:
        base = "/api/parse-assistant"
        assert client.get(base).status_code == 401
        login = client.post("/api/auth/login", json={"username": settings.admin_username, "password": settings.admin_password})
        csrf = {"X-CSRF-Token": login.json()["csrf"]}
        archive = client.get(base + "/package")
        assert archive.status_code == 200
        with ZipFile(BytesIO(archive.content)) as package:
            assert "manifest.json" in package.namelist()
            assert "project.js" not in package.namelist()
        catalogue = {"title": "示例作品", "episodes": [EPISODE]}
        assert client.post(base + "/imports", json=catalogue).status_code == 403
        response = client.post(base + "/imports", json=catalogue, headers=csrf)
        assert response.status_code == 200
        assert service.database.list_jobs() == []
        path = base + "/imports/" + response.json()["id"]
        assert "bilivideo" not in client.get(path).text
        assert client.get(base).json()["imports"][0]["count"] == 1

        picks = {"choices": [{"episode": 0, "video": 0, "audio": 0}]}
        result = client.post(path + "/jobs", json=picks, headers=csrf)
        job_id = result.json()["ids"][0]
        assert service.database.get_job(job_id)["format_selector"] == "(brvideo+braudio)"
        plan = service.database.get_download_naming(job_id)["plan"]
        assert "bilivideo" not in plan["assistant_playback"]
        assert client.post(path + "/jobs", json=picks, headers=csrf).json()["ids"] == []
        assert client.post(base + "/jobs", json={**picks, "catalogue": catalogue}, headers=csrf).status_code == 200
        malformed = {"title": "bad", "episodes": [{**EPISODE, "video": [{**EPISODE["video"][0], "url": "http://127.0.0.1/a.m4s"}]}]}
        rejected = client.post(base + "/imports", json=malformed, headers=csrf)
        assert rejected.status_code == 422
        assert "127.0.0.1" not in rejected.text


def test_movie_tv_push_uses_movie_title_and_locks_locally_resolved_audio(settings, monkeypatch):
    import json

    from bili_remote.bili import BiliTvClient
    from bili_remote.v2.download.naming import initial_filename

    app = create_app(settings)
    service = app.state.services
    monkeypatch.setattr(service.manager, "start", lambda: None)
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": settings.admin_username, "password": settings.admin_password})
        csrf = {"X-CSRF-Token": login.json()["csrf"]}
        catalogue = {"source": "tv", "movie": True, "title": "四海", "episodes": [{**EPISODE, "title": "正片", "number": None}]}
        choices = [{"episode": 0, "video": None, "audio": 0}]
        imported = client.post("/api/parse-assistant/imports", json=catalogue, headers=csrf)
        assert imported.status_code == 200
        path = "/api/parse-assistant/imports/" + imported.json()["id"]
        assert client.get(path).json()["source"] == "tv"
        response = client.post(path + "/jobs", json={"choices": choices}, headers=csrf)
        assert response.status_code == 200
        job_id = response.json()["ids"][0]
        job = service.database.get_job(job_id)
        assert job["title"] == "四海"
        assert job["api_mode"] == "tv"
        assert job["format_selector"] == "(braudio)"
        plan = service.database.get_download_naming(job_id)["plan"]
        playback = json.loads(service.secrets.decrypt_json(plan["assistant_playback"]))
        assert playback["info"]["title"] == "四海"
        assert playback["info"]["formats"][0]["http_headers"] == BiliTvClient.stream_headers
        assert "Referer" not in BiliTvClient.stream_headers
        assert [track["url"] for track in playback["info"]["formats"]] == [EPISODE["audio"][0]["url"]]
        assert initial_filename(plan, "m4a") == "四海.m4a"
        assert initial_filename(plan, collision=2) == "四海 (2).mp4"
        assert client.post(path + "/jobs", json={"choices": choices}, headers=csrf).json()["ids"] == []

        # Previously queued jobs can still display their saved catalogue title.
        service.database.update_job(job_id, title="正片")
        assert client.get("/api/jobs").json()[0]["title"] == "四海"

        # Session credentials cannot be carried in a media URL.
        episode = {**EPISODE, "audio": [{**EPISODE["audio"][0], "url": EPISODE["audio"][0]["url"] + "&access_key=redacted"}]}
        assert client.post("/api/parse-assistant/imports", json={**catalogue, "episodes": [episode]}, headers=csrf).status_code == 422


def test_retry_saved_tv_push_removes_web_referer(settings, monkeypatch, tmp_path):
    import asyncio
    import json
    import time
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from bili_remote.bili import BiliTvClient
    from bili_remote.downloader import YtDlpRunner
    from bili_remote.security import SecretBox

    settings.ensure_directories()
    secret = SecretBox(settings.app_secret)
    runner = YtDlpRunner(settings, SimpleNamespace(secret_box=secret))
    saved = {
        "expires": time.time() + 60,
        "info": {
            "formats": [
                {"format_id": key, "url": EPISODE[kind][0]["url"],
                 "http_headers": {"Referer": "https://www.bilibili.com/", "User-Agent": "older UA"}}
                for key, kind in (("brvideo", "video"), ("braudio", "audio"))
            ],
        },
    }

    async def start(*args, **kwargs):
        updated = json.loads(Path(args[args.index("--load-info-json") + 1]).read_text())
        assert len(updated["formats"]) == 2
        for old, track in zip(saved["info"]["formats"], updated["formats"]):
            assert track["url"] == old["url"]
            assert track["format_id"] == old["format_id"]
            assert track["http_headers"] == BiliTvClient.stream_headers
            assert "Referer" not in track["http_headers"]
        return SimpleNamespace(stdout=None, stderr=None, wait=AsyncMock(return_value=0))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    job = {"id": 7, "api_mode": "tv", "source_url": EPISODE["url"],
           "format_selector": "(brvideo+braudio)", "output_template": "%(id)s.%(ext)s",
           "_assistant_playback": secret.encrypt_json(json.dumps(saved))}
    asyncio.run(runner.download(job, tmp_path, tmp_path / "job.log", AsyncMock()))
    assert not list(settings.runtime_dir.glob("browser-info-*.json"))


def test_assistant_handoff_reuses_native_picker_and_locked_tracks(live_server):
    from playwright.sync_api import expect
    from test_browser_modules import browser_session

    base, app = live_server
    playwright, browser, page, errors = browser_session(base)
    try:
        upload = page.evaluate("""async catalogue => {
            const headers = {
                "Content-Type": "application/json",
                "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]').content
            };
            const response = await fetch("/api/parse-assistant/imports", {
                method: "POST", headers, body: JSON.stringify(catalogue)
            });
            return await response.json();
        }""", {
            "title": "测试多 P", "source": "tv", "active_episode": 1,
            "episodes": [
                {**EPISODE, "url": f"https://www.bilibili.com/video/BV1abc123456?p={number}",
                 "number": number, "title": f"片段 {number}",
                 "video": [{**EPISODE["video"][0], "quality": 120,
                            "width": 3840, "height": 1616, "fps": 25.000133940530404}]}
                for number in (1, 2, 3)
            ],
        })
        path = f"/api/parse-assistant/imports/{upload['id']}/jobs"
        probes, posted = [], []
        page.route("**/api/episodes/formats**", lambda route: (probes.append(route.request.url), route.abort()))
        page.route(f"**{path}", lambda route: (
            posted.append(route.request.post_data_json), route.fulfill(json={"ids": [7]})
        ))
        page.goto(f"{base}/?assistant_import={upload['id']}")
        expect(page.locator("[data-resolution]")).to_be_visible()
        expect(page.locator("[data-summary-source]")).to_have_text("云视听小电视")
        expect(page.locator("[data-batch-format] option").first).to_contain_text("2160p · 25 fps")
        expect(page.locator("[data-episode-list] button")).to_have_count(3)
        expect(page.get_by_role("button", name="2 · 片段 2")).to_have_attribute("aria-pressed", "true")
        page.locator("[data-naming-name]").fill("山间来信")
        page.locator("[data-naming-number]").fill("7")
        expect(page.locator("[data-naming-preview]")).to_contain_text("山间来信.P07.mp4")
        page.locator("[data-download-form] button[type=submit]").click()
        expect(page.locator("[data-notice]")).to_contain_text("已加入下载队列")
        assert posted[0]["choices"] == [{"episode": 1, "video": 0, "audio": 0}]
        assert posted[0]["naming"]["episode_numbers"] == {"1": 7}
        expect(page.locator("[data-download-form] button[type=submit]")).to_be_disabled()
        assert not probes
        assert not errors
        assert not app.state.services.database.list_jobs()
    finally:
        browser.close()
        playwright.stop()


def test_assistant_popup_keeps_source_switch_and_settings(live_server):
    from playwright.sync_api import expect
    from test_browser_modules import browser_session

    base, _ = live_server
    playwright, browser, page, errors = browser_session(base)
    try:
        page.add_init_script("""(() => {
            const url = PROJECT_URL;
            const stored = {projects: [{url, name: '测试项目'}], target: url, order: 'desc', rows: 4};
            window.chrome = {
                storage: {local: {
                    get: async keys => Array.isArray(keys) ? {} : {...keys, ...stored},
                    set: async values => Object.assign(stored, values)
                }},
                tabs: {query: async () => [{id: 1, url: 'https://www.bilibili.com/video/BV1abc123456'}]},
                scripting: {executeScript: async ({args}) => {
                    if (args[1].includes('/view')) return [{result: {body: {code: 0, data: {
                        title: '合成视频', bvid: 'BV1abc123456', aid: 1, pages: [{page: 1, cid: 10, duration: 60}]
                    }}}}];
                    return [{result: {body: {code: 0, data: {
                        dash: {
                            video: [{baseUrl: 'https://edge.bilivideo.com/v.m4s', id: 120,
                                     codecs: 'avc1.640028', width: 3840, height: 1616,
                                     frameRate: '250001/10000', bandwidth: 800000}],
                            audio: [{baseUrl: 'https://edge.bilivideo.com/a.m4s', id: 30280,
                                     codecs: 'mp4a.40.2', bandwidth: 120000}]
                        }
                    }}}}];
                }}
            };
        })()""".replace("PROJECT_URL", json.dumps(base)))
        page.set_viewport_size({"width": 320, "height": 610})
        page.goto(f"{base}/static/v2/download/assistant/popup.html")
        expect(page.get_by_role("button", name="Web 端")).to_be_enabled()
        expect(page.get_by_label("统一画质").locator("option").first).to_contain_text("2160p · 25 fps")
        page.get_by_role("button", name="设置", exact=True).click()
        expect(page.get_by_label("项目名称")).to_have_value("测试项目")
        expect(page.get_by_label("列表可见行数")).to_have_value("4")
        page.get_by_role("button", name="返回选集").click()
        page.get_by_role("button", name="TV 端").click()
        expect(page.get_by_role("button", name="TV 端")).to_have_attribute("aria-pressed", "true")
        expect(page.get_by_role("button", name="生成 TV 登录二维码")).to_be_visible()
        expect(page.get_by_label("统一画质")).not_to_contain_text("2160p")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors
    finally:
        browser.close()
        playwright.stop()


def test_assistant_smart_batch_recovers_remaster_numbers_from_older_extension(settings, monkeypatch):
    from bili_remote.v2.download.naming import initial_filename

    app = create_app(settings)
    services = app.state.services
    monkeypatch.setattr(services.manager, "start", lambda: None)
    catalogue = {"source": "tv", "title": "镇魂街 第二季", "episodes": [
        {**EPISODE, "number": None, "title": "重置版：第10话 浮生若梦"},
        {**EPISODE, "url": "https://www.bilibili.com/bangumi/play/ep22", "title": "1 故人归来"},
    ]}
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": settings.admin_username, "password": settings.admin_password})
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        result = client.post("/api/parse-assistant/imports", json=catalogue)
        assert result.status_code == 200
        path = "/api/parse-assistant/imports/" + result.json()["id"]
        metadata = client.get(path).json()
        assert metadata["episodes"][0]["number"] == 10

        picks = [{"episode": index, "video": 0, "audio": 0} for index in (0, 1)]
        preview = client.post("/api/download/naming/preview", json={
            "title": metadata["title"], "options": {}, "items": list(metadata["naming_items"].values()),
        }).json()
        assert preview["items"] == ["镇魂街.S02E10.mp4", "镇魂街.S02E01.mp4"]
        result = client.post(path + "/jobs", json={"choices": picks})
        assert result.status_code == 200, result.text
        plans = [services.database.get_download_naming(job)["plan"] for job in result.json()["ids"]]
        assert [initial_filename(plan) for plan in plans] == preview["items"]
