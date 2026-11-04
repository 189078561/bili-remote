"""Pure subtitle formatter for shared V2 ResourceContext data."""

from __future__ import annotations

from collections.abc import Mapping
import re
import unicodedata

from .rules import SubtitleRuleError, normalize_rule


def _mapping(value):
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def _value(value):
    """Unwrap FieldValue while retaining its source/manual marker for callers."""
    if hasattr(value, "model_dump") or hasattr(value, "dict"):
        value = _mapping(value)
    if isinstance(value, Mapping) and "value" in value:
        return value.get("value"), value.get("source"), bool(value.get("manual")), bool(value.get("confirmed"))
    return value, None, False, False


def _text(value):
    value, _source, _manual, _confirmed = _value(value)
    if value is None or isinstance(value, bool):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _canonical(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _season_cn(value):
    digits = "零一二三四五六七八九"
    if value == 0:
        return "零"
    if value < 0:
        return str(value)
    if value >= 10000:
        return str(value)
    units = ("", "十", "百", "千")
    output, zero_pending = [], False
    for place in range(3, -1, -1):
        digit = value // (10 ** place) % 10
        if digit:
            if zero_pending and output:
                output.append("零")
            if not (place == 1 and digit == 1 and not output):
                output.append(digits[digit])
            output.append(units[place])
            zero_pending = False
        elif output and value % (10 ** place) and place > 0:
            zero_pending = True
    return "".join(output)


def _integer(field):
    value, _source, _manual, _confirmed = _value(field)
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


class SubtitleService:
    """Render PT subtitle text from shared context; never fetch or persist data."""
    def render(self, context, subtitle_rule=None):
        return render(context, subtitle_rule)


def render(context, subtitle_rule=None):
    """Return SubtitleResult fields from one ResourceContext revision."""
    ctx = _mapping(context)
    if not ctx:
        raise SubtitleRuleError("context must be a ResourceContext object", fields=["context"])
    rule = normalize_rule(subtitle_rule)
    metadata = _mapping(ctx.get("metadata"))
    episode = _mapping(ctx.get("episode"))
    missing, warnings = [], []
    template = rule["template_id"]
    requested = set(rule["field_order"]) & set(rule["visible_fields"])
    media_type = _mapping(ctx.get("identity")).get("media_type")
    if template in {"tv_episode", "tv_season", "anime"} and media_type not in (None, "series", "anime"):
        warnings.append({"code": "media_type_mismatch", "message": "Subtitle template is for episodic media."})
    if template == "movie" and media_type not in (None, "movie"):
        warnings.append({"code": "media_type_mismatch", "message": "Movie subtitle template selected for non-movie media."})

    names = {
        "zh": _text(metadata.get("title_zh")),
        "zh_hant": _text(metadata.get("title_zh_hant")),
        "ja": _text(metadata.get("title_ja")),
        "original": _text(metadata.get("original_title")),
        "en": _text(metadata.get("title_en")),
    }
    for candidate_value in ctx.get("titles", []) if isinstance(ctx.get("titles", []), (list, tuple)) else []:
        candidate = _mapping(candidate_value)
        language = str(candidate.get("language") or "").lower().replace("_", "-")
        kind = candidate.get("kind")
        candidate_text = _text(candidate.get("text"))
        keys = []
        if kind == "original":
            keys.append("original")
        if language in {"zh", "zh-cn", "zh-hans", "zh-sg"}:
            keys.append("zh")
        elif language in {"zh-tw", "zh-hk", "zh-hant"}:
            keys.append("zh_hant")
        elif language == "ja" or language.startswith("ja-"):
            keys.append("ja")
        elif language == "en" or language.startswith("en-"):
            keys.append("en")
        for key in keys:
            if candidate_text and (candidate.get("selected") or not names[key]):
                names[key] = candidate_text
    selected = _text(ctx.get("selected_title"))
    if template == "anime":
        ordered_names = [names.get(key, "") for key in rule["name_order"]]
        if selected:
            ordered_names.insert(0, selected)
        title_parts = _dedupe(ordered_names)
    elif template == "movie":
        ordered_names = [names.get(key, "") for key in rule["name_order"]]
        if selected:
            ordered_names.insert(0, selected)
        title_parts = _dedupe(ordered_names)
    else:
        ordered_names = [names.get(key, "") for key in rule["name_order"]]
        if selected:
            ordered_names.insert(0, selected)
        title_parts = _dedupe(ordered_names)
    if not title_parts and template != "custom" and "title" in requested:
        missing.append("title")

    season = _integer(episode.get("season"))
    episode_number = _integer(episode.get("episode"))
    season_count = _integer(episode.get("season_episode_count"))
    year = _integer(metadata.get("year"))
    if template in {"tv_episode", "anime"} and episode_number is None and "episode" in requested:
        missing.append("episode")
    if template in {"tv_season", "anime"} and season is None and "season" in requested:
        missing.append("season")
    if template == "tv_season" and season_count is None and "season_episode_count" in requested:
        missing.append("season_episode_count")
    if template == "movie" and year is None and "year" in requested:
        missing.append("year")

    genres = _list_value(metadata.get("genres"))
    if not genres and template != "custom" and "genres" in requested:
        missing.append("genres")
    genres_text = rule["list_separator"].join(genres)
    cast = _credits(ctx, metadata, "cast", "actor", rule, prefer_bangumi=False)
    voices = _credits(ctx, metadata, "voice_cast", "voice_actor", rule, prefer_bangumi=(template == "anime"))
    directors = _credits(ctx, metadata, "directors", "director", rule, prefer_bangumi=False)
    if template != "anime" and template != "custom" and not cast and "cast" in requested:
        missing.append("cast")
    if template == "anime" and not voices and "voice_cast" in requested:
        missing.append("voice_cast")
    if template != "custom" and rule["show_director"] and "director" in requested and not directors:
        missing.append("director")

    sections = {"title": " ".join(title_parts)}
    if template == "tv_season":
        sections["season"] = f"第{_season_cn(season)}季" if season is not None else ""
        sections["season_episode_count"] = f"全{season_count}集" if season_count is not None else ""
    elif template == "anime":
        sections["season"] = f"第{season:02d}季" if season is not None else ""
        sections["episode"] = f"第{episode_number:02d}集" if episode_number is not None else ""
    elif template == "tv_episode":
        sections["episode"] = f"第{episode_number:02d}集" if episode_number is not None else ""
    sections["year"] = f"({year})" if year is not None else ""
    sections["genres"] = _labeled(rule, "genres", genres_text)
    sections["cast"] = _labeled(rule, "cast", rule["list_separator"].join(cast))
    sections["voice_cast"] = _labeled(rule, "voice_cast", rule["list_separator"].join(voices))
    sections["director"] = _labeled(rule, "director", rule["list_separator"].join(directors))

    if template == "custom":
        text, custom_missing = _render_custom(rule["custom_template"], {
            "Title": " ".join(title_parts), "TitleZh": names["zh"], "TitleZhHant": names["zh_hant"],
            "TitleJa": names["ja"], "OriginalTitle": names["original"], "TitleEn": names["en"],
            "Year": str(year) if year is not None else "", "Season": str(season) if season is not None else "",
            "Episode": str(episode_number) if episode_number is not None else "",
            "SeasonEpisodeCount": str(season_count) if season_count is not None else "",
            "Genres": genres_text, "Cast": rule["list_separator"].join(cast),
            "VoiceCast": rule["list_separator"].join(voices), "Director": rule["list_separator"].join(directors),
        })
        custom_fields = {
            "Title": "title", "TitleZh": "title_zh", "TitleZhHant": "title_zh_hant",
            "TitleJa": "title_ja", "OriginalTitle": "original_title", "TitleEn": "title_en",
            "Year": "year", "Season": "season", "Episode": "episode",
            "SeasonEpisodeCount": "season_episode_count", "Genres": "genres",
            "Cast": "cast", "VoiceCast": "voice_cast", "Director": "director",
        }
        missing.extend(custom_fields[field] for field in custom_missing)
    else:
        chosen = set(rule["visible_fields"])
        if not rule["show_director"]:
            chosen.discard("director")
        # Identity components form one compact heading. Metadata lists and
        # credits remain separate labelled sections.
        output, heading_parts = [], []
        heading_fields = {"title", "season", "episode", "season_episode_count", "year"}
        def flush_heading():
            if heading_parts:
                output.append(" ".join(heading_parts))
                heading_parts.clear()
        for key in rule["field_order"]:
            if key not in chosen or not sections.get(key):
                continue
            value = sections[key]
            if key in heading_fields:
                heading_parts.append(value)
            else:
                flush_heading()
                output.append(value)
        flush_heading()
        text = rule["section_separator"].join(output)
    return {"text": text, "missing_fields": list(dict.fromkeys(missing)), "warnings": warnings,
            "context_revision": ctx.get("revision")}


def _dedupe(values):
    output, seen = [], set()
    for value in values:
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        key = _canonical(value)
        if value and key not in seen:
            output.append(value)
            seen.add(key)
    return output


def _list_value(value):
    value, _source, _manual, _confirmed = _value(value)
    if not isinstance(value, (list, tuple)):
        return []
    return _dedupe([_text(item) for item in value])


def _credits(context, metadata, field, role, rule, *, prefer_bangumi):
    credits = context.get(field)
    if credits is None:
        credits = metadata.get(field, [])
    if not isinstance(credits, (list, tuple)):
        return []
    rows = []
    for index, item in enumerate(credits):
        credit = _mapping(item)
        if credit.get("role", role) != role:
            continue
        name = _text(credit.get("name"))
        if name:
            rows.append((credit, name, index))
    if prefer_bangumi:
        # Explicit manual credits are locked values; otherwise prefer the
        # Bangumi voice list when the resolver supplied one.
        manual = [row for row in rows if _value(row[0].get("name"))[2]]
        bangumi = [row for row in rows if _value(row[0].get("name"))[1] == "bangumi"]
        if manual:
            rows = manual
        elif bangumi:
            rows = bangumi
    preferred = rule["cast_order"]
    def rank(row):
        credit, name, index = row
        keys = [str(credit.get("person_id") or ""), f"name:{name}"]
        return min((preferred.index(key) for key in keys if key in preferred), default=len(preferred)), index
    rows.sort(key=rank)
    if any(isinstance(row[0].get("manual_order"), int) for row in rows):
        rows.sort(key=lambda row: (row[0].get("manual_order") if isinstance(row[0].get("manual_order"), int) else 10**9, rank(row)))
    names = _dedupe([name for _, name, _ in rows])
    limit = rule["cast_limit"]
    return names if limit == "all" else names[:limit]


def _labeled(rule, key, value):
    return f"{rule['labels'][key]}{rule['label_separator']}{value}" if value else ""


def _render_custom(template, values):
    missing, rendered_segments = [], []
    token = re.compile(r"\{([A-Za-z]+)(?::00)?\}")
    for segment in template.split(" | "):
        variables = list(token.finditer(segment))
        if not variables:
            if segment.strip():
                rendered_segments.append(segment.strip())
            continue
        absent = [match.group(1) for match in variables if not values.get(match.group(1))]
        if absent:
            missing.extend(absent)
            continue
        def substitute(match):
            key, value = match.group(1), values.get(match.group(1), "")
            if match.group(0).endswith(":00}"):
                try:
                    return f"{int(value):02d}"
                except (TypeError, ValueError):
                    return ""
            return str(value)
        rendered = re.sub(r"\s+", " ", token.sub(substitute, segment)).strip()
        if rendered:
            rendered_segments.append(rendered)
    return " | ".join(rendered_segments), missing
