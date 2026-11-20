"""Initial Bilibili filenames. PT templates and remote metadata are out of scope."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit

from pydantic import BaseModel, Field

from ...file_service import FileServiceError, validate_filename
from ..naming.templates import normalize_naming_text, value

NumberKind = Literal["episode", "part"]
_PLATFORM_ID = re.compile(r"(?:BV[A-Za-z0-9]{6,20}|(?:av|ep|ss|cid)[0-9]{1,20})\Z", re.I)
_VIDEO_LINK = re.compile(r"/video/(BV[A-Za-z0-9]{6,20}|av[0-9]{1,20})(?:/|$)", re.I)
_BANGUMI_LINK = re.compile(r"/bangumi/play/((?:ep|ss)[0-9]{1,20})(?:/|$)", re.I)
_DISPLAYED_NUMBER = re.compile(r"(?:第\s*)?([0-9]{1,5})(?:\s*[集话話])?\Z")
_REMASTER = r"重[制置]版"
_EDITION_PREFIX = re.compile(rf"^\s*[\[【（(]?{_REMASTER}[\]】）)]?[\s:：·._-]*")
_EDITION_SUFFIX = re.compile(rf"\s*[(（【\[]?{_REMASTER}[)）】\]]?\s*$")
_SEASON_TOKEN = r"第\s*([0-9]{1,3}|[零〇一二两三四五六七八九十百]{1,4})\s*季"
_SEASON_LABEL = re.compile(rf"^{_SEASON_TOKEN}\s*")
_SEASON_SUFFIX = re.compile(rf"(?:{_SEASON_TOKEN}|(?<![\w])S(\d{{1,3}}))\s*$", re.I)
_SEASON_EPISODE = re.compile(r"^S(\d{1,3})E(\d{1,5})(?!\d)", re.I)
_EPISODE_LABEL = re.compile(r"^第\s*([0-9]{1,5})\s*[集话話]")
_LEGACY_NUMBER = re.compile(r"^([0-9]{1,5})(?=\s|$)")
_CHINESE_DIGITS = "零一二三四五六七八九"
_CONTAINERS = {
    "mov": "mp4", "mp4": "mp4", "matroska": "mkv", "webm": "webm", "flv": "flv",
    "mpegts": "ts", "avi": "avi", "asf": "wmv", "ogg": "ogv",
}


class NameOptions(BaseModel):
    mode: Literal["smart", "source"] = "smart"
    title: str | None = Field(default=None, max_length=240)
    number_kind: NumberKind = "episode"
    number: int | None = Field(default=None, ge=1, le=99999)
    episode_numbers: dict[str, int] = Field(default_factory=dict, max_length=600)


class NameFacts(BaseModel):
    identifier: str | None = Field(default=None, max_length=32)
    number_kind: NumberKind | None = None
    number: int | None = Field(default=None, ge=1, le=99999)
    chapter: str | None = Field(default=None, max_length=300)
    extra_title: str | None = Field(default=None, max_length=300)
    movie: bool = False
    episode_id: str | None = Field(default=None, max_length=64)
    series_episode: bool = False
    season: int | None = Field(default=None, ge=1, le=999)
    remaster: bool = False


class NamePreviewRequest(BaseModel):
    title: str = Field(max_length=300)
    options: NameOptions
    items: list[NameFacts] = Field(max_length=600)


def positive_number(value):
    raw = str(value or "")
    return int(raw) if re.fullmatch(r"[0-9]{1,5}", raw) and int(raw) > 0 else None


def safe_identifier(value):
    return value if isinstance(value, str) and _PLATFORM_ID.fullmatch(value) else None


def url_facts(url):
    try:
        parsed = urlsplit(url if isinstance(url, str) else "")
    except ValueError:
        return NameFacts()
    host = parsed.hostname or ""
    if host and not (host == "bilibili.com" or host.endswith(".bilibili.com")):
        return NameFacts()
    video = _VIDEO_LINK.search(parsed.path)
    bangumi = _BANGUMI_LINK.search(parsed.path)
    part = positive_number(parse_qs(parsed.query).get("p", [None])[0]) if video else None
    return NameFacts(identifier=(video or bangumi)[1] if video or bangumi else None,
                     number_kind="part" if part else None, number=part)


def without_video_part(url):
    parsed = urlsplit(url)
    if not _VIDEO_LINK.search(parsed.path):
        return url
    return parsed._replace(query=urlencode([(k, v) for k, v in parse_qsl(parsed.query) if k != "p"])).geturl()


def is_movie_season(season):
    # Bilibili's PGC season type 2 is a movie.
    return str(season.get("type") or season.get("season_type") or "") == "2"


def episode_display_title(title, episode_title, number=None, *, movie=False, part=False):
    title = str(title or "").strip()
    detail = str(episode_title or "").strip()
    if movie:
        return (title or detail)[:300]
    if detail in {"正片", title}:
        detail = ""
    if number:
        numbered = re.match(rf"^(?:第\s*)?0*{number}(?:\s*[集话話]|(?=\s|$))\s*", detail)
        if numbered:
            detail = detail[numbered.end():].strip()
    episode_label = (f"P{number}" if part else f"第{number}集") if number else ""
    return " · ".join(filter(None, (title, episode_label, detail)))[:300]


def download_display_title(job, plan):
    if plan.get("display_title"):
        return plan["display_title"]
    if plan.get("assistant_playback") and job.get("title") == "正片":
        return plan.get("title") or job["title"]
    return job.get("title")


def season_number(value):
    value = (value or "").replace("两", "二").replace("〇", "零")
    if value.isascii() and value.isdecimal():
        number = int(value)
        return number if 1 <= number <= 999 else None
    if not value or not re.fullmatch(r"[一二三四五六七八九]?百[零一二三四五六七八九]?[十]?[一二三四五六七八九]?|[一二三四五六七八九]?十[一二三四五六七八九]?|[一二三四五六七八九]", value):
        return None
    number = digit = 0
    for char in value:
        if char in _CHINESE_DIGITS:
            digit = _CHINESE_DIGITS.index(char)
        else:
            number += (digit or 1) * {"百": 100, "十": 10}[char]
            digit = 0
    return number + digit or None


def episode_label(text, *, legacy=False):
    label = str(text or "").strip()
    remaster = bool(_EDITION_PREFIX.match(label) or _EDITION_SUFFIX.search(label))
    label = _EDITION_SUFFIX.sub("", _EDITION_PREFIX.sub("", label, count=1)).rstrip()
    season = None
    if match := _SEASON_EPISODE.match(label):
        return season_number(match[1]), positive_number(match[2]), remaster
    if match := _SEASON_LABEL.match(label):
        season = season_number(match[1])
        label = label[match.end():]
        remaster = remaster or bool(_EDITION_PREFIX.match(label))
        label = _EDITION_PREFIX.sub("", label, count=1)
    match = _EPISODE_LABEL.match(label) or _DISPLAYED_NUMBER.fullmatch(label)
    if not match and (legacy or remaster):
        match = _LEGACY_NUMBER.match(label)
    return season, positive_number(match[1]) if match else None, remaster


def episode_facts(episodes):
    # Keep a primary site's ordinal, an explicit episode marker, and list position distinct.
    result = {}
    for item in episodes:
        key = str(item["id"])
        group = item.get("group")
        movie = item.get("movie") is True
        main = group == "正片" and not movie
        raw_label = item.get("naming_label", item.get("title"))
        # Old snapshots may contain a generated display title for a missing raw label.
        if ("naming_label" not in item and "display_episode_number" in item
                and item["display_episode_number"] is None and raw_label == f"第{item.get('index')}话"
                and not item.get("chapter_title")):
            raw_label = None
        season, labelled, remaster = episode_label(raw_label, legacy="display_episode_number" not in item)
        reported = positive_number(item.get("display_episode_number"))
        number = (reported or labelled) if main else None
        _, scoped_season, scoped_remaster = _series_title(item.get("naming_season_title"))
        remaster = remaster or scoped_remaster
        if season is None and not remaster:
            season = scoped_season
        extra_title = str(item.get("title") or "").strip()[:300] if group and group != "正片" else None
        result[key] = NameFacts(
            identifier=safe_identifier("ep" + key), episode_id=key, series_episode=main, movie=movie,
            number=number, number_kind="episode" if number else None,
            season=season if main else None, remaster=remaster if main else False,
            chapter=str(item.get("chapter_title") or "")[:300] or None,
            extra_title=extra_title or None,
        )
    # Different seasons and editions may legitimately have the same episode number.
    counts = Counter((fact.season, fact.remaster, fact.number) for fact in result.values() if fact.number)
    for fact in result.values():
        if counts[fact.season, fact.remaster, fact.number] > 1:
            fact.number = fact.number_kind = None
    return result


def displayed_episode_number(text):
    return episode_label(text)[1]


def _series_title(title):
    original = str(title or "").strip()
    name = _EDITION_PREFIX.sub("", original, count=1)
    season = None
    remaster = name != original
    while name:
        cleaned = _EDITION_SUFFIX.sub("", name)
        if cleaned != name:
            remaster = True
            name = cleaned.strip(" ._-")
            continue
        suffix = _SEASON_SUFFIX.search(name)
        bracketed = name.rstrip(")）】]")
        if not suffix and bracketed != name:
            suffix = _SEASON_SUFFIX.search(bracketed)
        if not suffix:
            break
        season = season_number(suffix[1] or suffix[2])
        name = name[:suffix.start()].rstrip(" ._-[【（(")
    return name or "Bilibili", season, remaster


def batch_download_plans(title, facts, options):
    """Use only selected main episodes as evidence for an inherited season."""
    _, title_season, title_remaster = _series_title(title)
    seasons = [
        item.season or (title_season if item.series_episode and not (item.remaster or title_remaster) else None)
        for item in facts
    ]
    known = {season for season in seasons if season is not None}
    inherited = next(iter(known)) if len(known) == 1 else None
    plans = []
    for item, season in zip(facts, seasons, strict=True):
        identifier = item.identifier or ""
        episode_id = item.episode_id or (identifier[2:] if identifier.startswith("ep") else None)
        plans.append(download_plan(
            title, item, options, episode_id=episode_id,
            season=(season or inherited) if item.series_episode else None,
        ))
    return plans, len(known) > 1


def conflicting_filenames(plans):
    counts = Counter(initial_filename(plan) for plan in plans)
    return [name for name, count in counts.items() if count > 1]


def download_plan(title, facts, options, *, episode_id=None, all_parts=False, season=None):
    number, kind = facts.number, facts.number_kind
    if episode_id is not None:
        edited = positive_number(options.episode_numbers.get(episode_id))
        if edited:
            number, kind = edited, "part" if kind == "part" else "episode"
    elif not all_parts and options.number:
        number, kind = options.number, options.number_kind
    plan = {
        "title": (options.title or title or "").strip()[:300],
        "identifier": safe_identifier(facts.identifier),
        "number_kind": kind,
        "number": number,
        "all_parts": all_parts,
    }
    if facts.series_episode:
        plan["title"] = _series_title(plan["title"])[0]
        if season and kind == "episode" and number:
            plan["season"] = season
    if facts.movie:
        plan["movie"] = True
        plan["display_title"] = str(title or plan["title"]).strip()[:300]
    if episode_id is not None and not number and facts.extra_title:
        plan["extra_title"] = facts.extra_title
    return plan


def initial_filename(plan, extension="mp4", *, collision=None):
    extra_title = plan.get("extra_title")
    title = normalize_naming_text(extra_title or plan.get("title") or "Bilibili")
    # Reserve space for IDs, suffixes and language-qualified sidecars even for UTF-8 names.
    title = title.encode()[:155].decode(errors="ignore").strip(" ._-") or "Bilibili"
    try:
        validate_filename(title)
    except FileServiceError:
        title = "Bilibili." + title
    number = positive_number(plan.get("number"))
    kind = plan.get("number_kind")
    identifier = safe_identifier(plan.get("identifier"))
    if plan.get("movie"):
        label = None
    elif number and kind in {"episode", "part"}:
        label = ("E" if kind == "episode" else "P") + str(number).zfill(2)
        if kind == "episode" and (season := plan.get("season")):
            label = f"S{season:02d}" + label
    elif extra_title:
        label = None
    else:
        label = "Bili" + identifier if identifier and identifier.lower().startswith("ep") else identifier
    if safe_identifier(collision) and collision.lower().startswith("ep"):
        collision = "Bili" + collision
    if plan.get("movie") and isinstance(collision, int):
        title += f" ({collision})"
        collision = None
    suffix = ".".join(dict.fromkeys(part for part in (label, collision) if part))
    name = ".".join(part for part in (title, suffix, extension) if part)
    return validate_filename(name)


def final_extension(path: Path, probe: dict):
    original = path.suffix.lower().removeprefix(".")
    containers = (value(probe.get("container")) or "").split(",")
    if original in containers or original in {"m4v", "mov"} and "mp4" in containers:
        return original
    # ffprobe uses shared demuxers for MP4/MOV and Matroska/WebM.
    if "mp4" in containers:
        return "mp4"
    return next((_CONTAINERS[item] for item in containers if item in _CONTAINERS), original)
