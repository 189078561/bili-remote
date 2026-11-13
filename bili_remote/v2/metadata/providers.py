"""Read-only TMDB, Bangumi and optional PT-Gen adapters.

Development credentials are supplied explicitly by the composition root. This
module never reads environment files, production settings or saved metadata.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl

import httpx


class ProviderError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def now():
    return datetime.now(timezone.utc).isoformat()


def field(value, provider, ref):
    return {"value": value if value not in ("", []) else None, "source": provider, "source_ref": str(ref),
            "manual": False, "confirmed": False, "confidence": None, "updated_at": now()}


def title(text, language, kind, provider, ref):
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    identifier = hashlib.sha256((provider + ":" + str(ref) + ":" + language + ":" + kind + ":" + text).encode()).hexdigest()[:24]
    return {"id": identifier, "text": text, "language": language, "kind": kind, "source": provider, "source_ref": str(ref), "selected": False}


def identifier(value):
    if not re.fullmatch(r"[1-9]\d{0,15}", str(value)):
        raise ProviderError("invalid_id", "影视 ID 无效")
    return str(value)


class Provider:
    name = ""

    def __init__(self, *, client=None, timeout=20, configured=True, authentication=False):
        self.client, self.timeout = client, timeout
        self.configured = configured
        self.observations = []
        self._capabilities = {"provider": self.name, "state": "unconfigured", "checked_at": None,
                              "authentication": "configured" if authentication else "unconfigured",
                              "supported_features": [], "sample_schema_hash": None,
                              "warnings": [{"code": "not_verified", "message": "尚未执行真实开发接口核验"}]}

    async def request(self, method, url, *, params=None, headers=None, body=None):
        if not self.configured:
            raise ProviderError("unconfigured", "资料源尚未启用或配置开发凭据")
        own = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=False)
        try:
            async with client.stream(method, url, params=params, headers=headers, json=body, follow_redirects=False) as response:
                if response.status_code >= 300:
                    code = response.status_code
                    raise ProviderError("upstream_http_" + str(code), "资料接口请求失败（HTTP " + str(code) + "）")
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 4_000_000:
                        raise ProviderError("response_too_large", "资料响应过大")
                try:
                    data = json.loads(content)
                except (ValueError, UnicodeError) as exc:
                    raise ProviderError("invalid_json", "资料接口未返回有效 JSON") from exc
                if not isinstance(data, (dict, list)):
                    raise ProviderError("invalid_schema", "资料响应结构无效")
                self.observations.append({"method": method, "status": response.status_code, "content_type": response.headers.get("content-type"), "schema": schema_shape(data)})
                self.observations = self.observations[-20:]
                return data
        except httpx.TimeoutException as exc:
            raise ProviderError("upstream_timeout", "资料请求超时") from exc
        except httpx.HTTPError as exc:
            raise ProviderError("upstream_unavailable", "无法连接资料接口") from exc
        finally:
            if own:
                await client.aclose()

    def capabilities(self):
        from copy import deepcopy
        return deepcopy(self._capabilities)

    async def verify(self, selection, query):
        """Explicit development action only; never called by GET capabilities.

        A mock transport must not be used to claim real verification. The
        command-line verification utility refuses injected clients.
        """
        if self.client is not None:
            raise ProviderError("mock_not_verification", "注入的测试连接不能标记为真实核验")
        try:
            page = await self.search(query, selection.get("media_type", "anime"), page=1)
            detail = await self.detail(selection)
            shape = {"normalized": schema_shape({"page": page, "detail": detail}), "http": self.observations}
            self._capabilities.update(state="verified", checked_at=now(), supported_features=detail.pop("supported_features", []),
                                      sample_schema_hash=hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest(), warnings=[])
            return shape
        except ProviderError as exc:
            self._capabilities.update(state="unconfigured" if not self.configured else "failed", checked_at=now(), supported_features=[], warnings=[{"code": exc.code, "message": str(exc)}])
            return None


def schema_shape(item):
    if isinstance(item, dict):
        return {key: schema_shape(value) for key, value in sorted(item.items())}
    if isinstance(item, list):
        # Do not retain remote text, images, personal data or auth parameters.
        shapes = {json.dumps(schema_shape(x), sort_keys=True) for x in item[:1000]}
        return [json.loads(x) for x in sorted(shapes)]
    return "null" if item is None else type(item).__name__


class TMDBProvider(Provider):
    name = "tmdb"

    def __init__(self, token="", **kwargs):
        self.token = token
        super().__init__(configured=bool(token), authentication=bool(token), **kwargs)

    async def get(self, path, **params):
        if not self.token:
            raise ProviderError("unconfigured", "未配置 TMDB 开发凭据")
        headers = {}
        if self.token.startswith("eyJ") or len(self.token) > 64:
            headers["Authorization"] = "Bearer " + self.token
        else:
            params["api_key"] = self.token
        data = await self.request("GET", "https://api.themoviedb.org/3/" + path, params=params, headers=headers)
        if not isinstance(data, dict):
            raise ProviderError("invalid_schema", "TMDB 响应结构无效")
        return data

    async def search(self, query, media_type, year=None, page=1):
        namespace = "movie" if media_type == "movie" else "tv"
        params = {"query": query, "language": "zh-CN", "page": page}
        if year:
            params["year" if namespace == "movie" else "first_air_date_year"] = year
        direct = re.fullmatch(r"tmdb[:\s]+(?:(movie|tv)[:/])?(\d{1,12})", query.strip(), re.I)
        if direct:
            namespace = direct.group(1) or namespace
            media_type = "movie" if namespace == "movie" else "series"
            row = await self.get(namespace + "/" + direct.group(2), language="zh-CN")
            data = {"results": [row], "page": 1, "total_pages": 1}
        else:
            data = await self.get("search/" + namespace, **params)
        items = []
        for row in data.get("results", [])[:1000]:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            items.append({"provider": self.name, "id": identifier(row["id"]), "media_type": media_type,
                          "title": row.get("title" if namespace == "movie" else "name") or "",
                          "original_title": row.get("original_title" if namespace == "movie" else "original_name") or "",
                          "poster_url": "https://image.tmdb.org/t/p/w92" + row["poster_path"] if row.get("poster_path") else None,
                          "year": (row.get("release_date" if namespace == "movie" else "first_air_date") or "")[:4] or None})
        return {"items": items, "page": data.get("page", page), "total_pages": data.get("total_pages"), "warnings": []}

    async def detail(self, selection):
        ref = identifier(selection["id"])
        media_type = selection["media_type"]
        namespace = "movie" if media_type == "movie" else "tv"
        path = namespace + "/" + ref
        languages = ["zh-CN", "zh-TW", "ja-JP", "en-US"]
        received = await asyncio.gather(*(self.get(path, language=language, **({"append_to_response": "alternative_titles,credits,external_ids,translations"} if language == "zh-CN" else {})) for language in languages), return_exceptions=True)
        if all(isinstance(row, Exception) for row in received):
            raise next(row for row in received if isinstance(row, Exception))
        rows = [row if isinstance(row, dict) else {} for row in received]
        zh = next(row for row in rows if row)
        warnings = [{"code": "partial_locale", "ref": language, "message": "部分语言资料不可用，已保留其他结果"} for language, row in zip(languages, received) if isinstance(row, Exception)]
        key = "title" if namespace == "movie" else "name"
        titles = []
        translations = (zh.get("translations") or {}).get("translations", [])
        localized = {}
        for language, row in zip(languages, rows):
            lang, country = language.split("-")
            translation = next((item for item in translations if item.get("iso_639_1") == lang and item.get("iso_3166_1") == country), None)
            text = (translation.get("data") or {}).get(key) if translation else row.get(key)
            original_text = zh.get("original_" + key)
            if not translation and text == original_text and (zh.get("original_language") != lang or lang == "zh"):
                # Locale endpoints can fall back to the original name; this
                # does not establish a translation, particularly its script.
                text = None
            if language == "en-US" and re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", str(text or "")):
                continue
            localized[language] = text
            candidate = title(text, language, "english" if language == "en-US" else "localized", self.name, ref)
            if candidate:
                titles.append(candidate)
        original = zh.get("original_" + key)
        candidate = title(original, zh.get("original_language") or "und", "original", self.name, ref)
        if candidate:
            titles.append(candidate)
        for row in (zh.get("alternative_titles") or {}).get("titles" if namespace == "movie" else "results", []):
            candidate = title(row.get("title"), row.get("iso_639_1") or "und", "alternative", self.name, ref)
            if candidate:
                titles.append(candidate)
        credits = zh.get("credits") or {}
        cast, directors, voice = [], [], []
        for row in credits.get("cast", []):
            person = {"person_id": str(row["id"]) if row.get("id") else None, "name": field(row.get("name"), self.name, ref), "role": "actor", "character": row.get("character") or None, "provider_order": row.get("order")}
            cast.append(person)
            if "voice" in (row.get("character") or "").lower():
                voice.append({**person, "role": "voice_actor"})
        for row in credits.get("crew", []):
            if row.get("job") == "Director":
                directors.append({"person_id": str(row["id"]) if row.get("id") else None, "name": field(row.get("name"), self.name, ref), "role": "director", "character": None, "provider_order": None})
        metadata = {"title_zh": field(localized.get("zh-CN"), self.name, ref), "title_zh_hant": field(localized.get("zh-TW"), self.name, ref),
                    "title_ja": field(localized.get("ja-JP"), self.name, ref), "original_title": field(original, self.name, ref),
                    "year": field((zh.get("release_date" if namespace == "movie" else "first_air_date") or "")[:4] or None, self.name, ref),
                    "region": field([x.get("name") for x in zh.get("production_countries", []) if isinstance(x, dict) and x.get("name")], self.name, ref),
                    "release_date": field(zh.get("release_date" if namespace == "movie" else "first_air_date"), self.name, ref),
                    "overview": field(zh.get("overview") or rows[3].get("overview"), self.name, ref),
                    "poster_url": field("https://image.tmdb.org/t/p/original" + zh["poster_path"] if zh.get("poster_path") else None, self.name, ref),
                    "genres": field([x["name"] for x in zh.get("genres", []) if x.get("name")], self.name, ref),
                    "runtime": field(zh.get("runtime") if namespace == "movie" else next((x for x in zh.get("episode_run_time", []) if type(x) is int and x > 0), None), self.name, ref),
                    "cast": cast, "voice_cast": voice, "directors": directors, "links": {"tmdb": "https://www.themoviedb.org/" + namespace + "/" + ref}}
        imdb = (zh.get("external_ids") or {}).get("imdb_id") or zh.get("imdb_id")
        if re.fullmatch(r"tt\d+", str(imdb or "")):
            metadata["links"]["imdb"] = "https://www.imdb.com/title/" + imdb + "/"
        episodes = []
        episodes_verified = False
        season = selection.get("season")
        if namespace == "tv" and season is not None:
            if type(season) is not int or season < 0:
                raise ProviderError("invalid_season", "季号无效")
            try:
                season_data = await self.get(path + "/season/" + str(season), language="zh-CN")
                episodes = season_data.get("episodes")
                if not isinstance(episodes, list):
                    raise ProviderError("invalid_schema", "TMDB 季集列表无效")
                episodes_verified = True
            except ProviderError:
                episodes = []
                warnings.append({"code": "season_unavailable", "message": "所选季集列表不可用，请人工指定映射与总集数"})
        return {"identity": {"media_type": media_type, "tmdb_id": ref, "bangumi_id": None, "selected_by": "manual", "confidence": None, "confirmed": True},
                "titles": titles, "metadata": metadata, "episodes": episodes, "episodes_verified": episodes_verified, "season": season, "warnings": warnings,
                "supported_features": ["search", "multilingual_titles", "alternative_titles", "credits", "external_ids"] + (["season_episodes"] if episodes_verified else [])}


class BangumiProvider(Provider):
    name = "bangumi"

    def __init__(self, token="", **kwargs):
        self.token = token
        super().__init__(authentication=bool(token), **kwargs)

    async def api(self, method, path, *, body=None, params=None):
        headers = {"User-Agent": "BiliRemoteV2/0.1 (read-only development metadata)"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        return await self.request(method, "https://api.bgm.tv/v0/" + path, headers=headers, body=body, params=params)

    async def search(self, query, media_type, year=None, page=1):
        filters = {"type": [2]}
        if year:
            filters["air_date"] = [">=" + str(year) + "-01-01", "<=" + str(year) + "-12-31"]
        data = await self.api("POST", "search/subjects", body={"keyword": query, "sort": "match", "filter": filters}, params={"limit": 20, "offset": (page - 1) * 20})
        if not isinstance(data, dict) or not isinstance(data.get("data"), list):
            raise ProviderError("invalid_schema", "Bangumi 搜索结构无效")
        return {"items": [{"provider": self.name, "id": identifier(x["id"]), "media_type": "anime", "title": x.get("name_cn") or x.get("name") or "", "original_title": x.get("name") or "", "year": (x.get("date") or "")[:4] or None} for x in data["data"] if isinstance(x, dict) and x.get("id")],
                "page": page, "total_pages": (int(data["total"]) + 19) // 20 if type(data.get("total")) is int else None, "warnings": []}

    async def detail(self, selection):
        ref = identifier(selection["id"])
        responses = await asyncio.gather(self.api("GET", "subjects/" + ref), self.api("GET", "subjects/" + ref + "/persons"), self.api("GET", "subjects/" + ref + "/characters"), self.api("GET", "subjects/" + ref + "/subjects"), return_exceptions=True)
        subject = responses[0]
        if isinstance(subject, Exception):
            raise subject
        if not isinstance(subject, dict):
            raise ProviderError("invalid_schema", "Bangumi 详情结构无效")
        persons, characters, related = [item if isinstance(item, list) else [] for item in responses[1:]]
        warnings = [{"code": "partial_credits", "message": "部分演职员或关联资料不可用，已保留条目信息"}] if any(not isinstance(item, list) for item in responses[1:]) else []
        titles = []
        original_language = "ja" if re.search(r"[\u3040-\u30ff]", str(subject.get("name") or "")) else "und"
        for text, lang, kind in ((subject.get("name_cn"), "zh", "localized"), (subject.get("name"), original_language, "original")):
            candidate = title(text, lang, kind, self.name, ref)
            if candidate:
                titles.append(candidate)
        language_keys = {"简体中文名": "zh-Hans", "繁体中文名": "zh-Hant", "中文名": "zh-Hans", "英文名": "en", "日文名": "ja", "日本語名": "ja"}
        for box in subject.get("infobox", []):
            if not isinstance(box, dict):
                continue
            language = language_keys.get(box.get("key"), "und")
            if box.get("key") not in language_keys and box.get("key") not in {"别名", "別名"}:
                continue
            values = box.get("value", [])
            if isinstance(values, str):
                values = [values]
            for entry in values if isinstance(values, list) else []:
                text = entry.get("v") if isinstance(entry, dict) else entry
                lang = language_keys.get(entry.get("k"), language) if isinstance(entry, dict) else language
                candidate = title(text, lang, "alternative", self.name, ref)
                if candidate:
                    titles.append(candidate)
        voice = []
        for character in characters:
            for actor in character.get("actors", []):
                voice.append({"person_id": str(actor["id"]) if actor.get("id") else None, "name": field(actor.get("name"), self.name, ref),
                              "role": "voice_actor", "character": character.get("name") or None, "provider_order": len(voice)})
        directors = [{"person_id": str(x["id"]) if x.get("id") else None, "name": field(x.get("name"), self.name, ref), "role": "director", "character": None, "provider_order": i} for i, x in enumerate(persons) if x.get("relation") in {"导演", "監督", "总导演", "総監督"}]
        metadata = {"title_zh": field(subject.get("name_cn"), self.name, ref), "original_title": field(subject.get("name"), self.name, ref),
                    "year": field((subject.get("date") or "")[:4] or None, self.name, ref), "overview": field(subject.get("summary"), self.name, ref),
                    "poster_url": field((subject.get("images") or {}).get("large"), self.name, ref),
                    "genres": field(None, self.name, ref), "cast": [], "voice_cast": voice, "directors": directors,
                    "links": {"bangumi": "https://bgm.tv/subject/" + ref}}
        for key, lang in (("title_zh_hant", "zh-Hant"), ("title_ja", "ja")):
            text = next((x["text"] for x in titles if x["language"] == lang), None)
            metadata[key] = field(text, self.name, ref)
        # Relationships are only references, never assumed cross-provider IDs.
        for i, item in enumerate(related[:100]):
            if item.get("id"):
                metadata["links"]["bangumi_related_" + str(i)] = "https://bgm.tv/subject/" + identifier(item["id"])
        return {"identity": {"media_type": "anime", "tmdb_id": None, "bangumi_id": ref, "selected_by": "manual", "confidence": None, "confirmed": True},
                "titles": titles, "metadata": metadata, "episodes": [], "season": None, "warnings": warnings,
                "supported_features": ["search", "subject", "title_aliases", "voice_cast", "directors", "related_subjects"]}


class PTGenProvider(Provider):
    name = "ptgen"

    def __init__(self, endpoint="", api_key="", **kwargs):
        self.endpoint, self.api_key = endpoint, api_key
        parsed = urlsplit(endpoint)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password
        if endpoint and not valid:
            raise ProviderError("invalid_endpoint", "PT-Gen 地址无效")
        super().__init__(configured=bool(endpoint), authentication=bool(api_key), **kwargs)

    async def get(self, params, *, search=False):
        if not self.endpoint:
            raise ProviderError("unconfigured", "未配置 PT-Gen 开发接口")
        parsed = urlsplit(self.endpoint)
        path = parsed.path.rstrip("/")
        if search and path.endswith("/info"):
            path = path[:-4] + "search"
            params = {"q": params["search"], "source": "douban"}
        params = {**dict(parse_qsl(parsed.query)), **params}
        if self.api_key:
            params["apikey"] = self.api_key
        endpoint = urlunsplit(parsed._replace(path=path or "/", query="", fragment=""))
        data = await self.request("GET", endpoint, params=params)
        if not isinstance(data, dict) or data.get("success") is False or data.get("error"):
            raise ProviderError("upstream_rejected", "PT-Gen 无法获取资料")
        return data

    async def search(self, query, media_type, year=None, page=1):
        if page != 1:
            return {"items": [], "page": page, "total_pages": 1, "warnings": []}
        data = await self.get({"search": query}, search=True)
        if not isinstance(data.get("data"), list):
            raise ProviderError("unsupported_search", "该 PT-Gen 未返回搜索列表")
        items = []
        for row in data["data"][:100]:
            if not isinstance(row, dict):
                continue
            match = re.fullmatch(r"https://movie\.douban\.com/subject/(\d+)/?", str(row.get("link") or ""))
            if match:
                items.append({"provider": self.name, "id": match.group(1), "media_type": media_type, "title": row.get("title") or "", "year": row.get("year") or None})
        return {"items": items, "page": 1, "total_pages": 1, "warnings": []}

    async def detail(self, selection):
        ref = identifier(selection["id"])
        link = "https://movie.douban.com/subject/" + ref + "/"
        data = await self.get({"url": link})
        row = data.get("data") if isinstance(data.get("data"), dict) else data
        # Formatted text is deliberately not parsed as facts. Deployment
        # variants with no structured fields return a partial proposal.
        titles = []
        for text, lang, kind in ((row.get("chinese_title"), "zh-Hans", "localized"), (row.get("foreign_title"), "und", "original")):
            item = title(text, lang, kind, self.name, ref)
            if item:
                titles.append(item)
        year = str(row.get("year") or "")
        metadata = {"title_zh": field(row.get("chinese_title"), self.name, ref), "original_title": field(row.get("foreign_title"), self.name, ref),
                    "year": field(year if re.fullmatch(r"\d{4}", year) else None, self.name, ref), "overview": field(row.get("introduction"), self.name, ref),
                    "poster_url": field(row.get("poster"), self.name, ref), "genres": field(row.get("genre") if isinstance(row.get("genre"), list) else None, self.name, ref),
                    "cast": [], "voice_cast": [], "directors": [], "links": {"douban": link}}
        return {"identity": None, "titles": titles, "metadata": metadata, "episodes": [], "season": None,
                "supported_features": ["search", "detail"] + (["structured_titles"] if titles else [])}
