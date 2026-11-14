from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from bili_remote.downloader import DownloadFailure
from bili_remote.validation import compile_format_profile
from bili_remote.web import create_app


SEASON = {
    "season_id": 28747,
    "title": "凡人修仙传",
    "episodes": [
        {
            "ep_id": 1001,
            "aid": 111,
            "cid": 10001,
            "title": "1",
            "long_title": "入世",
            "duration": 1440000,
        },
        {
            "ep_id": 1002,
            "aid": 112,
            "cid": 10002,
            "title": "2",
            "long_title": "试炼",
            "duration": 1500000,
        },
        {
            "ep_id": 2001,
            "aid": 211,
            "cid": 20001,
            "title": "花絮1",
            "duration": 90000,
        },
    ],
}

SECTIONS = {
    "main_section": {
        "episodes": [
            {
                "id": 1001,
                "aid": 111,
                "cid": 10001,
                "title": "1",
                "long_title": "入世",
                "duration": 1440000,
            },
            {
                "id": 1002,
                "aid": 112,
                "cid": 10002,
                "title": "2",
                "long_title": "试炼",
                "duration": 1500000,
            },
        ]
    },
    "section": [
        {
            "title": "特别花絮",
            "episodes": [
                {
                    "id": 2001,
                    "aid": 211,
                    "cid": 20001,
                    "title": "花絮1",
                    "duration": 90000,
                }
            ],
        }
    ],
}


