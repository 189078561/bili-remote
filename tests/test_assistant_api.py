from fastapi.testclient import TestClient
from test_media_tools import login, wait
from test_screenshots import make_video

from bili_remote.web import create_app


def test_search_matches_whole_directory_before_pagination_and_keeps_safety(settings):
    settings.ensure_directories()
    for i in range(35):
        (settings.download_root / f"video-{i:03}.mkv").write_bytes(b"test")
    (settings.download_root / "中文作品.MKV").write_bytes(b"test")
    with TestClient(create_app(settings)) as client:
        login(client)
        result = client.get(
            "/api/files/browse",
            params={"root_key": "downloads", "page_size": 1, "search": "VIDEO-034"},
        ).json()
        assert result["total"] == 1 and result["items"][0]["name"] == "video-034.mkv"
        result = client.get(
            "/api/files/browse", params={"root_key": "downloads", "search": "中文"}
        ).json()
        assert result["total"] == 1
        assert (
            client.get(
                "/api/files/browse", params={"root_key": "downloads", "path": "../", "search": "x"}
            ).status_code
            == 422
        )


def test_per_request_layout_and_time_do_not_modify_global_defaults(settings):
    settings.ensure_directories()
    make_video(settings.download_root / "sample.mp4")
    with TestClient(create_app(settings)) as client:
        login(client)
        services = client.app.state.services
        before = services.database.get_settings()
        payload = {
            "sources": [{"root_key": "downloads", "relative_path": "sample.mp4"}],
            "output_mode": "collage",
            "columns": 1,
            "rows": 1,
            "total": 1,
            "thumbnail_width": 480,
            "width": 480,
            "timestamps": ["00:00:01"],
            "format": "jpg",
            "style": "script",
            "auto_upload": False,
            "content_type": "custom",
            "skip_start_seconds": 0,
            "skip_end_seconds": 0,
        }
        result = client.post("/api/files/screenshots", json=payload)
        assert result.status_code == 202, result.text
        task = wait(client, result.json()["task"]["id"])
        assert task["status"] == "completed", task
        session = client.get("/api/files/screenshots/" + result.json()["session_id"]).json()
        output = next(iter(session["final_outputs"].values()))
        assert (output["columns"], output["rows"]) == (1, 1)
        assert output["sample_timestamps"] == ["00:00:01"]
        assert output["output_width"] > 480 and output["output_size_bytes"] > 0
        download = client.get(
            "/api/files/image",
            params={"root_key": "images", "path": output["output_relative_path"], "download": True},
        )
        assert download.headers["content-type"] == "image/jpeg"
        assert "attachment" in download.headers["content-disposition"]
        assert download.content[:2] == b"\xff\xd8"
        assert services.database.get_settings() == before
        invalid = client.post(
            "/api/files/screenshots", json={**payload, "total": 4, "timestamps": []}
        )
        assert invalid.status_code == 422
        assert "容量不足" in invalid.text
