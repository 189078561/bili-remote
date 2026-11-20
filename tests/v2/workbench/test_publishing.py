from fastapi.testclient import TestClient

from bili_remote.web import create_app


def test_publish_preview_selected_chapter_reports_and_images(settings):
    app = create_app(settings)
    poster = "https://image.tmdb.org/t/p/original/work.jpg"
    thumb = "https://image.tmdb.org/t/p/w500/work.jpg"
    screenshot = "https://example.test/screenshot.jpg"
    intro = (
        "◎译　　名　篇章别名\n◎片　　名　独立篇章\n◎年　　代　2026\n"
        "◎产　　地　中国大陆\n◎类　　别　动画\n◎语　　言　汉语普通话\n"
        "◎上映日期　2026-06-13\n◎集　　数　52\n◎片　　长　20分钟\n"
        "◎导　　演　导演甲\n◎编　　剧　编剧乙\n◎主　　演　演员甲\n　　　　　演员乙\n"
        "\n◎简　　介\n\n　　当前篇章完整剧情。\n\n　　人工补充。"
    )
    payload = {
        "context": {"schema_version": 1, "context_id": "fixture", "revision": 1,
                    "metadata": {"year": {"value": 2020, "source": "tmdb"},
                                 "overview": {"value": "旧系列剧情", "source": "tmdb"},
                                 "poster_url": {"value": poster, "source": "tmdb"}}},
        "release_title": "独立主标题",
        "subtitle": "独立副标题",
        "overview": f"[center][img]{thumb}[/img][/center]\n\n{intro}\n[img]{poster}[/img]",
        "overview_is_introduction": True,
        "screenshots": [{"kind": "upload", "id": str(i), "url": screenshot} for i in range(2)],
        "links": [screenshot],
    }
    with TestClient(app) as client:
        login = client.post("/api/auth/login", json={"username": "admin", "password": "test-password"})
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf"]
        files = app.state.services.v2.naming("test").files
        filename = "media-report-test.txt"
        report = (
            "MediaInfo\nComplete name : /srv/private/episode[01].mkv\n"
            "Complete name : D:\\Private\\episode02.mkv\n"
            "Complete name : Local Name.mkv\nBDInfo\nDisc Label: DISC"
        )
        (app.state.services.files.root("release_info").path / filename).write_text(report, encoding="utf-8")
        payload["reports"] = [{"kind": "report", "id": "test", "resource": files.reference("release_info", filename)}]
        response = client.post("/api/v2/publishing/preview", json=payload)
        assert response.status_code == 200, response.text
        content = response.json()["content"]
        assert content == (
            f"[img]{poster}[/img]\n\n{intro}\n\n"
            "[code]\nMediaInfo\nComplete name : episode&#91;01&#93;.mkv\n"
            "Complete name : episode02.mkv\nComplete name : Local Name.mkv\nBDInfo\nDisc Label: DISC\n[/code]"
            f"\n\n[img]{screenshot}[/img]"
        )
        response = client.post("/api/v2/publishing/preview", json={
            **payload, "format": "html", "screenshots": [], "links": [],
        })
        assert response.status_code == 200, response.text
        content = response.json()["content"]
        assert content.count("<img") == 1 and "<pre>MediaInfo" in content
        assert "episode[01].mkv" in content and "/srv/private/" not in content
        assert "◎主　　演" in content and "人工补充。" in content
        assert screenshot not in content
