from bili_remote.v2.subtitle import render


def context():
    return {
        "revision": 7,
        "identity": {"media_type": "series"},
        "metadata": {
            "title_zh": {"value": "长夜", "source": "tmdb", "manual": False},
            "title_zh_hant": {"value": "長夜", "source": "tmdb"},
            "title_en": {"value": "Long Night", "source": "tmdb"},
            "genres": {"value": ["剧情", "历史"], "source": "tmdb"},
        },
        "episode": {
            "season": {"value": 1, "source": "tmdb"},
            "episode": {"value": 2, "source": "manual", "manual": True},
        },
        "cast": [
            {"person_id": "a", "role": "actor", "name": {"value": "演员甲", "source": "tmdb"}},
            {"person_id": "b", "role": "actor", "name": {"value": "演员乙", "source": "tmdb"}},
            {"person_id": "c", "role": "actor", "name": {"value": "演员丙", "source": "tmdb"}},
            {"person_id": "d", "role": "actor", "name": {"value": "演员丁", "source": "tmdb"}},
        ],
    }


def test_tv_episode_official_sample_and_context_revision():
    result = render(context())
    assert result["text"] == "长夜 第02集 | 类型：剧情 历史 | 主演：演员甲 演员乙 演员丙"
    assert result["missing_fields"] == []
    assert result["context_revision"] == 7


def test_tv_episode_field_visibility_order_labels_and_actor_preferences():
    rule = {
        "field_order": ["cast", "title", "episode", "genres"],
        "visible_fields": ["title", "episode", "genres", "cast"],
        "cast_limit": 8,
        "cast_order": ["d", "b"],
        "labels": {"genres": "题材", "cast": "主演阵容"},
        "label_separator": ": ",
        "section_separator": " / ",
    }
    result = render(context(), rule)
    assert result["text"] == "主演阵容: 演员丁 演员乙 演员甲 演员丙 / 长夜 第02集 / 题材: 剧情 历史"
    assert result["missing_fields"] == []


def test_manual_field_is_rendered_without_mutating_context():
    source = context()
    before = repr(source)
    result = render(source)
    assert result["text"].startswith("长夜 第02集")
    assert source["episode"]["episode"]["manual"] is True
    assert repr(source) == before


def test_missing_values_do_not_create_empty_labels_or_separators():
    source = context()
    source["metadata"]["genres"]["value"] = []
    source["cast"] = []
    result = render(source)
    assert result["text"] == "长夜 第02集"
    assert result["missing_fields"] == ["genres", "cast"]


def test_hidden_missing_fields_are_not_reported_as_missing():
    source = context()
    source["metadata"]["genres"]["value"] = []
    source["cast"] = []
    result = render(source, {"visible_fields": ["title", "episode"], "field_order": ["title", "episode"]})
    assert result["text"] == "长夜 第02集"
    assert result["missing_fields"] == []


def test_cast_limit_supports_three_five_eight_and_all():
    source = context()
    source["cast"] = [
        {"person_id": str(index), "role": "actor", "name": {"value": f"演员{index}", "source": "tmdb"}}
        for index in range(1, 11)
    ]
    for limit, expected_count in ((3, 3), (5, 5), (8, 8), ("all", 10)):
        result = render(source, {"cast_limit": limit})
        rendered_cast = result["text"].split("主演：", 1)[1].split(" | ", 1)[0]
        assert len(rendered_cast.split()) == expected_count


def test_renderer_does_not_open_files_or_make_network_connections(monkeypatch):
    import builtins
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("renderer attempted external I/O")

    monkeypatch.setattr(builtins, "open", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    assert render(context())["text"].startswith("长夜 第02集")
