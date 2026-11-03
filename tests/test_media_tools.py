from __future__ import annotations

import struct
import sys
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_screenshots import make_video

from bili_remote.color import color_metadata, conversion_filter
from bili_remote.database import Database
from bili_remote.file_service import FileService, FileServiceError, OperationCancelled
from bili_remote.image_hosts import PixhostClient
from bili_remote.media_tools import optical_choices, optical_input, run_tool
from bili_remote.metadata import MetadataService
from bili_remote.publishing import PublishService
from bili_remote.qbittorrent import QbittorrentService
from bili_remote.screenshots import ScreenshotService
from bili_remote.security import SecretBox
from bili_remote.torrents import TorrentService, bdecode
from bili_remote.web import create_app


def setup_services(settings):
    settings.ensure_directories()
    db = Database(settings.database_path)
    db.initialize()
    files = FileService(settings, db)
    secret = SecretBox(settings.app_secret)
    return db, files, secret


def login(client):
    result = client.post("/api/auth/login", json={"username": "admin", "password": "test-password"})
    client.headers["X-CSRF-Token"] = result.json()["csrf"]


def wait(client, task_id):
    for _ in range(200):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] not in {"running", "queued"}:
            return task
        time.sleep(0.03)
    pytest.fail("工具任务超时")


def test_tmdb_search_details_and_ptgen_proposals(settings):
    db, _, secret = setup_services(settings)
    db.update_settings(
        {"tmdb_api_key": secret.encrypt_json("test-key"), "ptgen_url": "https://ptgen.example/"}
    )
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.host == "ptgen.example":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "format": "[b]简介[/b]\nhttps://movie.douban.com/subject/123/",
                },
            )
        if "search" in request.url.path:
            return httpx.Response(
                200,
                json={"results": [{"id": 10, "name": "测试剧", "first_air_date": "2024-01-01"}]},
            )
        english = request.url.params.get("language") == "en-US"
        return httpx.Response(
            200,
            json={
                "id": 10,
                "name": "Test Show" if english else "测试剧",
                "first_air_date": "2024-01-01",
                "external_ids": {"imdb_id": "tt1234"},
                "poster_path": "/poster.jpg",
            },
        )

    service = MetadataService(db, secret, transport=httpx.MockTransport(respond))
    assert service.search("测试剧")["items"][0]["tmdb_id"] == 10
    detail = service.detail("tv", 10)["proposal"]
    assert detail["title_zh"] == "测试剧" and detail["title_en"] == "Test Show"
    assert detail["imdb_url"] == "https://www.imdb.com/title/tt1234/"
    proposed = service.ptgen(detail["imdb_url"])["proposal"]
    assert proposed["related_links"] == ["https://movie.douban.com/subject/123/"]
    saved = service.save({"fields": detail})
    assert service.get(saved["id"])["fields"]["title_en"] == "Test Show"
    assert requests[0].url.params["api_key"] == "test-key"


@pytest.mark.parametrize(
    "profile,compat,allowed", [(5, 0, False), (7, 1, True), (8, 1, True), (8, 0, False)]
)
def test_dv_only_uses_confirmed_compatible_base_layer(profile, compat, allowed):
    media = {
        "dv_profile": profile,
        "dv_bl_signal_compatibility_id": compat,
        "color_transfer": "smpte2084",
    }
    if allowed:
        chain, processor = conversion_filter(media, "auto", {"zscale": True})
        assert "tonemap" in chain and processor == "zscale-base-layer"
    else:
        with pytest.raises(FileServiceError):
            conversion_filter(media, "auto", {"zscale": True})
    chain, processor = conversion_filter(media, "auto", {"libplacebo": True})
    assert "apply_dolbyvision=true" in chain and processor == "libplacebo"


def test_dv_probe_preserves_profile():
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "color_transfer": "smpte2084",
                "side_data_list": [
                    {
                        "side_data_type": "DOVI configuration record",
                        "dv_profile": 8,
                        "dv_bl_signal_compatibility_id": 1,
                    }
                ],
            }
        ]
    }
    assert color_metadata(payload)["dv_profile"] == 8


