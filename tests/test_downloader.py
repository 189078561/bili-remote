from __future__ import annotations

import asyncio
import hashlib

from bili_remote.bili import BiliTvClient
from bili_remote.downloader import YtDlpRunner
from bili_remote.validation import render_episode_template


def test_render_episode_template_escapes_percent_and_separators() -> None:
    rendered = render_episode_template(
        "Bilibili/%(playlist_title)s/S%(playlist_index)02d - "
        "%(title)s [%(id)s].%(ext)s",
        season_title="A/B",
        index=7,
        title="100% 特别/篇",
        episode_id="123",
    )
    assert rendered == "Bilibili/A／B/S07 - 100%% 特别／篇 [123].%(ext)s"
    assert "%(ext)s" in rendered


def test_parse_progress_supports_known_and_estimated_totals() -> None:
    known = YtDlpRunner._parse_progress(
        "__PROGRESS__ 12.5%|125|1000|1200|2048|8"
    )
    assert known == {
        "progress": 12.5,
        "downloaded_bytes": 125,
        "total_bytes": 1000,
        "speed": 2048.0,
        "eta": 8,
    }
    estimated = YtDlpRunner._parse_progress(
        "__PROGRESS__ 25.0%|250|NA|1000|NA|NA"
    )
    assert estimated == {
        "progress": 25.0,
        "downloaded_bytes": 250,
        "total_bytes": 1000,
        "speed": None,
        "eta": None,
    }
    fallback = YtDlpRunner._parse_progress(
        "__PROGRESS__ NA|500|NA|2000|1000|3"
    )
    assert fallback == {
        "progress": 25.0,
        "downloaded_bytes": 500,
        "total_bytes": 2000,
        "speed": 1000.0,
        "eta": 3,
    }


def test_normalize_formats_hides_video_below_1080p() -> None:
    formats = YtDlpRunner._normalize_formats(
        [
            {
                "format_id": "720",
                "vcodec": "avc1.64001F",
                "acodec": "none",
                "height": 720,
            },
            {
                "format_id": "1080",
                "vcodec": "avc1.640028",
                "acodec": "none",
                "height": 1080,
            },
            {
                "format_id": "audio",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
            },
        ]
    )
    assert [item["format_id"] for item in formats] == ["1080", "audio"]


def test_batch_profiles_ignore_video_below_1080p() -> None:
    profiles = YtDlpRunner._aggregate_format_profiles(
        [
            [
                {
                    "format_id": "720",
                    "vcodec": "avc1.64001F",
                    "height": 720,
                    "fps": 30,
                    "tbr": 2000,
                },
                {
                    "format_id": "1080",
                    "vcodec": "avc1.640028",
                    "height": 1080,
                    "fps": 30,
                    "tbr": 4000,
                },
            ]
        ]
    )
    assert [item["height"] for item in profiles] == [1080]


def test_parse_aria2_progress_reports_parallel_download() -> None:
    update = YtDlpRunner._parse_aria2_progress(
        "[#abc123 1.5MiB/10.0MiB(15%) CN:8 DL:2.0MiB ETA:4s]"
    )
    assert update == {
        "progress": 15.0,
        "downloaded_bytes": 1572864,
        "total_bytes": 10485760,
        "speed": 2097152.0,
        "eta": 4,
    }


def test_aria2_defaults_are_balanced_for_bilibili(settings) -> None:
    runner = YtDlpRunner(settings, None)
    args = runner._aria2_args()
    assert "--max-connection-per-server=16" in args
    assert "--split=16" in args
    assert "--min-split-size=20M" in args
    assert "--piece-length=5M" in args
    assert "--summary-interval=1" in args


def test_aria2_connections_downgrade_after_cdn_rate_limits(settings) -> None:
    settings.aria2_connections = 16
    settings.aria2_resume_retries = 2
    runner = YtDlpRunner(settings, None)

    assert runner._aria2_connection_plan() == (16, 8, 4)
    assert "--max-connection-per-server=8" in runner._aria2_args(8)
    assert "--split=8" in runner._aria2_args(8)


def test_cdn_rate_limit_and_temporary_statuses_are_recoverable() -> None:
    messages = (
        "ERROR: unable to download video data: HTTP Error 567",
        "ERROR: HTTP Error 429: Too Many Requests",
        "ERROR: HTTP Error 503: Service Unavailable",
        "errorCode=502",
        "HTTP/1.1 504 Gateway Timeout",
    )
    assert all(YtDlpRunner._is_cdn_failure(message) for message in messages)
    assert not YtDlpRunner._is_cdn_failure("disk full: status 507")


