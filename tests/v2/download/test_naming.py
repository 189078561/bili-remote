import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from bili_remote.v2.download.finalize import finished_paths, staging_directory
from bili_remote.v2.download.naming import NameOptions, download_plan, episode_facts, initial_filename, url_facts
from bili_remote.web import create_app


VIDEO_URL = "https://www.bilibili.com/video/BV1xx411c7mD"
SERIES_URL = "https://www.bilibili.com/bangumi/play/ep3854802"


def test_initial_names_use_displayed_episode_and_preserve_unknown_ids():
    options = NameOptions()
    episodes = [
        {"id": "3854802", "group": "正片", "display_episode_number": 200, "chapter_title": "暮兰之战24", "group_index": 24},
        {"id": "3854803", "group": "正片", "display_episode_number": 201, "chapter_title": "暮兰之战25", "group_index": 25},
        {"id": "42", "group": "花絮", "display_episode_number": 24},
        {"id": "86", "group": "正片", "chapter_title": "暮兰之战24", "group_index": 24},
    ]
    facts = episode_facts(episodes)
    assert initial_filename(download_plan("凡人修仙传", facts["3854802"], options)) == "凡人修仙传.E200.mp4"
    for item in episodes[2:]:
        assert not facts[item["id"]].number
        assert f".Biliep{item['id']}." in initial_filename(download_plan("凡人修仙传", facts[item["id"]], options))
    assert initial_filename(download_plan("普通视频", url_facts(VIDEO_URL), options)) == "普通视频.BV1xx411c7mD.mp4"
    assert initial_filename(download_plan("普通视频", url_facts(VIDEO_URL + "?p=2"), options)) == "普通视频.P02.mp4"
    edited = NameOptions(title="Mortal’s Journey", episode_numbers={"3854802": 178})
    assert initial_filename(download_plan("凡人修仙传", facts["3854802"], edited, episode_id="3854802")) == "Mortals.Journey.E178.mp4"
    extra = episode_facts([{"id": "4507157", "group": "PV花絮", "title": "兢慎篇 预热PV"}])["4507157"]
    plan = download_plan("盗妖行", extra, options, episode_id="4507157")
    assert initial_filename(plan) == "兢慎篇.预热PV.mp4"
    assert initial_filename(plan, collision=plan["identifier"]) == "兢慎篇.预热PV.Biliep4507157.mp4"
    overridden = download_plan("盗妖行", extra, NameOptions(episode_numbers={"4507157": 89}), episode_id="4507157")
    assert initial_filename(overridden) == "盗妖行.E89.mp4"


def test_stage_blocks_symlinks_and_ignores_partial_paths(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "video.mp4").write_bytes(b"leave untouched")
    root = tmp_path / "media"
    root.mkdir()
    (root / ".bili-remote-job-1").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        staging_directory(root, 1)
    stage = staging_directory(root, 2)
    (stage / "video.mp4.part").write_bytes(b"partial")
    (stage / "link.mp4").symlink_to(outside / "video.mp4")
    assert finished_paths(stage, [str(stage / "video.mp4.part"), str(stage / "link.mp4"), str(outside / "video.mp4")]) == []


