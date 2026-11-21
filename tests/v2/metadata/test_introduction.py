import asyncio

import httpx
import pytest

from bili_remote.v2.metadata.introduction import IntroductionOptions, IntroductionRequest, IntroductionService
from bili_remote.v2.metadata.imdb_ratings import IMDbRatings, official_ratings
from bili_remote.v2.metadata.providers import BangumiProvider, PTGenProvider, ProviderError, TMDBProvider
from bili_remote.v2.workbench.publishing import PublishingService


@pytest.fixture(autouse=True)
def offline_ratings(monkeypatch):
    monkeypatch.setattr(official_ratings, "lookup", lambda title_id: None)


@pytest.mark.parametrize("source,expected_rating", [
    ({"imdb_id": "tt38775487"}, ""),
    ({"imdb": {"id": "tt38775487"}, "ratings": {"imdb": {"average": 7.1, "votes": 335}}}, "7.1/10 (335 人评价)"),
    ({"imdb_link": "https://m.imdb.com/title/tt38775487/?ref_=test", "imdb_score": 7.1}, "7.1/10"),
    ({"format": "◎IMDb链接　[url=https://www.imdb.com/title/tt38775487/?ref_=test]IMDb[/url]"}, ""),
])
def test_douban_normalizes_same_subject_imdb(source, expected_rating):
    class PTGen:
        async def get(self, params):
            assert params["url"].endswith("/subject/37508847/")
            return {"site": "douban", "id": "37508847", "title": "合成测试", **source}
    result = asyncio.run(IntroductionService({"ptgen": PTGen()}).build(
        IntroductionRequest(source="ptgen", reference="37508847", matched_movie_id="12")))
    assert result["text"].count("◎IMDb链接　https://www.imdb.com/title/tt38775487/") == 1
    assert [line for line in result["text"].splitlines() if line.startswith("◎IMDb评分")] == (
        ["◎IMDb评分　" + expected_rating] if expected_rating else [])


@pytest.mark.parametrize("reverse_douban,valid", [("777", True), ("888", False)])
def test_imdb_supplement_requires_exact_reverse_subject(reverse_douban, valid):
    class PTGen:
        async def get(self, params):
            if "imdb.com" in params["url"]:
                return {"site": "imdb", "sid": "tt1234567", "douban_id": reverse_douban}
            return {"site": "douban", "id": "777", "title": "同名作品", "summary": "所选条目剧情"}
    class TMDB:
        async def get(self, path, **kwargs):
            assert path == "movie/12"
            return {"id": 12, "external_ids": {"imdb_id": "tt1234567"}}
    result = asyncio.run(IntroductionService({"ptgen": PTGen(), "tmdb": TMDB()}).build(
        IntroductionRequest(source="ptgen", reference="777", matched_movie_id="12")))
    assert ("◎IMDb链接　https://www.imdb.com/title/tt1234567/" in result["text"]) is valid
    assert "所选条目剧情" in result["text"]


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


def test_other_douban_series_uses_all_structured_credits_and_discloses_missing_writer():
    actors = [{"name": f"演员 {i:03}"} for i in range(1, 121)]

    def response(request):
        assert request.url.params["url"] == "https://movie.douban.com/subject/25754848/"
        return httpx.Response(200, json={
            "site": "douban", "id": "25754848", "this_title": ["琅琊榜"], "trans_title": ["Nirvana in Fire"],
            "episodes": 54, "language": ["汉语普通话"], "director": [{"name": "导演甲"}], "cast": actors,
            "format": "◎主　　演　" + "\n　　　　　".join(actor["name"] for actor in actors[:15]),
        })

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            service = IntroductionService({"ptgen": PTGenProvider("https://test.example/info", client=client)})
            return await service.build(IntroductionRequest(source="ptgen", reference="25754848"))

    result = asyncio.run(run())
    intro = result["text"]
    assert "◎集　　数　54" in intro and "◎译　　名　Nirvana in Fire" in intro
    assert intro.count("演员 ") == 120 and "演员 120" in intro
    assert "◎编　　剧" not in intro and "凡人修仙传" not in intro
    assert result["warnings"] == ["PT-Gen 本次未提供：编剧。"]