def test_tv_formats_have_stable_ids() -> None:
    formats = YtDlpRunner._tv_formats(
        {
            "video": [
                {
                    "id": 125,
                    "base_url": "https://example.com/v.m4s",
                    "backup_url": ["https://backup.example.com/v.m4s"],
                    "codecs": "hev1.2.4.L153",
                    "width": 3840,
                    "height": 2160,
                    "frame_rate": "60/1",
                    "bandwidth": 18000000,
                }
            ],
            "audio": [
                {
                    "id": 30280,
                    "base_url": "https://example.com/a.m4s",
                    "codecs": "mp4a.40.2",
                    "bandwidth": 128000,
                }
            ],
            "support_formats": [{"quality": 125, "display_desc": "4K HDR"}],
        }
    )
    assert [item["format_id"] for item in formats] == ["tv125-hevc", "tva30280"]
    assert formats[0]["url"] == "https://example.com/v.m4s"
    assert formats[0]["_tv_candidates"] == [
        "https://example.com/v.m4s",
        "https://backup.example.com/v.m4s",
    ]
    assert "Referer" not in formats[0]["http_headers"]
    assert formats[0]["dynamic_range"] == "HDR10"
    assert formats[0]["http_headers"]["User-Agent"].startswith(
        "Mozilla/5.0 (Linux; Android 13)"
    )


def test_prepare_tv_formats_uses_working_backup_url(settings) -> None:
    settings.tv_cdn_probe_enabled = True
    runner = YtDlpRunner(settings, None)
    formats = runner._tv_formats(
        {
            "video": [
                {
                    "id": 112,
                    "base_url": "https://blocked.example.com/v.m4s",
                    "backup_url": ["https://working.example.com/v.m4s"],
                    "codecs": "avc1.640028",
                    "width": 1920,
                    "height": 1080,
                    "bandwidth": 1000000,
                }
            ],
            "audio": [
                {
                    "id": 30280,
                    "base_url": "https://blocked.example.com/a.m4s",
                    "backup_url": ["https://working.example.com/a.m4s"],
                    "codecs": "mp4a.40.2",
                    "bandwidth": 128000,
                }
            ],
        }
    )

    async def probe(_client, url, _headers):
        return "working.example.com" in url

    runner._probe_tv_url = probe
    prepared = asyncio.run(runner._prepare_tv_formats(formats))
    assert [item["url"] for item in prepared] == [
        "https://working.example.com/v.m4s",
        "https://working.example.com/a.m4s",
    ]
    assert all("_tv_candidates" in item for item in prepared)


def test_tv_formats_only_expose_returned_quality(settings) -> None:
    formats = YtDlpRunner._tv_formats(
        {
            "video": [
                {
                    "id": 112,
                    "base_url": "https://example.com/1080.m4s",
                    "codecs": "avc1.640028",
                    "width": 1920,
                    "height": 1080,
                    "bandwidth": 3000000,
                }
            ],
            "audio": [
                {
                    "id": 30280,
                    "base_url": "https://example.com/a.m4s",
                    "codecs": "mp4a.40.2",
                    "bandwidth": 128000,
                }
            ],
            "support_formats": [
                {"quality": 120, "display_desc": "4K"},
                {"quality": 116, "display_desc": "1080P60"},
                {"quality": 112, "display_desc": "1080P"},
            ],
        }
    )
    assert [item["format_id"] for item in formats] == [
        "tv112-avc",
        "tva30280",
    ]


def test_cdn_probe_prefers_fastest_and_keeps_original_fallback(settings) -> None:
    settings.tv_cdn_probe_enabled = True
    runner = YtDlpRunner(settings, None)
    formats = [
        {
            "format_id": "tv112-avc",
            "url": "https://base.example.com/v.m4s",
            "_tv_candidates": [
                "https://base.example.com/v.m4s",
                "https://fast.example.com/v.m4s",
            ],
            "vcodec": "avc1.640028",
            "acodec": "none",
            "height": 1080,
            "http_headers": {},
        }
    ]

    async def probe(_client, url, _headers):
        return 10.0 if "fast.example.com" in url else 2.0

    runner._probe_tv_url = probe
    prepared = asyncio.run(runner._prepare_tv_formats(formats))
    assert prepared[0]["url"] == "https://fast.example.com/v.m4s"
    assert prepared[0]["_tv_candidates"] == [
        "https://fast.example.com/v.m4s",
        "https://base.example.com/v.m4s",
    ]


