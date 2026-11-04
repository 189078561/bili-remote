import asyncio
from copy import deepcopy

import httpx
import pytest

from bili_remote.v2.metadata import MetadataResolver, TMDBProvider, BangumiProvider, PTGenProvider, ProviderError
from bili_remote.v2.metadata.resolver import deduplicate_titles


def tmdb_transport(request):
    path = request.url.path
    if "/search/" in path:
        return httpx.Response(200, json={"page": 1, "total_pages": 2, "results": [{"id": 100, "name": "测试剧", "original_name": "Original", "first_air_date": "2023-01-01"}]})
    if "/season/" in path:
        return httpx.Response(200, json={"episodes": [{"id": n, "season_number": 2, "episode_number": n} for n in (1, 2, 3)]})
    language = request.url.params.get("language")
    names = {"zh-CN": "简体名称", "zh-TW": "繁體名稱", "ja-JP": "原題", "en-US": "English Title"}
    return httpx.Response(200, json={"id": 100, "name": names[language], "original_name": "原題", "original_language": "ja", "first_air_date": "2023-01-01", "overview": "description", "poster_path": "/image.jpg", "genres": [{"name": "Drama"}], "alternative_titles": {"results": [{"title": "Alternative", "iso_639_1": "en"}]}, "credits": {"cast": [{"id": 3, "name": "Actor", "character": "Person (voice)", "order": 0}], "crew": [{"id": 4, "name": "Director", "job": "Director"}]}, "external_ids": {"imdb_id": "tt123"}})


def test_tmdb_multilingual_alternatives_season_credits_and_custom():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(tmdb_transport)) as client:
            provider = TMDBProvider("development-placeholder", client=client)
            resolver = MetadataResolver({"tmdb": provider})
            page = await resolver.search("test", "series", ["tmdb"], year=2023)
            assert page["items"][0]["id"] == "100"
            result = await resolver.resolve({"provider": "tmdb", "id": "100", "media_type": "series", "season": 2})
            context = result["context"]
            assert {x["text"] for x in context["titles"]} == {"简体名称", "繁體名稱", "原題", "English Title", "Alternative"}
            assert context["selected_title"]["value"] is None
            assert context["metadata"]["cast"][0]["person_id"] == "3"
            assert context["metadata"]["directors"][0]["role"] == "director"
            assert context["episode"]["season_episode_count"]["value"] == 3
            english = next(x for x in context["titles"] if x["kind"] == "english")
            resolver.select_title(context, english["id"])
            assert context["selected_title"]["manual"]
            custom = await resolver.resolve({"provider": "tmdb", "id": "100", "media_type": "series", "custom_title": "My Show"}, context)
            assert custom["context"]["selected_title"]["value"] == "My Show"
            assert provider.capabilities()["state"] != "verified"
            with pytest.raises(ProviderError, match="真实核验"):
                await provider.verify({"id": "100", "media_type": "series"}, "test")
    asyncio.run(run())


def test_manual_locks_and_automatic_identity_proposal():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(tmdb_transport)) as client:
            resolver = MetadataResolver({"tmdb": TMDBProvider("dev", client=client)})
            initial = (await resolver.resolve({"provider": "tmdb", "id": "100", "media_type": "series"}))["context"]
            initial["metadata"]["year"].update(value=1999, manual=True, source="manual")
            initial["selected_title"].update(value="Locked", manual=True, source="manual")
            refreshed = await resolver.resolve({"provider": "tmdb", "id": "100", "media_type": "series"}, initial)
            assert refreshed["context"]["metadata"]["year"]["value"] == 1999
            assert refreshed["context"]["selected_title"]["value"] == "Locked"
            different = await resolver.resolve({"provider": "tmdb", "id": "200", "media_type": "series", "selected_by": "automatic"}, initial)
            assert different["context"]["identity"]["tmdb_id"] == "100"
            assert different["changes"][0]["applied"] is False
    asyncio.run(run())


