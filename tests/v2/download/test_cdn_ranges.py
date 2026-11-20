from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET

import httpx
import pytest

from bili_remote.downloader import YtDlpRunner
from bili_remote.v2.download.aria2c_range import metalink_command, selected_track_size


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "span", "count", "expected"), [
    (206, "bytes 0-2047/9001", 2048, 9001),
    (206, "bytes 0-1499/1500", 1500, 1500),
    (206, "bytes 4-2051/9001", 2048, None),
    (206, "bytes 0-2047/*", 2048, None),
    (206, "bytes 0-2047/9001", 1750, None),
    (200, "", 2048, None),
])
async def test_probe_certifies_only_the_exact_range(status, span, count, expected):
    def serve(request):
        assert request.headers["range"] == "bytes=0-2047"
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(status, headers={"Content-Range": span}, content=b"v" * count)
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        result = await YtDlpRunner._probe_url_speed(
            client, "https://cdn.test/video", {}, sample_bytes=2048, timeout=2
        )
    assert result.speed > 0
    assert result.range_size == expected


def test_metalink_uses_only_the_verified_track_and_original_output(tmp_path):
    url = "https://cdn.test/v?id=1&track=8"
    info = tmp_path / "info.json"
    track = {"url": url, "acodec": "none", "_cdn_range_sizes": {url: 20000}}
    info.write_text(json.dumps({"formats": [track]}))
    opts = ["--header", "Referer: https://www.bilibili.com/", "--split=8", "--dir", "./dir/"]
    name = "S01 - 1 来世，你可以找我报仇！ [3065290].f30112.mp4.part"
    tail = ["--out", f"./{name}", "--", url]
    command, xml = metalink_command(opts + tail, info)
    assert command[:len(opts)] == opts
    assert command[-1] == "--metalink-file=-"
    file = ET.fromstring(xml).find("{*}file")
    assert file.get("name") == name
    assert file.find("{*}url").text == url and file.find("{*}size").text == "20000"
    assert metalink_command(opts + tail[:-1] + ["https://other.test/v"], info) is None
    for name in ("../escape.part", "./a&b.part", "./a%2Fb.part", "./a\\b.part", "./a\tb.part"):
        assert metalink_command(opts + ["--out", name, "--", url], info) is None
    for fields in ({"fragments": [{"url": url}]}, {"acodec": "aac"}):
        info.write_text(json.dumps({"formats": [{**track, **fields}]}))
        assert metalink_command(opts + tail, info) is None


@pytest.mark.asyncio
async def test_rank_prioritizes_verified_ranges_for_parallel_dash(settings):
    from bili_remote.downloader import CDNProbe

    settings.tv_cdn_probe_enabled = True
    runner = YtDlpRunner(settings, None)
    runner.aria2_executable = "aria2c"
    peers = {"plain": CDNProbe(20), "wrong-size": CDNProbe(50, 9999), "range": CDNProbe(10, 10000)}

    async def probe(_client, url, _headers):
        return peers[url]

    runner._probe_tv_url = probe
    item = {"url": "plain", "filesize": 10000, "acodec": "none", "_cdn_candidates": list(peers)}
    await runner._rank_cdn_candidates([item], candidate_key="_cdn_candidates", drop_unavailable=False)
    assert item["url"] == "range"
    assert item["_cdn_range_sizes"] == {"range": 10000}
    assert set(item["_cdn_candidates"]) == set(peers)


@pytest.mark.parametrize(("status", "span", "encoding", "body", "verified"), [
    (206, "bytes 0-0/6917295620", "identity", b"v", True),
    (206, "bytes 0-0/6917295619", "identity", b"v", False),
    (206, "bytes 1-1/6917295620", "identity", b"v", False),
    (206, "bytes 0-0/6917295620", "gzip", b"v", False),
    (206, "bytes 0-0/6917295620", "identity", b"", False),
    (200, "", "identity", b"vv", False),
])
def test_uncached_selected_track_validates_one_byte(monkeypatch, status, span, encoding, body, verified):
    url = "https://cdn.test/uhd"
    track = {"url": url, "filesize": 6917295620, "acodec": "none",
             "http_headers": {"Referer": "https://www.bilibili.com/", "Cookie": "private"}}

    def respond(request, timeout):
        assert request.full_url == url and request.get_method() == "GET"
        assert request.get_header("Range") == "bytes=0-0"
        assert request.get_header("Cookie") is None and request.get_header("Referer")
        response = io.BytesIO(body)
        response.status = status
        response.headers = {"Content-Range": span, "Content-Encoding": encoding}
        return response

    monkeypatch.setattr("bili_remote.v2.download.aria2c_range.urlopen", respond)
    assert selected_track_size(track, url) == (6917295620 if verified else None)


