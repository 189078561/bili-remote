from bili_remote.v2.subtitle import render


def anime_context():
    return {
        "revision": 3,
        "identity": {"media_type": "anime", "bangumi_id": "123"},
        "metadata": {
            "title_zh": {"value": "魔法少女育成计划restart", "source": "tmdb"},
            "title_zh_hant": {"value": "魔法少女育成計畫restart", "source": "tmdb"},
            "title_ja": {"value": "魔法少女育成計画restart", "source": "bangumi"},
            "original_title": {"value": "魔法少女育成計画restart", "source": "tmdb"},
            "title_en": {"value": "Magical Girl Raising Project Restart", "source": "tmdb"},
            "genres": {"value": ["动画", "奇幻"], "source": "tmdb"},
        },
        "episode": {
            "season": {"value": 1, "source": "tmdb"},
            "episode": {"value": 5, "source": "tmdb"},
        },
        "voice_cast": [
            {"person_id": "tm-1", "role": "voice_actor", "name": {"value": "TMDB声优", "source": "tmdb"}},
            {"person_id": "bg-1", "role": "voice_actor", "name": {"value": "声优甲", "source": "bangumi"}},
            {"person_id": "bg-2", "role": "voice_actor", "name": {"value": "声优乙", "source": "bangumi"}},
        ],
    }


def test_anime_multilingual_names_deduplicate_and_bangumi_voice_cast_wins():
    result = render(anime_context(), {"template_id": "anime"})
    assert result["text"] == (
        "魔法少女育成计划restart 魔法少女育成計畫restart 魔法少女育成計画restart 第01季 第05集"
        " | 类型：动画 / 奇幻 | 声优：声优甲 / 声优乙"
    )
    assert result["missing_fields"] == []
    assert result["context_revision"] == 3


def test_anime_keeps_manual_voice_over_bangumi_refresh_and_can_change_name_order():
    context = anime_context()
    context["voice_cast"].append({
        "person_id": "user-1", "role": "voice_actor",
        "name": {"value": "人工声优", "source": "manual", "manual": True, "confirmed": True},
    })
    result = render(context, {
        "template_id": "anime",
        "name_order": ["en", "zh", "ja", "zh_hant", "original"],
        "cast_limit": "all",
        "list_separator": " / ",
    })
    assert result["text"].startswith("Magical Girl Raising Project Restart 魔法少女育成计划restart")
    assert "声优：人工声优" in result["text"]
    assert result["missing_fields"] == []
