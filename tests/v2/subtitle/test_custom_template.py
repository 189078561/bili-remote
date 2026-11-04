import pytest

from bili_remote.v2.subtitle import SubtitleRuleError, normalize_rule, render


def test_custom_template_interpolates_only_whitelisted_fields_and_omits_empty_sections():
    context = {
        "revision": 19,
        "titles": [
            {"text": "电影中文", "language": "zh-CN", "kind": "localized"},
            {"text": "Movie Original", "language": "en", "kind": "original"},
        ],
        "metadata": {"year": {"value": 2024, "source": "manual", "manual": True},
                     "genres": ["科幻", "冒险"]},
        "episode": {"season": 1, "episode": 5},
    }
    result = render(context, {
        "template_id": "custom",
        "custom_template": "{TitleZh} {OriginalTitle} ({Year}) | 第{Episode:00}集 | {Cast}",
    })
    assert result["text"] == "电影中文 Movie Original (2024) | 第05集"
    assert result["context_revision"] == 19
    assert "cast" in result["missing_fields"]


def test_resolver_title_candidates_support_all_declared_languages_and_manual_name_choice():
    context = {
        "revision": 20,
        "titles": [
            {"text": "中文", "language": "zh-CN", "kind": "localized", "source": "tmdb"},
            {"text": "繁體", "language": "zh-TW", "kind": "localized", "source": "tmdb"},
            {"text": "English", "language": "en-US", "kind": "english", "source": "tmdb"},
            {"text": "日本語", "language": "ja-JP", "kind": "original", "source": "bangumi"},
        ],
        "selected_title": {"value": "使用者選名", "source": "manual", "manual": True},
        "metadata": {"genres": []},
    }
    result = render(context, {
        "template_id": "tv_episode", "name_order": ["zh", "zh_hant", "en", "ja"],
        "visible_fields": ["title"],
    })
    assert result["text"] == "使用者選名 中文 繁體 English 日本語"
    assert result["context_revision"] == 20


@pytest.mark.parametrize("template", [
    "{Platform}",
    "{Title.__class__}",
    "{Title[0]}",
    "{Title!r}",
    "{Title:02d}",
    "{Title} {Unknown}",
])
def test_custom_template_rejects_unapproved_variables_and_formatting(template):
    with pytest.raises(SubtitleRuleError):
        normalize_rule({"template_id": "custom", "custom_template": template})


def test_rule_schema_rejects_removed_platform_and_unknown_fields():
    for rule in ({"platform": "streaming"}, {"template_id": "tv_episode", "field_order": ["platform"]}):
        with pytest.raises(SubtitleRuleError):
            normalize_rule(rule)
