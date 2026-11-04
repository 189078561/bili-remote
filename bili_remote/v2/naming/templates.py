"""Whitelist-only PT templates; unknown technical values remain absent."""
from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath
from string import Formatter

from ...file_service import validate_filename
from .errors import NamingError

DEFAULT_TEMPLATES = {
    "movie": "{Title}.{Year}.{Resolution}.{Source}.{VideoCodec}.{AudioCodec}-{Group}.{Extension}",
    "series": "{Title}.{Year}.S{Season:00}E{Episode:00}.{Resolution}.{Source}.{VideoCodec}.{AudioCodec}-{Group}.{Extension}",
    "anime": "{Title}.{Year}.S{Season:00}E{Episode:00}.{Resolution}.{Source}.{VideoCodec}.{AudioCodec}-{Group}.{Extension}",
    "season": "{Title}.{Year}.S{Season:00}.{Resolution}.{Source}.{VideoCodec}.{AudioCodec}-{Group}",
}
FIELDS = {"Title", "Year", "Season", "Episode", "Resolution", "Source", "VideoCodec", "AudioCodec", "Group", "Extension", "HDR", "AudioChannels", "FrameRate", "BitDepth", "OriginalName"}
FIELD_MAP = {"Resolution": "resolution", "VideoCodec": "video_codec", "AudioCodec": "audio_codec", "HDR": "hdr", "AudioChannels": "audio_channels", "FrameRate": "frame_rate", "BitDepth": "bit_depth"}


def value(item):
    return item.get("value") if isinstance(item, dict) and "value" in item else item


def validate_template(template):
    if not isinstance(template, str) or not template.strip() or len(template) > 500 or any(x in template for x in ("/", "\\", "\0")):
        raise NamingError("invalid_template", "模板为空、过长或包含路径")
    try:
        parts = list(Formatter().parse(template))
    except ValueError as exc:
        raise NamingError("invalid_template", "模板花括号无效") from exc
    for literal, field, spec, conversion in parts:
        if field is None:
            continue
        if field not in FIELDS or conversion or spec not in {"", "00"} or (spec and field not in {"Season", "Episode"}):
            raise NamingError("invalid_template", "模板变量或格式不受支持", fields=[field])
    return parts


def _component(text, separator="."):
    text = unicodedata.normalize("NFKC", str(text))
    # Reject path-like titles; replace filename punctuation transparently via
    # a warning emitted by the caller, never use a title as a path.
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", text)
    return re.sub(r"\s+", separator, text).strip(" .")