def login(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf"]


def install_fake_season(app) -> None:
    bili = app.state.services.bili

    async def season_view(*, season_id=None, ep_id=None):
        return SEASON

    async def season_sections(_season_id):
        return SECTIONS

    bili.season_view = season_view
    bili.season_sections = season_sections


def wait_for_job(client: TestClient, job_id: int, statuses: set[str]) -> dict:
    job = None
    for _ in range(80):
        jobs = client.get("/api/jobs").json()
        job = next((item for item in jobs if item["id"] == job_id), None)
        if job and job["status"] in statuses:
            return job
        time.sleep(0.1)
    assert job is not None
    return job


def test_api_requires_login_and_csrf(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/api/jobs").status_code == 401
        csrf = login(client)
        assert client.get("/api/jobs").status_code == 200
        response = client.post(
            "/api/jobs",
            json={
                "url": "https://www.bilibili.com/video/BV1",
                "format_selector": "bv*+ba/b",
                "all_parts": False,
                "output_template": "Bilibili/%(title)s.%(ext)s",
                "output_dir": "",
            },
        )
        assert response.status_code == 403
        response = client.post(
            "/api/jobs",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/video/BV1",
                "format_selector": "bv*+ba/b",
                "all_parts": False,
                "output_template": "Bilibili/%(title)s.%(ext)s",
                "output_dir": "",
                "api_mode": "web",
            },
        )
        assert response.status_code == 200, response.text


def test_resolve_and_download_job(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        resolved = client.post(
            "/api/resolve",
            headers=headers,
            json={"url": "https://www.bilibili.com/video/BV1TEST"},
        )
        assert resolved.status_code == 200, resolved.text
        data = resolved.json()
        assert data["title"] == "Test Video"
        assert data["formats"][0]["dynamic_range"] == "DV"
        assert data["api_mode"] == "web"
        assert data["episodes"] == []

        created = client.post(
            "/api/jobs",
            headers=headers,
            json={
                "url": "https://www.bilibili.com/video/BV1TEST",
                "format_selector": "bv*+ba/b",
                "all_parts": False,
                "output_template": "Bilibili/%(uploader)s/%(title)s.%(ext)s",
                "output_dir": "Inbox",
                "title": "Test Video",
            },
        )
        assert created.status_code == 200, created.text
        job = wait_for_job(client, created.json()["id"], {"completed", "failed"})
        assert job["status"] == "completed", job
        path = Path(job["result_path"])
        assert path.exists()
        assert path.parent.parent.name == "Bilibili"
        assert job["total_bytes"] == 1000
        assert job["speed"] == 0.0


def test_delete_finished_job_removes_record_and_log_only(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        created = client.post(
            "/api/jobs",
            headers=headers,
            json={
                "url": "https://www.bilibili.com/video/BV1TEST",
                "format_selector": "bv*+ba/b",
                "all_parts": False,
                "output_template": "Bilibili/%(title)s.%(ext)s",
                "output_dir": "",
            },
        )
        assert created.status_code == 200, created.text
        job = wait_for_job(client, created.json()["id"], {"completed", "failed"})
        assert job["status"] == "completed", job
        result_path = Path(job["result_path"])
        stored_job = app.state.services.database.get_job(job["id"])
        log_path = Path(stored_job["log_path"])
        assert result_path.exists()
        assert log_path.exists()

        deleted = client.delete(f"/api/jobs/{job['id']}", headers=headers)
        assert deleted.status_code == 200, deleted.text
        assert app.state.services.database.get_job(job["id"]) is None
        assert not log_path.exists()
        assert result_path.exists()


def test_delete_active_job_is_rejected(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        csrf = login(client)
        job_id = app.state.services.database.create_job(
            source_url="https://www.bilibili.com/video/BV1TEST",
            kind="manual",
            output_template="Bilibili/%(title)s.%(ext)s",
            output_dir="",
        )
        deleted = client.delete(
            f"/api/jobs/{job_id}", headers={"X-CSRF-Token": csrf}
        )
        assert deleted.status_code == 409
        assert app.state.services.database.get_job(job_id)["status"] == "queued"


def test_download_falls_back_to_sdr_when_stream_fails(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        csrf = login(client)
        created = client.post(
            "/api/jobs",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/video/BV1TEST",
                "format_selector": "FAILHDR+ba",
                "all_parts": False,
                "output_template": "Bilibili/%(title)s.%(ext)s",
                "output_dir": "",
            },
        )
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]
        job = wait_for_job(client, job_id, {"completed", "failed"})
        assert job["status"] == "completed", job
        assert job["format_selector"] == "bv*[dynamic_range=SDR]+ba/b"
        log = client.get(f"/api/jobs/{job_id}/log").json()["content"]
        assert "自动回退到 bv*[dynamic_range=SDR]+ba/b" in log


def test_aria2_failure_falls_back_to_native_downloader(
    settings, monkeypatch
) -> None:
    monkeypatch.setattr(
        "bili_remote.downloader.shutil.which", lambda _name: "/usr/bin/aria2c"
    )
    app = create_app(settings)
    with TestClient(app) as client:
        csrf = login(client)
        created = client.post(
            "/api/jobs",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/video/BV1TEST?ariafail=1",
                "format_selector": "bv*+ba/b",
                "all_parts": False,
                "output_template": "Bilibili/%(title)s.%(ext)s",
                "output_dir": "",
            },
        )
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]
        job = wait_for_job(client, job_id, {"completed", "failed"})
        assert job["status"] == "completed", job
        log = client.get(f"/api/jobs/{job_id}/log").json()["content"]
        assert "aria2c 多连接下载已启用：配置 8 连接，当前 8 连接" in log
        assert "__ARIA__using external downloader" in log
        assert "使用断点续传重试" in log
        assert "aria2c 连续中断，已清理临时分片，回退到 yt-dlp 原生下载" in log


def test_aria2_resumes_after_connection_drop(settings, monkeypatch) -> None:
    monkeypatch.setattr(
        "bili_remote.downloader.shutil.which", lambda _name: "/usr/bin/aria2c"
    )
    app = create_app(settings)
    with TestClient(app) as client:
        csrf = login(client)
        created = client.post(
            "/api/jobs",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/video/BV1TEST?ariaresume=1",
                "format_selector": "bv*+ba/b",
                "all_parts": False,
                "output_template": "Bilibili/%(title)s.%(ext)s",
                "output_dir": "",
            },
        )
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]
        job = wait_for_job(client, job_id, {"completed", "failed"})
        assert job["status"] == "completed", job
        log = client.get(f"/api/jobs/{job_id}/log").json()["content"]
        assert "使用断点续传重试" in log
        assert "回退到 yt-dlp 原生下载" not in log