@pytest.mark.asyncio
async def test_confirmed_transfer_races_real_ranges_and_closes_losing_requests(monkeypatch, settings):
    import asyncio
    from collections import Counter

    settings.tv_cdn_probe_enabled = True
    runner = YtDlpRunner(settings, None)
    runner.aria2_executable = "aria2c"
    slow, fast, wrong = (f"https://{host}.test/video.m4s" for host in ("slow", "fast", "wrong"))
    size = 24 * 1024**2
    reads = Counter()
    closed = Counter()

    async def serve(request):
        host = request.url.host
        reads[host] += 1
        assert request.headers["accept-encoding"] == "identity"
        assert request.headers["referer"] == "https://www.bilibili.com/"
        start, end = map(int, request.headers["range"][6:].split("-"))
        if host == "slow.test":
            try:
                await asyncio.Event().wait()
            finally:
                closed[host] += 1
        if host == "wrong.test":
            return httpx.Response(200, content=b"wrong representation")
        assert start in (0, 4 * 1024**2)
        assert end - start + 1 == settings.cdn_probe_sample_bytes // 2
        return httpx.Response(206, headers={"Content-Range": f"bytes {start}-{end}/{size}"},
                              content=b"x" * (end - start + 1))

    client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_: client)
    track = {"format_id": "30112", "url": slow, "acodec": "none", "filesize": size,
             "_cdn_candidates": [slow, wrong, fast], "http_headers": {"Referer": "https://www.bilibili.com/"}}
    await runner._rank_cdn_candidates([track], candidate_key="_cdn_candidates",
                                      drop_unavailable=False, confirmed=True)
    assert track["url"] == fast and track["_cdn_range_sizes"] == {fast: size}
    assert track["_cdn_candidates"] == [fast, slow, wrong]
    assert reads == {"fast.test": 2, "wrong.test": 2, "slow.test": 2}
    assert closed == {"slow.test": 2}


@pytest.mark.asyncio
async def test_mirror_race_rejects_unrelated_tracks_bad_lengths_and_weak_range_responses():
    from bili_remote.v2.download.cdn import race_mirrors

    urls = ["https://a.test/v", "https://a.test/v?dup", "https://b.test/v", "https://c.test/other"]
    size = 16 * 1024**2
    seen = []

    def serve(request):
        seen.append(str(request.url))
        start, end = map(int, request.headers["range"][6:].split("-"))
        # One host sends a short body. The other returns a different total.
        total = size if request.url.host == "a.test" else size + 10
        length = end - start + (0 if total == size else 1)
        return httpx.Response(206, headers={"Content-Range": f"bytes {start}-{end}/{total}"},
                              content=b"x" * length)

    args = dict(expected_size=size, sample_bytes=256, timeout=.5, connections=8)
    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        assert await race_mirrors(client, urls, {}, **args) is None
        assert seen.count(urls[0]) == seen.count(urls[2]) == 2 and len(seen) == 4
        seen.clear()
        for overrides in ({"expected_size": 2048}, {"connections": 2}):
            assert await race_mirrors(client, urls, {}, **(args | overrides)) is None
        assert not seen


@pytest.mark.asyncio
async def test_range_race_deadline_releases_connections_before_fallback(settings, monkeypatch):
    import asyncio
    from bili_remote.downloader import CDNProbe

    settings.tv_cdn_probe_enabled = True
    settings.cdn_probe_timeout_seconds = .05
    runner = YtDlpRunner(settings, None)
    runner.aria2_executable = "aria2c"
    urls = ["https://a.test/v", "https://b.test/v"]
    active = set()

    async def serve(request):
        key = (request.url.host, request.headers["range"])
        active.add(key)
        try:
            await asyncio.Event().wait()
        finally:
            active.remove(key)

    async def previous_probe(client, url, headers):
        assert not active
        return CDNProbe(10, 8388608) if url == urls[1] else None

    client = httpx.AsyncClient(transport=httpx.MockTransport(serve))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)
    runner._probe_tv_url = previous_probe
    track = {"url": urls[0], "vcodec": "avc", "acodec": "none", "_tv_candidates": urls}
    result = await runner._prepare_tv_formats([track], confirmed=True)
    assert result == [track] and track["url"] == urls[1]
    assert "_cdn_sample" not in track
