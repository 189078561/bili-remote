from copy import deepcopy

import pytest

from bili_remote.v2.naming.memory import NamingMemoryService, validate_memory_rules
from bili_remote.v2.naming.errors import NamingError


class Repository:
    def __init__(self):
        self.records = {}

    def get(self, identity, season):
        # Deliberately overbroad; the service must filter uncertain identities.
        return {"layers": list(deepcopy(self.records).values())}

    def put(self, scope, key, rules, expected_revision):
        index = (scope, key["identity_key"], key["season_key"])
        old = self.records.get(index, {})
        if old.get("revision", 0) != expected_revision:
            raise NamingError("memory_revision_conflict", "规则版本冲突", status=409)
        record = {"scope": scope, **key, **deepcopy(rules), "revision": expected_revision + 1}
        self.records[index] = record
        return deepcopy(record)


def test_three_layers_manual_precedence_and_uncertain_identity():
    repository = Repository()
    memory = NamingMemoryService(repository)
    memory.put("global", {}, {"naming_rule": {"group": "global", "source": "WEB-DL"}}, 0)
    memory.put("series", {"identity_key": "tmdb:tv:100"}, {"selected_title": "Selected", "naming_rule": {"group": "series"}}, 0)
    memory.put("season", {"identity_key": "tmdb:tv:100", "season_key": 2}, {"naming_rule": {"group": "season"}, "episode_rules": {"offset": 3}}, 0)
    result = memory.get("tmdb:tv:100", 2)
    assert result["effective"]["naming_rule"]["group"] == "season"
    assert result["effective"]["selected_title"] == "Selected"
    assert result["effective"]["episode_rules"]["offset"] == 3
    assert memory.get("tmdb:tv:100", 1)["effective"]["naming_rule"]["group"] == "series"
    manual = memory.get("tmdb:tv:100", 2, {"naming_rule": {"group": "manual"}})
    assert manual["effective"]["naming_rule"]["group"] == "manual"
    uncertain = memory.get({"tmdb_id": "100", "confirmed": False}, 2)
    assert len(uncertain["layers"]) == 1
    assert uncertain["effective"]["naming_rule"]["group"] == "global"
    assert repository.records[("season", "tmdb:tv:100", 2)]["naming_rule"]["group"] == "season"


def test_repository_revision_conflict_and_recreated_service():
    repository = Repository()
    service = NamingMemoryService(repository)
    service.put("global", {}, {"aliases": ["Alias"]}, 0)
    with pytest.raises(NamingError) as error:
        service.put("global", {}, {"aliases": ["New"]}, 0)
    assert error.value.status == 409
    assert NamingMemoryService(repository).get()["effective"]["aliases"] == ["Alias"]


@pytest.mark.parametrize("rules", [
    {"overview": "draft"}, {"generated_names": ["name.mkv"]}, {"subtitle_text": "draft"},
    {"naming_rule": {"project_id": 1}}, {"naming_rule": {"source": {"value": "WEB-DL", "html": "draft"}}},
    {"subtitle_rule": {"visible_fields": ["platform"]}}, {"subtitle_rule": {"custom_template": "{platform}"}},
    {"subtitle_rule": {"labels": {"platform": "platform"}}}, {"episode_rules": {"overrides": {"a": {"episode": 1, "bbcode": "draft"}}}},
    {"episode_rules": {"mappings": [{"project_id": 1}]}},
])
def test_rejects_generated_content_and_nested_bypasses(rules):
    with pytest.raises(NamingError):
        validate_memory_rules(rules)


@pytest.mark.parametrize("scope,key", [("global", {"identity_key": "tmdb:tv:1"}), ("series", {"identity_key": "tmdb:tv:1", "season_key": 1}), ("season", {"identity_key": "tmdb:tv:1"}), ("season", {"identity_key": "http://bad", "season_key": 1})])
def test_scope_keys_are_strict(scope, key):
    with pytest.raises(NamingError):
        NamingMemoryService(Repository()).put(scope, key, {}, 0)