def test_named_completion_path_sidecars_conflict_and_probe_reuse(settings, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required")
    app = create_app(settings)
    services = app.state.services
    database = services.database
    database.initialize()
    root = settings.download_root
    root.mkdir(parents=True, exist_ok=True)
    template = "Bilibili/%(title)s.%(ext)s"
    job_id = database.create_job(source_url=SERIES_URL, kind="manual", output_template=template, output_dir=str(root),
                                 naming_plan={"title": "凡人修仙传", "identifier": "ep3854802", "number_kind": "episode", "number": 200})
    destination = root / "Bilibili"
    destination.mkdir()
    existing = destination / "凡人修仙传.E200.mp4"
    existing.write_bytes(b"original video")
    stage_paths = []

    async def download(job, directory, log_path, update):
        assert directory != root
        assert job["output_template"] == template
        folder = directory / "Bilibili"
        folder.mkdir()
        source = folder / "S200 - 200 暮兰之战24 [3854802].mp4"
        # Deliberately give a Matroska file a misleading legacy suffix.
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "color=size=320x180:rate=10", "-t", "0.2", "-c:v", "libx264", "-f", "matroska", str(source)], check=True)
        source.with_suffix(".chs.srt").write_text("subtitle")
        (folder / "ignored.mp4.part").write_bytes(b"partial")
        # Independent sidecar collision must choose another name for both files.
        (destination / "凡人修仙传.E200.chs.srt").write_text("existing subtitle")
        (destination / "凡人修仙传.E200.mkv").write_bytes(b"already there")
        stage_paths.append(source)
        await update({"result_path": str(source)})
        return str(source)

    monkeypatch.setattr(services.runner, "download", download)
    asyncio.run(services.manager._execute_download(database.get_job(job_id)))
    job = database.get_job(job_id)
    final = Path(job["result_path"])
    assert job["status"] == "completed"
    assert final.name == "凡人修仙传.E200.Biliep3854802.mkv"
    assert final.is_file() and final.with_suffix(".chs.srt").read_text() == "subtitle"
    assert existing.read_bytes() == b"original video"
    assert (destination / "凡人修仙传.E200.mkv").read_bytes() == b"already there"
    assert (destination / "凡人修仙传.E200.chs.srt").read_text() == "existing subtitle"
    assert not stage_paths[0].exists()
    assert database.get_download_naming(job_id)["outputs"][0]["probe"]["video_codec"]["value"] == "H264"

    async def unexpected_probe(*args, **kwargs):
        pytest.fail("handoff must reuse evidence for the unchanged downloaded file")

    monkeypatch.setattr(services.v2.probe, "probe", unexpected_probe)
    context = asyncio.run(services.v2.download_context("test-owner", job_id))["context"]
    assert context["resources"][0]["relative_path"] == final.relative_to(root).as_posix()
    assert context["episode"]["episode"]["value"] == 200
    assert context["episode"]["season"]["value"] is None
    assert context["files"][0]["probe"]["video_codec"]["value"] == "H264"
    assert context["files"][0]["sidecars"][0]["ref"]["relative_path"].endswith(".chs.srt")


def test_multi_part_outputs_use_extractor_parts_and_keep_unrelated_files(settings, monkeypatch):
    from bili_remote.v2.download.finalize import finalize_download

    root = settings.download_root
    root.mkdir(parents=True, exist_ok=True)
    stage = staging_directory(root, 3)
    paths = []
    item_facts = {}
    for part in (2, 5):
        source = stage / f"source-part-{part}.mp4"
        source.write_bytes(f"synthetic part {part}".encode())
        paths.append(str(source))
        item_facts[str(source)] = {"webpage_url": VIDEO_URL + f"?p={part}", "bvid": "BV1xx411c7mD", "episode_index": 99}
    (root / "示例.P02.chs.srt").write_text("old subtitle")
    (stage / "source-part-2.chs.srt").write_text("new subtitle")
    (stage / "source-part-2.f137.mp4.part").write_bytes(b"partial")
    options = NameOptions()
    plan = download_plan("示例", url_facts(VIDEO_URL), options, all_parts=True)

    async def probe(*args, **kwargs):
        return {"container": {"value": "mov,mp4,m4a"}, "video_codec": {"value": "AV1"}}

    monkeypatch.setattr("bili_remote.v2.download.finalize.MediaProbeAdapter.probe", probe)
    outputs, _ = asyncio.run(finalize_download(settings, root, stage, 3, plan, paths, {}, item_facts))
    assert [Path(item["path"]).name for item in outputs] == ["示例.P02.BV1xx411c7mD.mp4", "示例.P05.mp4"]
    assert [item["origin"]["episode_index"] for item in outputs] == [2, 5]
    assert not (root / "示例.P02.mp4").exists()
    assert (root / "示例.P02.chs.srt").read_text() == "old subtitle"
    assert (root / "示例.P02.BV1xx411c7mD.chs.srt").read_text() == "new subtitle"
    assert (stage / "source-part-2.f137.mp4.part").exists()


