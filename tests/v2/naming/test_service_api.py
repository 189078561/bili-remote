import asyncio
from copy import deepcopy
import json
import sqlite3
from contextlib import contextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bili_remote.database import Database
from bili_remote.file_service import FileService
from bili_remote.v2.naming import NamingService, NamingError, create_router
from bili_remote.v2.naming.probe import probe_from_payload
from bili_remote.v2.metadata import MetadataResolver
from bili_remote.v2.metadata.providers import Provider, field, title


class Audit:
    def __init__(self): self.records = {}
    def create(self, value): self.update(value)
    def update(self, value): self.records[value["id"]] = deepcopy(value)
    def get(self, batch_id): return deepcopy(self.records.get(batch_id))


class Tasks:
    """Contract fake only; production queue registration is integration-owned."""
    def __init__(self): self.calls = []
    def enqueue_naming(self, batch_id, idempotency_key, undo=False):
        self.calls.append({"batch_id": batch_id, "idempotency_key": idempotency_key, "undo": undo, "project_id": None})
        return {"kind": "processing", "id": len(self.calls), "task_type": "v2_rename_undo" if undo else "v2_rename_apply", "status": "queued", "progress": None, "created_at": "2026-11-04T00:00:00+00:00", "finished_at": None, "artifact_refs": [], "error": None, "retry_allowed": False, "cancel_allowed": True}
    def enqueue_probe(self, refs, expected):
        self.calls.append({"resources": refs, "expected_fingerprints": expected})
        return {"kind": "processing", "id": len(self.calls), "task_type": "v2_media_probe", "status": "queued"}