def mpls(path, segments):
    raw = bytearray(40)
    raw[:8] = b"MPLS0200"
    struct.pack_into(">I", raw, 8, 40)
    header = bytearray(10)
    struct.pack_into(">H", header, 6, len(segments))
    raw += header
    for clip, start, end in segments:
        item = bytearray(32)
        struct.pack_into(">H", item, 0, 30)
        item[2:7] = clip.encode()
        item[7:11] = b"M2TS"
        struct.pack_into(">II", item, 14, int(start * 45000), int(end * 45000))
        raw += item
    path.write_bytes(raw)


def test_optical_selection_uses_full_playlist_and_largest_dvd_group(settings):
    setup_services(settings)
    root = settings.download_root / "BD"
    playlist = root / "BDMV" / "PLAYLIST"
    playlist.mkdir(parents=True)
    mpls(playlist / "00001.mpls", [("00001", 0, 10)])
    mpls(playlist / "00002.mpls", [("00002", 2, 22), ("00003", 0, 30)])
    info = optical_choices(root)
    assert info["selected"] == "00002" and info["choices"][0]["duration_seconds"] == 50
    selected = optical_input(root, "00001")
    assert selected["input"].startswith("bluray:") and selected["input_args"] == ["-playlist", "1"]
    dvd = settings.download_root / "DVD" / "VIDEO_TS"
    dvd.mkdir(parents=True)
    for name, size in [
        ("VTS_01_1.VOB", 10),
        ("VTS_02_1.VOB", 15),
        ("VTS_02_2.VOB", 15),
        ("VTS_01_0.IFO", 1),
        ("VTS_02_0.IFO", 1),
    ]:
        (dvd / name).write_bytes(b"a" * size)
    selected = optical_input(dvd)
    assert selected["selected"] == "02"
    assert selected["input"].startswith("concat:") and "VTS_02_2.VOB" in selected["input"]
    assert [p.suffix for p in selected["reports"]] == [".IFO", ".VOB"]
    (dvd / "escape").symlink_to(settings.download_root)
    with pytest.raises(FileServiceError, match="符号链接"):
        optical_input(dvd)


def test_defaults_generate_styled_and_raw_png_without_double_decoration(settings):
    db, files, _ = setup_services(settings)
    db.update_settings(
        {
            "screenshot_default_width": "0",
            "collage_default_total": "2",
            "collage_default_content_type": "custom",
            "screenshot_default_sampling": "uniform",
        }
    )
    make_video(settings.download_root / "video.mp4", duration=4)
    service = ScreenshotService(settings, files)
    prepared = service.prepare_create(
        {"sources": [{"root_key": "downloads", "relative_path": "video.mp4"}]}
    )
    service.run(
        prepared["task_input"],
        task_id=1,
        log=lambda _: None,
        should_cancel=lambda: False,
        progress=lambda _: None,
    )
    result = service.get_session(prepared["session_id"])
    assert result["status"] == "completed"
    assert result["params"]["sampling_mode"] == "uniform"
    shots = result["videos"][0]["items"]
    assert [r["actual_timestamp_seconds"] for r in shots] == [1, 3]
    with Image.open(settings.images_root / shots[0]["raw_relative_path"]) as raw:
        assert raw.format == "PNG" and raw.size == (320, 180)
    with Image.open(settings.images_root / shots[0]["relative_path"]) as styled:
        assert styled.height > 210 and styled.width > 320
    layout = service.prepare_collage(
        result["id"], {"layout_mode": "custom", "columns": 1, "rows": 4, "thumbnail_width": 320}
    )
    service.run_collage(
        layout, task_id=2, log=lambda _: None, should_cancel=lambda: False, progress=lambda _: None
    )
    assert (settings.images_root / "video_collage.jpg").exists()
    # A retry preserves successful item identities instead of recapturing them.
    service.run(
        prepared["task_input"],
        task_id=1,
        log=lambda _: None,
        should_cancel=lambda: False,
        progress=lambda _: None,
    )
    assert [x["id"] for x in service.get_session(result["id"])["videos"][0]["items"]] == [
        x["id"] for x in shots
    ]


def test_cancellable_tool_is_killed_promptly():
    cancelled = threading.Event()
    timer = threading.Timer(0.15, cancelled.set)
    timer.start()
    start = time.monotonic()
    with pytest.raises(OperationCancelled):
        run_tool(
            [sys.executable, "-c", "import time; time.sleep(15)"], should_cancel=cancelled.is_set
        )
    timer.join()
    assert time.monotonic() - start < 3


