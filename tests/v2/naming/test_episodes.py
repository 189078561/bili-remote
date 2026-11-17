import pytest

from bili_remote.v2.naming.episodes import parse_episode, resolve_episode, mapping_key
from bili_remote.v2.naming.errors import NamingError


@pytest.mark.parametrize("name,season,episode", [("show.S01E02.mkv", 1, 2), ("S01 EP02.mp4", 1, 2), ("S01 - 02.mkv", 1, 2), ("第02集.mkv", None, 2), ("第02话.mp4", None, 2), ("EP02.mkv", None, 2), ("01.mkv", None, 1)])
def test_common_episode_syntax(name, season, episode):
    assert parse_episode(name) == (season, episode, False)


def test_mapping_priority_cross_season_and_series_identity():
    origin = {"season_id": "10", "ep_id": "20", "cid": "30", "episode_index": 8}
    identity = {"tmdb_id": "100", "media_type": "series"}
    mapping = {"bili_season_id": "10", "bili_ep_id": "20", "bili_cid": "30", "tmdb_id": "100", "tmdb_season": 2, "tmdb_episode": 3, "method": "manual", "confirmed": True}
    result = resolve_episode("S01E01.mkv", origin, identity, [mapping])
    assert result["episode"]["season"]["value"] == 2
    assert result["episode"]["episode"]["value"] == 3
    assert result["episode"]["mapping_key"] == mapping_key(origin, identity)
    wrong = resolve_episode("S01E01.mkv", origin, {**identity, "tmdb_id": "101"}, [mapping])
    assert wrong["episode"]["episode"]["value"] == 8
    assert wrong["mapping"] is None
    assert wrong["episode"]["season"]["value"] is None


def test_tmdb_selected_season_real_episode_count_not_total_or_order():
    episodes = [{"season_number": 2, "episode_number": n, "id": str(n)} for n in (1, 2, 3)]
    result = resolve_episode("S02E02.mkv", tmdb_episodes=episodes)
    assert result["episode"]["episode"]["source"] == "tmdb"
    assert result["episode"]["season_episode_count"]["value"] == 3
    unresolved = resolve_episode("title.mkv", tmdb_episodes=episodes)
    assert unresolved["episode"]["episode"]["value"] is None


def test_start_descending_offset_override_and_manual_lock():
    rules = {"episode_mode": "start", "start_episode": 10, "episode_step": 2, "episode_direction": "decrement"}
    episode = resolve_episode("EP01.mkv", rules=rules, index=2)["episode"]
    assert episode["episode"]["value"] == 6
    assert resolve_episode("EP01.mkv", rules={"episode_mode": "offset", "episode_rules": {"offset": 4}})["episode"]["episode"]["value"] == 5
    episode["episode"]["source_ref"] = "explicit-user-override"
    result = resolve_episode("EP02.mkv", previous=episode, rules={"episode_overrides": {"EP02.mkv": 99}})
    assert result["episode"]["episode"]["value"] == 6  # prior manual lock wins re-identification
    with pytest.raises(NamingError):
        resolve_episode("EP01.mkv", rules={"episode_mode": "start", "start_episode": 1, "episode_direction": "decrement"}, index=2)


def test_ambiguous_and_unknown_never_guess():
    assert parse_episode("S01E01 S01E02.mkv")[2]
    assert resolve_episode("Some film 2023.mkv")["episode"]["episode"]["value"] is None

@pytest.mark.parametrize("filename", ["凡人修仙传 第200集.mp4", "凡人.S200E200.mp4", "S200 - 200 凡人.mp4"])
def test_continuous_episode_never_promotes_ordinal_to_season(filename):
    result = resolve_episode(filename, rules={"episode_numbering": "seasonal", "season_default": 1})
    assert result["episode"]["season"]["value"] is None
    assert result["episode"]["episode"]["value"] == 200


def test_bili_episode_index_and_parts_are_separate_from_tmdb_season():
    identity = {"tmdb_id": "100"}
    evidence = [{"season_number": 2, "episode_number": 8, "id": "123"}]
    result = resolve_episode("Show 第200集.mp4", {"ep_id": "9", "episode_index": 8}, identity, tmdb_episodes=evidence)
    assert result["episode"]["episode"]["value"] == 200
    assert result["episode"]["season"]["value"] is None
    part = resolve_episode("Show.P08.mp4", {"bvid": "BV1", "episode_index": 8}, identity, tmdb_episodes=evidence)
    assert part["episode"]["episode"]["value"] is None
    assert "bili_part_not_episode" in {item["code"] for item in part["warnings"]}
    seasonal = resolve_episode("Show.E08.mp4", tmdb_episodes=evidence)
    assert seasonal["episode"]["season"]["value"] == 2
    assert seasonal["episode"]["season"]["source"] == "tmdb"
    continuous = resolve_episode("Show.S02E08.mp4", tmdb_episodes=evidence, rules={"episode_numbering": "continuous"})
    assert continuous["episode"]["season"]["value"] is None
    assert continuous["episode"]["episode"]["value"] == 8
