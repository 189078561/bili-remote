from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bili_remote.web import create_app


def make_video(path: Path) -> None:
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
            "testsrc2=size=320x180:rate=10:duration=2",
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
    for _ in range(400):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in {"completed", "failed", "cancelled"}:
            return task
        time.sleep(0.03)
    raise AssertionError("拼图任务未在预期时间内完成")


def collage_payload(path: str, **overrides) -> dict:
    payload = {
        "sources": [{"root_key": "downloads", "relative_path": path}],
        "layout_mode": "grid",
        "columns": 1,
        "rows": 2,
        "thumbnail_width": 240,
        "content_type": "custom",
        "skip_start_seconds": 0,
        "skip_end_seconds": 0,
        "output_relative_path": "",
        "overwrite": False,
    }
    payload.update(overrides)
    return payload


def test_collage_api_generates_image_and_hides_source_paths(settings) -> None:
    settings.ensure_directories()
    make_video(settings.download_root / "episode.mp4")
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/files/collage/status").status_code == 401
        login(client)
        status = client.get("/api/files/collage/status")
        assert status.status_code == 200, status.text
        assert status.json()["available"] is True

        response = client.post(
            "/api/files/collage",
            json=collage_payload("episode.mp4"),
        )
        assert response.status_code == 202, response.text
        created = response.json()
        assert created["outputs"][0]["output_name"] == "episode_collage.jpg"
        task = wait_task(client, created["task"]["id"])
        assert task["status"] == "completed", task
        assert task["result"]["succeeded"] == 1
        result = task["result"]["items"][0]
        assert result["source_name"] == "episode.mp4"
        assert result["sample_timestamps"] == ["00:00:00", "00:00:01"]
        assert str(settings.download_root) not in str(task)

        image = client.get(
            "/api/files/image",
            params={"root_key": "images", "path": "episode_collage.jpg"},
        )
        assert image.status_code == 200, image.text
        assert image.headers["content-type"] == "image/jpeg"
        assert image.headers["x-content-type-options"] == "nosniff"
        assert image.content.startswith(b"\xff\xd8\xff")

        tasks = client.get("/api/tasks", params={"task_type": "collage"}).json()
        assert any(item["id"] == created["task"]["id"] for item in tasks)


def test_collage_api_returns_conflicts_and_accepts_explicit_overwrite(settings) -> None:
    settings.ensure_directories()
    make_video(settings.download_root / "episode.mp4")
    (settings.images_root / "episode_collage.jpg").write_bytes(b"old")
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        conflict = client.post(
            "/api/files/collage",
            json=collage_payload("episode.mp4"),
        )
        assert conflict.status_code == 409, conflict.text
        detail = conflict.json()["detail"]
        assert detail["code"] == "collage_conflicts"
        assert detail["conflicts"][0]["reason"] == "目标文件已存在"
        assert (settings.images_root / "episode_collage.jpg").read_bytes() == b"old"

        accepted = client.post(
            "/api/files/collage",
            json=collage_payload("episode.mp4", overwrite=True),
        )
        assert accepted.status_code == 202, accepted.text
        task = wait_task(client, accepted.json()["task"]["id"])
        assert task["status"] == "completed", task
        assert (settings.images_root / "episode_collage.jpg").read_bytes().startswith(
            b"\xff\xd8\xff"
        )


def test_collage_api_rejects_traversal_and_non_video(settings) -> None:
    settings.ensure_directories()
    (settings.download_root / "notes.txt").write_text("hello", encoding="utf-8")
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        traversal = client.post(
            "/api/files/collage",
            json=collage_payload("../data/bili-remote.sqlite3"),
        )
        assert traversal.status_code == 422
        non_video = client.post(
            "/api/files/collage",
            json=collage_payload("notes.txt"),
        )
        assert non_video.status_code == 422
        assert "不支持的文件类型" in non_video.text


def test_collage_settings_are_registered_and_persisted(settings) -> None:
    settings.ensure_directories()
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        schema = client.get("/api/settings/schema").json()
        group = next(item for item in schema["groups"] if item["key"] == "collage")
        assert group["title"] == "视频截图与拼图"
        fields = {
            field["key"]: field
            for section in group["sections"]
            for field in section["fields"]
        }
        assert fields["collage_default_layout_mode"]["default"] == "grid"
        assert fields["collage_default_columns"]["default"] == 5
        assert fields["collage_default_total"]["default"] == 20
        assert fields["collage_default_width"]["minimum"] == 240
        assert fields["collage_default_gap"]["default"] == 3
        assert fields["screenshot_default_width"]["default"] == 1920
        assert fields["screenshot_output_relative_path"]["directory_root"] == "images"
        assert fields["collage_output_relative_path"]["control"] == "relative-directory"
        assert fields["collage_output_relative_path"]["directory_root"] == "images"
        saved = client.put(
            "/api/settings",
            json={
                "values": {
                    "collage_default_layout_mode": "count",
                    "collage_default_columns": 4,
                    "collage_default_rows": 3,
                    "collage_default_total": 11,
                    "collage_default_width": 800,
                    "collage_default_gap": 9,
                    "screenshot_default_width": 2560,
                    "collage_default_content_type": "movie",
                    "collage_custom_skip_start": 12,
                    "collage_custom_skip_end": 20,
                    "collage_output_relative_path": "collage/exports",
                    "screenshot_output_relative_path": "screenshots/exports",
                }
            },
        )
        assert saved.status_code == 200, saved.text
        values = saved.json()["values"]
        assert values["collage_default_layout_mode"] == "count"
        assert values["collage_default_columns"] == 4
        assert values["collage_default_rows"] == 3
        assert values["collage_default_total"] == 11
        assert values["collage_default_width"] == 800
        assert values["collage_default_gap"] == 9
        assert values["screenshot_default_width"] == 2560
        assert values["collage_default_content_type"] == "movie"
        assert values["collage_custom_skip_start"] == 12
        assert values["collage_custom_skip_end"] == 20
        assert values["collage_output_relative_path"] == "collage/exports"
        assert values["screenshot_output_relative_path"] == "screenshots/exports"

    restarted = create_app(settings)
    with TestClient(restarted) as client:
        login(client)
        values = client.get("/api/settings").json()["values"]
        assert values["collage_default_layout_mode"] == "count"
        assert values["collage_default_columns"] == 4
        assert values["collage_default_total"] == 11
        assert values["collage_default_width"] == 800
        assert values["collage_default_gap"] == 9
        assert values["screenshot_default_width"] == 2560
        assert values["collage_output_relative_path"] == "collage/exports"
        assert values["screenshot_output_relative_path"] == "screenshots/exports"