def test_torrent_background_snapshot_and_explicit_empty_trackers(settings):
    settings.ensure_directories()
    (settings.download_root / "sample.bin").write_bytes(b"abc" * 5000)
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        response = client.put(
            "/api/settings",
            json={
                "values": {
                    "torrent_default_private": False,
                    "torrent_default_piece_length": "32K",
                    "torrent_default_trackers": ["https://tracker.example/announce"],
                }
            },
        )
        assert response.status_code == 200, response.text
        response = client.post(
            "/api/torrents/jobs", json={"source_relative_path": "sample.bin", "trackers": []}
        )
        assert response.status_code == 202, response.text
        task = wait(client, response.json()["task"]["id"])
        assert task["status"] == "completed", task
        assert task["input"]["private"] is False and task["input"]["piece_length"] == "32K"
        record = task["result"]["record"]
        torrent = bdecode((settings.torrents_root / record["output_relative_path"]).read_bytes())
        assert b"private" not in torrent[b"info"] and b"announce" not in torrent


def test_metadata_credentials_not_returned_in_settings(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        response = client.put(
            "/api/settings",
            json={"values": {"tmdb_api_key": "secret-tmdb", "ptgen_api_key": "secret-ptgen"}},
        )
        assert response.status_code == 200
        assert "secret-tmdb" not in response.text and "secret-ptgen" not in response.text
        assert response.json()["secrets_configured"]["tmdb_api_key"]
        assert "secret-tmdb" not in app.state.services.database.get_settings()["tmdb_api_key"]


def test_pter_report_order_and_draft_readiness(settings):
    db, files, secret = setup_services(settings)
    service = PublishService(db, TorrentService(files, db), QbittorrentService(db, secret))
    raw = "General\nComplete name : example.mkv\n\nVideo\nFormat : AVC\n"
    document = db.create_release_document(
        title="Test",
        source_root_key="downloads",
        source_relative_path="test.mkv",
        output_root_key="release_info",
        format="bbcode",
        media={
            "disc_kind": "video",
            "reports": [{"id": "a", "kind": "MediaInfo", "valid": True, "text": raw}],
        },
        file_list=[],
        generated_text="",
    )
    image = db.create_image_upload(
        provider="pixhost",
        source_kind="file",
        source_root_key="images",
        source_relative_path="shot.png",
        source_name="shot.png",
        source_fingerprint="abc",
        source_size_bytes=1,
    )
    db.update_image_upload(
        image,
        status="completed",
        remote_url="https://img.example/shot.png",
        thumbnail_url="https://img.example/thumb.png",
    )
    fields = {
        "title_en": "Test Show",
        "title_zh": "测试剧",
        "year": "2024",
        "poster_url": "https://img.example/poster.png",
        "category": "TV",
        "quality": "WEB-DL",
        "region": "中国",
        "classification_confirmed": True,
    }
    result = service.create(
        {
            "metadata": fields,
            "description": "影视简介",
            "release_document_id": document,
            "image_upload_ids": [image],
        }
    )
    assert result["status"] == "ready", result["warnings"]
    assert f"[hide=MediaInfo]{raw}[/hide]" in result["bbcode"]
    assert (
        result["bbcode"].index("poster.png")
        < result["bbcode"].index("影视简介")
        < result["bbcode"].index("[hide=MediaInfo]")
        < result["bbcode"].index("shot.png")
    )
    draft = service.update(
        result["id"],
        {"title": "中文主标题", "metadata": {**fields, "classification_confirmed": False}},
    )
    assert draft["status"] == "draft" and draft["metadata"]["checks"]["errors"]
    assert raw in draft["bbcode"]


def test_upload_job_retries_only_failed_files(settings):
    from bili_remote.image_hosts import ImageHostError

    settings.ensure_directories()
    for name in ["a.png", "b.png"]:
        Image.new("RGB", (10, 10), "blue").save(settings.images_root / name)
    calls = []

    class FakeHost:
        def upload(self, path, media_type, timeout):
            calls.append(path.name)
            if path.name == "b.png" and calls.count("b.png") == 1:
                raise ImageHostError("temporary failure")
            return {
                "remote_url": "https://images.example/" + path.name,
                "thumbnail_url": "https://images.example/thumb/" + path.name,
                "remote_id": path.name,
            }

    app = create_app(settings)
    app.state.services.image_hosts._client_factory = lambda *_: FakeHost()
    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/uploads/jobs",
            json={
                "provider": "pixhost",
                "items": [
                    {"root_key": "images", "relative_path": "a.png"},
                    {"root_key": "images", "relative_path": "b.png"},
                ],
            },
        )
        assert response.status_code == 202, response.text
        task_id = response.json()["task"]["id"]
        failed = wait(client, task_id)
        assert failed["status"] == "failed" and failed["result"]["succeeded"] == 1
        assert client.post(f"/api/tasks/{task_id}/retry").status_code == 200
        completed = wait(client, task_id)
        assert completed["status"] == "completed"
        assert calls == ["a.png", "b.png", "b.png"]
        ids = [r["id"] for r in completed["result"]["uploads"]]
        links = client.get(
            "/api/uploads/formats",
            params=[("ids", ids[1]), ("ids", ids[0]), ("format", "raw"), ("variant", "thumbnail")],
        ).json()
        assert links["links"] == [
            "https://images.example/thumb/b.png",
            "https://images.example/thumb/a.png",
        ]