def render_filename(context, media_file, rules=None, overrides=None, template_id=None):
    rules = {**(rules or {}), **(overrides or {})}
    identity = context.get("identity") or {}
    kind = template_id or rules.get("template_id") or identity.get("media_type") or "movie"
    if kind in {"anime_single", "tv"}:
        kind = {"anime_single": "anime", "tv": "series"}[kind]
    if kind not in DEFAULT_TEMPLATES:
        raise NamingError("invalid_template", "不支持的模板类型")
    template = (rules.get("templates") or {}).get(kind, DEFAULT_TEMPLATES[kind])
    parts = validate_template(template)
    titles = context.get("titles", [])
    selected_id = rules.get("selected_title_id")
    candidate = next((x["text"] for x in titles if x.get("id") == selected_id), None)
    if selected_id and candidate is None:
        raise NamingError("invalid_title", "名称候选不存在")
    title = rules.get("custom_title") or candidate or value(context.get("selected_title"))
    year = value((context.get("metadata") or {}).get("year"))
    episode = media_file.get("episode") or context.get("episode") or {}
    season = value(episode.get("season"))
    if season is None:
        season = rules.get("season_default", 1)
    number = value(episode.get("episode"))
    for field, number_value in (("Season", season), ("Episode", number)):
        if number_value is not None and (type(number_value) is not int or number_value < (0 if field == "Season" else 1)):
            raise NamingError("invalid_episode", "季号须为非负整数，集号须为正整数", fields=[field])
    if year not in (None, "") and not re.fullmatch(r"\d{4}", str(year)):
        raise NamingError("invalid_year", "年份须为四位数字")
    path = PurePosixPath(media_file["ref"]["relative_path"])
    values = {"Title": title, "Year": year, "Season": season, "Episode": number,
              "Source": value(rules.get("source", {"value": "WEB-DL"})),
              "Group": value(rules.get("group", {"value": "HDVWEB"})),
              "Extension": path.suffix.lstrip("."), "OriginalName": path.stem}
    probe = media_file.get("probe") or {}
    values.update({field: value(probe.get(key)) for field, key in FIELD_MAP.items()})
    values["VideoCodec"] = {"H264": "H.264", "H265": "H.265"}.get(values["VideoCodec"], values["VideoCodec"])
    if isinstance(values["HDR"], list):
        values["HDR"] = ".".join(values["HDR"])
    rate = values["FrameRate"]
    if isinstance(rate, dict):
        try:
            values["FrameRate"] = f"{rate['numerator'] / rate['denominator']:.3f}".rstrip("0").rstrip(".")
        except (KeyError, TypeError, ZeroDivisionError):
            values["FrameRate"] = None
    missing, warnings, rendered = [], [], []
    separator = rules.get("title_separator", ".")
    if separator not in {".", " ", "_", "-"}:
        raise NamingError("invalid_rule", "名称分隔符无效")
    for literal, field, spec, _ in parts:
        rendered.append(literal)
        if field is None:
            continue
        item = values.get(field)
        if item in (None, "", []):
            if field not in missing:
                missing.append(field)
            # Remove the preceding S/E marker when the associated number is
            # absent. Missing Episode still blocks execution in the service.
            if field in {"Season", "Episode"} and rendered[-1].endswith("S" if field == "Season" else "E"):
                rendered[-1] = rendered[-1][:-1]
            rendered.append("")
        elif spec:
            rendered.append(str(item).zfill(max(2, int(rules.get("episode_width", 2)) if field == "Episode" else 2)))
        else:
            cleaned = _component(item, separator if field == "Title" else ".")
            if cleaned != re.sub(r"\s+", separator if field == "Title" else ".", str(item)).strip(" ."):
                warnings.append({"code": "sanitized_field", "field": field, "message": "文件名非法字符已替换，请检查预览"})
            rendered.append(cleaned)
    name = "".join(rendered)
    name = re.sub(r"[.]{2,}", ".", name)
    name = re.sub(r"\.+(?=-)", "", name)
    name = re.sub(r"[_-]+(?=\.)", "", name).strip(" ._-")
    # Optional tags are never silently inferred; they are added only when an
    # explicit switch is on and evidence exists for this file.
    tags = []
    for switch, field in (("include_hdr", "HDR"), ("include_channels", "AudioChannels"), ("include_frame_rate", "FrameRate"), ("include_bit_depth", "BitDepth")):
        if rules.get(switch) and field not in {part[1] for part in parts}:
            if values[field] not in (None, "", []):
                tags.append(_component(values[field]))
            else:
                missing.append(field)
    for tag in rules.get("extra_tags", []):
        if not isinstance(tag, str) or len(tag) > 100:
            raise NamingError("invalid_rule", "附加标签无效")
        tags.append(_component(tag))
    if tags:
        if kind == "season":
            name += "." + ".".join(tags)
        else:
            suffix = "." + path.suffix.lstrip(".")
            if name.endswith(suffix):
                name = name[:-len(suffix)] + "." + ".".join(tags) + suffix
    validate_filename(name)
    if kind != "season" and not name.casefold().endswith(path.suffix.casefold()):
        raise NamingError("invalid_extension", "模板必须保留真实文件扩展名")
    warnings.extend({"code": "missing_field", "field": field, "message": "缺少字段：" + field} for field in missing)
    return {"filename": name, "missing_fields": missing, "warnings": warnings, "values": values}


def season_common_probe(files):
    """Only identical confirmed technical values may label a season directory."""
    result, warnings = {}, []
    for key in FIELD_MAP.values():
        fields = [(f.get("probe") or {}).get(key) for f in files]
        if fields and all(isinstance(x, dict) and x.get("confirmed") and value(x) == value(fields[0]) for x in fields):
            result[key] = fields[0]
        else:
            result[key] = None
            warnings.append({"code": "mixed_season_parameters", "field": key, "message": "整季参数不一致或未确认，已省略"})
    return result, warnings