def test_aria2_downgrades_connections_before_native_fallback(
    settings, monkeypatch
) -> None:
    monkeypatch.setattr(
        "bili_remote.downloader.shutil.which", lambda _name: "/usr/bin/aria2c"
    )
    app = create_app(settings)
    with TestClient(app) as client:
        csrf = login(client)
        created = client.post(
            "/api/jobs",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/video/BV1TEST?ariadownshift=1",
                "format_selector": "bv*+ba/b",
                "all_parts": True,
                "output_template": "Bilibili/%(title)s.%(ext)s",
                "output_dir": "",
            },
        )
        assert created.status_code == 200, created.text
        job = wait_for_job(client, created.json()["id"], {"completed", "failed"})
        assert job["status"] == "completed", job
        log = client.get(f"/api/jobs/{job['id']}/log").json()["content"]
        assert "CDN 限流时按 x8 → x6 → x4 自动降档" in log
        assert "HTTP Error 567" in log
        assert "HTTP Error 429" in log
        assert "via aria2c x8" in log
        assert "via aria2c x6" in log
        assert "via aria2c x4" in log
        assert "回退到 yt-dlp 原生下载" not in log


def test_aria2_partial_files_are_removed_before_native_fallback(
    settings, monkeypatch
) -> None:
    monkeypatch.setattr(
        "bili_remote.downloader.shutil.which", lambda _name: "/usr/bin/aria2c"
    )
    app = create_app(settings)
    with TestClient(app) as client:
        csrf = login(client)
        created = client.post(
            "/api/jobs",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/video/BV1TEST?ariapartial=1",
                "format_selector": "bv*+ba/b",
                "all_parts": False,
                "output_template": "Bilibili/%(title)s.%(ext)s",
                "output_dir": "",
            },
        )
        assert created.status_code == 200, created.text
        job = wait_for_job(client, created.json()["id"], {"completed", "failed"})
        assert job["status"] == "completed", job
        result_path = Path(job["result_path"])
        assert result_path.exists()
        assert not Path(str(result_path) + ".part").exists()
        assert not Path(str(result_path) + ".part.aria2").exists()


def test_bangumi_ss_returns_full_episode_list_without_default(settings) -> None:
    app = create_app(settings)
    install_fake_season(app)
    with TestClient(app) as client:
        csrf = login(client)
        resolved = client.post(
            "/api/resolve",
            headers={"X-CSRF-Token": csrf},
            json={"url": "https://www.bilibili.com/bangumi/play/ss28747"},
        )
        assert resolved.status_code == 200, resolved.text
        data = resolved.json()
        assert data["title"] == "凡人修仙传"
        assert data["is_playlist"] is True
        assert data["playlist_count"] == 2
        assert data["default_episode_id"] is None
        assert [episode["id"] for episode in data["episodes"]] == [
            "1001",
            "1002",
            "2001",
        ]
        assert data["episodes"][2]["group"] == "特别花絮"
        assert data["formats"][0]["dynamic_range"] == "DV"


def test_bangumi_ep_defaults_to_current_episode(settings) -> None:
    app = create_app(settings)
    install_fake_season(app)
    with TestClient(app) as client:
        csrf = login(client)
        resolved = client.post(
            "/api/resolve",
            headers={"X-CSRF-Token": csrf},
            json={"url": "https://www.bilibili.com/bangumi/play/ep1002"},
        )
        assert resolved.status_code == 200, resolved.text
        data = resolved.json()
        assert data["default_episode_id"] == "1002"
        current = next(item for item in data["episodes"] if item["id"] == "1002")
        assert current["group_index"] == 2
        assert current["title"] == "2 试炼"


def test_bangumi_episode_list_survives_format_probe_failure(settings) -> None:
    app = create_app(settings)
    install_fake_season(app)

    async def fail_probe(*_args, **_kwargs):
        raise DownloadFailure("解析超时，请稍后重试")

    app.state.services.runner._resolve_payload = fail_probe
    with TestClient(app) as client:
        csrf = login(client)
        resolved = client.post(
            "/api/resolve",
            headers={"X-CSRF-Token": csrf},
            json={"url": "https://www.bilibili.com/bangumi/play/ss28747"},
        )
        assert resolved.status_code == 200, resolved.text
        data = resolved.json()
        assert len(data["episodes"]) == 3
        assert data["formats"] == []
        assert "格式预解析失败" in data["warning"]