def test_bangumi_structured_aliases_voice_cast_related_and_non_japanese():
    def handler(request):
        path = request.url.path
        if path.endswith("/persons"):
            return httpx.Response(200, json=[{"id": 4, "name": "导演", "relation": "监督"}, {"id": 5, "name": "监督", "relation": "監督"}])
        if path.endswith("/characters"):
            return httpx.Response(200, json=[{"id": 7, "name": "Character", "actors": [{"id": 8, "name": "Voice"}]}])
        if path.endswith("/subjects") and request.method == "GET":
            return httpx.Response(200, json=[{"id": 9, "relation": "续集"}])
        if path.endswith("search/subjects"):
            return httpx.Response(200, json={"data": [{"id": 1, "name": "Original", "name_cn": "中文", "date": "2020-01-01"}], "total": 1})
        return httpx.Response(200, json={"id": 1, "name": "Original", "name_cn": "中文", "date": "2020-01-01", "infobox": [{"key": "别名", "value": [{"k": "繁体中文名", "v": "繁體"}, {"v": "Alias"}]}], "images": {"large": "https://example.com/poster"}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = BangumiProvider(client=client)
            resolver = MetadataResolver({"bangumi": provider})
            page = await resolver.search("test", "anime", ["bangumi"])
            assert page["items"][0]["id"] == "1"
            result = await resolver.resolve({"provider": "bangumi", "id": "1", "media_type": "anime"})
            metadata = result["context"]["metadata"]
            assert metadata["voice_cast"][0]["character"] == "Character"
            assert metadata["title_zh_hant"]["value"] == "繁體"
            assert metadata["title_ja"]["value"] is None
            assert metadata["links"]["bangumi_related_0"].endswith("/9")
            assert metadata["genres"]["value"] is None  # tags are not genres
    asyncio.run(run())


@pytest.mark.parametrize("status,code", [(403, "upstream_http_403"), (429, "upstream_http_429"), (302, "upstream_http_302")])
def test_upstream_failure_partial_search_no_secret_urls(status, code):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, headers={"Location": "https://evil.invalid/?apikey=secret"}))) as client:
            resolver = MetadataResolver({"tmdb": TMDBProvider("sensitive-dev-placeholder", client=client)})
            result = await resolver.search("test", "movie", ["tmdb"])
            assert result["items"] == []
            assert result["warnings"][0]["code"] == code
            assert "sensitive" not in str(result) and "evil.invalid" not in str(result)
    asyncio.run(run())


def test_timeout_invalid_json_and_unconfigured_degrade():
    async def run():
        for transport, expected in [(httpx.MockTransport(lambda request: httpx.Response(200, text="not JSON")), "invalid_json"), (httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("secret url", request=request))), "upstream_timeout")]:
            async with httpx.AsyncClient(transport=transport) as client:
                resolver = MetadataResolver({"tmdb": TMDBProvider("dev", client=client)})
                result = await resolver.resolve({"provider": "tmdb", "id": "1", "media_type": "movie"})
                assert result["context"]["warnings"][0]["code"] == expected
        resolver = MetadataResolver()
        result = await resolver.search("test", "movie", ["tmdb", "ptgen"])
        assert len(result["warnings"]) == 2
        assert (await resolver.capabilities("tmdb"))["state"] == "unconfigured"
    asyncio.run(run())


@pytest.mark.parametrize("endpoint,search_path,param", [("https://ptgen.invalid/api/info", "/api/search", "q"), ("https://ptgen.invalid/", "/", "search")])
def test_ptgen_deployment_paths_optional_key_and_partial_fields(endpoint, search_path, param):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.params["apikey"] == "dev-key"
        if request.url.params.get(param):
            assert request.url.path == search_path
            return httpx.Response(200, json={"data": [{"title": "Film", "year": "1999", "link": "https://movie.douban.com/subject/1291843/"}]})
        return httpx.Response(200, json={"data": {"format": "A formatted description", "chinese_title": "名称", "year": "1999"}})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = PTGenProvider(endpoint, "dev-key", client=client)
            resolver = MetadataResolver({"ptgen": provider})
            page = await resolver.search("Film", "movie", ["ptgen"])
            assert page["items"][0]["id"] == "1291843"
            result = await resolver.resolve({"provider": "ptgen", "id": "1291843", "media_type": "movie"})
            assert result["context"]["metadata"]["title_zh"]["value"] == "名称"
            assert result["context"]["identity"] is None
            assert "format" not in result["context"]["metadata"]
    asyncio.run(run())


def test_nfkc_title_dedup_preserves_display():
    rows = [{"id": "1", "text": "ＡＢＣ  Title", "language": "en"}, {"id": "2", "text": "ABC Title", "language": "und"}]
    assert deduplicate_titles(rows) == [rows[0]]


def test_partial_locale_and_season_failure_preserve_available_metadata():
    def handler(request):
        if request.url.params.get("language") == "zh-TW" or "/season/" in request.url.path:
            return httpx.Response(503)
        return tmdb_transport(request)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            resolver = MetadataResolver({"tmdb": TMDBProvider("dev", client=client)})
            result = await resolver.resolve({"provider": "tmdb", "id": "100", "media_type": "series", "season": 2})
            assert result["context"]["metadata"]["year"]["value"] == "2023"
            assert result["context"]["metadata"]["title_zh_hant"]["value"] is None
            assert result["context"]["episode"].get("season_episode_count") is None
            assert {x["code"] for x in result["context"]["warnings"]} >= {"partial_locale", "season_unavailable"}
    asyncio.run(run())


def test_locale_fallback_is_not_a_translated_or_english_name():
    def handler(request):
        return httpx.Response(200, json={"id": 1, "name": "日本語原題", "original_name": "日本語原題", "original_language": "ja", "first_air_date": "2020-01-01"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await MetadataResolver({"tmdb": TMDBProvider("dev", client=client)}).resolve({"provider": "tmdb", "id": "1", "media_type": "anime"})
            assert not any(x["kind"] == "english" for x in result["context"]["titles"])
            assert result["context"]["metadata"]["title_zh"]["value"] is None
            assert result["context"]["metadata"]["title_zh_hant"]["value"] is None
    asyncio.run(run())
