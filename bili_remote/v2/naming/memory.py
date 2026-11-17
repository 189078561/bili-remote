"""Rule-only memory semantics over the integration-owned repository protocol."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re

from .errors import NamingError
from .templates import validate_template

RULE_FIELDS = {"template_id", "templates", "selected_title_id", "custom_title", "title_separator", "source", "group", "season_default", "explicit_season", "episode_numbering", "episode_width", "include_hdr", "include_channels", "include_frame_rate", "include_bit_depth", "extra_tags", "episode_mode", "start_episode", "episode_step", "episode_direction", "episode_overrides", "auto_rename_high_confidence"}
SUBTITLE_FIELDS = {"template_id", "field_order", "visible_fields", "cast_limit", "cast_order", "name_order", "labels", "label_separator", "section_separator", "list_separator", "show_director", "custom_template"}
EPISODE_FIELDS = {"offset", "start", "step", "direction", "overrides", "mappings"}
FIELD_VALUE_FIELDS = {"value", "source", "source_ref", "manual", "confirmed", "confidence", "updated_at"}
DEFAULT_RULES = {"source": {"value": "WEB-DL", "source": "default", "manual": False, "confirmed": False},
                 "group": {"value": "HDVWEB", "source": "default", "manual": False, "confirmed": False},
                 "episode_width": 2, "auto_rename_high_confidence": False}


def _strict(value, allowed, field):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise NamingError("invalid_memory_rule", "规则含不支持字段", fields=[field])


def _text(value, name, limit=500):
    if not isinstance(value, str) or len(value) > limit or "\0" in value:
        raise NamingError("invalid_memory_rule", "规则文本无效", fields=[name])


def _episode_override(value):
    if type(value) is int:
        if value < 1:
            raise NamingError("invalid_episode", "集数须为正整数")
    elif isinstance(value, dict):
        _strict(value, {"season", "episode"}, "episode_override")
        for key, number in value.items():
            if type(number) is not int or number < (0 if key == "season" else 1):
                raise NamingError("invalid_episode", "季集号无效")
    else:
        raise NamingError("invalid_episode", "集数覆盖无效")


def validate_naming_rule(rule):
    _strict(rule, RULE_FIELDS, "naming_rule")
    for key, item in rule.items():
        if key == "templates":
            _strict(item, {"movie", "series", "anime", "season"}, key)
            for template in item.values():
                validate_template(template)
        elif key in {"source", "group"}:
            if isinstance(item, dict):
                _strict(item, FIELD_VALUE_FIELDS, key)
                _text(item.get("value"), key, 100)
                if item.get("source", "manual") not in {"manual", "default", "memory"}:
                    raise NamingError("invalid_memory_rule", "片源和制作组是规则，不能标记为探测事实")
                for flag in ("manual", "confirmed"):
                    if flag in item and type(item[flag]) is not bool:
                        raise NamingError("invalid_memory_rule", "规则来源标记无效")
                if item.get("source_ref") is not None:
                    _text(item["source_ref"], key, 200)
                    if "://" in item["source_ref"]:
                        raise NamingError("invalid_memory_rule", "规则引用不能包含 URL 或认证参数")
                if item.get("confidence") is not None and (type(item["confidence"]) not in {int, float} or not 0 <= item["confidence"] <= 1):
                    raise NamingError("invalid_memory_rule", "规则置信度无效")
                if "updated_at" in item:
                    try:
                        parsed = datetime.fromisoformat(item["updated_at"])
                        if parsed.tzinfo is None:
                            raise ValueError
                    except (ValueError, TypeError) as exc:
                        raise NamingError("invalid_memory_rule", "规则更新时间须含时区") from exc
            else:
                _text(item, key, 100)
        elif key in {"season_default", "explicit_season"} and item is None:
            continue
        elif key in {"season_default", "explicit_season", "episode_width", "start_episode", "episode_step"}:
            minimum = 0 if key in {"season_default", "explicit_season"} else 1
            if type(item) is not int or not minimum <= item <= (8 if key == "episode_width" else 100000):
                raise NamingError("invalid_memory_rule", "规则数字无效", fields=[key])
        elif key.startswith("include_") or key == "auto_rename_high_confidence":
            if type(item) is not bool:
                raise NamingError("invalid_memory_rule", "规则开关无效", fields=[key])
        elif key == "extra_tags":
            if not isinstance(item, list) or len(item) > 30:
                raise NamingError("invalid_memory_rule", "附加标签无效")
            for tag in item:
                _text(tag, key, 100)
        elif key == "episode_overrides":
            if not isinstance(item, dict) or len(item) > 1000:
                raise NamingError("invalid_memory_rule", "集数覆盖无效")
            for source, override in item.items():
                _text(source, key, 1000)
                _episode_override(override)
        elif item is not None:
            _text(item, key)
    if rule.get("episode_mode", "detected") not in {"detected", "start", "offset"} or rule.get("episode_direction", "increment") not in {"increment", "decrement"}:
        raise NamingError("invalid_memory_rule", "集数模式或方向无效")
    if rule.get("episode_numbering", "auto") not in {"auto", "continuous", "seasonal"}:
        raise NamingError("invalid_memory_rule", "季集编号方式无效")
    if rule.get("template_id", "movie") not in {"movie", "series", "anime", "anime_single", "season"}:
        raise NamingError("invalid_memory_rule", "模板类型无效")
    return deepcopy(rule)


def validate_memory_rules(rules):
    _strict(rules, {"selected_title", "aliases", "naming_rule", "subtitle_rule", "episode_rules"}, "rules")
    if "selected_title" in rules and rules["selected_title"] is not None:
        _text(rules["selected_title"], "selected_title", 500)
    if "aliases" in rules:
        if not isinstance(rules["aliases"], list) or len(rules["aliases"]) > 100:
            raise NamingError("invalid_memory_rule", "别名列表无效")
        for text in rules["aliases"]:
            _text(text, "aliases", 500)
    if "naming_rule" in rules:
        validate_naming_rule(rules["naming_rule"])
    if "subtitle_rule" in rules:
        from ..subtitle.rules import normalize_rule, SubtitleRuleError
        try:
            normalize_rule(rules["subtitle_rule"])
        except SubtitleRuleError as exc:
            raise NamingError("invalid_memory_rule", str(exc), fields=["subtitle_rule"]) from exc
    if "episode_rules" in rules:
        validate_episode_rules(rules["episode_rules"])
    return deepcopy(rules)


def validate_episode_rules(episodes):
    _strict(episodes, EPISODE_FIELDS, "episode_rules")
    for key in {"offset", "start", "step"} & episodes.keys():
        if type(episodes[key]) is not int or abs(episodes[key]) > 100000 or key in {"start", "step"} and episodes[key] < 1:
            raise NamingError("invalid_memory_rule", "季集规则数字无效")
    if episodes.get("direction", "increment") not in {"increment", "decrement"}:
        raise NamingError("invalid_memory_rule", "集数方向无效")
    if not isinstance(episodes.get("overrides", {}), dict) or len(episodes.get("overrides", {})) > 1000:
        raise NamingError("invalid_memory_rule", "逐集规则列表无效")
    for source, item in episodes.get("overrides", {}).items():
        _text(source, "overrides", 1000)
        _episode_override(item)
    mappings = episodes.get("mappings", [])
    if not isinstance(mappings, list) or len(mappings) > 1000:
        raise NamingError("invalid_memory_rule", "映射列表无效")
    for mapping in mappings:
        _strict(mapping, {"bili_season_id", "bili_ep_id", "bili_cid", "tmdb_id", "tmdb_season", "tmdb_episode", "method", "confirmed"}, "mappings")
        if not mapping.get("bili_ep_id") and not mapping.get("bili_cid") or not re.fullmatch(r"[1-9]\d*", str(mapping.get("tmdb_id", ""))):
            raise NamingError("invalid_memory_rule", "映射必须含来源集号和 TMDB 系列")
        _episode_override({"season": mapping.get("tmdb_season"), "episode": mapping.get("tmdb_episode")})
        for key in ("bili_season_id", "bili_ep_id", "bili_cid"):
            if mapping.get(key) is not None and not re.fullmatch(r"[1-9]\d*", str(mapping[key])):
                raise NamingError("invalid_memory_rule", "来源 ID 无效")
        if mapping.get("method") not in {"structured", "tmdb", "filename", "manual", "memory"} or type(mapping.get("confirmed")) is not bool:
            raise NamingError("invalid_memory_rule", "映射来源或确认标记无效")
    return deepcopy(episodes)


def merge_layers(layers, manual=None):
    if manual is not None:
        validate_memory_rules(manual)
    effective = {"naming_rule": deepcopy(DEFAULT_RULES), "subtitle_rule": {}, "episode_rules": {}}
    origins = {}
    for layer in sorted(layers, key=lambda x: {"global": 0, "series": 1, "season": 2}[x["scope"]]) + ([{"scope": "manual", **manual}] if manual else []):
        rules = layer.get("rules", layer)
        for section in ("naming_rule", "subtitle_rule", "episode_rules"):
            for key, item in rules.get(section, {}).items():
                # Nested maps override individual keys without dropping other
                # remembered language/season rules.
                if isinstance(item, dict) and isinstance(effective[section].get(key), dict) and key not in {"source", "group"}:
                    effective[section][key] = {**effective[section][key], **deepcopy(item)}
                else:
                    effective[section][key] = deepcopy(item)
                origins[section + "." + key] = {"scope": layer["scope"], "revision": layer.get("revision")}
        for key in ("selected_title", "aliases"):
            if key in rules:
                effective[key] = deepcopy(rules[key])
    effective["sources"] = origins
    return effective


def identity_key(identity):
    if identity is None:
        return None
    if isinstance(identity, str):
        if not re.fullmatch(r"tmdb:(?:movie|tv):[1-9]\d*|bangumi:subject:[1-9]\d*", identity):
            raise NamingError("invalid_identity", "影视身份键无效")
        return identity
    if not isinstance(identity, dict) or not identity.get("confirmed"):
        return None
    if identity.get("tmdb_id"):
        return identity_key("tmdb:" + ("movie" if identity.get("media_type") == "movie" else "tv") + ":" + str(identity["tmdb_id"]))
    if identity.get("bangumi_id"):
        return identity_key("bangumi:subject:" + str(identity["bangumi_id"]))
    return None


class NamingMemoryService:
    def __init__(self, repository):
        self.repository = repository

    def get(self, identity=None, season=None, manual=None):
        key = identity_key(identity)
        if season is not None and (type(season) is not int or season < 0):
            raise NamingError("invalid_memory_key", "季号无效")
        result = self.repository.get(key, season if key else None)
        layers = result.get("layers", []) if isinstance(result, dict) else list(result)
        # Defend against an overly broad repository result: uncertain identity
        # can only receive global preferences.
        layers = [x for x in layers if x["scope"] == "global" or key and x.get("identity_key") == key and (x["scope"] == "series" or x.get("season_key") == season)]
        return {"layers": deepcopy(layers), "effective": merge_layers(layers, manual),
                "revision": {x["scope"]: x.get("revision", 0) for x in layers}}

    def put(self, scope, key, rules, expected_revision):
        if scope not in {"global", "series", "season"} or type(expected_revision) is not int or expected_revision < 0:
            raise NamingError("invalid_memory_key", "记忆层级或版本无效")
        _strict(key, {"identity_key", "season_key"}, "key")
        key = deepcopy(key)
        if scope == "global":
            if key.get("identity_key") not in {None, "global"} or key.get("season_key") not in {None, -1}:
                raise NamingError("invalid_memory_key", "全局规则不能绑定影视或季号")
            key = {"identity_key": "global", "season_key": -1}
        else:
            key["identity_key"] = identity_key(key.get("identity_key"))
            if not key["identity_key"]:
                raise NamingError("invalid_memory_key", "必须选择影视身份")
            if scope == "series":
                if key.get("season_key") not in {None, -1}:
                    raise NamingError("invalid_memory_key", "系列规则不能绑定季号")
                key["season_key"] = -1
            elif type(key.get("season_key")) is not int or key["season_key"] < 0:
                raise NamingError("invalid_memory_key", "季集规则必须绑定非负季号")
        return self.repository.put(scope, key, validate_memory_rules(rules), expected_revision)
