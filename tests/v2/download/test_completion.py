from __future__ import annotations

import asyncio

import pytest

from bili_remote.v2.download import DownloadCompletionAdapter


class NamingFake:
    def __init__(self, result=None, error=None):
        self.calls = []
        self.result = result if result is not None else {"context": {"revision": 1}, "warnings": []}
        self.error = error

    async def analyze(self, **kwargs):
        await asyncio.sleep(0)
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def origin(**overrides):
    value = {
        "bvid": "BVsynthetic123",
        "season_id": 42,
        "ep_id": 8,
        "cid": 9001,
        "original_title": "合成剧集标题",
        "episode_index": 3,
        "api_mode": "tv",
        "original_resource": {
            "root_key": "downloads",
            "relative_path": "Synthetic/episode.mkv",
            "kind": "file",
            "fingerprint": "synthetic-sha256",
        },
    }
    value.update(overrides)
    return value


@pytest.mark.asyncio
async def test_completion_passes_contract_fields_once_and_is_idempotent():
    naming = NamingFake()
    adapter = DownloadCompletionAdapter(naming)
    first, second = await asyncio.gather(
        adapter.on_completed(71, [origin()]),
        adapter.on_completed(71, [origin()]),
    )
    assert first == second
    assert len(naming.calls) == 1
    assert naming.calls[0]["origins"] == [{
        "job_id": 71,
        "bvid": "BVsynthetic123",
        "season_id": "42",
        "ep_id": "8",
        "cid": "9001",
        "original_title": "合成剧集标题",
        "episode_index": 3,
        "api_mode": "tv",
        "original_resource": {
            "root_key": "downloads",
            "relative_path": "Synthetic/episode.mkv",
            "kind": "file",
            "fingerprint": "synthetic-sha256",
        },
    }]
    assert first.context == {"revision": 1}
    assert first.naming_available is True


@pytest.mark.asyncio
async def test_missing_origin_facts_remain_null_without_filename_inference():
    naming = NamingFake()
    adapter = DownloadCompletionAdapter(naming)
    proposal = await adapter.on_completed(72, [origin(
        bvid=None, season_id=None, ep_id=None, cid=None, original_title=None, episode_index=None
    )])
    sent = naming.calls[0]["origins"][0]
    assert [sent[key] for key in ("bvid", "season_id", "ep_id", "cid", "original_title", "episode_index")] == [None] * 6
    assert proposal.resources[0]["relative_path"] == "Synthetic/episode.mkv"
    assert proposal.warnings == ()


@pytest.mark.asyncio
async def test_naming_outage_keeps_download_completion_and_returns_safe_warning():
    adapter = DownloadCompletionAdapter(NamingFake(error=RuntimeError("secret cookie=do-not-return")))
    proposal = await adapter.on_completed(73, [origin()])
    assert proposal.job_id == 73
    assert proposal.naming_available is False
    assert "do-not-return" not in str(proposal.warnings)
    assert proposal.warnings[0]["code"] == "recognition_unavailable"


@pytest.mark.asyncio
async def test_invalid_resource_path_is_rejected_without_calling_naming():
    naming = NamingFake()
    adapter = DownloadCompletionAdapter(naming)
    proposal = await adapter.on_completed(74, [origin(original_resource={
        "root_key": "downloads", "relative_path": "../outside.mkv", "kind": "file", "fingerprint": "x"
    })])
    assert proposal.naming_available is False
    assert proposal.warnings[0]["code"] == "invalid_download_origin"
    assert naming.calls == []


@pytest.mark.asyncio
async def test_resource_path_must_use_posix_separators():
    naming = NamingFake()
    adapter = DownloadCompletionAdapter(naming)
    proposal = await adapter.on_completed(75, [origin(original_resource={
        "root_key": "downloads", "relative_path": "Synthetic\\episode.mkv", "kind": "file", "fingerprint": "x"
    })])
    assert proposal.naming_available is False
    assert naming.calls == []


@pytest.mark.asyncio
async def test_missing_completed_file_reference_returns_warning_without_guessing():
    naming = NamingFake()
    adapter = DownloadCompletionAdapter(naming)
    proposal = await adapter.on_completed(76, [])
    assert proposal.naming_available is False
    assert proposal.warnings[0]["code"] == "invalid_download_origin"
    assert proposal.resources == ()
    assert naming.calls == []
