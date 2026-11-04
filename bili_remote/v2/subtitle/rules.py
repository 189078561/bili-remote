"""Strict validation and normalization for subtitle formatting rules."""

from __future__ import annotations

import re
from collections.abc import Mapping


class SubtitleRuleError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_subtitle_rule", fields: list[str] | None = None):
        super().__init__(message)
        self.code = code
        self.fields = fields or []


FIELDS = (
    "title", "season", "episode", "season_episode_count", "year", "genres",
    "voice_cast", "director", "cast",
)
TEMPLATES = {"tv_episode", "tv_season", "anime", "movie", "custom"}
RULE_KEYS = {
    "template_id", "field_order", "visible_fields", "cast_limit", "cast_order",
    "name_order", "labels", "label_separator", "section_separator", "list_separator",
    "show_director", "custom_template",
}
DEFAULTS = {
    "tv_episode": {
        "field_order": ["title", "episode", "genres", "cast"],
        "visible_fields": ["title", "episode", "genres", "cast"],
    },
    "tv_season": {
        "field_order": ["title", "season", "season_episode_count", "genres", "cast"],
        "visible_fields": ["title", "season", "season_episode_count", "genres", "cast"],
    },
    "anime": {
        "field_order": ["title", "season", "episode", "genres", "voice_cast"],
        "visible_fields": ["title", "season", "episode", "genres", "voice_cast"],
    },
    "movie": {
        "field_order": ["title", "year", "genres", "director", "cast"],
        "visible_fields": ["title", "year", "genres", "director", "cast"],
    },
    "custom": {"field_order": ["title", "genres", "cast"], "visible_fields": ["title", "genres", "cast"]},
}
DEFAULT_LABELS = {"genres": "类型", "cast": "主演", "voice_cast": "声优", "director": "导演"}
CUSTOM_VARIABLES = {
    "Title", "TitleZh", "TitleZhHant", "TitleJa", "OriginalTitle", "TitleEn",
    "Year", "Season", "Episode", "SeasonEpisodeCount", "Genres", "Cast", "VoiceCast", "Director",
}