def test_pixhost_direct_and_thumbnail_links(settings, monkeypatch):
    import bili_remote.image_hosts as module

    settings.ensure_directories()
    path = settings.images_root / "a.png"
    Image.new("RGB", (10, 10), "red").save(path)
    original = httpx.Client

    def handle(request):
        assert request.url.host == "api.pixhost.to"
        return httpx.Response(
            200,
            json={
                "th_url": "https://t12.pixhost.to/thumbs/50/a.png",
                "show_url": "https://pixhost.to/show/50/a.png",
            },
        )

    monkeypatch.setattr(
        module.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    result = PixhostClient().upload(path, "image/png", 5)
    assert result["remote_url"] == "https://img12.pixhost.to/images/50/a.png"
    assert result["thumbnail_url"] == "https://t12.pixhost.to/thumbs/50/a.png"


def test_bdinfo_selected_playlist_uses_cli_filename(settings, monkeypatch):
    from types import SimpleNamespace

    import bili_remote.release_info as module
    from bili_remote.release_info import ReleaseInfoService

    db, files, _ = setup_services(settings)
    source = settings.download_root / "Disc"
    (source / "BDMV").mkdir(parents=True)
    service = ReleaseInfoService(settings, files, db)
    monkeypatch.setattr(service, "_find_executable", lambda *_: "/tool/bdinfo")
    report = (
        "DISC INFO:\nDisc Title: Test\nPLAYLIST REPORT:\nName: 00002.MPLS\n"
        "VIDEO:\nHEVC\nAUDIO:\nAAC\nSUBTITLES:\nNone\n"
    )

    def execute(command, **kwargs):
        assert command[1:3] == ["-m", "00002.MPLS"]
        return SimpleNamespace(returncode=0, stdout=report, stderr="")

    monkeypatch.setattr(module.subprocess, "run", execute)
    text, warning = service._bdinfo_text(source, "00002")
    assert text == report and warning is None


def test_recaptured_screenshot_does_not_reuse_stale_upload(settings):
    from bili_remote.image_hosts import ImageHostService

    db, files, secret = setup_services(settings)
    path = settings.images_root / "shot.png"
    Image.new("RGB", (10, 10), "red").save(path)

    class Host:
        def upload(self, *_):
            return {"remote_url": "https://images.example/shot.png"}

    service = ImageHostService(files, db, secret, client_factory=lambda *_: Host())
    upload = service.upload_file(
        root_key="images", relative_path="shot.png", session_id="session", session_item_id="item"
    )
    assert upload["status"] == "completed"
    Image.new("RGB", (20, 10), "blue").save(path)
    session = service.attach_to_session({"id": "session", "videos": [{"items": [{"id": "item"}]}]})
    assert session["videos"][0]["items"][0]["upload"]["status"] == "stale"


def test_configured_thumbnail_workflow_is_durable_and_legacy_compatible(settings):
    settings.ensure_directories()
    make_video(settings.download_root / "sample.mp4")
    with TestClient(create_app(settings)) as client:
        login(client)
        services = client.app.state.services
        services.database.update_settings(
            {
                "screenshot_output_mode": "collage",
                "collage_default_columns": "3",
                "collage_default_rows": "3",
                "collage_default_width": "240",
                "screenshot_default_width": "320",
                "collage_default_content_type": "custom",
                "collage_custom_skip_start": "0",
                "collage_custom_skip_end": "0",
                "screenshot_default_sampling": "uniform",
            }
        )
        result = client.post(
            "/api/files/screenshots",
            json={
                "output_mode": "collage",
                "sources": [{"root_key": "downloads", "relative_path": "sample.mp4"}],
            },
        )
        assert result.status_code == 202, result.text
        task = wait(client, result.json()["task"]["id"])
        assert task["status"] == "completed", task
        assert task["result"]["stage"] == "collage"
        session_id = result.json()["session_id"]
        session = client.get(f"/api/files/screenshots/{session_id}").json()
        assert session["counts"]["completed"] == 9
        output = next(iter(session["final_outputs"].values()))
        path = settings.images_root / output["output_relative_path"]
        with Image.open(path) as image:
            assert image.format == "PNG"
        before = path.stat().st_mtime_ns
        services.database.update_settings(
            {"collage_default_columns": "1", "collage_default_rows": "4"}
        )
        retried = services.screenshots.finish_output(
            session_id, task_id=task["id"], log=lambda _: None, should_cancel=lambda: False
        )
        assert retried["succeeded"] == 1
        assert path.stat().st_mtime_ns == before
        assert session["params"]["collage_options"]["columns"] == 3
        old = client.post(
            "/api/files/screenshots",
            json={
                "sources": [{"root_key": "downloads", "relative_path": "sample.mp4"}],
                "total": 1,
            },
        )
        old_task = wait(client, old.json()["task"]["id"])
        assert old_task["status"] == "completed", old_task
        old_session = client.get(f"/api/files/screenshots/{old.json()['session_id']}").json()
        assert not old_session.get("final_outputs")
        assert (
            client.get("/api/files/screenshots/sessions").json()["sessions"][0]["sources"][0][
                "relative_path"
            ]
            == "sample.mp4"
        )


def test_collage_workflow_retry_keeps_successful_outputs_and_uploads(settings):
    from bili_remote.image_hosts import ImageHostError

    settings.ensure_directories()
    for name in ("a.mp4", "b.mp4"):
        make_video(settings.download_root / name)
    calls = []

    class Host:
        def upload(self, path, media_type, timeout):
            calls.append(path.name)
            if path.name == "b_collage.png" and calls.count(path.name) == 1:
                raise ImageHostError("retry this upload")
            return {"remote_url": "https://images.example/" + path.name, "remote_id": path.name}

    app = create_app(settings)
    app.state.services.image_hosts._client_factory = lambda *_: Host()
    with TestClient(app) as client:
        login(client)
        services = app.state.services
        services.database.update_settings(
            {
                "collage_default_columns": "1",
                "collage_default_rows": "1",
                "collage_default_width": "240",
                "screenshot_default_width": "320",
                "collage_default_content_type": "custom",
                "collage_custom_skip_start": "0",
                "collage_custom_skip_end": "0",
                "screenshot_auto_upload": "true",
            }
        )
        response = client.post(
            "/api/files/screenshots",
            json={
                "output_mode": "collage",
                "sources": [
                    {"root_key": "downloads", "relative_path": name} for name in ("a.mp4", "b.mp4")
                ],
            },
        ).json()
        task = wait(client, response["task"]["id"])
        assert task["status"] == "failed", task
        session = services.screenshots.get_session(response["session_id"])
        outputs = list(session["final_outputs"].values())
        mtimes = [
            (settings.images_root / item["output_relative_path"]).stat().st_mtime_ns
            for item in outputs
        ]
        assert client.post(f"/api/tasks/{task['id']}/retry").status_code == 200
        task = wait(client, task["id"])
        assert task["status"] == "completed", task
        assert calls.count("a_collage.png") == 1
        assert calls.count("b_collage.png") == 2
        assert mtimes == [
            (settings.images_root / item["output_relative_path"]).stat().st_mtime_ns
            for item in outputs
        ]


def test_thumbnail_timestamp_settings_are_validated_and_snapshotted(settings):
    from bili_remote.settings_registry import parse_stored_values
    from bili_remote.validation import ValidationError

    db, files, _ = setup_services(settings)
    make_video(settings.download_root / "timestamps.mp4")
    with pytest.raises(ValidationError):
        parse_stored_values({"screenshot_default_timestamps": ["wrong"]}, settings)
    db.update_settings(
        parse_stored_values(
            {
                "screenshot_default_sampling": "timestamps",
                "screenshot_default_timestamps": ["00:00.250", "00:01.500"],
            },
            settings,
        )
    )
    service = ScreenshotService(settings, files)
    prepared = service.prepare_create(
        {
            "output_mode": "single",
            "sources": [{"root_key": "downloads", "relative_path": "timestamps.mp4"}],
        }
    )
    assert prepared["task_input"]["timestamps"] == [0.25, 1.5]
    assert prepared["task_input"]["total"] == 2
    db.update_settings(parse_stored_values({"screenshot_default_timestamps": ["00:02"]}, settings))
    assert service.get_session(prepared["session_id"])["params"]["timestamps"] == [0.25, 1.5]


def test_cancelled_montage_can_resume_without_rebuilding_success(settings):
    from test_screenshots import create_data, run_session

    db, files, _ = setup_services(settings)
    db.update_settings(
        {
            "collage_default_columns": "1",
            "collage_default_rows": "1",
            "collage_default_width": "240",
        }
    )
    for name in ("one.mp4", "two.mp4"):
        make_video(settings.download_root / name)
    service = ScreenshotService(settings, files)
    data = create_data("one.mp4", "two.mp4", total=1)
    data["output_mode"] = "collage"
    _, session_id = run_session(service, data)

    def cancelled():
        return len(service.get_session(session_id).get("final_outputs") or {}) == 1

    with pytest.raises(OperationCancelled):
        service.finish_output(session_id, task_id=1, log=lambda _: None, should_cancel=cancelled)
    first = next(iter(service.get_session(session_id)["final_outputs"].values()))
    path = settings.images_root / first["output_relative_path"]
    before = path.stat().st_mtime_ns
    result = service.finish_output(
        session_id, task_id=1, log=lambda _: None, should_cancel=lambda: False
    )
    assert result["succeeded"] == 2
    assert before == path.stat().st_mtime_ns


def test_ptgen_follows_local_api_redirect_without_exposing_credentials(settings):
    db, _, secret = setup_services(settings)
    token = "private-ptgen-key"
    db.update_settings(
        {"ptgen_url": "https://ptgen.example/", "ptgen_api_key": secret.encrypt_json(token)}
    )
    paths = []

    def respond(request):
        paths.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(
                302, headers={"Location": "/api/v1/info?" + str(request.url.query.decode())}
            )
        assert request.url.params["apikey"] == token
        return httpx.Response(200, json={"success": True, "format": "真实接口格式的简介"})

    service = MetadataService(db, secret, transport=httpx.MockTransport(respond))
    result = service.ptgen("https://www.imdb.com/title/tt12879782/")
    assert result["proposal"]["ptgen_text"] == "真实接口格式的简介"
    assert paths == ["/", "/api/v1/info"]


@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (403, {}, "API Key"),
        (404, {}, "完整 API 路径"),
        (
            200,
            {"success": False, "error": "IMDb page parse failed: JSON-LD script not found"},
            "IMDb 页面",
        ),
    ],
)
def test_ptgen_errors_identify_cause_and_redact_secrets(settings, status, payload, expected):
    from bili_remote.metadata import MetadataError

    db, _, secret = setup_services(settings)
    token = "do-not-leak-this-key"
    db.update_settings(
        {
            "ptgen_url": "https://ptgen.example/api/v1/info",
            "ptgen_api_key": secret.encrypt_json(token),
        }
    )
    service = MetadataService(
        db,
        secret,
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload)),
    )
    with pytest.raises(MetadataError) as raised:
        service.ptgen("https://www.imdb.com/title/tt12879782/")
    assert expected in str(raised.value)
    assert token not in str(raised.value)
    assert "apikey=" not in str(raised.value)