def test_batch_jobs_render_episode_templates(settings) -> None:
    app = create_app(settings)
    install_fake_season(app)
    with TestClient(app) as client:
        csrf = login(client)
        response = client.post(
            "/api/jobs/batch",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/bangumi/play/ss28747",
                "episode_ids": ["1002", "2001"],
                "api_mode": "web",
                "format_selector": "bv*+ba/b",
                "output_template": (
                    "Bilibili/%(playlist_title)s/S%(playlist_index)02d - "
                    "%(title)s [%(id)s].%(ext)s"
                ),
                "output_dir": "Anime",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["count"] == 2
        ids = response.json()["ids"]
        database = app.state.services.database
        first = database.get_job(ids[0])
        second = database.get_job(ids[1])
        assert first["title"] == "2 试炼"
        assert first["output_template"] == (
            "Bilibili/凡人修仙传/S02 - 2 试炼 [1002].%(ext)s"
        )
        assert first["source_url"].endswith("/ep1002")
        assert second["title"] == "花絮1"
        assert second["output_template"] == (
            "Bilibili/凡人修仙传/S01 - 花絮1 [2001].%(ext)s"
        )
        assert first["api_mode"] == "web"


def test_batch_jobs_default_selector_without_format_profile(settings) -> None:
    app = create_app(settings)
    install_fake_season(app)
    with TestClient(app) as client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        multiple = client.post(
            "/api/jobs/batch",
            headers=headers,
            json={
                "url": "https://www.bilibili.com/bangumi/play/ss28747",
                "episode_ids": ["1001", "1002"],
                "format_selector": "1001+ba",
                "output_template": "%(title)s.%(ext)s",
                "output_dir": "",
            },
        )
        assert multiple.status_code == 200, multiple.text
        for job_id in multiple.json()["ids"]:
            assert app.state.services.database.get_job(job_id)["format_selector"] == (
                "bv*+ba/b"
            )

        single = client.post(
            "/api/jobs/batch",
            headers=headers,
            json={
                "url": "https://www.bilibili.com/bangumi/play/ss28747",
                "episode_ids": ["1002"],
                "format_selector": "1002+ba",
                "output_template": "%(title)s.%(ext)s",
                "output_dir": "",
            },
        )
        assert single.status_code == 200, single.text
        assert app.state.services.database.get_job(
            single.json()["ids"][0]
        )["format_selector"] == "1002+ba"


def test_batch_jobs_apply_format_profile_to_every_episode(settings) -> None:
    app = create_app(settings)
    install_fake_season(app)
    with TestClient(app) as client:
        csrf = login(client)
        response = client.post(
            "/api/jobs/batch",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/bangumi/play/ss28747",
                "episode_ids": ["1001", "1002"],
                "format_profile": {
                    "height": 1080,
                    "fps_high": True,
                    "vcodec": "hevc",
                    "dynamic_range": "HDR10",
                },
                "format_selector": "1001+ba",
                "output_template": "%(title)s.%(ext)s",
                "output_dir": "",
            },
        )
        assert response.status_code == 200, response.text
        expected = compile_format_profile(
            height=1080,
            fps_high=True,
            vcodec="hevc",
            dynamic_range="HDR10",
        )
        for job_id in response.json()["ids"]:
            assert app.state.services.database.get_job(job_id)["format_selector"] == (
                expected
            )


