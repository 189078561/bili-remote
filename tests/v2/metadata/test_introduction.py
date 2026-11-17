import asyncio

import httpx
import pytest

from bili_remote.v2.metadata.introduction import IntroductionOptions, IntroductionRequest, IntroductionService
from bili_remote.v2.metadata.providers import BangumiProvider, PTGenProvider, ProviderError, TMDBProvider
from bili_remote.v2.workbench.publishing import PublishingService


def test_ptgen_preserves_extended_fields_cast_and_aliases_without_placeholder_scores():
    cast = "\n".join(f"　　　　　演員 {i:02}" for i in range(2, 31))
    formatted = (
        "[img]https://poster.example/test.jpg[/img]\n\n"
        "◎译　　名　甲 / 乙 / 丙 / 丁 / 戊\n"
        "◎片　　名　剧集篇章\n◎年　　代　2026\n"
        "◎IMDb评分　8.1/10 from 390 users\n◎产　　地　暂无\n◎类　　别　\n"
        "◎豆瓣评分　/10 from 0 users\n"
        "◎制作公司　示例公司\n◎主　　演　演員 01\n"
        + cast + "\n\n◎简　　介\n\n　　当季剧情。"
    )
    calls = []

    def response(request):
        assert request.url.params["format"] == "bbcode"
        calls.append(request.url.params["url"])
        return httpx.Response(200, json={"success": True, "data": {
            "format": formatted, "language": ["汉语普通话"], "director": ["导演甲", "导演乙"],
            "aliases": ["甲", "增补别名"], "cast": ["演員 30", "增补演员"]
        }})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            service = IntroductionService({"ptgen": PTGenProvider("https://test.example/info", client=client)})
            return await service.build(IntroductionRequest(source="ptgen", reference="https://movie.douban.com/subject/12345/?from=search"))

    result = asyncio.run(run())
    assert calls == ["https://movie.douban.com/subject/12345/"]
    intro = result["text"]
    assert "甲 / 乙 / 丙 / 丁 / 戊 / 增补别名" in intro
    assert intro.count("演員 30") == 1 and "增补演员" in intro
    assert all(value in intro for value in ("◎制作公司　示例公司", "8.1/10 from 390 users", "汉语普通话", "导演乙", "当季剧情"))
    assert "◎豆瓣链接　https://movie.douban.com/subject/12345/" in intro
    assert "[img]" not in intro and "0 users" not in intro.replace("390 users", "")
    assert all(field not in intro for field in ("◎豆瓣评分", "◎产　　地", "◎类　　别"))


@pytest.mark.parametrize("with_format", [True, False])
def test_ptgen_universal_formats_douban_chapter_with_all_cast(with_format):
    aliases = ["凡人修仙传 第177-228集", "凡人修仙传 第五季", "A Mortal's Journey 5", "Fate/stay night"]
    cast = ["钱文青 Wenqing Qian", *(f"演员 {n}" for n in range(2, 21)), "刘三木 Sanmu Liu"]
    overview = "韩立返回天南境内途中，从慕兰法士手中救下了旧识。"
    row = {
        "trans_title": aliases, "this_title": ["凡人修仙传 年番4"], "chinese_title": "凡人修仙传 年番4",
        "foreign_title": "A Mortal's Journey", "year": 2026, "region": ["中国大陆"], "genre": ["动作", "动画", "奇幻"],
        "language": ["汉语普通话"], "playdate": ["2026-06-13(中国大陆)"], "episodes": 52, "duration": "20 分钟",
        "ratings": {"douban": {"formatted": "/10 from 0 users", "votes": 0}},
        "director": [{"name": "王裕仁 Yuren Wang"}, {"name": "林宇昂 Yuang Lin"}],
        "writer": [{"name": "王裕仁 Yuren Wang"}, {"name": "李圆 Yuan Li"}],
        "cast": [{"name": name} for name in cast], "introduction": overview,
    }
    if with_format:
        row["formats"] = {"bbcode": (
            f"◎译　　名　{'/'.join(aliases)}\n◎片　　名　凡人修仙传 年番4\n"
            "◎片　　长　20 分钟\n◎豆瓣评分　/10 from 0 users\n◎主　　演　"
            + "\n　　　　　".join(cast[:15]) + f"\n\n◎简　　介\n\n　　{overview}\n\n◎标　　签　动画"
        )}

    class Provider:
        async def get(self, query):
            return row

    service = IntroductionService({"ptgen": Provider()})
    result = asyncio.run(service.build(IntroductionRequest(source="ptgen", reference="37457082")))
    expected = [
        "◎译　　名　" + " / ".join(aliases), "◎片　　名　凡人修仙传 年番4", "◎年　　代　2026",
        "◎产　　地　中国大陆", "◎类　　别　动作 / 动画 / 奇幻", "◎语　　言　汉语普通话",
        "◎上映日期　2026-06-13(中国大陆)", "◎豆瓣链接　https://movie.douban.com/subject/37457082/",
        "◎集　　数　52", "◎片　　长　20分钟", "◎导　　演　王裕仁 Yuren Wang / 林宇昂 Yuang Lin",
        "◎编　　剧　王裕仁 Yuren Wang / 李圆 Yuan Li", "◎主　　演　" + cast[0],
        *("　　　　　" + name for name in cast[1:]), "", "◎简　　介", "", "　　" + overview,
    ]
    if with_format:
        expected.extend(["", "◎标　　签　动画"])
    assert result["text"].splitlines() == expected