def test_ptgen_does_not_forward_key_to_cross_origin_redirect(settings):
    from bili_remote.metadata import MetadataError

    db, _, secret = setup_services(settings)
    db.update_settings(
        {"ptgen_url": "https://ptgen.example/", "ptgen_api_key": secret.encrypt_json("key")}
    )
    calls = []

    def respond(request):
        calls.append(request.url.host)
        return httpx.Response(302, headers={"Location": "https://other.example/api?apikey=key"})

    service = MetadataService(db, secret, transport=httpx.MockTransport(respond))
    with pytest.raises(MetadataError, match="其他地址"):
        service.ptgen("https://www.imdb.com/title/tt12879782/")
    assert calls == ["ptgen.example"]


@pytest.mark.parametrize(
    "endpoint,path,query_key",
    [
        ("https://ptgen.example/api/v1/info", "/api/v1/search", "q"),
        ("https://ptgen.example/api/info/", "/api/search", "q"),
        ("https://ptgen.example/worker?deployment=one", "/worker", "search"),
        ("https://ptgen.example/", "/", "search"),
    ],
)
def test_ptgen_title_search_preserves_auth_and_requires_candidate_selection(
    settings, endpoint, path, query_key
):
    db, _, secret = setup_services(settings)
    db.update_settings(
        {
            "ptgen_url": endpoint,
            "ptgen_api_key": secret.encrypt_json("private-search-token"),
        }
    )
    calls = []

    def respond(request):
        calls.append(request)
        assert request.url.path == path
        assert request.url.params[query_key] == "凡人修仙传"
        assert request.url.params["apikey"] == "private-search-token"
        if path == "/worker":
            assert request.url.params["deployment"] == "one"
        return httpx.Response(
            200,
            json={
                "success": True,
                "data": [
                    {
                        "title": "凡人修仙传 年番4",
                        "year": "2026",
                        "link": "https://movie.douban.com/subject/1234/",
                        "subtitle": "动画",
                    },
                    {
                        "title": "凡人修仙传",
                        "year": "2020",
                        "link": "https://movie.douban.com/subject/5678",
                    },
                    {"title": "Invalid", "link": "https://other.example/subject/1234/"},
                ],
            },
        )

    service = MetadataService(db, secret, transport=httpx.MockTransport(respond))
    result = service.ptgen_search(" 凡人修仙传 ")
    assert [item["year"] for item in result["items"]] == ["2026", "2020"]
    assert result["items"][1]["douban_url"].endswith("/5678/")
    assert len(calls) == 1  # Search does not generate/adopt the first result.
    assert service.list() == []