def test_episode_formats_endpoint_returns_selected_episode_formats(settings) -> None:
    app = create_app(settings)
    install_fake_season(app)
    with TestClient(app) as client:
        csrf = login(client)
        response = client.post(
            "/api/episodes/formats",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/bangumi/play/ep1002",
                "api_mode": "web",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["api_mode"] == "web"
        assert data["webpage_url"].endswith("/ep1002")
        assert [item["format_id"] for item in data["formats"]] == ["126", "30280"]


def test_batch_episode_formats_reports_profile_coverage(settings) -> None:
    app = create_app(settings)
    install_fake_season(app)
    hd = {
        "format_id": "30112",
        "width": 1920,
        "height": 1080,
        "fps": 30,
        "vcodec": "avc1.640028",
        "acodec": "none",
        "dynamic_range": "SDR",
        "tbr": 4000,
    }
    uhd = {
        "format_id": "30126",
        "width": 3840,
        "height": 2160,
        "fps": 60,
        "vcodec": "hev1.2.4.L153",
        "acodec": "none",
        "dynamic_range": "HDR10",
        "tbr": 18000,
    }

    async def episode_formats(url, api_mode="web"):
        formats = [hd.copy()]
        if url.endswith("/ep1001"):
            formats.insert(0, uhd.copy())
        return {"formats": formats}

    app.state.services.runner.episode_formats = episode_formats
    with TestClient(app) as client:
        csrf = login(client)
        response = client.post(
            "/api/episodes/formats/batch",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/bangumi/play/ss28747",
                "episode_ids": ["1001", "1002"],
                "api_mode": "web",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["checked"] == 2
        assert data["selected_count"] == 2
        assert data["sampled"] is False
        assert data["failed"] == 0
        assert [
            (
                item["height"],
                item["vcodec"],
                item["dynamic_range"],
                item["coverage"],
            )
            for item in data["profiles"]
        ] == [
            (2160, "hevc", "HDR10", 1),
            (1080, "avc", "SDR", 2),
        ]


def test_tv_episode_formats_endpoint(settings) -> None:
    app = create_app(settings)
    install_fake_season(app)

    async def tv_playinfo(*, aid, cid, ep_id):
        return {
            "video": [
                {
                    "id": 112,
                    "base_url": "https://example.com/video.m4s",
                    "codecs": "avc1.640028",
                    "width": 1920,
                    "height": 1080,
                    "bandwidth": 1000000,
                }
            ],
            "audio": [
                {
                    "id": 30280,
                    "base_url": "https://example.com/audio.m4s",
                    "codecs": "mp4a.40.2",
                    "bandwidth": 128000,
                }
            ],
            "support_formats": [{"quality": 112, "display_desc": "1080P"}],
        }

    app.state.services.bili.tv_bangumi_playinfo = tv_playinfo
    with TestClient(app) as client:
        csrf = login(client)
        app.state.services.bili.save_tv_login(
            "tv-token", None, {"mid": 1, "uname": "TV Tester"}
        )
        response = client.post(
            "/api/episodes/formats",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/bangumi/play/ep1002",
                "api_mode": "tv",
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["api_mode"] == "tv"
        assert [item["format_id"] for item in data["formats"]] == [
            "tv112-avc",
            "tva30280",
        ]


def test_parse_history_restore_and_delete(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        url = "https://www.bilibili.com/video/BV1TEST"
        for _ in range(2):
            response = client.post("/api/resolve", headers=headers, json={"url": url})
            assert response.status_code == 200, response.text
        records = client.get("/api/history").json()
        assert len(records) == 1
        assert records[0]["parse_count"] == 2
        detail = client.get(f"/api/history/{records[0]['id']}")
        assert detail.status_code == 200
        assert detail.json()["formats"][0]["format_id"] == "126"
        assert detail.json()["source_url"] == url
        deleted = client.delete(
            f"/api/history/{records[0]['id']}", headers=headers
        )
        assert deleted.status_code == 200
        assert client.get("/api/history").json() == []


def test_tv_resolve_requires_login(settings) -> None:
    app = create_app(settings)
    install_fake_season(app)
    with TestClient(app) as client:
        csrf = login(client)
        response = client.post(
            "/api/resolve",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/bangumi/play/ep1002",
                "api_mode": "tv",
            },
        )
        assert response.status_code == 422
        assert "TV 扫码登录" in response.json()["detail"]


def test_tv_job_requires_login(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        csrf = login(client)
        created = client.post(
            "/api/jobs",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/bangumi/play/ep1001",
                "api_mode": "tv",
                "format_selector": "bv*+ba/b",
                "output_template": "%(title)s.%(ext)s",
                "output_dir": "",
            },
        )
        assert created.status_code == 200, created.text
        job = wait_for_job(client, created.json()["id"], {"auth_required", "failed"})
        assert job["status"] == "auth_required"
        assert "TV 扫码登录" in job["error"]


def test_tv_download_uses_info_json(settings) -> None:
    app = create_app(settings)
    install_fake_season(app)
    calls: list[dict] = []

    async def tv_playinfo(*, aid, cid, ep_id):
        calls.append({"aid": aid, "cid": cid, "ep_id": ep_id})
        return {
            "video": [
                {
                    "id": 112,
                    "base_url": "https://example.com/video.m4s",
                    "codecs": "avc1.640028",
                    "width": 1920,
                    "height": 1080,
                    "frame_rate": "30/1",
                    "bandwidth": 1000000,
                    "size": 1000,
                }
            ],
            "audio": [
                {
                    "id": 30280,
                    "base_url": "https://example.com/audio.m4s",
                    "codecs": "mp4a.40.2",
                    "bandwidth": 128000,
                    "size": 200,
                }
            ],
            "support_formats": [{"quality": 112, "display_desc": "1080P"}],
        }

    app.state.services.bili.tv_bangumi_playinfo = tv_playinfo
    with TestClient(app) as client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        app.state.services.bili.save_tv_login(
            "tv-token", None, {"mid": 1, "uname": "TV Tester"}
        )
        created = client.post(
            "/api/jobs",
            headers=headers,
            json={
                "url": "https://www.bilibili.com/bangumi/play/ep1001",
                "api_mode": "tv",
                "format_selector": "bv*+ba/b",
                "output_template": "Bilibili/%(title)s.%(ext)s",
                "output_dir": "",
                "title": "1 入世",
            },
        )
        assert created.status_code == 200, created.text
        job_id = created.json()["id"]
        job = wait_for_job(client, job_id, {"completed", "failed", "auth_required"})
        assert job["status"] == "completed", job
        assert Path(job["result_path"]).exists()
        log = client.get(f"/api/jobs/{job_id}/log").json()["content"]
        assert "__INFO__Loaded TV info JSON" in log
        assert calls and calls[0]["ep_id"] == "1001"


def test_tv_download_maps_cdn_403_to_clear_error(settings) -> None:
    app = create_app(settings)
    install_fake_season(app)

    async def tv_playinfo(*, aid, cid, ep_id):
        return {
            "video": [
                {
                    "id": 112,
                    "base_url": "https://example.com/video.m4s",
                    "codecs": "avc1.640028",
                    "width": 1920,
                    "height": 1080,
                    "bandwidth": 1000000,
                }
            ],
            "audio": [
                {
                    "id": 30280,
                    "base_url": "https://example.com/audio.m4s",
                    "codecs": "mp4a.40.2",
                    "bandwidth": 128000,
                }
            ],
        }

    app.state.services.bili.tv_bangumi_playinfo = tv_playinfo
    with TestClient(app) as client:
        csrf = login(client)
        app.state.services.bili.save_tv_login(
            "tv-token", None, {"mid": 1, "uname": "TV Tester"}
        )
        created = client.post(
            "/api/jobs",
            headers={"X-CSRF-Token": csrf},
            json={
                "url": "https://www.bilibili.com/bangumi/play/ep1001",
                "api_mode": "tv",
                "format_selector": "bv*+ba/b",
                "output_template": "Bilibili/%(title)s.%(ext)s",
                "output_dir": "",
                "title": "__TV403__",
            },
        )
        assert created.status_code == 200, created.text
        job = wait_for_job(client, created.json()["id"], {"failed"})
        assert job["status"] == "failed"
        assert "TV 片源 CDN 返回 403" in job["error"]
        assert "Web 线路" in job["error"]


def test_auth_error_moves_job_to_auth_required(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        csrf = login(client)
        headers = {"X-CSRF-Token": csrf}
        created = client.post(
            "/api/jobs",
            headers=headers,
            json={
                "url": "https://www.bilibili.com/video/authfail",
                "format_selector": "bv*+ba/b",
                "all_parts": False,
                "output_template": "%(title)s.%(ext)s",
                "output_dir": "",
            },
        )
        job = wait_for_job(client, created.json()["id"], {"auth_required"})
        assert job["status"] == "auth_required"