def test_naming_options_are_previewed_and_persisted_by_existing_apis(settings, monkeypatch):
    from fastapi.testclient import TestClient
    from bili_remote.downloader import ResolvedMedia
    from bili_remote.v2.download.naming import displayed_episode_number

    app = create_app(settings)
    services = app.state.services
    monkeypatch.setattr(services.manager, "start", lambda: None)
    raw = [{"id": 3854802, "title": "200", "long_title": "暮兰之战24"},
           {"id": 3854803, "title": "新章", "long_title": "暮兰之战25"}]
    episodes, _ = services.runner._build_episodes({}, {
        "main_section": {"episodes": raw},
        "section": [{"title": "PV花絮", "episodes": [{"id": 4507157, "title": "兢慎篇 预热PV"}]}],
    })
    assert displayed_episode_number("暮兰之战24") is None

    async def resolve(url, mode):
        return ResolvedMedia("凡人修仙传", "", None, url, True, 2, [], episodes, "3854802", mode)

    monkeypatch.setattr(services.runner, "resolve", resolve)
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "test-password"})
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        resolved = client.post("/api/resolve", json={"url": SERIES_URL}).json()
        assert resolved["naming_items"]["3854802"]["number"] == 200
        assert resolved["naming_items"]["3854802"]["chapter"] == "暮兰之战24"
        assert resolved["naming_items"]["3854803"]["number"] is None
        options = {"mode": "smart", "title": "凡人 修仙传", "episode_numbers": {"3854802": 198}}
        preview = client.post("/api/download/naming/preview", json={
            "options": options, "title": resolved["title"], "items": list(resolved["naming_items"].values())
        })
        assert preview.json()["items"] == [
            "凡人.修仙传.E198.mp4", "凡人.修仙传.Biliep3854803.mp4", "兢慎篇.预热PV.mp4"
        ]
        batch = client.post("/api/jobs/batch", json={
            "url": SERIES_URL, "episode_ids": ["3854802", "3854803", "4507157"], "naming": options
        })
        assert batch.status_code == 200, batch.text
        plans = [services.database.get_download_naming(job)["plan"] for job in batch.json()["ids"]]
        assert [plan["number"] for plan in plans] == [198, None, None]
        assert [initial_filename(plan) for plan in plans] == preview.json()["items"]
        single = client.post("/api/jobs", json={"url": VIDEO_URL + "?p=5", "naming": {"mode": "smart"}, "title": "测试"})
        assert single.status_code == 200, single.text
        single_plan = services.database.get_download_naming(single.json()["id"])["plan"]
        assert initial_filename(single_plan) == "测试.P05.mp4"
        legacy = client.post("/api/jobs", json={"url": VIDEO_URL})
        assert not services.database.get_download_naming(legacy.json()["id"])


def test_movie_classification_applies_only_to_main_feature(settings, tmp_path):
    from bili_remote.downloader import YtDlpRunner
    from bili_remote.v2.download.finalize import _publish
    from bili_remote.v2.download.naming import NameOptions, download_plan, episode_facts

    runner = YtDlpRunner(settings, None)
    groups = {
        "main_section": {"episodes": [{"id": 21, "title": "正片"}]},
        "section": [{"title": "预告", "episodes": [{"id": 22, "title": "首支预告"}]}],
    }
    episodes, _ = runner._build_episodes({"type": 2}, groups)
    facts = episode_facts(episodes)
    assert facts["21"].movie and not facts["22"].movie

    stage = tmp_path / "stage"
    stage.mkdir()
    track = stage / "ep21.mp4"
    track.write_bytes(b"new movie")
    old = tmp_path / "四海.mp4"
    old.write_bytes(b"existing movie")
    plan = download_plan("四海", facts["21"], NameOptions(), episode_id="21")
    result, _ = _publish(track, [], stage, tmp_path, plan, "mp4", 1, 1)
    assert result.name == "四海 (2).mp4"
    assert old.read_bytes() == b"existing movie"
    assert result.read_bytes() == b"new movie"
