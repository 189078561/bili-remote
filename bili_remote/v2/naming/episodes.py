"""Episode evidence priority and explicit user mapping, without order guesses."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import PurePosixPath
import re

from .errors import NamingError


def field(value, source, ref=None, confirmed=False):
    return {"value": value, "source": source, "source_ref": ref, "manual": source == "manual", "confirmed": confirmed,
            "confidence": 1.0 if source in {"manual", "bili"} and value is not None else None,
            "updated_at": datetime.now(timezone.utc).isoformat()}


def val(item):
    return item.get("value") if isinstance(item, dict) else item


def mapping_key(origin, identity=None):
    origin, identity = origin or {}, identity or {}
    return "bili:{season}:{ep}:{cid}|tmdb:{tmdb}".format(season=origin.get("season_id") or "", ep=origin.get("ep_id") or "", cid=origin.get("cid") or "", tmdb=identity.get("tmdb_id") or "")


def parse_episode(path):
    stem = PurePosixPath(path).stem
    patterns = [
        r"(?i)(?:^|[^a-z0-9])S(?P<s>\d{1,3})[ ._-]*EP?(?P<e>\d{1,4})(?!\d)",
        r"(?i)(?:^|[^a-z0-9])S(?P<s>\d{1,3})\s*[-–—]\s*(?P<e>\d{1,4})(?!\d)",
        r"第\s*(?P<s>\d{1,3})\s*季\s*第\s*(?P<e>\d{1,4})\s*[集话話]",
        r"第\s*(?P<e>\d{1,4})\s*[集话話]",
        r"(?i)(?:^|[^a-z0-9])EP?(?P<e>\d{1,4})(?!\d)",
        r"^(?P<e>\d{1,3})$",
    ]
    candidates = set()
    for pattern in patterns:
        for match in re.finditer(pattern, stem):
            groups = match.groupdict()
            candidates.add((int(groups["s"]) if groups.get("s") is not None else None, int(groups["e"])))
        if candidates:
            break
    if len(candidates) == 1:
        return (*next(iter(candidates)), False)
    return None, None, len(candidates) > 1


def resolve_episode(relative_path, origin=None, identity=None, mappings=(), tmdb_episodes=(), previous=None, rules=None, index=0):
    origin, identity, rules = origin or {}, identity or {}, rules or {}
    numbering = rules.get("episode_numbering", "auto")
    season = rules.get("explicit_season") if numbering == "seasonal" else None
    season_source = "manual" if season is not None else "default"
    episode, method, confirmed, reference = None, "default", False, None
    key = mapping_key(origin, identity) if origin else "file:" + relative_path
    warnings = []
    mapping = None
    has_bili_episode = bool(origin.get("season_id") or origin.get("ep_id"))
    source_index = origin.get("episode_index")
    has_index = type(source_index) is int and source_index > 0
    detected_season, detected_episode, ambiguous = parse_episode(relative_path)
    # Legacy download names can repeat the absolute episode in both positions.
    # Real high-numbered seasons can still be set by a confirmed map or manually.
    repeated_range = detected_season is not None and re.search(r"(?i)S0*%d\s*[-–—]\s*0*%d(?!\d)" % (detected_season, detected_season), PurePosixPath(relative_path).stem)
    if detected_season is not None and detected_season == detected_episode and (detected_season >= 100 or repeated_range):
        warnings.append({"code": "suspect_filename_season", "message": "文件名季号与连续集号相同且异常，未采用该季号"})
        detected_season = None
    if ambiguous:
        warnings.append({"code": "ambiguous_episode", "message": "文件名包含多个集号，请人工指定"})
    if has_index and not has_bili_episode:
        if PurePosixPath(relative_path).stem.isdecimal() and detected_episode == source_index:
            detected_episode = None
        warnings.append({"code": "bili_part_not_episode", "message": f"B站分 P（P{source_index}）不作为剧集集号"})
    if numbering != "continuous" and identity.get("tmdb_id") and any(origin.get(k) for k in ("ep_id", "cid")):
        matching = [candidate for candidate in mappings
                    if candidate.get("confirmed") and candidate.get("tmdb_id") == identity["tmdb_id"]
                    and all(str(candidate.get("bili_" + name) or "") == str(origin.get(name) or "")
                            for name in ("season_id", "ep_id", "cid"))]
        if len({(item.get("tmdb_season"), item.get("tmdb_episode")) for item in matching}) > 1:
            warnings.append({"code": "ambiguous_mapping", "message": "同一来源存在多个季集映射，请人工修正"})
        elif matching:
            mapping = deepcopy(matching[0])
    if mapping:
        season, episode = mapping["tmdb_season"], mapping["tmdb_episode"]
        method = season_source = {"structured": "bili"}.get(mapping.get("method"), mapping.get("method", "memory"))
        confirmed, reference = True, key
    elif has_bili_episode and has_index:
        if detected_season is None and detected_episode:
            # The source's displayed episode can differ from its playlist position.
            episode = detected_episode
        else:
            episode = source_index
        method, reference = "bili", key
        confirmed = numbering == "continuous" or not identity.get("tmdb_id")
    elif detected_episode:
        if detected_season is not None and season is None and numbering != "continuous":
            season, season_source = detected_season, "filename"
        episode, method, reference = detected_episode, "filename", relative_path
        actual = [item for item in tmdb_episodes if type(item.get("season_number")) is int
                  and item.get("episode_number") == episode
                  and (detected_season is None or item["season_number"] == detected_season)]
        if len(actual) == 1 and numbering != "continuous" and season_source != "manual" and not warnings:
            season = actual[0]["season_number"]
            method = season_source = "tmdb"
            reference, confirmed = str(actual[0].get("id") or ""), True

    season_field = field(season, season_source, reference, season_source != "default")
    episode_rules = rules.get("episode_rules") or {}
    mode = rules.get("episode_mode", "detected")
    if mode not in {"detected", "start", "offset"}:
        raise NamingError("invalid_episode_rule", "集数模式无效")
    if mode == "start":
        start = rules.get("start_episode", episode_rules.get("start", 1))
        step = rules.get("episode_step", episode_rules.get("step", 1))
        direction = rules.get("episode_direction", episode_rules.get("direction", "increment"))
        if type(start) is not int or type(step) is not int or step < 1 or direction not in {"increment", "decrement"}:
            raise NamingError("invalid_episode_rule", "起始集数、步长或方向无效")
        episode, method, confirmed, reference = start + index * step * (1 if direction == "increment" else -1), "manual", True, "rule:start"
    elif mode == "offset" and episode is not None:
        offset = episode_rules.get("offset", 0)
        if type(offset) is not int:
            raise NamingError("invalid_episode_rule", "集数偏移必须为整数")
        episode, method, confirmed, reference = episode + offset, "manual", True, "rule:offset"
    overrides = {**episode_rules.get("overrides", {}), **rules.get("episode_overrides", {})}
    if key in overrides or relative_path in overrides:
        item = overrides.get(key, overrides.get(relative_path))
        if not isinstance(item, dict):
            item = {"episode": item}
        if set(item) - {"season", "episode"}:
            raise NamingError("invalid_episode_rule", "逐集覆盖字段无效")
        if "season" in item:
            season_field = field(item["season"], "manual", "rule:override", True)
        if "episode" in item:
            episode, method, confirmed, reference = item["episode"], "manual", True, "rule:override"
    output = {"season": season_field, "episode": field(episode, method, reference, confirmed),
              "season_episode_count": field(None, "tmdb"), "scope": "single", "mapping_key": key}
    for name in ("season", "episode"):
        old = (previous or {}).get(name)
        if isinstance(old, dict) and old.get("manual") and not str(old.get("source_ref") or "").startswith("rule:"):
            output[name] = deepcopy(old)
    for name in ("season", "episode"):
        number = val(output[name])
        if number is not None and (type(number) is not int or number < (0 if name == "season" else 1)):
            raise NamingError("invalid_episode", "季号或集号无效", fields=[name])
    final_season, final_episode = val(output["season"]), val(output["episode"])
    # Keep the evidence in the context when changing output mode. Renderers
    # omit it in continuous mode so switching back can recover the real season.
    if final_season is not None and numbering != "continuous":
        season_episodes = [item for item in tmdb_episodes if item.get("season_number") == final_season]
        if season_episodes:
            output["season_episode_count"] = field(len(season_episodes), "tmdb", str(identity.get("tmdb_id") or ""), True)
    if final_episode is None:
        warnings.append({"code": "missing_episode", "message": "未确认集数，请人工指定"})
    return {"episode": output, "mapping": mapping, "warnings": warnings}
