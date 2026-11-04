from bili_remote.v2.subtitle import render


def test_tv_season_uses_selected_season_episode_count_not_series_total():
    context = {
        "revision": 11,
        "identity": {"media_type": "series"},
        "metadata": {
            "title_zh": {"value": "迷雾档案", "source": "tmdb"},
            "genres": {"value": ["剧情", "悬疑"], "source": "tmdb"},
            # Deliberately unrelated series-level total: the subtitle must only
            # read EpisodeSelection.season_episode_count.
            "episode_count": {"value": 123, "source": "tmdb"},
        },
        "episode": {
            "season": {"value": 1, "source": "tmdb"},
            "scope": "season",
            "season_episode_count": {"value": 52, "source": "tmdb", "confirmed": True},
        },
        "cast": [
            {"person_id": "a", "role": "actor", "name": {"value": "演员甲", "source": "tmdb"}},
            {"person_id": "b", "role": "actor", "name": {"value": "演员乙", "source": "tmdb"}},
        ],
    }
    result = render(context, {"template_id": "tv_season"})
    assert result["text"] == "迷雾档案 第一季 全52集 | 类型：剧情 悬疑 | 主演：演员甲 演员乙"
    assert result["missing_fields"] == []
    assert result["context_revision"] == 11


def test_tv_season_does_not_infer_count_from_other_context_fields():
    context = {
        "revision": 12,
        "metadata": {"title_zh": "剧名", "genres": ["剧情"], "episode_count": 100},
        "episode": {"season": 2, "scope": "season"},
        "cast": [],
    }
    result = render(context, {"template_id": "tv_season"})
    assert result["text"] == "剧名 第二季 | 类型：剧情"
    assert result["missing_fields"] == ["season_episode_count", "cast"]