def test_tmdb_season_uses_only_selected_season_fields_and_cover():
    paths = []

    def response(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/tv/8"):
            return httpx.Response(200, json={"name": "整部剧集", "overview": "2020 原剧情", "seasons": [
                {"season_number": 4, "name": "年番4／慕兰之战", "air_date": "2026-07-01"}
            ]})
        return httpx.Response(200, json={
            "id": 85, "season_number": 4, "name": "年番4／慕兰之战", "overview": "2026 篇章剧情。",
            "air_date": "2026-07-01", "poster_path": "/season4.jpg", "vote_average": 9.5,
            "translations": {"translations": [{"data": {"name": f"译名 {n}"}} for n in range(1, 13)]},
            "episodes": [{"runtime": 22}, {"runtime": 24}],
            "aggregate_credits": {
                "cast": [{"name": f"演员 {n}", "roles": [{"character": "Hero (voice)"}]} for n in range(1, 17)],
                "crew": [{"name": "当季导演", "jobs": [{"job": "Director"}]}, {"name": "当季编剧", "jobs": [{"job": "Screenplay"}]}],
            },
        })

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            service = IntroductionService({"tmdb": TMDBProvider("fixture", client=client)})
            seasons = await service.options(IntroductionOptions(source="tmdb_season", query="8"))
            return seasons, await service.build(IntroductionRequest(source="tmdb_season", reference="8", season=4))

    seasons, result = asyncio.run(run())
    assert seasons["items"][0]["season"] == 4
    assert paths == ["/3/tv/8", "/3/tv/8/season/4"]
    intro = result["text"]
    assert all(label in intro for label in ("2026 篇章剧情", "译名 12", "演员 16", "当季导演", "当季编剧", "22分钟 / 24分钟"))
    assert all(label not in intro for label in ("2020", "整部剧集", "豆瓣评分", "IMDb评分", "9.5", "◎产　　地"))
    assert result["poster"] == "https://image.tmdb.org/t/p/original/season4.jpg"
    combined = PublishingService().compose(
        {"revision": 1, "metadata": {"year": {"value": 2020}}}, "", "", intro,
        overview_is_introduction=True, introduction_poster=result["poster"]
    )["content"]
    assert combined.count("◎简　　介") == 1 and "2020" not in combined


def test_auto_series_does_not_copy_series_intro_and_invalid_links_are_rejected():
    context = {"schema_version": 1, "context_id": "test", "revision": 1,
               "identity": {"media_type": "anime", "tmdb_id": "8"}}
    service = IntroductionService({})
    assert not asyncio.run(service.build(IntroductionRequest(context=context, automatic=True)))["text"]
    with pytest.raises(ProviderError, match="有效的豆瓣"):
        asyncio.run(service.build(IntroductionRequest(source="ptgen", reference="https://movie.douban.com.example.org/subject/123")))


def test_bangumi_renders_full_voice_list_and_localized_writers():
    def response(request):
        if request.url.path.endswith("/persons"):
            return httpx.Response(200, json=[{"name": "编剧甲", "relation": "脚本"}, {"name": "导演甲", "relation": "导演"}])
        if request.url.path.endswith("/characters"):
            return httpx.Response(200, json=[{"name": f"角色 {n}", "actors": [{"name": f"演员 {n}"}]} for n in range(1, 21)])
        return httpx.Response(200, json={"id": 8, "name": "番組", "name_cn": "中文名", "summary": "剧情",
                       "date": "2026-07-01", "total_episodes": 17, "infobox": [{"key": "别名", "value": [{"v": "别名甲"}, {"v": "别名乙"}]}],
                       "rating": {"total": 150, "score": 8.4}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            service = IntroductionService({"bangumi": BangumiProvider(client=client)})
            return await service.build(IntroductionRequest(source="bangumi", reference="8"))

    result = asyncio.run(run())
    assert all(label in result["text"] for label in ("别名甲 / 别名乙", "演员 20（角色 20）", "编剧甲", "导演甲", "◎Bangumi评分　8.4"))
    assert "豆瓣评分" not in result["text"]


def test_intro_api_and_preview_preserve_chosen_block_without_naming_data(settings):
    from fastapi.testclient import TestClient
    from bili_remote.web import create_app

    app = create_app(settings)

    class Provider:
        async def get(self, query):
            assert query == {"url": "https://movie.douban.com/subject/18282/", "format": "bbcode"}
            return {"data": {"format": "◎片　　名　独立篇章\n◎年　　代　2026\n\n◎简　　介\n\n　　篇章剧情。"}}

    with TestClient(app) as client:
        endpoint = "/api/v2/publishing/introduction"
        assert client.post(endpoint, json={"source": "ptgen", "reference": "18282"}).status_code == 401
        login = client.post("/api/auth/login", json={"username": "admin", "password": "test-password"})
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        app.state.services.v2.resolver.providers["ptgen"] = Provider()
        response = client.post(endpoint, json={"source": "ptgen", "reference": "18282"})
        assert response.status_code == 200, response.text
        edited = response.json()["text"].replace("篇章剧情。", "<script>篇章</script> 用户补充。")
        context = {"schema_version": 1, "context_id": "fixture", "revision": 1,
                   "metadata": {"year": {"value": 2020, "source": "tmdb"}, "overview": {"value": "整部作品剧情", "source": "tmdb"}}}
        result = client.post("/api/v2/publishing/preview", json={
            "context": context, "overview": edited, "overview_is_introduction": True,
            "introduction_poster": None, "format": "html",
        })
        assert result.status_code == 200, result.text
        content = result.json()["content"]
        assert "2026" in content and "用户补充" in content and content.count("◎简　　介") == 1
        assert "2020" not in content and "整部作品剧情" not in content and "<script>" not in content
