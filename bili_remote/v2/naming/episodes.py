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
    season = rules.get("season_default", 1)
    episode, method, confirmed, reference = None, "default", False, None
    key = mapping_key(origin, identity) if origin else "file:" + relative_path
    warnings = []
    mapping = None
    matching = []
    for candidate in mappings:
        # A mapping must identify the source episode, the series and the season.
        source_matches = bool(origin) and any(origin.get(source) for source in ("ep_id", "cid"))
        source_matches = source_matches and all(str(candidate.get("bili_" + name) or "") == str(origin.get(name) or "") for name in ("season_id", "ep_id", "cid"))
        if source_matches and candidate.get("tmdb_id") == identity.get("tmdb_id"):
            matching.append(candidate)
    if len(matching) > 1 and len({(x.get("tmdb_season"), x.get("tmdb_episode")) for x in matching}) > 1:
        warnings.append({"code": "ambiguous_mapping", "message": "同一来源存在多个季集映射，请人工修正"})
    elif matching:
        mapping = deepcopy(matching[0])
        season, episode = mapping.get("tmdb_season"), mapping.get("tmdb_episode")
        method, confirmed, reference = mapping.get("method", "memory"), bool(mapping.get("confirmed")), key
    elif type(origin.get("episode_index")) is int and origin["episode_index"] > 0:
        episode, method, reference = origin["episode_index"], "bili", key
        # Index is structured evidence, but it does not prove a TMDB season
        # translation. Explicit confirmation is still required for that link.
        confirmed = not bool(identity.get("tmdb_id"))
    else:
        # TMDB only applies when a matching real episode is identifiable from
        # the filename; never assign episode N from arbitrary file list order.
        detected_season, detected_episode, ambiguous = parse_episode(relative_path)
        if ambiguous:
            warnings.append({"code": "ambiguous_episode", "message": "文件名包含多个集号，请人工指定"})
        elif detected_episode:
            season = detected_season if detected_season is not None else season
            episode, method, reference = detected_episode, "filename", relative_path
            actual = [x for x in tmdb_episodes if x.get("season_number", season) == season and x.get("episode_number") == episode]
            if len(actual) == 1:
                method, reference, confirmed = "tmdb", str(actual[0].get("id") or ""), True
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
        episode, method, confirmed = start + index * step * (1 if direction == "increment" else -1), "manual", True
        reference = "rule:start"
    elif mode == "offset" and episode is not None:
        offset = episode_rules.get("offset", 0)
        if type(offset) is not int:
            raise NamingError("invalid_episode_rule", "集数偏移必须为整数")
        episode, method, confirmed = episode + offset, "manual", True
        reference = "rule:offset"
    overrides = {**episode_rules.get("overrides", {}), **rules.get("episode_overrides", {})}
    if key in overrides or relative_path in overrides:
        item = overrides.get(key, overrides.get(relative_path))
        if isinstance(item, dict):
            if set(item) - {"season", "episode"}:
                raise NamingError("invalid_episode_rule", "逐集覆盖字段无效")
            season, episode = item.get("season", season), item.get("episode", episode)
        else:
            episode = item
        method, confirmed = "manual", True
    for name, number in (("season", season), ("episode", episode)):
        if number is not None and (type(number) is not int or number < (0 if name == "season" else 1)):
            raise NamingError("invalid_episode", "季号或集号无效", fields=[name])
    season_source = method if mapping or method == "manual" or parse_episode(relative_path)[0] is not None else "default"
    season_source = {"structured": "bili"}.get(season_source, season_source)
    episode_source = {"structured": "bili"}.get(method, method)
    output = {"season": field(season, season_source, reference, confirmed), "episode": field(episode, episode_source, reference, confirmed),
              "season_episode_count": field(None, "tmdb"), "scope": "single", "mapping_key": key}
    season_episodes = [x for x in tmdb_episodes if x.get("season_number", season) == season]
    if season_episodes:
        output["season_episode_count"] = field(len(season_episodes), "tmdb", str(identity.get("tmdb_id") or ""), True)
    if previous:
        for name in ("season", "episode", "season_episode_count"):
            if isinstance(previous.get(name), dict) and previous[name].get("manual") and not str(previous[name].get("source_ref") or "").startswith("rule:"):
                output[name] = deepcopy(previous[name])
    if episode is None:
        warnings.append({"code": "missing_episode", "message": "未确认集数，请人工指定"})
    if origin and identity.get("tmdb_id") and episode is not None:
        mapping = {"bili_season_id": origin.get("season_id"), "bili_ep_id": origin.get("ep_id"), "bili_cid": origin.get("cid"),
                   "tmdb_id": identity["tmdb_id"], "tmdb_season": val(output["season"]), "tmdb_episode": val(output["episode"]),
                   "method": {"bili": "structured", "default": "filename"}.get(method, method), "confirmed": bool(output["episode"].get("confirmed"))}
    return {"episode": output, "mapping": mapping, "warnings": warnings}
