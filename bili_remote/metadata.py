"""TMDB and pt-gen-cfworker adapters. All fetched content is a reviewable proposal."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

import httpx

from .database import utc_now
from .file_service import FileServiceError


class MetadataError(FileServiceError):
    pass


class MetadataService:
    def __init__(self, database, secrets, *, transport=None, default_ptgen_url=""):
        self.default_ptgen_url = default_ptgen_url
        self.database, self.secrets, self.transport = database, secrets, transport

    def _secret(self, key):
        value = self.database.get_settings().get(key)
        if not value:
            return ""
        try:
            return str(self.secrets.decrypt_json(value))
        except ValueError as exc:
            raise MetadataError("资料服务密钥无法解密，请重新填写") from exc

    def _json(self, url, *, params=None, headers=None, follow_redirects=False):
        try:
            timeout = int(self.database.get_settings().get("metadata_timeout_seconds", 20))
            with httpx.Client(timeout=timeout, transport=self.transport) as client:
                response = client.get(url, params=params, headers=headers)
                if follow_redirects:
                    origin = urlsplit(url)
                    for _ in range(5):
                        if not response.is_redirect:
                            break
                        target = urljoin(str(response.url), response.headers.get("location", ""))
                        destination = urlsplit(target)
                        if (destination.scheme, destination.netloc) != (
                            origin.scheme,
                            origin.netloc,
                        ):
                            raise MetadataError("资料服务重定向到其他地址，请填写最终 API 地址")
                        response = client.get(target, headers=headers)
                    if response.is_redirect:
                        raise MetadataError("资料服务重定向次数过多，请检查 API 地址")
            response.raise_for_status()
            if len(response.content) > 4_000_000:
                raise MetadataError("资料响应过大")
            data = response.json()
            if not isinstance(data, dict):
                raise MetadataError("资料服务返回了无效数据")
            return data
        except MetadataError:
            raise
        except httpx.TimeoutException as exc:
            raise MetadataError("资料请求超时，请检查服务连接或在设置中调整超时") from exc
        except httpx.HTTPStatusError as exc:
            # Never expose request URLs/headers: credentials may be in query strings.
            code = exc.response.status_code
            if code in {401, 403}:
                detail = "访问被拒绝，请检查 API Key 与服务访问权限"
            elif code == 404:
                detail = "接口不存在，请检查是否填写完整 API 路径"
            elif code == 429:
                detail = "请求过于频繁，请稍后重试"
            elif 300 <= code < 400:
                detail = "接口发生重定向，请填写最终 API 地址"
            else:
                detail = "资料服务异常，请检查上游服务"
            raise MetadataError(f"{detail}（HTTP {code}）") from exc
        except httpx.HTTPError as exc:
            raise MetadataError("无法连接资料服务，请检查服务地址和网络") from exc
        except ValueError as exc:
            raise MetadataError("资料服务未返回有效 JSON，请检查是否填成了网页地址") from exc

    def _tmdb(self, path, **params):
        token = self._secret("tmdb_api_key")
        if not token:
            raise MetadataError("请先在设置中填写 TMDB API 凭据")
        headers = {}
        if token.startswith("eyJ") or len(token) > 64:
            headers["Authorization"] = "Bearer " + token
        else:
            params["api_key"] = token
        return self._json("https://api.themoviedb.org/3/" + path, params=params, headers=headers)

    def search(self, query: str, media_type="tv", year=None, page=1):
        if media_type not in {"movie", "tv"} or not query.strip() or len(query) > 200:
            raise MetadataError("请输入电影或剧集名称")
        params = {"query": query.strip(), "language": "zh-CN", "page": page}
        if year:
            params["year" if media_type == "movie" else "first_air_date_year"] = year
        data = self._tmdb(f"search/{media_type}", **params)
        return {
            "items": [self._normalize(item, {}, media_type) for item in data.get("results", [])],
            "page": data.get("page", 1),
            "total_pages": data.get("total_pages", 1),
        }

    @staticmethod
    def _normalize(zh, en, media_type):
        poster = zh.get("poster_path") or en.get("poster_path")
        name = "title" if media_type == "movie" else "name"
        english = str(en.get(name) or "")
        # en-US may fall back to the original CJK name; it is not an English title.
        if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", english):
            english = ""
        return {
            "tmdb_id": zh.get("id"),
            "media_type": media_type,
            "title_zh": zh.get(name) or "",
            "title_en": english,
            "original_title": zh.get("original_" + name) or "",
            "year": str(
                zh.get("release_date" if media_type == "movie" else "first_air_date") or ""
            )[:4],
            "overview": zh.get("overview") or en.get("overview") or "",
            "poster_url": "https://image.tmdb.org/t/p/original" + poster if poster else "",
            "genres": [x.get("name") for x in zh.get("genres", [])],
            "countries": [x.get("name") for x in zh.get("production_countries", [])],
            "imdb_url": "",
            "douban_url": "",
            "source": "tmdb",
        }

    def detail(self, media_type, tmdb_id):
        if media_type not in {"tv", "movie"} or tmdb_id < 1:
            raise MetadataError("作品标识无效")
        zh = self._tmdb(
            f"{media_type}/{tmdb_id}", language="zh-CN", append_to_response="external_ids"
        )
        en = self._tmdb(f"{media_type}/{tmdb_id}", language="en-US")
        data = self._normalize(zh, en, media_type)
        imdb = (zh.get("external_ids") or {}).get("imdb_id") or zh.get("imdb_id")
        if imdb and re.fullmatch(r"tt\d+", imdb):
            data["imdb_url"] = f"https://www.imdb.com/title/{imdb}/"
        return {"proposal": data, "raw": {"zh": zh, "en": en}}

    def ptgen_endpoint(self, stored):
        return stored.get("ptgen_url", "").strip() or self.default_ptgen_url

    def _ptgen_request(self, params, *, search=False):
        endpoint = self.ptgen_endpoint(self.database.get_settings())
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise MetadataError("请先设置有效的 PT-Gen 服务地址")
        if search:
            # Universal's info and search routes are siblings. Legacy workers
            # accept ?search= at their configured endpoint instead.
            path = parsed.path.rstrip("/")
            if path.endswith("/info"):
                path = path[:-4] + "search"
                params = {"q": params["search"], "source": "douban"}
            endpoint = urlunsplit(parsed._replace(path=path or "/"))
        params = {**dict(parse_qsl(urlsplit(endpoint).query)), **params}
        endpoint = urlunsplit(urlsplit(endpoint)._replace(query="", fragment=""))
        key = self._secret("ptgen_api_key")
        if key:
            params["apikey"] = key
        try:
            data = self._json(endpoint, params=params, follow_redirects=True)
        except MetadataError as exc:
            if search and "HTTP 403" in str(exc):
                raise MetadataError(
                    "PT-Gen 搜索被拒绝，请检查服务是否禁用了搜索以及 API Key 权限；"
                    "也可直接填写豆瓣链接"
                ) from exc
            raise
        if data.get("success") is False or data.get("error"):
            error = str(data.get("error") or "")
            if "json-ld" in error.lower():
                raise MetadataError(
                    "PT-Gen 无法解析 IMDb 页面（缺少或无效的 JSON-LD 数据）；"
                    "可改用豆瓣链接，或检查 PT-Gen 的 IMDb 抓取器"
                )
            if "apikey" in error.lower():
                raise MetadataError("PT-Gen 拒绝访问，请检查 API Key")
            if search:
                raise MetadataError(
                    "PT-Gen 豆瓣搜索失败，请检查服务的搜索开关、网络或豆瓣 Cookie；也可直接填写链接"
                )
            raise MetadataError("PT-Gen 未能获取资料，请保留现有内容或手动补充")
        return data

    def ptgen_search(self, query):
        if not query.strip() or len(query) > 200:
            raise MetadataError("请输入电影或剧集名称")
        data = self._ptgen_request({"search": query.strip()}, search=True)
        rows = data.get("data")
        if not isinstance(rows, list):
            raise MetadataError(
                "PT-Gen 未返回搜索列表，请检查服务是否支持片名搜索；也可直接填写豆瓣链接"
            )
        items = []
        for row in rows[:100]:
            if not isinstance(row, dict):
                continue
            link = str(row.get("link") or "")
            if not re.fullmatch(r"https://movie\.douban\.com/subject/\d+/?", link):
                continue
            items.append(
                {
                    "title_zh": str(row.get("title") or ""),
                    "year": str(row.get("year") or "").strip(),
                    "overview": str(row.get("subtitle") or ""),
                    "douban_url": link.rstrip("/") + "/",
                    "source": "ptgen",
                }
            )
        return {"items": items}

    def ptgen(self, link):
        if not re.fullmatch(
            r"https://(?:movie\.douban\.com/subject/\d+|www\.imdb\.com/title/tt\d+)/?", link
        ):
            raise MetadataError("请提供标准豆瓣或 IMDb 链接")
        data = self._ptgen_request({"url": link})
        payload = data.get("data") if isinstance(data.get("data"), dict) else data
        text = str(payload.get("format") or data.get("format") or "")
        if not text:
            raise MetadataError("PT-Gen 返回的资料中没有简介")
        urls = re.findall(
            r"https://movie\.douban\.com/subject/\d+/|https://www\.imdb\.com/title/tt\d+/", text
        )
        correlated = {"douban_url" if "douban.com" in url else "imdb_url": url for url in urls}
        correlated["douban_url" if "douban.com" in link else "imdb_url"] = link
        title_en = str(payload.get("foreign_title") or "").strip()
        if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", title_en):
            title_en = ""
        details = {
            "title_zh": str(payload.get("chinese_title") or "").strip(),
            "title_en": title_en,
            "year": str(payload.get("year") or "").strip(),
            "overview": str(payload.get("introduction") or "").strip(),
            "genres": payload.get("genre"),
            "countries": payload.get("region"),
        }
        return {
            "proposal": {
                **{k: v for k, v in details.items() if v},
                **correlated,
                "ptgen_text": text,
                "source": "ptgen",
                "lookup_url": link,
                "related_links": list(dict.fromkeys(urls)),
                "poster_url": str(payload.get("poster") or ""),
            },
            "raw": data,
        }

    def save(self, data):
        fields = data.get("fields") or {}
        if len(json.dumps(fields, ensure_ascii=False)) > 2_000_000:
            raise MetadataError("影视资料过大")
        record_id = data.get("id")
        now = utc_now()
        values = (
            json.dumps(fields, ensure_ascii=False),
            str(data.get("source_root_key") or ""),
            str(data.get("source_relative_path") or ""),
            now,
        )
        if record_id:
            if not self.get(record_id):
                raise MetadataError("影视资料不存在")
            self.database.execute(
                "UPDATE media_metadata SET fields_json=?, source_root_key=?, "
                "source_relative_path=?, updated_at=? WHERE id=?",
                (*values, record_id),
            )
        else:
            record_id = self.database.execute(
                "INSERT INTO media_metadata(fields_json,source_root_key,source_relative_path,"
                "updated_at,created_at) VALUES (?,?,?,?,?)",
                (*values, now),
            )
        return self.get(record_id)

    def get(self, record_id):
        row = self.database.fetch_one("SELECT * FROM media_metadata WHERE id=?", (record_id,))
        if row:
            row["fields"] = json.loads(row.pop("fields_json"))
        return row

    def list(self):
        return [
            self.get(row["id"])
            for row in self.database.fetch_all(
                "SELECT id FROM media_metadata ORDER BY updated_at DESC LIMIT 100"
            )
        ]
