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


class NamePreviewRequest(BaseModel):
    title: str = Field(max_length=300)
    options: NameOptions
    items: list[NameFacts] = Field(max_length=8)


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


def episode_facts(episodes):
    # Use the platform's displayed number, never list/group position or chapter digits.
    counts = Counter(item.get("display_episode_number") for item in episodes if item.get("group") == "正片")
    result = {}
    for item in episodes:
        number = item.get("display_episode_number")
        group = item.get("group")
        if not number or group != "正片" or counts[number] != 1:
            number = None
        key = str(item["id"])
        extra_title = str(item.get("title") or "").strip()[:300] if group and group != "正片" else None
        result[key] = NameFacts(identifier=safe_identifier("ep" + key), number=number,
                                number_kind="episode" if number else None,
                                chapter=str(item.get("chapter_title") or "")[:300] or None,
                                extra_title=extra_title or None, movie=item.get("movie") is True)
    return result


def displayed_episode_number(text):
    match = _DISPLAYED_NUMBER.fullmatch(str(text or "").strip())
    return positive_number(match[1]) if match else None


def download_plan(title, facts, options, *, episode_id=None, all_parts=False):
    number, kind = facts.number, facts.number_kind
    if episode_id is not None:
        edited = positive_number(options.episode_numbers.get(episode_id))
        if edited:
            number, kind = edited, "episode"
    elif not all_parts and options.number:
        number, kind = options.number, options.number_kind
    plan = {
        "title": (options.title or title or "").strip()[:300],
        "identifier": safe_identifier(facts.identifier),
        "number_kind": kind,
        "number": number,
        "all_parts": all_parts,
    }
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
