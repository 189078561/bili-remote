from __future__ import annotations

import shutil
import subprocess
import time

import pytest
from fastapi.testclient import TestClient

from bili_remote.web import create_app


def make_video(path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("测试环境没有 ffmpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=10:duration=3",
            "-c:v",
            "mpeg4",
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip("无法生成测试视频")


def login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]


def wait_task(client: TestClient, task_id: int) -> dict:
    for _ in range(600):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in {"completed", "failed", "cancelled"}:
            return task
        time.sleep(0.03)
    raise AssertionError("截图任务未在预期时间内完成")


def screenshot_payload(path: str = "episode.mp4", **overrides) -> dict:
    payload = {
        "sources": [{"root_key": "downloads", "relative_path": path}],
        "format": "jpg", "style": "plain",
        "total": 3,
        "width": 240,
        "content_type": "custom",
        "skip_start_seconds": 0,
        "skip_end_seconds": 0,
        "output_relative_path": "",
    }
    payload.update(overrides)
    return payload


def test_screenshot_api_creates_manageable_persistent_session(settings) -> None:
    settings.ensure_directories()
    make_video(settings.download_root / "episode.mp4")
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/files/screenshots/status").status_code == 401
        login(client)
        status = client.get("/api/files/screenshots/status")
        assert status.status_code == 200
        assert status.json()["available"] is True
        assert status.json()["limits"]["items_per_video"] == 120

        created = client.post(
            "/api/files/screenshots", json=screenshot_payload()
        )
        assert created.status_code == 202, created.text
        created_data = created.json()
        session_id = created_data["session_id"]
        task = wait_task(client, created_data["task"]["id"])
        assert task["status"] == "completed", task
        assert task["result"]["image_succeeded"] == 3

        session = client.get(f"/api/files/screenshots/{session_id}")
        assert session.status_code == 200, session.text
        data = session.json()
        assert data["counts"]["completed"] == 3
        assert data["videos"][0]["media"]["width"] == 320
        items = data["videos"][0]["items"]
        assert [item["index"] for item in items] == [0, 1, 2]
        assert all(item["upload"]["status"] == "unavailable" for item in items)
        image = client.get(
            "/api/files/image",
            params={"root_key": "images", "path": items[0]["relative_path"]},
        )
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/jpeg"
        downloaded = client.get(
            "/api/files/image",
            params={
                "root_key": "images",
                "path": items[0]["relative_path"],
                "download": "true",
            },
        )
        assert downloaded.status_code == 200
        assert downloaded.content == image.content
        assert downloaded.headers["content-disposition"].startswith("attachment;")
        assert items[0]["relative_path"].rsplit("/", 1)[-1] in downloaded.headers[
            "content-disposition"
        ]

        recaptured = client.post(
            f"/api/files/screenshots/{session_id}/items/{items[0]['id']}/recapture",
            json={"timestamp": "00:00:02"},
        )
        assert recaptured.status_code == 200, recaptured.text
        assert recaptured.json()["videos"][0]["items"][0]["timestamp"] == "00:00:02"

        reverse_ids = [item["id"] for item in reversed(items)]
        ordered = client.put(
            f"/api/files/screenshots/{session_id}/items/order",
            json={"orders": {data["videos"][0]["id"]: reverse_ids}},
        )
        assert ordered.status_code == 200, ordered.text
        assert [item["id"] for item in ordered.json()["videos"][0]["items"]] == reverse_ids

        sessions = client.get("/api/files/screenshots/sessions").json()["sessions"]
        assert sessions[0]["id"] == session_id
        assert sessions[0]["image_count"] == 3

    restarted = create_app(settings)
    with TestClient(restarted) as client:
        login(client)
        persisted = client.get(f"/api/files/screenshots/{session_id}")
        assert persisted.status_code == 200
        assert persisted.json()["counts"]["completed"] == 3


def test_screenshot_api_accepts_explicit_timestamps(settings) -> None:
    settings.ensure_directories()
    make_video(settings.download_root / "episode.mp4")
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        created = client.post(
            "/api/files/screenshots",
            json=screenshot_payload(timestamps=["00:00:02", "00:00:01"]),
        )
        assert created.status_code == 202, created.text
        task = wait_task(client, created.json()["task"]["id"])
        assert task["status"] == "completed", task
        session = client.get(
            f"/api/files/screenshots/{created.json()['session_id']}"
        ).json()
        assert session["params"]["sampling_mode"] == "timestamps"
        assert [item["timestamp"] for item in session["videos"][0]["items"]] == [
            "00:00:01",
            "00:00:02",
        ]

        duplicate = client.post(
            "/api/files/screenshots",
            json=screenshot_payload(timestamps=["00:00:01", "00:00:01"]),
        )
        assert duplicate.status_code == 422
        assert "重复" in duplicate.text


def test_screenshot_api_builds_collage_from_saved_images_and_checks_conflicts(settings) -> None:
    settings.ensure_directories()
    make_video(settings.download_root / "episode.mp4")
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        created = client.post(
            "/api/files/screenshots", json=screenshot_payload()
        ).json()
        wait_task(client, created["task"]["id"])
        session = client.get(
            f"/api/files/screenshots/{created['session_id']}"
        ).json()
        video = session["videos"][0]
        selections = [
            {
                "video_id": video["id"],
                "item_ids": [item["id"] for item in video["items"][:2]],
            }
        ]
        body = {
            "selections": selections,
            "layout_mode": "auto",
            "columns": 2,
            "thumbnail_width": 240,
            "gap": 7,
            "output_relative_path": "",
            "overwrite": False,
        }
        collage = client.post(
            f"/api/files/screenshots/{created['session_id']}/collage",
            json=body,
        )
        assert collage.status_code == 202, collage.text
        task = wait_task(client, collage.json()["task"]["id"])
        assert task["status"] == "completed", task
        assert task["result"]["succeeded"] == 1
        output = settings.images_root / "episode_collage.jpg"
        assert output.is_file()
        assert output.stat().st_size > 0
        preview = client.get(
            "/api/files/image",
            params={"root_key": "images", "path": "episode_collage.jpg"},
        )
        assert preview.status_code == 200
        assert preview.content.startswith(b"\xff\xd8\xff")

        conflict = client.post(
            f"/api/files/screenshots/{created['session_id']}/collage",
            json=body,
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["code"] == "screenshot_collage_conflicts"
        assert output.is_file()


def test_screenshot_api_rejects_traversal_and_invalid_session(settings) -> None:
    settings.ensure_directories()
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        traversal = client.post(
            "/api/files/screenshots",
            json=screenshot_payload("../data/bili-remote.sqlite3"),
        )
        assert traversal.status_code == 422
        missing = client.get("/api/files/screenshots/" + "0" * 32)
        assert missing.status_code == 422
