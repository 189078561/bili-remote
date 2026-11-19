import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from bili_remote.bili import BiliTVAuthError, BiliTVRegionError
from bili_remote.downloader import DownloadFailure, TVSourceUnavailable, YtDlpRunner


URL = "https://www.bilibili.com/video/BV1TEST"
DASH = {
    "video": [
        {"id": 120, "height": 2160, "codecs": "avc1", "base_url": "https://tv.test/sdr.m4s"},
        {"id": 125, "height": 2160, "codecs": "hev1.2.4", "base_url": "https://tv.test/hdr.m4s"},
    ],
    "audio": [{"id": 30280, "codecs": "mp4a.40.2", "base_url": "https://tv.test/audio.m4s"}],
}
WEB_VIDEO = {
    "format_id": "100198", "height": 2160, "quality": 120, "vcodec": "avc1",
    "acodec": "none", "url": "https://web.test/100198.m4s", "ext": "mp4",
}
WEB_AUDIO = {
    "format_id": "30280", "vcodec": "none", "acodec": "mp4a.40.2",
    "url": "https://web.test/audio.m4s", "ext": "m4a",
}


@pytest.fixture
def runner(settings):
    settings.tv_cdn_probe_enabled = False
    auth = Mock()
    auth.load_tv_token.return_value = "test"
    auth.video_view = AsyncMock(return_value={"title": "Test", "aid": 1, "cid": 2})
    auth.tv_video_playinfo = AsyncMock(return_value=DASH)
    runner = YtDlpRunner(settings, auth)
    runner._create_cookie_file = Mock(return_value=None)
    runner._resolve_payload = AsyncMock(return_value={"formats": [WEB_VIDEO.copy(), WEB_AUDIO.copy()]})
    return runner


def test_tv_preview_marks_hdr_and_keeps_only_tv_tracks(runner):
    result = asyncio.run(runner.episode_formats(URL, "tv", detailed=True))
    tracks = {item["format_id"]: item for item in result["formats"]}
    assert result["source_mode"] == "tv"
    assert tracks["tv125-hevc"]["dynamic_range"] == "HDR10"
    assert tracks["tv125-hevc"]["locked_selector"] == "(tv125-hevc+tva30280)"
    assert tracks["tv120-avc"]["dynamic_range"] == "SDR"
    runner._resolve_payload.assert_not_called()


def test_confirmed_absence_falls_back_in_preview_but_not_in_download(runner, tmp_path):
    runner.auth_service.tv_video_playinfo.return_value = {"video": []}
    preview = asyncio.run(runner.episode_formats(URL, "tv", detailed=True))
    assert preview["source_mode"] == "web"
    assert "已切换到 Web" in preview["warning"]
    assert preview["formats"][0]["locked_selector"] == "(100198+30280)"

    job = {"id": 1, "api_mode": "tv", "source_url": URL, "format_selector": "(tv125-hevc+tva30280)"}
    with pytest.raises(TVSourceUnavailable):
        asyncio.run(runner.download(job, tmp_path, tmp_path / "download.log", AsyncMock()))
    runner._resolve_payload.assert_awaited_once()


@pytest.mark.parametrize("failure", [BiliTVAuthError("需要登录"), BiliTVRegionError("地区受限")])
def test_preview_does_not_switch_source_on_upstream_errors(runner, failure):
    runner.auth_service.tv_video_playinfo.side_effect = failure
    with pytest.raises(DownloadFailure, match=str(failure)):
        asyncio.run(runner.episode_formats(URL, "tv", detailed=True))
    runner._resolve_payload.assert_not_called()


def test_preview_does_not_switch_source_when_tv_cdn_fails(runner):
    runner._prepare_tv_formats = AsyncMock(return_value=[])
    with pytest.raises(DownloadFailure, match="CDN"):
        asyncio.run(runner.episode_formats(URL, "tv"))
    runner._resolve_payload.assert_not_called()


def test_batch_detects_source_for_the_requested_episode(runner):
    main = {"id": 41, "aid": 1, "cid": 20, "title": "1"}
    extra = {"id": 42, "aid": 2, "cid": 21, "title": "花絮"}
    runner.auth_service.season_view = AsyncMock(return_value={"season_id": 4, "episodes": [main]})
    runner.auth_service.season_sections = AsyncMock(return_value={
        "main_section": {"episodes": [main]}, "section": [{"title": "花絮", "episodes": [extra]}],
    })

    async def playinfo(*, aid, cid, ep_id):
        if ep_id == "41":
            assert (aid, cid) == (1, 20)
            return DASH
        assert (ep_id, aid, cid) == ("42", 2, 21)
        return {"video": []}

    runner.auth_service.tv_bangumi_playinfo = AsyncMock(side_effect=playinfo)
    result = asyncio.run(runner.batch_episode_formats(
        "https://www.bilibili.com/bangumi/play/ss4", ["41", "42"], "tv", detailed=True
    ))
    assert result["episode_sources"] == {"41": "tv", "42": "web"}
    assert result["episode_formats"]["41"][0]["locked_selector"] == "(tv120-avc+tva30280)"
    assert result["episode_formats"]["42"][0]["locked_selector"] == "(100198+30280)"
    assert result["checked"] == 2


def test_locked_web_download_uses_only_mirrors_of_same_track(runner):
    runner.settings.line_compare_enabled = True
    runner.auth_service.web_playinfo = AsyncMock(return_value={
        "video": [
            {"id": 120, "codecs": "avc1", "base_url": "https://cdn.test/100035.m4s"},
            {"id": 120, "codecs": "avc1", "base_url": "https://cdn.test/100198.m4s"},
        ],
    })
    result = asyncio.run(runner._web_download_info(URL, None, "(100198+30280)"))
    assert result["formats"][0]["_cdn_candidates"] == [
        "https://web.test/100198.m4s", "https://cdn.test/100198.m4s",
    ]
    runner.auth_service.tv_video_playinfo.assert_not_called()
