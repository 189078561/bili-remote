from bili_remote.v2.subtitle import render


def movie_context():
    return {
        "revision": 4,
        "identity": {"media_type": "movie", "tmdb_id": "42", "selected_by": "manual", "confirmed": True},
        "metadata": {
            "title_zh": {"value": "星际回声", "source": "tmdb"},
            "original_title": {"value": "Echoes Beyond", "source": "tmdb"},
            "title_en": {"value": "Echoes Beyond", "source": "tmdb"},
            "year": {"value": 2025, "source": "tmdb", "confirmed": True},
            "genres": {"value": ["剧情", "科幻"], "source": "tmdb"},
            "directors": [
                {"person_id": "d1", "role": "director", "name": {"value": "导演甲", "source": "tmdb"}},
            ],
            "cast": [
                {"person_id": "a", "role": "actor", "name": {"value": "演员甲", "source": "tmdb"}},
                {"person_id": "b", "role": "actor", "name": {"value": "演员乙", "source": "tmdb"}},
            ],
        },
    }


def test_movie_official_sample():
    result = render(movie_context(), {"template_id": "movie"})
    assert result["text"] == (
        "星际回声 Echoes Beyond | 类型：剧情 科幻"
        " | 导演：导演甲 | 主演：演员甲 演员乙"
    )
    assert result["missing_fields"] == []
    assert result["context_revision"] == 4


def test_movie_director_can_be_hidden_and_missing_year_is_not_guessed():
    context = movie_context()
    context["metadata"]["year"] = None
    result = render(context, {"template_id": "movie", "show_director": False})
    assert result["text"] == "星际回声 Echoes Beyond | 类型：剧情 科幻 | 主演：演员甲 演员乙"
    assert result["missing_fields"] == []


def test_config_can_reorder_or_hide_all_fields_without_dangling_separators():
    result = render(movie_context(), {
        "template_id": "movie",
        "field_order": ["cast", "title", "year", "genres", "director"],
        "visible_fields": ["cast", "title", "year"],
        "cast_limit": "all",
        "labels": {"cast": "演员"},
        "section_separator": " / ",
    })
    assert result["text"] == "演员：演员甲 演员乙 / 星际回声 Echoes Beyond"
    assert "导演：" not in result["text"]
    assert "类型：" not in result["text"]
