"""Independent publication metadata selection and PT-Gen-style rendering."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

from pydantic import BaseModel, Field
from typing import Literal

from ..contracts import ResourceContext
from .douban_references import USER_SUPPLIED_DOUBAN
from .providers import ProviderError, identifier

_FIELDS = (
    ("译　　名", "aliases"), ("片　　名", "title"), ("年　　代", "year"),
    ("产　　地", "region"), ("类　　别", "genres"), ("语　　言", "languages"),
    ("上映日期", "date"), ("豆瓣评分", "douban_rating"), ("豆瓣链接", "douban_url"),
    ("IMDb评分", "imdb_rating"), ("IMDb链接", "imdb_url"),
    ("Bangumi评分", "bangumi_rating"), ("Bangumi链接", "bangumi_url"),
    ("季　　数", "season_count"), ("季　　号", "season"), ("集　　数", "episode_count"),
    ("片　　长", "runtime"), ("导　　演", "directors"), ("编　　剧", "writers"),
    ("主　　演", "cast"), ("配音演员", "voice"),
    ("制作公司", "production"), ("原　　作", "original_work"), ("音　　乐", "music"),
)
_MULTILINE = {"cast", "voice"}
_LIST_FIELDS = {"aliases", "region", "genres", "languages", "production", "original_work", "music", "directors", "writers", *_MULTILINE}
_EMPTY = {"", "-", "—", "暂无", "暂无评分", "未知", "未评分", "n/a", "none", "null"}
_LEADING_COVER = re.compile(r"^\s*(?:\[center\]\s*)?\[img\][^\r\n\[]+\[/img\](?:\s*\[/center\])?\s*", re.I)
Source = Literal["current", "tmdb_season", "ptgen", "bangumi"]


class IntroductionRequest(BaseModel):
    context: ResourceContext | None = None
    source: Source = "current"
    reference: str = Field(default="", max_length=300)
    season: int | None = Field(default=None, ge=0, le=10000)
    automatic: bool = False


class IntroductionOptions(BaseModel):
    source: Literal["tmdb_season", "ptgen", "bangumi"]
    query: str = Field(max_length=200)


def text(value):
    if isinstance(value, dict):
        return text(value.get("value") if "value" in value else value.get("name") or value.get("v") or value.get("title"))
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        result = str(value).strip()
        return result if result.casefold() not in _EMPTY else ""
    return ""


def names(values):
    if not isinstance(values, (list, tuple)):
        values = re.split(r"\r?\n|\s+/\s+", text(values))
    return list(dict.fromkeys(item for raw in values if (item := text(raw))))


def count(value):
    number = text(value)
    return number if number.isascii() and number.isdigit() and int(number) > 0 else ""


def runtimes(value):
    entries = value if isinstance(value, list) else [value]
    return " / ".join(dict.fromkeys(f"{item}分钟" if item.isdigit() else item
                                    for value in entries if (item := text(value)) and item != "0"))


def rating(value):
    if isinstance(value, dict):
        votes = value.get("count", value.get("votes", value.get("ratingCount", value.get("total"))))
        if votes is not None and not count(votes):
            return ""
        value = value.get("score", value.get("value", value.get("ratingValue")))
    result = text(value)
    match = re.match(r"^(\d+(?:\.\d+)?)(?=$|\s|/|分)", result)
    if not match or not 0 < float(match[1]) <= 10 or re.search(r"\bfrom\s+0\s+users?\b|(?:^|\D)0\s*(?:人|票)", result, re.I):
        return ""
    return result


def canonical_reference(value, source):
    raw = value.strip()
    if source == "ptgen":
        host, path = "movie.douban.com", r"/subject/([1-9]\d{0,15})/?"
    else:
        host, path = "bgm.tv", r"/subject/([1-9]\d{0,15})/?"
    if raw.isascii() and raw.isdigit():
        return identifier(raw)
    try:
        url = urlsplit(raw)
    except ValueError:
        url = None
    match = re.fullmatch(path, url.path) if url and url.scheme in {"https", "http"} and url.netloc == host else None
    if not match:
        raise ProviderError("invalid_reference", "请填写有效的豆瓣 ID 或条目链接" if source == "ptgen" else "请填写有效的 Bangumi ID 或条目链接")
    return match[1]


def _link(value, label):
    patterns = {
        "douban": r"https://movie\.douban\.com/subject/[1-9]\d{0,15}/?",
        "imdb": r"https://www\.imdb\.com/title/tt\d{1,16}/?",
        "bangumi": r"https://bgm\.tv/subject/[1-9]\d{0,15}/?",
    }
    url = text(value)
    return url if re.fullmatch(patterns[label], url) else ""


def tmdb_poster(path):
    return "https://image.tmdb.org/t/p/original" + path if isinstance(path, str) and re.fullmatch(r"/[\w.-]+", path) else None


def render_introduction(fields):
    lines = []
    original = text(fields.get("title"))
    for label, key in _FIELDS:
        if key == "aliases":
            entries = [item for item in names(fields.get(key)) if item != original]
        elif key in _LIST_FIELDS:
            entries = names(fields.get(key))
        else:
            entry = text(fields.get(key))
            if key == "runtime":
                entry = re.sub(r"(?<=\d)\s+分钟", "分钟", entry)
            entries = [entry] if entry else []
        if entries:
            separator = "\n　　　　　" if key in _MULTILINE else " / "
            lines.append("◎" + label + "　" + separator.join(entries))
    overview = text(fields.get("overview"))
    if overview:
        if lines:
            lines.append("")
        lines += ["◎简　　介", "", "\n".join("　　" + line.strip() if line.strip() else "" for line in overview.splitlines())]
    return "\n".join(lines)


def _heading_pattern(label):
    return r"\s*".join(re.escape(char) for char in label if not char.isspace())


def clean_ptgen_format(formatted):
    formatted = _LEADING_COVER.sub("", str(formatted or "").strip(), count=1)
    lines = []
    known = re.compile(r"^\s*◎\s*(?:" + "|".join(_heading_pattern(label) for label, _ in _FIELDS) + r")[\s:：]*(.*)$", re.I)
    source_lines = formatted.splitlines()
    for index, line in enumerate(source_lines):
        # A long cast or alias block may span multiple indented lines.
        found = re.fullmatch(r"\s*◎\s*(豆瓣|IMDb)\s*评分[\s:：]*(.*)", line, re.I)
        if found and not rating(found[2]):
            continue
        field = known.fullmatch(line)
        next_line = source_lines[index + 1] if index + 1 < len(source_lines) else ""
        continuation = bool(next_line.strip() and next_line[0].isspace() and not next_line.lstrip().startswith("◎"))
        if field and not text(field[1]) and not continuation:
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


def complete_ptgen_format(formatted, fields):
    """Render one PT-Gen entry, retaining unrecognized fields and full lists."""
    headers = list(re.finditer(r"(?m)^[^\S\r\n]*◎[^\S\r\n]*([^\r\n]*)", formatted))
    if not headers:
        return render_introduction({**fields, "overview": fields.get("overview") or formatted})

    matched = {key: re.compile(_heading_pattern(label) + r"(?=$|[\s:：])", re.I) for label, key in _FIELDS}
    combined = dict(fields)
    extra = []
    after_overview = []
    seen_overview = False
    prefix = formatted[:headers[0].start()].strip()
    if prefix:
        extra.append(prefix)
    for index, heading in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(formatted)
        block = formatted[heading.start():end].strip()
        label = heading[1]
        if summary_heading := re.match(r"简\s*介(?=$|[\s:：])", label):
            overview = (label[summary_heading.end():].lstrip(" :：　") + formatted[heading.end():end]).strip()
            if overview:
                combined["overview"] = overview
            seen_overview = True
            continue
        for key, pattern in matched.items():
            found = pattern.match(label)
            if not found:
                continue
            raw = (label[found.end():].lstrip(" :：　") + formatted[heading.end():end]).strip()
            if key in _LIST_FIELDS:
                structured = names(fields.get(key))
                # Universal renders the same alias array with bare slashes.
                if key == "aliases" and raw.replace(" / ", "/") in {
                    "/".join(structured), "/".join(names(fields.get("translated_aliases")))
                }:
                    combined[key] = structured
                else:
                    combined[key] = names([*names(raw), *structured])
            elif raw:
                combined[key] = raw
            break
        else:
            (after_overview if seen_overview else extra).append(block)

    overview = render_introduction({"overview": combined.pop("overview", None)})
    working = render_introduction(combined)
    meta = "\n".join(block for block in [working, *extra] if block)
    return "\n\n".join(block for block in (meta, overview, *after_overview) if block)


def context_fields(context, ref=None):
    all_fields = (context or {}).get("metadata") or {}
    data = all_fields
    titles = (context or {}).get("titles", [])
    if ref:
        def belongs(item):
            return isinstance(item, dict) and item.get("source") == "tmdb" and str(item.get("source_ref")) == str(ref)

        data = {key: item for key, item in all_fields.items() if belongs(item)}
        for key in ("cast", "directors", "voice_cast"):
            data[key] = [item for item in all_fields.get(key, []) if belongs(item.get("name"))]
        titles = [item for item in titles if belongs(item)]
    links = data.get("links") or {}
    values = {
        "title": data.get("original_title") or data.get("title_zh"),
        "aliases": [data.get("title_zh"), data.get("title_en"), data.get("title_zh_hant"), data.get("title_ja"),
                    *(item.get("text") for item in titles if item.get("kind") != "custom")],
        "year": data.get("year"), "date": data.get("release_date"), "region": data.get("region"),
        "genres": (data.get("genres") or {}).get("value"), "runtime": runtimes((data.get("runtime") or {}).get("value")),
        "overview": data.get("overview"), "directors": data.get("directors"), "cast": data.get("cast"),
        "voice": data.get("voice_cast"), "douban_url": _link(data.get("douban_url") or links.get("douban"), "douban"),
        "imdb_url": _link(links.get("imdb"), "imdb"),
    }
    douban = data.get("douban_rating") or {}
    if isinstance(douban, dict) and douban.get("source") in {"ptgen", "douban"}:
        values["douban_rating"] = rating(douban)
    if isinstance(values["region"], dict):
        values["region"] = values["region"].get("value")
    return values


def _roles(row):
    return {text(row.get("job")), *(text(item.get("job")) for item in (row.get("jobs") or []))}


def tmdb_fields(row, *, season=False):
    translations = (row.get("translations") or {}).get("translations") or []
    alternatives = row.get("alternative_titles") or {}
    title = row.get("original_title") or row.get("original_name") or row.get("title") or row.get("name")
    credits = row.get("aggregate_credits") or row.get("credits") or {}
    episodes = row.get("episodes")
    crew = [*credits.get("crew", [])]
    for episode in episodes or []:
        crew.extend(episode.get("crew") or [])
    actors = [*credits.get("cast", []), *credits.get("guest_stars", [])]
    for episode in episodes or []:
        actors.extend(episode.get("guest_stars") or [])
    date = text(row.get("release_date") or row.get("first_air_date") or row.get("air_date"))
    imdb = text((row.get("external_ids") or {}).get("imdb_id") or row.get("imdb_id"))
    duration = row.get("runtime") or row.get("episode_run_time") or [item.get("runtime") for item in episodes or []]
    return {
        "title": title,
        "aliases": [row.get("title"), row.get("name"), *(x.get("title") for x in (alternatives.get("titles") or alternatives.get("results") or [])),
                    *((x.get("data") or {}).get("title") or (x.get("data") or {}).get("name") for x in translations)],
        "year": date[:4] if re.match(r"^\d{4}", date) else "", "date": date,
        "region": [*names(row.get("origin_country")), *names(row.get("production_countries"))],
        "genres": names(row.get("genres")),
        "languages": [item.get("name") or item.get("english_name") for item in row.get("spoken_languages") or []] or row.get("languages"),
        "imdb_url": _link("https://www.imdb.com/title/" + imdb + "/", "imdb"),
        "season": row.get("season_number") if season else None, "season_count": count(row.get("number_of_seasons")),
        "episode_count": count(row.get("number_of_episodes")) or (len(episodes) if episodes else None),
        "runtime": runtimes(duration), "overview": row.get("overview"),
        "production": names(row.get("production_companies")),
        "music": [person.get("name") for person in crew if _roles(person) & {"Original Music Composer"}],
        "directors": [person.get("name") for person in crew if _roles(person) & {"Director", "Series Director"}],
        "writers": [person.get("name") for person in crew if _roles(person) & {"Writer", "Screenplay", "Teleplay", "Series Composition"}],
        "cast": [person.get("name") for person in actors],
        "voice": [person.get("name") for person in actors if "voice" in text(person.get("character")).casefold()
                  or any("voice" in text(role.get("character")).casefold() for role in (person.get("roles") or []))],
    }


def ptgen_rating(row, site):
    structured = (row.get("ratings") or {}).get(site)
    if isinstance(structured, dict):
        if structured.get("votes") is not None and not count(structured["votes"]):
            return ""
        valid = rating(structured.get("formatted")) or rating({**structured, "score": structured.get("average")})
        if valid:
            return valid
    raw = rating(row.get(site + "_rating") or row.get(site + "_score"))
    if raw:
        return raw
    average, votes = row.get(site + "_rating_average"), row.get(site + "_votes")
    return rating(f"{average}/10 from {votes} users") if count(votes) else ""


def ptgen_fields(row, reference):
    translated = names(row.get("trans_title"))
    aliases = list(translated)
    for key in ("translated_title", "translation", "aliases", "aka", "alt_title", "chinese_title"):
        aliases.extend(names(row.get(key)))
    original = row.get("this_title") or row.get("original_title") or row.get("foreign_title") or row.get("title") or row.get("chinese_title")
    imdb = (row.get("ratings") or {}).get("imdb") or {}
    return {
        "aliases": aliases, "translated_aliases": translated, "title": " / ".join(names(original)),
        "year": row.get("year"), "region": row.get("region") or row.get("countries"),
        "genres": row.get("genre") or row.get("genres"), "languages": row.get("language") or row.get("languages"),
        "date": " / ".join(names(row.get("playdate") or row.get("release_date") or row.get("releaseDate"))),
        "douban_url": "https://movie.douban.com/subject/" + reference + "/",
        "douban_rating": ptgen_rating(row, "douban"),
        "imdb_url": _link(imdb.get("link") or row.get("imdb_url") or row.get("imdb_link"), "imdb"),
        "imdb_rating": ptgen_rating(row, "imdb"),
        "season_count": count(row.get("seasons")), "episode_count": count(row.get("episodes") or row.get("episode_count")),
        "runtime": runtimes(row.get("runtime") or row.get("duration") or row.get("length")),
        "directors": row.get("director") or row.get("directors"), "writers": row.get("writer") or row.get("writers") or row.get("screenwriter"),
        "cast": row.get("cast") or row.get("actors"), "voice": row.get("voice_cast"),
        "production": row.get("production_companies") or row.get("production"),
        "original_work": row.get("original_work"), "music": row.get("music"),
        "overview": row.get("introduction") or row.get("overview") or row.get("summary"),
    }


class IntroductionService:
    def __init__(self, providers):
        self.providers = providers

    def provider(self, name):
        provider = self.providers.get(name)
        if not provider:
            raise ProviderError("unconfigured", "当前资料源未启用，可选择其他来源或直接粘贴资料")
        return provider

    async def ptgen_call(self, method, *args):
        try:
            return await getattr(self.provider("ptgen"), method)(*args)
        except ProviderError as exc:
            message = {
                "unconfigured": "请在设置的「TMDB 与 PT-Gen」中填写 PT-Gen 服务地址及所需的 API Key。",
                "upstream_http_401": "PT-Gen 鉴权失败，请在设置中检查 PT-Gen API Key（HTTP 401）。",
                "upstream_http_403": "PT-Gen 拒绝访问，请检查 API Key 权限及服务端的豆瓣访问状态（HTTP 403）。",
                "upstream_rejected": "PT-Gen 未能读取豆瓣资料，请检查 PT-Gen 服务端的豆瓣访问状态。",
            }.get(exc.code)
            if not message:
                raise
            raise ProviderError(exc.code, message) from exc

    async def options(self, request):
        if request.source == "tmdb_season":
            ref = identifier(request.query.strip())
            row = await self.provider("tmdb").get("tv/" + ref, language="zh-CN")
            return {"items": [
                {"reference": ref, "season": item["season_number"], "title": text(item.get("name")) or f"第 {item['season_number']} 季",
                 "date": text(item.get("air_date")), "series_title": text(row.get("name"))}
                for item in row.get("seasons", []) if type(item.get("season_number")) is int and item["season_number"] >= 0
            ]}
        query = request.query.strip()
        if request.source == "ptgen":
            page = await self.ptgen_call("search", query, "anime")
        else:
            page = await self.provider("bangumi").search(query, "anime")
        return {"items": [{"reference": str(item["id"]), "title": item.get("title"), "date": item.get("year")}
                          for item in page["items"]], "warnings": page.get("warnings", [])}

    async def build(self, request):
        ctx = request.context.model_dump(exclude_none=True) if request.context else {}
        identity = ctx.get("identity") or {}
        source = request.source
        label, poster, warnings = "", None, []
        if source == "current":
            ref = identity.get("tmdb_id")
            if request.automatic and identity.get("media_type") in {"series", "anime"} and (ref or identity.get("bangumi_id")):
                return {"text": "", "poster": None, "label": "", "warnings": [],
                        "message": "请选择发布简介对应的季或篇章；也可明确读取当前匹配的整部作品。"}
            fields = context_fields(ctx, ref)
            label = "当前匹配资料"
            cached_poster = (ctx.get("metadata") or {}).get("poster_url") or {}
            if ref and cached_poster.get("source") == "tmdb" and str(cached_poster.get("source_ref")) == ref:
                poster = text(cached_poster)
            if ref:
                namespace = "movie" if identity.get("media_type") == "movie" else "tv"
                try:
                    row = await self.provider("tmdb").get(namespace + "/" + identifier(ref), language="zh-CN",
                                                          append_to_response="alternative_titles,translations,credits,external_ids" + (",aggregate_credits" if namespace == "tv" else ""))
                    if str(row.get("id")) != ref:
                        raise ProviderError("invalid_schema", "TMDB 未返回所选条目资料")
                    fresh = tmdb_fields(row)
                    # Preserve already fetched language variants of this same ID.
                    fresh["aliases"] = names([*names(fields["aliases"]), *names(fresh["aliases"])])
                    fields = fresh
                    poster = tmdb_poster(row.get("poster_path")) or poster
                except ProviderError as exc:
                    warnings.append(str(exc))
                label = "TMDB 整部作品" if namespace == "tv" else "TMDB 电影"
            elif identity.get("bangumi_id") and not request.automatic:
                return await self.build(IntroductionRequest(source="bangumi", reference=identity["bangumi_id"]))
        elif source == "tmdb_season":
            ref = identifier(request.reference)
            if request.season is None:
                raise ProviderError("invalid_season", "请选择具体季")
            row = await self.provider("tmdb").get(f"tv/{ref}/season/{request.season}", language="zh-CN",
                                                  append_to_response="credits,aggregate_credits,external_ids,translations")
            if row.get("season_number") != request.season:
                raise ProviderError("invalid_schema", "TMDB 未返回所选季的资料")
            fields = tmdb_fields(row, season=True)
            poster = tmdb_poster(row.get("poster_path"))
            label = f"TMDB · 第 {request.season} 季 · {text(fields['title'])}"
        elif source == "ptgen":
            return await self.douban(canonical_reference(request.reference, source))
        else:
            ref = canonical_reference(request.reference, source)
            fields, warnings = await self.bangumi(ref)
            label = "Bangumi · " + ref + " · " + text(fields["title"])
        formatted = render_introduction(fields)
        return {"text": formatted, "label": label, "poster": poster, "warnings": warnings,
                "message": "" if formatted else "此条目暂无可用简介资料，可改选来源或直接粘贴。"}

    async def douban(self, reference):
        supplied = USER_SUPPLIED_DOUBAN.get(reference, {})
        url = "https://movie.douban.com/subject/" + reference + "/"
        label = "PT-Gen · 豆瓣 " + reference
        try:
            data = await self.ptgen_call("get", {"url": url, "format": "bbcode"})
        except ProviderError as exc:
            if not supplied:
                raise
            return {
                "text": render_introduction(supplied), "poster": None,
                "label": "用户提供的资料 · 豆瓣 " + reference,
                "warnings": [str(exc) + " 本次采用该条目的用户提供资料。"],
            }
        row = data.get("data") if isinstance(data.get("data"), dict) else data
        remote_id = row.get("id") or row.get("sid")
        if row.get("site") == "douban" and remote_id and str(remote_id) != reference:
            raise ProviderError("invalid_schema", "PT-Gen 返回的豆瓣条目与所填 ID 不一致。")
        formatted = clean_ptgen_format((row.get("formats") or {}).get("bbcode") or row.get("format") or data.get("format"))
        fields = ptgen_fields(row, reference)
        content = complete_ptgen_format(formatted, fields)
        if content == "◎豆瓣链接　" + url and not supplied:
            raise ProviderError("empty_introduction", "PT-Gen 未返回可用资料，可尝试其他条目或直接粘贴")

        labels = {**{key: name for name, key in _FIELDS}, "overview": "简　　介"}
        def missing(key):
            return not re.search(r"(?m)^◎" + _heading_pattern(labels[key]) + r"(?:$|[^\S\r\n])", content)

        supplement = {key: value for key, value in supplied.items() if missing(key)}
        warnings = []
        if supplement:
            content = complete_ptgen_format(formatted, {**fields, **supplement})
            warnings.append("已用该条目的用户提供资料补充：" +
                            "、".join(labels[key].replace("　", "") for key in supplement) + "。")
        elif not supplied:
            missing_labels = [labels[key].replace("　", "") for key in
                              ("aliases", "languages", "directors", "writers", "cast") if missing(key)]
            if len(missing_labels) >= 3:
                warnings.append("PT-Gen 本次未返回：" + "、".join(missing_labels) + "。可在简介中补充。")
        return {"text": content, "poster": None, "label": label, "warnings": warnings}

    async def bangumi(self, ref):
        provider = self.provider("bangumi")
        subject, people, characters = await asyncio.gather(
            *(provider.api("GET", "subjects/" + ref + path) for path in ("", "/persons", "/characters")), return_exceptions=True)
        if isinstance(subject, Exception):
            raise subject
        if not isinstance(subject, dict) or str(subject.get("id")) != ref:
            raise ProviderError("invalid_schema", "Bangumi 未返回所选条目资料")
        info = {str(item.get("key")): item.get("value") for item in subject.get("infobox", []) if isinstance(item, dict)}
        date = text(subject.get("date"))
        fields = {
            "title": subject.get("name") or subject.get("name_cn"),
            "aliases": [subject.get("name_cn"), *(alias for key in ("别名", "別名", "中文名", "英文名", "简体中文名", "繁体中文名", "日文名") for alias in names(info.get(key)))],
            "date": date, "year": date[:4] if re.match(r"^\d{4}", date) else "",
            "region": info.get("产地") or info.get("国家 / 地区"), "genres": subject.get("meta_tags"), "languages": info.get("语言"),
            "episode_count": count(subject.get("total_episodes")) or count(subject.get("eps")),
            "runtime": runtimes(info.get("每集时长") or info.get("片长")), "overview": subject.get("summary"),
            "bangumi_url": "https://bgm.tv/subject/" + ref, "bangumi_rating": rating(subject.get("rating")),
            "production": info.get("动画制作"), "original_work": info.get("原作"), "music": info.get("音乐"),
            "directors": [row.get("name") for row in people if row.get("relation") in {"导演", "監督", "总导演", "総監督"}] if isinstance(people, list) else [],
            "writers": [row.get("name") for row in people if row.get("relation") in {"编剧", "脚本", "系列构成", "シリーズ構成"}] if isinstance(people, list) else [],
            "cast": [row.get("name") for row in people if row.get("relation") in {"主演", "出演", "演员"}] if isinstance(people, list) else [],
            "voice": [f"{text(actor.get('name'))}（{text(item.get('name'))}）" for item in characters
                      for actor in item.get("actors", []) if text(actor.get("name")) and text(item.get("name"))] if isinstance(characters, list) else [],
        }
        fields["directors"].extend(names(info.get("导演")))
        return fields, [] if isinstance(people, list) and isinstance(characters, list) else ["Bangumi 部分演职员资料暂不可用"]