def _field_list(value: object, key: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise SubtitleRuleError(f"{key} must be a list of field names", fields=[key])
    if len(set(value)) != len(value) or any(item not in FIELDS for item in value):
        raise SubtitleRuleError(f"{key} contains duplicate or unknown fields", fields=[key])
    return list(value)


def normalize_rule(rule: Mapping | None = None) -> dict:
    """Return a complete safe rule without mutating the caller's mapping."""
    if rule is None:
        rule = {}
    if not isinstance(rule, Mapping) and (hasattr(rule, "model_dump") or hasattr(rule, "dict")):
        rule = rule.model_dump(mode="python") if hasattr(rule, "model_dump") else rule.dict()
    if not isinstance(rule, Mapping):
        raise SubtitleRuleError("rule must be an object", fields=["rule"])
    unknown = set(rule) - RULE_KEYS
    if unknown:
        raise SubtitleRuleError("unknown subtitle rule fields", fields=sorted(map(str, unknown)))
    template_id = rule.get("template_id", "tv_episode")
    if not isinstance(template_id, str) or template_id not in TEMPLATES:
        raise SubtitleRuleError("unknown subtitle template", fields=["template_id"])
    defaults = DEFAULTS[template_id]
    order = _field_list(rule.get("field_order", defaults["field_order"]), "field_order")
    visible = _field_list(rule.get("visible_fields", defaults["visible_fields"]), "visible_fields")
    cast_limit = rule.get("cast_limit", 3)
    if cast_limit not in (3, 5, 8, "all"):
        raise SubtitleRuleError("cast_limit must be 3, 5, 8, or 'all'", fields=["cast_limit"])
    cast_order = rule.get("cast_order", [])
    if not isinstance(cast_order, (list, tuple)) or any(not isinstance(v, str) for v in cast_order):
        raise SubtitleRuleError("cast_order must be a list of person IDs or names", fields=["cast_order"])
    if len(set(cast_order)) != len(cast_order):
        raise SubtitleRuleError("cast_order must not contain duplicates", fields=["cast_order"])
    default_name_order = {
        "anime": ["zh", "zh_hant", "ja"],
        "movie": ["zh", "original"],
        "tv_episode": ["zh"],
        "tv_season": ["zh"],
        "custom": ["zh", "zh_hant", "ja", "original", "en"],
    }[template_id]
    name_order = rule.get("name_order", default_name_order)
    valid_names = {"zh", "zh_hant", "en", "ja", "original"}
    if not isinstance(name_order, (list, tuple)) or any(
        not isinstance(v, str) or v not in valid_names for v in name_order
    ) or len(set(name_order)) != len(name_order):
        raise SubtitleRuleError("name_order contains invalid or duplicate language keys", fields=["name_order"])
    labels = rule.get("labels", {})
    if not isinstance(labels, Mapping) or set(labels) - set(DEFAULT_LABELS):
        raise SubtitleRuleError("labels may customize only supported subtitle labels", fields=["labels"])
    normalized_labels = dict(DEFAULT_LABELS)
    for key, value in labels.items():
        if not isinstance(value, str) or len(value) > 24 or any(ord(char) < 32 for char in value):
            raise SubtitleRuleError("labels must be short plain text", fields=[f"labels.{key}"])
        normalized_labels[key] = value
    label_separator = rule.get("label_separator", "：")
    section_separator = rule.get("section_separator", " | ")
    list_separator = rule.get("list_separator", " / " if template_id == "anime" else " ")
    for key, value, limit in (("label_separator", label_separator, 8), ("section_separator", section_separator, 16), ("list_separator", list_separator, 8)):
        if not isinstance(value, str) or len(value) > limit or any(ord(ch) < 32 for ch in value):
            raise SubtitleRuleError(f"{key} must be short plain text", fields=[key])
    show_director = rule.get("show_director", True)
    if not isinstance(show_director, bool):
        raise SubtitleRuleError("show_director must be boolean", fields=["show_director"])
    custom_template = rule.get("custom_template")
    if custom_template is not None and not isinstance(custom_template, str):
        raise SubtitleRuleError("custom_template must be text or null", fields=["custom_template"])
    if template_id == "custom" or custom_template is not None:
        if custom_template is None or not custom_template or len(custom_template) > 500:
            raise SubtitleRuleError("custom template must contain 1 to 500 characters", fields=["custom_template"])
        if any(ord(ch) < 32 and ch not in "\t\n" for ch in custom_template):
            raise SubtitleRuleError("custom template contains control characters", fields=["custom_template"])
        variables = re.findall(r"\{([^{}]+)\}", custom_template)
        if not variables or "{" in re.sub(r"\{[^{}]+\}", "", custom_template) or "}" in re.sub(r"\{[^{}]+\}", "", custom_template):
            raise SubtitleRuleError("custom template must use at least one supported variable", fields=["custom_template"])
        for variable in variables:
            name = variable.split(":", 1)[0]
            if name not in CUSTOM_VARIABLES or (":" in variable and (name not in {"Season", "Episode", "SeasonEpisodeCount"} or variable.split(":", 1)[1] != "00")):
                raise SubtitleRuleError(f"unsupported custom template variable: {variable}", fields=["custom_template"])
    elif custom_template is not None and len(custom_template) > 500:
        raise SubtitleRuleError("custom_template exceeds 500 characters", fields=["custom_template"])
    return {
        "template_id": template_id, "field_order": order, "visible_fields": visible,
        "cast_limit": cast_limit, "cast_order": list(cast_order), "name_order": list(name_order),
        "labels": normalized_labels, "label_separator": label_separator,
        "section_separator": section_separator, "list_separator": list_separator,
        "show_director": show_director, "custom_template": custom_template,
    }
