import asyncio
import copy

import pytest

from bili_remote.v2.subtitle import SubtitleRuleError, SubtitleRuleMemory, identity_key


class MemoryRepositoryStub:
    """Contract test double; it is not the application's persistence backend."""
    def __init__(self):
        self.rows = {}
        self.calls = []

    def get(self, identity, season):
        self.calls.append(("get", identity, season))
        if identity is None:
            return {"revision": 2, "layers": [
                {"scope": "global", "revision": 2, "subtitle_rule": {
                    "template_id": "tv_episode", "cast_limit": 5, "labels": {"cast": "演员"},
                }},
            ]}
        key = identity_key(identity)
        global_row = self.rows.get(("global", "global"))
        series_row = self.rows.get(("series", key))
        layers = []
        if global_row:
            layers.append({"scope": "global", "revision": global_row["revision"],
                           "subtitle_rule": global_row["rules"]["subtitle_rule"]})
        if series_row:
            layers.append({"scope": "series", "revision": series_row["revision"],
                           "subtitle_rule": series_row["rules"]["subtitle_rule"]})
        return {"revision": (series_row or global_row or {}).get("revision", 0), "layers": layers}

    def put(self, scope, key, rules, expected_revision):
        self.calls.append(("put", scope, key, copy.deepcopy(rules), expected_revision))
        row_key = (scope, key)
        current = self.rows.get(row_key)
        revision = current["revision"] if current else 0
        if revision != expected_revision:
            raise RuntimeError("revision conflict")
        record = {"revision": revision + 1, "rules": copy.deepcopy(rules)}
        self.rows[row_key] = record
        return record


IDENTITY = {"media_type": "series", "tmdb_id": "12345", "confirmed": True, "selected_by": "manual"}


def test_resolve_reuses_global_and_series_rule_layers_and_current_override_wins():
    repository = MemoryRepositoryStub()
    memory = SubtitleRuleMemory(repository)
    global_rule = {"template_id": "tv_episode", "cast_limit": 8, "labels": {"cast": "卡司"}}
    asyncio.run(memory.save(scope="global", rule=global_rule, expected_revision=0))
    series_rule = {"template_id": "tv_episode", "cast_limit": 3, "labels": {"genres": "题材"}}
    asyncio.run(memory.save(scope="series", identity=IDENTITY, rule=series_rule, expected_revision=0))

    resolved = asyncio.run(memory.resolve(IDENTITY))
    assert resolved["subtitle_rule"]["cast_limit"] == 3
    assert resolved["subtitle_rule"]["labels"]["cast"] == "卡司"
    assert resolved["subtitle_rule"]["labels"]["genres"] == "题材"
    assert resolved["identity_key"] == "tmdb:tv:12345"
    assert [layer["scope"] for layer in resolved["layers"]] == ["global", "series"]
    overridden = asyncio.run(memory.resolve(IDENTITY, overrides={"cast_limit": 5}))
    assert overridden["subtitle_rule"]["cast_limit"] == 5


def test_memory_can_be_reopened_and_only_persists_rule_configuration():
    repository = MemoryRepositoryStub()
    first = SubtitleRuleMemory(repository)
    result = asyncio.run(first.save(scope="series", identity=IDENTITY, rule={
        "template_id": "anime", "cast_limit": "all", "custom_template": None,
    }, expected_revision=0))
    reopened = SubtitleRuleMemory(repository)
    resolved = asyncio.run(reopened.resolve(IDENTITY))
    assert result["revision"] == 1
    assert resolved["subtitle_rule"]["template_id"] == "anime"
    serialized = repr(repository.rows)
    assert "subtitle_text" not in serialized
    assert "draft" not in serialized
    assert "release_title" not in serialized
    put_call = next(call for call in repository.calls if call[0] == "put")
    assert set(put_call[3]) == {"subtitle_rule"}


def test_unconfirmed_identity_never_uses_series_memory():
    repository = MemoryRepositoryStub()
    memory = SubtitleRuleMemory(repository)
    result = asyncio.run(memory.resolve({"media_type": "series", "tmdb_id": "12345", "confirmed": False}))
    assert result["identity_key"] is None
    assert repository.calls[-1] == ("get", None, None)


def test_series_memory_requires_confirmed_identity_and_rules_reject_text_fields():
    memory = SubtitleRuleMemory(MemoryRepositoryStub())
    with pytest.raises(SubtitleRuleError):
        asyncio.run(memory.save(scope="series", identity={**IDENTITY, "confirmed": False},
                                rule={"template_id": "tv_episode"}, expected_revision=0))
    with pytest.raises(SubtitleRuleError):
        asyncio.run(memory.save(scope="global", rule={"subtitle_text": "finished copy"}, expected_revision=0))


def test_identity_keys_follow_shared_tmdb_and_bangumi_namespaces():
    assert identity_key({"media_type": "movie", "tmdb_id": "8", "confirmed": True}) == "tmdb:movie:8"
    assert identity_key({"media_type": "anime", "bangumi_id": "900", "confirmed": True}) == "bangumi:subject:900"


def test_memory_repository_revision_conflict_is_not_hidden_by_adapter():
    repository = MemoryRepositoryStub()
    memory = SubtitleRuleMemory(repository)
    asyncio.run(memory.save(scope="global", rule={"cast_limit": 5}, expected_revision=0))
    with pytest.raises(RuntimeError, match="revision conflict"):
        asyncio.run(memory.save(scope="global", rule={"cast_limit": 8}, expected_revision=0))
