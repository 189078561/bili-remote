"""Race bounded, parallel byte ranges on the mirrors of a confirmed DASH track."""

import asyncio
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class MirrorSample:
    url: str
    size: int
    speed: float
    hosts: int


async def _read_range(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], start: int, length: int,
) -> int | None:
    request_headers = httpx.Headers(headers)
    request_headers.update({"Range": f"bytes={start}-{start + length - 1}", "Accept-Encoding": "identity"})
    async with client.stream("GET", url, headers=request_headers) as response:
        span = re.fullmatch(r"bytes (\d{1,19})-(\d{1,19})/(\d{1,19})",
                            response.headers.get("content-range", ""), re.I)
        if (response.status_code != 206 or not span
                or response.headers.get("content-encoding", "identity").lower() != "identity"):
            return None
        first, last, size = map(int, span.groups())
        if first != start or last != start + length - 1 or not last < size < 2**63:
            return None
        received = 0
        async for chunk in response.aiter_bytes():
            received += len(chunk)
            if received > length:
                return None
        return size if received == length else None


async def race_mirrors(
    client: httpx.AsyncClient, urls: list[str], headers: dict[str, str], *,
    expected_size: int | None, sample_bytes: int, timeout: float, connections: int,
) -> MirrorSample | None:
    # Use only URLs for the same media path. Legacy selectors may offer different
    # representations with the same quality and codec. Never race those together.
    original = urlsplit(urls[0])
    mirrors = {}
    for url in urls:
        parsed = urlsplit(url)
        if parsed.scheme in ("http", "https") and parsed.path == original.path and parsed.hostname:
            mirrors.setdefault(parsed.hostname, url)
    limit = min(3, connections // 2)
    if limit < 2 or len(mirrors) < 2:
        return None
    candidates = list(mirrors.values())[:limit]
    # Split the configured per-node sampling budget over two separate pieces.
    length = sample_bytes // 2
    offset = max(4 * 1024**2, 4 * length)
    if expected_size and expected_size < offset + length:
        return None

    async def sample(url: str) -> MirrorSample | None:
        started = time.monotonic()
        reads = [asyncio.create_task(_read_range(client, url, headers, start, length))
                 for start in (0, offset)]
        try:
            first, second = await asyncio.gather(*reads)
            if first and first == second and (not expected_size or first == expected_size):
                speed = 2 * length / max(time.monotonic() - started, 0.001)
                return MirrorSample(url, first, speed, len(candidates))
        except (httpx.HTTPError, OSError):
            pass
        finally:
            for read in reads:
                read.cancel()
            await asyncio.gather(*reads, return_exceptions=True)
        return None

    probes = [asyncio.create_task(sample(url)) for url in candidates]
    try:
        async with asyncio.timeout(timeout):
            for probe in asyncio.as_completed(probes):
                result = await probe
                if result:
                    return result
    except TimeoutError:
        pass
    finally:
        for probe in probes:
            probe.cancel()
        await asyncio.gather(*probes, return_exceptions=True)
    return None