@pytest.mark.parametrize("status", [200, 401])
def test_exact_douban_subject_completes_legacy_mobile_response(status):
    mobile = {
        "site": "douban", "sid": "37457082", "success": True,
        "format": (
            "◎片　　名　凡人修仙传 年番4\n◎年　　代　2026\n◎产　　地　中国大陆\n"
            "◎类　　别　动作 / 动画 / 奇幻\n◎上映日期　2026-06-13(中国大陆)\n"
            "◎豆瓣评分　/10 from 0 users\n◎片　　长　20分钟\n\n"
            "◎简　　介\n\n　　韩立返回天南境内途中，从慕兰法士手中救下了旧识。"
        ),
    }

    def response(request):
        assert request.url.params["url"] == "https://movie.douban.com/subject/37457082/"
        return httpx.Response(status, json=mobile if status == 200 else {})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            service = IntroductionService({"ptgen": PTGenProvider("https://test.example/info", client=client)})
            return await service.build(IntroductionRequest(source="ptgen", reference="37457082"))

    result = asyncio.run(run())
    intro = result["text"]
    alias_line = intro.splitlines()[0]
    assert alias_line.startswith("◎译　　名　凡人修仙传 第177-228集")
    assert alias_line.count(" / ") == 6 and alias_line.endswith("A Mortal's Journey: Fighting in Mulan Grassland")
    assert "◎语　　言　汉语普通话" in intro and "◎集　　数　52" in intro
    assert "◎导　　演　王裕仁 Yuren Wang / 林宇昂 Yuang Lin" in intro
    assert "◎编　　剧　王裕仁 Yuren Wang / 李圆 Yuan Li" in intro
    cast = intro.split("◎主　　演　", 1)[1].split("\n\n", 1)[0].splitlines()
    assert len(cast) == 21 and cast[0] == "钱文青 Wenqing Qian" and cast[-1] == "　　　　　刘三木 Sanmu Liu"
    assert "豆瓣评分" not in intro
    if status == 200:
        assert "已用该条目的用户提供资料补充" in result["warnings"][0]
        assert intro.endswith("救下了旧识。")
    else:
        assert "用户提供的资料" in result["label"]
        assert intro.endswith("一战成名！")


@pytest.mark.parametrize("status, payload, expected", [
    (401, {}, "API Key"),
    (403, {}, "豆瓣访问状态"),
    (200, {"data": {"site": "douban", "id": "12345", "title": "错误条目"}}, "ID 不一致"),
])
def test_douban_failure_does_not_return_other_metadata(status, payload, expected):
    seen = []

    def response(request):
        seen.append(request.url.params["url"])
        return httpx.Response(status, json=payload)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            service = IntroductionService({"ptgen": PTGenProvider("https://test.example/info", client=client)})
            with pytest.raises(ProviderError, match=expected):
                await service.build(IntroductionRequest(source="ptgen", reference="12346"))

    asyncio.run(run())
    assert seen == ["https://movie.douban.com/subject/12346/"]


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


@pytest.mark.parametrize("kind,child_genres,linked", [
    ("series", ["动画"], True), ("series", ["剧情"], False), ("movie", ["动画"], False),
])
def test_confirmed_parent_only_contributes_its_link(kind, child_genres, linked):
    class PTGen:
        async def get(self, params):
            return {"site": "douban", "id": "234", "title": "云山 年番4", "year": "2026",
                    "genre": child_genres, "episodes": 52, "cast": ["演员甲"],
                    "format": "◎豆瓣评分　8.2/10 (120 人评价)\n◎简　　介\n\n　　篇章独有剧情"}
    def field(value):
        return {"source": "tmdb", "source_ref": "567", "value": value}
    ns = "movie" if kind == "movie" else "tv"
    ctx = {"context_id": "selection", "revision": 1, "identity": {"media_type": kind, "tmdb_id": "567", "confirmed": True},
           "metadata": {"title_zh": field("云山"), "year": field("2020"), "genres": field(["动画"]),
                        "cast": [{"name": field("演员甲"), "role": "actor"}],
                        "links": {"tmdb": f"https://www.themoviedb.org/{ns}/567", "imdb": "tt1234567"}}}
    class Ratings:
        def lookup(self, title_id):
            pytest.fail("Series votes must not be looked up for a chapter")
    result = asyncio.run(IntroductionService({"ptgen": PTGen()}, Ratings()).build(
        IntroductionRequest(source="ptgen", reference="234", context=ctx)))
    assert ("◎IMDb链接　https://www.imdb.com/title/tt1234567/" in result["text"]) == linked
    assert "◎IMDb评分" not in result["text"]
    assert "◎豆瓣评分　8.2/10 (120 人评价)" in result["text"] and "篇章独有剧情" in result["text"]