def test_ptgen_search_and_detail_api_work_without_tmdb_or_imdb(settings):
    with TestClient(create_app(settings)) as client:
        services = client.app.state.services
        services.database.update_settings({"ptgen_url": "https://ptgen.example/api/v1/info"})
        calls = []

        def respond(request):
            calls.append(str(request.url))
            if request.url.path.endswith("/search"):
                return httpx.Response(
                    200,
                    json={
                        "success": True,
                        "data": [
                            {
                                "title": "测试剧 第二季",
                                "year": "2025",
                                "link": "https://movie.douban.com/subject/5678/",
                            },
                        ],
                    },
                )
            assert request.url.params["url"] == "https://movie.douban.com/subject/5678/"
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "chinese_title": "测试剧 第二季",
                        "foreign_title": "Test Show Season 2",
                        "year": " 2025",
                        "poster": "https://images.example/poster.jpg",
                        "genre": ["动画"],
                        "region": ["中国大陆"],
                        "introduction": "原始简介",
                        "format": "[b]原始发布简介[/b]\nhttps://www.imdb.com/title/tt5678/",
                    },
                },
            )

        services.metadata.transport = httpx.MockTransport(respond)
        assert client.get("/api/media/ptgen/search?query=测试剧").status_code == 401
        login(client)
        candidates = client.get("/api/media/ptgen/search?query=测试剧").json()["items"]
        response = client.post("/api/media/ptgen", json={"url": candidates[0]["douban_url"]})
        assert response.status_code == 200
        fields = response.json()["proposal"]
        assert fields["title_en"] == "Test Show Season 2"
        assert fields["year"] == "2025"
        assert fields["overview"] == "原始简介"
        assert fields["genres"] == ["动画"]
        assert fields["related_links"] == ["https://www.imdb.com/title/tt5678/"]
        assert len(calls) == 2 and all("ptgen.example" in url for url in calls)


@pytest.mark.parametrize(
    "status,payload,expected",
    [
        (403, {"error": "this ptgen disallow search"}, "禁用了搜索"),
        (200, {"success": False, "error": "Failed to search Douban"}, "豆瓣搜索失败"),
        (200, {"success": True, "format": "not a search result"}, "未返回搜索列表"),
    ],
)
def test_ptgen_search_reports_actionable_failures(settings, status, payload, expected):
    from bili_remote.metadata import MetadataError

    db, _, secret = setup_services(settings)
    db.update_settings({"ptgen_url": "https://ptgen.example/api/v1/info"})
    service = MetadataService(
        db,
        secret,
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload)),
    )
    with pytest.raises(MetadataError, match=expected):
        service.ptgen_search("测试剧")