class Probe:
    async def probe(self, path, *, source_ref=None, previous=None):
        return probe_from_payload({"streams": [{"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080}, {"codec_type": "audio", "codec_name": "aac"}]})


@pytest.fixture
def setup(settings):
    settings.ensure_directories()
    database = Database(settings.database_path)
    database.initialize()
    service = NamingService(FileService(settings, database), Probe(), owner_session="session", audit=Audit(), tasks=Tasks())
    return service, settings.download_root, database


def analyze(service, resources, **kwargs):
    return asyncio.run(service.analyze(resources, **kwargs))


def test_preview_zero_writes_technical_overrides_sidecars_apply_and_undo(setup, monkeypatch):
    service, root, database = setup
    for name in ("a.mkv", "a.chs.forced.srt", "a.cht.ass", "a.eng.SDH.srt", "sound.flac"):
        (root / name).write_bytes(name.encode())
    ref = service.files.reference("downloads", "a.mkv")
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    before_states = {p.name: (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in root.iterdir()}
    writes = []
    connect = database.connect
    @contextmanager
    def observed_connection():
        with connect() as connection:
            def authorize(action, table, column, db_name, trigger):
                if action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE}:
                    writes.append((action, table))
                return sqlite3.SQLITE_OK
            connection.set_authorizer(authorize)
            yield connection
    monkeypatch.setattr(database, "connect", observed_connection)
    context = analyze(service, [ref], manual_overrides={"selected_title": "Movie", "year": 2023})
    sound = service.files.reference("downloads", "sound.flac")
    overrides = {"file_values": {"a.mkv": {"video_codec": "H264", "frame_rate": "24000/1001"}}, "sidecars": [{"ref": sound, "video_ref": ref, "kind": "external_audio", "suffix_tokens": ["eng"], "association": "manual", "confirmed": True}]}
    preview = service.preview(context, overrides)
    assert preview["can_apply"]
    assert len(preview["operations"]) == 5
    assert preview["changed_count"] == 5
    assert preview["unchanged_count"] == 0
    assert preview["conflict_count"] == 0
    assert preview["manual_confirmation_count"] == 0
    assert preview["operations"][0]["target_relative_path"] == "Movie.2023.1080p.WEB-DL.H264.AAC-HDVWEB.mkv"
    assert any(x["target_relative_path"].endswith(".chs.forced.srt") for x in preview["operations"])
    assert any(x["target_relative_path"].endswith(".eng.flac") for x in preview["operations"])
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    assert {p.name: (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns) for p in root.iterdir()} == before_states
    assert writes == []
    assert not service.executor.audit.records and not service.tasks.calls
    assert len(service.previews[preview["preview_token"]]["operations"]) == 5
    async def run():
        result = await service.apply(preview["preview_token"], preview["context_revision"], "once")
        assert result == await service.apply(preview["preview_token"], preview["context_revision"], "once")
        assert result == await service.apply(preview["preview_token"], preview["context_revision"], "another-key")
        assert len(service.tasks.calls) == 1
        assert service.tasks.calls[0]["project_id"] is None
        assert "context" not in json.dumps(service.tasks.calls)
        service.executor.execute(result["batch_id"])
        reverse = await service.undo_preview(result["batch_id"])
        assert reverse["can_apply"]
        undo = await service.apply(reverse["preview_token"], reverse["context_revision"], "undo-once", undo=True)
        service.executor.execute(undo["batch_id"])
    asyncio.run(run())
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    for table in ("release_projects", "publish_sessions", "media_metadata"):
        assert database.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0


def test_manual_names_use_supplied_complete_filename(setup):
    service, root, _ = setup
    (root / "a.mkv").write_bytes(b"a")
    refs = [service.files.reference("downloads", "a.mkv")]
    context = analyze(service, refs, manual_overrides={"selected_title": "A", "year": 2023})
    preview = service.preview(context, {"manual_names": {"a.mkv": "Custom.Name.2023.mkv"}})
    assert preview["operations"][0]["target_relative_path"] == "Custom.Name.2023.mkv"
    assert preview["context_revision"] > context["revision"]


def test_session_context_provenance_revision_expiry_idempotency(setup):
    service, root, _ = setup
    (root / "a.mkv").write_bytes(b"a")
    refs = [service.files.reference("downloads", "a.mkv")]
    context = analyze(service, refs, manual_overrides={"selected_title": "A"})
    forged = deepcopy(context)
    forged["files"][0]["probe"]["video_codec"]["value"] = "x265"
    preview = service.preview(forged)
    assert "x265" not in preview["operations"][0]["target_relative_path"]
    updated = service.preview(context, {"manual_names": {"a.mkv": "B"}})
    assert updated["context_revision"] > preview["context_revision"]
    with pytest.raises(NamingError) as stale:
        asyncio.run(service.apply(preview["preview_token"], preview["context_revision"], "old"))
    assert stale.value.code == "stale_context"
    asyncio.run(service.apply(updated["preview_token"], updated["context_revision"], "key"))
    with pytest.raises(NamingError) as conflict:
        asyncio.run(service.apply(updated["preview_token"], 999, "key"))
    assert conflict.value.code == "idempotency_conflict"
    service.previews[updated["preview_token"]]["deadline"] = 0
    with pytest.raises(NamingError) as expired:
        asyncio.run(service.apply(updated["preview_token"], updated["context_revision"], "new-key"))
    assert expired.value.code == "expired_preview"


def test_batch_episodes_manual_fix_and_duplicate_detection(setup):
    service, root, _ = setup
    for name in ("EP01.mkv", "EP02.mkv"):
        (root / name).write_bytes(name.encode())
    context = analyze(service, [service.files.reference("downloads", x) for x in ("EP01.mkv", "EP02.mkv")], manual_overrides={"selected_title": "Series"})
    preview = service.preview(context, rules={"template_id": "series", "episode_mode": "start", "start_episode": 10, "episode_direction": "decrement"})
    assert "E10" in preview["operations"][0]["target_relative_path"]
    assert "E09" in preview["operations"][1]["target_relative_path"]
    context["revision"] = preview["context_revision"]
    duplicate = service.preview(context, {"episode_overrides": {"EP01.mkv": 3, "EP02.mkv": 3}}, rules={"template_id": "series"})
    assert not duplicate["can_apply"]
    assert any(x["code"] == "duplicate_episode" for x in duplicate["conflicts"])
    assert duplicate["conflict_count"] == len(duplicate["conflicts"])
    assert duplicate["manual_confirmation_count"] == 0


def test_episode_offset_rule_applies_uniform_shift(setup):
    service, root, _ = setup
    for name in ("EP01.mkv", "EP02.mkv"):
        (root / name).write_bytes(name.encode())
    context = analyze(service, [service.files.reference("downloads", x) for x in ("EP01.mkv", "EP02.mkv")], manual_overrides={"selected_title": "Series"})
    preview = service.preview(context, rules={"template_id": "series", "episode_mode": "offset", "episode_rules": {"offset": 3}})
    assert "E04" in preview["operations"][0]["target_relative_path"]
    assert "E05" in preview["operations"][1]["target_relative_path"]
    assert preview["changed_count"] == 2
    assert preview["conflict_count"] == 0


def test_no_change_preview_keeps_target_and_is_not_applicable(setup):
    service, root, _ = setup
    name = "Film.2023.1080p.WEB-DL.H265.AAC-HDVWEB.mkv"
    (root / name).write_bytes(b"already named")
    context = analyze(
        service,
        [service.files.reference("downloads", name)],
        manual_overrides={"selected_title": "Film", "year": 2023},
    )
    preview = service.preview(context)

    assert preview["operations"][0]["source"]["relative_path"] == name
    assert preview["operations"][0]["target_relative_path"] == name
    assert preview["changed_count"] == 0
    assert preview["unchanged_count"] == 1
    assert preview["conflict_count"] == 0
    assert preview["manual_confirmation_count"] == 0
    assert preview["conflicts"] == []
    assert preview["manual_confirmations"] == []
    assert preview["notices"][0]["code"] == "no_changes"
    assert not preview["can_apply"]
    assert service.previews[preview["preview_token"]]["operations"] == []
    with pytest.raises(NamingError) as blocked:
        asyncio.run(service.apply(preview["preview_token"], preview["context_revision"], "no-change"))
    assert blocked.value.code == "no_changes"
    assert not service.executor.audit.records


def test_preview_mixes_unchanged_display_rows_with_changed_execution_rows(setup):
    service, root, _ = setup
    unchanged = "Series.2023.S01E02.1080p.WEB-DL.H265.AAC-HDVWEB.mkv"
    (root / unchanged).write_bytes(b"one")
    (root / "Series.S01E02.old.mkv").write_bytes(b"two")
    refs = [service.files.reference("downloads", name) for name in (unchanged, "Series.S01E02.old.mkv")]
    context = analyze(service, refs, manual_overrides={"selected_title": "Series", "year": 2023})
    preview = service.preview(context, rules={"template_id": "series"}, overrides={
        "episode_overrides": {unchanged: 2, "Series.S01E02.old.mkv": 2},
    })

    assert preview["changed_count"] == 1
    assert preview["unchanged_count"] == 1
    assert len(preview["operations"]) == 2
    assert {item["source"]["relative_path"] for item in preview["operations"]} == {
        unchanged, "Series.S01E02.old.mkv",
    }
    assert len(service.previews[preview["preview_token"]]["operations"]) == 1


def test_filename_episode_selects_series_template_and_records_provenance(setup):
    service, root, _ = setup
    (root / "Motion.Study.S01E01.mp4").write_bytes(b"media")
    path = root / "Motion.Study.S01E01.mp4"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    context = analyze(
        service,
        [service.files.reference("downloads", path.name)],
        manual_overrides={"selected_title": "Motion Study", "year": 2024},
    )
    assert context["files"][0]["episode"]["season"]["value"] == 1
    assert context["files"][0]["episode"]["episode"]["value"] == 1
    assert context["effective_rules"]["naming_rule"]["template_id"] == "series"
    assert context["effective_rules"]["sources"]["naming_rule.template_id"]["reason"] == "filename_episode"
    preview = service.preview(context)
    assert ".S01E01." in preview["operations"][0]["target_relative_path"]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_template_precedence_explicit_manual_memory_identity_then_fallback(setup):
    service, root, _ = setup
    (root / "Motion.Study.S01E01.mp4").write_bytes(b"episode")
    (root / "plain.mkv").write_bytes(b"plain")

    explicit = analyze(
        service,
        [service.files.reference("downloads", "Motion.Study.S01E01.mp4")],
        rules={"template_id": "anime"},
        manual_overrides={"selected_title": "Motion Study", "year": 2024},
    )
    assert explicit["effective_rules"]["naming_rule"]["template_id"] == "anime"
    assert explicit["effective_rules"]["sources"]["naming_rule.template_id"]["reason"] == "explicit_rule"

    fallback = analyze(
        service,
        [service.files.reference("downloads", "plain.mkv")],
        manual_overrides={"selected_title": "Plain", "year": 2024},
    )
    assert fallback["effective_rules"]["naming_rule"]["template_id"] == "movie"
    assert fallback["effective_rules"]["sources"]["naming_rule.template_id"]["reason"] == "movie_fallback"


def test_explicit_source_and_group_override_remembered_provenance(setup):
    service, root, _ = setup
    (root / "Motion.Study.S01E01.mp4").write_bytes(b"episode")

    class Memory:
        def get(self, identity, season):
            return {
                "effective": {
                    "naming_rule": {"source": "WEBRip", "group": "REMEMBERED"},
                    "episode_rules": {},
                    "subtitle_rule": {},
                    "selected_title": None,
                    "sources": {
                        "naming_rule.source": {"scope": "season", "revision": 2},
                        "naming_rule.group": {"scope": "series", "revision": 3},
                    },
                }
            }

    service.memory = Memory()
    context = analyze(
        service,
        [service.files.reference("downloads", "Motion.Study.S01E01.mp4")],
        rules={"source": "BluRay"},
        manual_overrides={"selected_title": "Motion Study", "year": 2024},
    )

    assert context["effective_rules"]["naming_rule"]["source"]["value"] == "BluRay"
    assert context["effective_rules"]["naming_rule"]["source"]["source"] == "manual"
    assert context["effective_rules"]["sources"]["naming_rule.source"] == {
        "scope": "manual",
        "reason": "explicit_rule",
        "source_ref": None,
    }
    assert context["effective_rules"]["sources"]["naming_rule.group"] == {
        "scope": "series",
        "revision": 3,
    }


def test_directory_scan_season_preview_and_undo(setup):
    service, root, _ = setup
    season = root / "old-season"
    season.mkdir()
    (season / "EP01.mkv").write_bytes(b"one")
    (season / "EP02.mkv").write_bytes(b"two")
    context = analyze(service, [service.files.reference("downloads", "old-season")], manual_overrides={"selected_title": "Series", "year": 2023})
    preview = service.preview(context, rules={"template_id": "season"})
    assert len(preview["operations"]) == 1 and preview["can_apply"]
    async def run():
        result = await service.apply(preview["preview_token"], preview["context_revision"], "directory")
        service.executor.execute(result["batch_id"])
        assert not season.exists()
        assert (root / preview["operations"][0]["target_relative_path"] / "EP01.mkv").read_bytes() == b"one"
        reverse = await service.undo_preview(result["batch_id"])
        undo = await service.apply(reverse["preview_token"], reverse["context_revision"], "directory-undo", undo=True)
        service.executor.execute(undo["batch_id"])
    asyncio.run(run())
    assert season.exists()


def test_season_preview_keeps_unconfirmed_identity_blocking(setup):
    service, root, _ = setup
    season = root / "Film.2023"
    season.mkdir()
    (season / "EP01.mkv").write_bytes(b"one")
    service.resolver = MetadataResolver({"tmdb": RecognitionProvider()})
    context = analyze(
        service,
        [service.files.reference("downloads", "Film.2023")],
        manual_overrides={"selected_title": "Film"},
    )
    assert context["identity"]["selected_by"] == "automatic"
    preview = service.preview(context, rules={"template_id": "season"})
    assert not preview["can_apply"]
    assert preview["conflicts"] == []
    assert preview["manual_confirmations"] == [{
        "code": "identity_confirmation_required",
        "message": "自动匹配的影视身份尚未确认，请人工选择或确认",
    }]
    with pytest.raises(NamingError) as blocked:
        asyncio.run(service.apply(preview["preview_token"], preview["context_revision"], "season-identity"))
    assert blocked.value.code == "manual_confirmation_required"


def client_for(service):
    app = FastAPI()
    app.include_router(create_router(lambda request: service, authenticate=lambda request: request.headers.get("X-Session"), csrf=lambda request: request.headers.get("X-CSRF-Token") == "csrf"))
    return TestClient(app)


def test_api_auth_csrf_confirmation_and_unified_validation(setup):
    service, root, _ = setup
    (root / "a.mkv").write_bytes(b"a")
    client = client_for(service)
    body = {"resources": [service.files.reference("downloads", "a.mkv")], "manual_overrides": {"selected_title": "Film"}}
    assert client.post("/api/v2/resources/resolve", json=body).status_code == 401
    assert client.post("/api/v2/resources/resolve", json=body, headers={"X-Session": "session"}).status_code == 403
    headers = {"X-Session": "session", "X-CSRF-Token": "csrf"}
    response = client.post("/api/v2/resources/resolve", json=body, headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    context = response.json()["context"]
    response = client.post("/api/v2/naming/preview", json={"context": context}, headers=headers)
    preview = response.json()
    payload = {"preview_token": preview["preview_token"], "expected_revision": preview["context_revision"], "idempotency_key": "key", "confirm": "true"}
    assert client.post("/api/v2/naming/apply", json=payload, headers=headers).status_code == 422
    payload["confirm"] = True
    response = client.post("/api/v2/naming/apply", json=payload, headers=headers)
    assert response.status_code == 202
    assert response.json()["task"]["kind"] == "processing"
    assert (root / "a.mkv").exists()  # enqueue is not inline rename
    invalid = client.get("/api/v2/metadata/search?query=", headers=headers)
    assert invalid.status_code == 422 and invalid.json()["detail"]["code"] == "invalid_request"
    assert invalid.headers["cache-control"] == "no-store"
    assert client.post("/api/v2/naming/preview", json={"context": context, "overwrite": True}, headers=headers).status_code == 422
    assert client.post("/api/v2/naming/preview", json={"context": context}, headers={**headers, "X-Session": "another"}).status_code == 403


def test_stale_source_apply_and_missing_integrations_fail_closed(setup):
    service, root, _ = setup
    (root / "a.mkv").write_bytes(b"a")
    context = analyze(service, [service.files.reference("downloads", "a.mkv")], manual_overrides={"selected_title": "Film"})
    preview = service.preview(context)
    (root / "a.mkv").write_bytes(b"new content")
    with pytest.raises(NamingError) as conflict:
        asyncio.run(service.apply(preview["preview_token"], preview["context_revision"], "drift"))
    assert conflict.value.status == 409
    assert not service.executor.audit.records
    context = analyze(service, [service.files.reference("downloads", "a.mkv")], manual_overrides={"selected_title": "Film"})
    preview = service.preview(context)
    service.tasks = None
    with pytest.raises(NamingError) as blocked:
        asyncio.run(service.apply(preview["preview_token"], preview["context_revision"], "integration"))
    assert blocked.value.status == 503


class RecognitionProvider(Provider):
    name = "tmdb"
    async def search(self, query, media_type, year=None, page=1):
        return {"items": [{"provider": "tmdb", "id": "100", "media_type": "movie", "title": "Film", "year": "2023"}] if media_type == "movie" else [], "page": 1, "total_pages": 1, "warnings": []}
    async def detail(self, selection):
        return {"identity": {"media_type": selection["media_type"], "tmdb_id": selection["id"], "bangumi_id": None, "selected_by": "manual", "confirmed": True, "confidence": None}, "titles": [title("Film", "en", "original", "tmdb", "100")], "metadata": {"year": field(2023, "tmdb", "100")}, "episodes": [], "season": None}


def test_automatic_match_is_a_proposal_until_identity_confirmed(setup):
    service, root, _ = setup
    (root / "Film.2023.mkv").write_bytes(b"file")
    refs = [service.files.reference("downloads", "Film.2023.mkv")]
    service.resolver = MetadataResolver({"tmdb": RecognitionProvider()})
    context = analyze(service, refs, manual_overrides={"selected_title": "Film"})
    assert context["identity_candidates"][0]["id"] == "100"
    assert context["metadata"]["year"]["value"] == 2023
    assert context["identity"]["selected_by"] == "automatic"
    proposal = service.preview(context)
    assert not proposal["can_apply"]
    assert proposal["conflicts"] == []
    assert proposal["manual_confirmation_count"] == 1
    assert proposal["manual_confirmations"][0]["code"] == "identity_confirmation_required"
    confirmed = analyze(service, refs, context=context, selection={"provider": "tmdb", "id": "100", "media_type": "movie"})
    assert confirmed["identity"]["confirmed"]
    assert service.preview(confirmed)["can_apply"]


def test_only_verified_download_origin_is_used_and_mapping_requires_confirmation(setup):
    service, root, _ = setup
    (root / "S01E01.mkv").write_bytes(b"file")
    ref = service.files.reference("downloads", "S01E01.mkv")
    origin = {"job_id": 9, "season_id": "10", "ep_id": "20", "cid": "30", "episode_index": 8, "original_resource": ref, "original_title": "Show", "api_mode": "web", "bvid": None}
    service.resolver = MetadataResolver({"tmdb": RecognitionProvider()})
    unverified = analyze(service, [ref], origins=[origin], selection={"provider": "tmdb", "id": "100", "media_type": "series"}, manual_overrides={"selected_title": "Series"})
    assert unverified["files"][0]["episode"]["episode"]["value"] == 1
    assert not unverified["origins"]
    class Origins:
        def get(self, job_id, resource):
            assert job_id == 9
            return deepcopy(origin)
    service.origins = Origins()
    context = analyze(service, [ref], origins=[origin], selection={"provider": "tmdb", "id": "100", "media_type": "series"}, manual_overrides={"selected_title": "Series"})
    assert context["files"][0]["episode"]["episode"]["value"] == 8
    continuous = service.preview(context)
    assert continuous["can_apply"]
    assert ".E08." in continuous["operations"][0]["target_relative_path"]
    context["revision"] = continuous["context_revision"]
    seasonal = {"episode_numbering": "seasonal", "explicit_season": 2}
    unresolved = service.preview(context, rules=seasonal)
    assert not unresolved["can_apply"]
    assert unresolved["conflicts"] == []
    assert unresolved["manual_confirmations"][0]["code"] == "mapping_confirmation_required"
    context["revision"] = unresolved["context_revision"]
    manual = service.preview(context, {"file_values": {"S01E01.mkv": {"season": 2, "episode": 3}}}, rules=seasonal)
    assert manual["can_apply"] and "S02E03" in manual["operations"][0]["target_relative_path"]


def test_enqueue_ambiguous_failure_reuses_prepared_batch_and_queue_key(setup):
    service, root, _ = setup
    (root / "a.mkv").write_bytes(b"A")
    context = analyze(service, [service.files.reference("downloads", "a.mkv")], manual_overrides={"selected_title": "A"})
    preview = service.preview(context)
    original = service.tasks.enqueue_naming
    calls = []
    def enqueue(batch_id, key, undo=False):
        calls.append((batch_id, key))
        if len(calls) == 1:
            raise RuntimeError("transient queue failure")
        return original(batch_id, key, undo)
    service.tasks.enqueue_naming = enqueue
    with pytest.raises(RuntimeError):
        asyncio.run(service.apply(preview["preview_token"], preview["context_revision"], "first"))
    result = asyncio.run(service.apply(preview["preview_token"], preview["context_revision"], "retry"))
    assert len(service.executor.audit.records) == 1
    assert calls[0] == calls[1]
    assert result["batch_id"] == calls[0][0]


@pytest.mark.parametrize("overrides", [{"file_values": []}, {"file_values": {"a.mkv": {"frame_rate": "1/0"}}}, {"sidecars": "a.srt"}])
def test_invalid_overrides_do_not_mutate_context_revision(setup, overrides):
    service, root, _ = setup
    (root / "a.mkv").write_bytes(b"A")
    context = analyze(service, [service.files.reference("downloads", "a.mkv")], manual_overrides={"selected_title": "A"})
    with pytest.raises(NamingError):
        service.preview(context, overrides)
    assert service.contexts[context["context_id"]]["revision"] == context["revision"]


def test_processing_handlers_receive_minimal_inputs_and_return_real_results(setup):
    from bili_remote.v2.naming.tasks import NamingTaskHandlers
    service, root, _ = setup
    (root / "a.mkv").write_bytes(b"A")
    ref = service.files.reference("downloads", "a.mkv")
    context = analyze(service, [ref], manual_overrides={"selected_title": "A"})
    preview = service.preview(context)
    async def run():
        handlers = NamingTaskHandlers(service)
        result = await handlers.handle("v2_media_probe", {"resources": [ref], "expected_fingerprints": {"a.mkv": ref["fingerprint"]}})
        assert result["files"][0]["probe"]["video_codec"]["value"] == "H265"
        assert "context" not in result and "metadata" not in result
        with pytest.raises(NamingError):
            await handlers.handle("v2_rename_apply", {"batch_id": "a", "overview": "draft"})
        task = await service.apply(preview["preview_token"], preview["context_revision"], "handler")
        result = await handlers.handle("v2_rename_apply", {"batch_id": task["batch_id"]})
        assert result["changed_count"] == 1 and result["status"] == "completed"
        assert not (root / "a.mkv").exists()
    asyncio.run(run())


def test_memory_routes_revision_conflict_and_generated_content_rejection(setup):
    from bili_remote.v2.naming.memory import NamingMemoryService
    from tests.v2.naming.test_memory import Repository
    service, _, _ = setup
    service.memory = NamingMemoryService(Repository())
    client = client_for(service)
    headers = {"X-Session": "session", "X-CSRF-Token": "csrf"}
    body = {"scope": "global", "expected_revision": 0, "naming_rule": {"group": "Example"}}
    response = client.put("/api/v2/naming/memory", json=body, headers=headers)
    assert response.status_code == 200 and response.json()["revision"] == 1
    assert client.put("/api/v2/naming/memory", json=body, headers=headers).status_code == 409
    read = client.get("/api/v2/naming/memory", headers=headers)
    assert read.json()["effective"]["naming_rule"]["group"] == "Example"
    assert read.headers["cache-control"] == "no-store"
    body["subtitle_text"] = "must not persist"
    assert client.put("/api/v2/naming/memory", json=body, headers=headers).status_code == 422


def test_continuous_and_seasonal_batch_preview_apply_collision_and_undo(setup):
    service, root, _ = setup
    folder = root / "season"
    folder.mkdir()
    sources = ("S200 - 200.mkv", "第201集.mkv", "S200 - 200.chs.srt")
    for name in sources:
        (folder / name).write_bytes(name.encode())
    context = analyze(service, [service.files.reference("downloads", folder.name)],
                      manual_overrides={"selected_title": "Mortal’s Journey", "year": 2020})
    rules = {"template_id": "series"}
    automatic = service.preview(context, rules=rules)
    assert automatic["can_apply"]
    assert any(".E200." in item["target_relative_path"] for item in automatic["operations"])
    assert all(".S200" not in item["target_relative_path"] and ".S01" not in item["target_relative_path"]
               for item in automatic["operations"])
    context["revision"] = automatic["context_revision"]
    seasonal_rules = {**rules, "episode_numbering": "seasonal"}
    missing = service.preview(context, rules=seasonal_rules)
    assert not missing["can_apply"]
    assert {item["code"] for item in missing["conflicts"]} == {"missing_season"}
    context["revision"] = missing["context_revision"]
    overrides = {"episode_overrides": {
        "season/S200 - 200.mkv": {"season": 1, "episode": 178},
        "season/第201集.mkv": {"season": 1, "episode": 179},
    }}
    old = service.preview(context, overrides, rules=seasonal_rules)
    context["revision"] = old["context_revision"]
    plan = service.preview(context, overrides, rules=seasonal_rules)
    assert plan["can_apply"] and plan["episode"]["season"]["value"] == 1
    targets = {item["target_relative_path"] for item in plan["operations"]}
    assert len(targets) == 3
    assert all("/Mortals.Journey.2020.S01E" in path for path in targets)
    with pytest.raises(NamingError, match="最新结果"):
        asyncio.run(service.apply(old["preview_token"], old["context_revision"], "obsolete"))
    collision = root / next(path for path in targets if path.endswith(".mkv"))
    collision.write_bytes(b"existing")
    blocked = service.preview(context, overrides, rules=seasonal_rules)
    assert not blocked["can_apply"] and blocked["conflicts"]
    assert collision.read_bytes() == b"existing"
    collision.unlink()
    final = service.preview(context, overrides, rules=seasonal_rules)
    assert targets == {item["target_relative_path"] for item in final["operations"]}
    queued = asyncio.run(service.apply(final["preview_token"], final["context_revision"], "confirmed"))
    service.executor.execute(queued["batch_id"])
    assert {str(path.relative_to(root)) for path in folder.iterdir()} == targets
    undo = asyncio.run(service.undo_preview(queued["batch_id"]))
    restored = asyncio.run(service.apply(undo["preview_token"], undo["context_revision"], "undo", undo=True))
    service.executor.execute(restored["batch_id"])
    assert {file.name: file.read_bytes() for file in folder.iterdir()} == {name: name.encode() for name in sources}