@pytest.mark.parametrize("imdb,douban,expected", [
    ({"average": 8.1, "votes": "2,030"}, "7.1/10", ["IMDb评分", "IMDb链接", "豆瓣评分", "豆瓣链接"]),
    ({"average": 8.1}, "/10 from 0 users", ["IMDb评分", "IMDb链接", "豆瓣链接"]),
    ({"average": 0, "votes": 0}, 7.1, ["IMDb链接", "豆瓣评分", "豆瓣链接"]),
    ({}, "", ["IMDb链接", "豆瓣链接"]),
])
def test_scores_are_independent_and_survive_editor_and_preview(imdb, douban, expected):
    class PTGen:
        async def get(self, params):
            return {"title": "样本", "ratings": {"imdb": imdb}, "imdb_id": "tt1234567",
                    "douban_rating": douban, "format": "◎自定字段　保留文本"}
    result = asyncio.run(IntroductionService({"ptgen": PTGen()}).build(
        IntroductionRequest(source="ptgen", reference="234")))
    content = result["text"]
    assert [line[1:].split("　")[0] for line in content.splitlines() if line.startswith(("◎IMDb", "◎豆瓣"))] == expected
    if imdb.get("votes"):
        assert "8.1/10 (2,030 人评价)" in content
    assert "◎自定字段　保留文本" in content
    assert PublishingService().compose({"revision": 1}, "", "", content, overview_is_introduction=True)["content"] == content


def test_official_rating_matches_exact_identifier_and_caches(monkeypatch):
    ratings = IMDbRatings()
    downloads = []
    def download():
        downloads.append(1)
        return b"tconst\taverageRating\tnumVotes\ntt12345678\t9.9\t25\ntt1234567\t7.9\t250\n"
    monkeypatch.setattr(ratings, "download", download)
    assert ratings.lookup("tt1234567") == {"average": "7.9", "votes": "250"}
    assert ratings.lookup("tt3456789") is None
    assert downloads == [1]


@pytest.mark.parametrize("source_link,lookup_id", [(None, "tt1234567"), ("tt9999999", "tt9999999")])
def test_film_rating_lookup_uses_final_subject_and_preserves_douban(source_link, lookup_id):
    class PTGen:
        async def get(self, params):
            return {"title": "样本", "year": 2026, "director": ["甲"], "imdb_id": source_link,
                    "douban_rating": {"ratingValue": 7.7, "ratingCount": 500}}
    class Ratings:
        def lookup(self, title_id):
            assert title_id == lookup_id
            return {"average": "7.2", "votes": 40}
    def field(value):
        return {"source": "tmdb", "source_ref": "12", "value": value}
    ctx = {"context_id": "film", "revision": 1, "identity": {"media_type": "movie", "tmdb_id": "12", "confirmed": True},
           "metadata": {"title_zh": field("样本"), "year": field(2026), "directors": [{"name": field("甲"), "role": "director"}],
                        "links": {"tmdb": "https://www.themoviedb.org/movie/12", "imdb": "tt1234567"}}}
    result = asyncio.run(IntroductionService({"ptgen": PTGen()}, Ratings()).build(
        IntroductionRequest(source="ptgen", reference="301", context=ctx)))
    assert f"◎IMDb链接　https://www.imdb.com/title/{lookup_id}/" in result["text"]
    assert "◎IMDb评分　7.2/10 (40 人评价)" in result["text"]
    assert "◎豆瓣评分　7.7/10 (500 人评价)" in result["text"]