def test_cdn_failure_rotates_to_original_url() -> None:
    info = {
        "formats": [
            {
                "format_id": "tv112-avc",
                "url": "https://fast.example.com/v.m4s",
                "_tv_candidates": [
                    "https://fast.example.com/v.m4s",
                    "https://base.example.com/v.m4s",
                ],
            }
        ]
    }
    assert YtDlpRunner._advance_cdn_info(info)
    assert info["formats"][0]["url"] == "https://base.example.com/v.m4s"


def test_web_download_info_maps_playurl_candidates(settings) -> None:
    settings.tv_cdn_probe_enabled = True
    settings.line_compare_enabled = False
    runner = YtDlpRunner(settings, None)

    class FakeAuth:
        async def video_view(self, **_kwargs):
            return {"aid": 1, "pages": [{"cid": 2}]}

        async def web_playinfo(self, **_kwargs):
            return {
                "video": [
                    {
                        "id": 126,
                        "baseUrl": "https://base.example.com/v.m4s",
                        "backupUrl": ["https://fast.example.com/v.m4s"],
                        "codecs": "hev1.2.4.L153",
                    }
                ],
                "audio": [
                    {
                        "id": 30280,
                        "baseUrl": "https://base.example.com/a.m4s",
                        "backupUrl": ["https://fast.example.com/a.m4s"],
                        "codecs": "mp4a.40.2",
                    }
                ],
            }

        def load_tv_token(self):
            return None

    runner.auth_service = FakeAuth()

    async def resolve(*_args, **_kwargs):
        return {
            "id": "BV1TEST",
            "formats": [
                {
                    "format_id": "30126",
                    "url": "https://base.example.com/v.m4s",
                    "vcodec": "hev1.2.4.L153",
                    "acodec": "none",
                    "height": 2160,
                    "quality": 126,
                },
                {
                    "format_id": "30280",
                    "url": "https://base.example.com/a.m4s",
                    "vcodec": "none",
                    "acodec": "mp4a.40.2",
                },
            ],
        }

    runner._resolve_payload = resolve

    async def probe(_client, url, _headers):
        return 20.0 if "fast.example.com" in url else 2.0

    runner._probe_tv_url = probe
    info = asyncio.run(
        runner._web_download_info(
            "https://www.bilibili.com/video/BV1TEST", None
        )
    )
    assert info is not None
    urls = {item["format_id"]: item["url"] for item in info["formats"]}
    assert urls == {
        "30126": "https://fast.example.com/v.m4s",
        "30280": "https://fast.example.com/a.m4s",
    }


def test_tv_download_info_never_mixes_web_streams(settings) -> None:
    settings.tv_cdn_probe_enabled = False
    settings.line_compare_enabled = True
    runner = YtDlpRunner(settings, None)

    class FakeAuth:
        def load_tv_token(self):
            return "tv-token"

        async def video_view(self, **_kwargs):
            return {
                "aid": 1,
                "title": "Test Video",
                "duration": 60,
                "pages": [{"cid": 2}],
            }

        async def tv_video_playinfo(self, **_kwargs):
            return {
                "video": [
                    {
                        "id": 112,
                        "base_url": "https://tv.example.com/v.m4s",
                        "codecs": "avc1.640032",
                        "width": 1920,
                        "height": 1080,
                        "bandwidth": 3000000,
                    }
                ],
                "audio": [
                    {
                        "id": 30280,
                        "base_url": "https://tv.example.com/a.m4s",
                        "codecs": "mp4a.40.2",
                        "bandwidth": 128000,
                    }
                ],
            }

        async def web_playinfo(self, **_kwargs):
            raise AssertionError("TV jobs must not request Web streams")

    runner.auth_service = FakeAuth()
    info = asyncio.run(
        runner._tv_download_info(
            "https://www.bilibili.com/video/BV1TEST", {}
        )
    )
    assert all(
        item["url"].startswith("https://tv.example.com/")
        for item in info["formats"]
    )


def test_tv_request_signature_is_sorted_md5() -> None:
    params = {"local_id": "0", "appkey": BiliTvClient.appkey}
    expected = hashlib.md5(
        ("appkey=" + BiliTvClient.appkey + "&local_id=0" + BiliTvClient.appsec).encode()
    ).hexdigest()
    assert BiliTvClient._sign(params) == expected
