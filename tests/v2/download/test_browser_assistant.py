from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from bili_remote.web import create_app


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
