#!/usr/bin/env python3
"""Give aria2 the verified length of a single DASH track selected by yt-dlp."""

from __future__ import annotations

import http.client
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def selected_track_size(item: dict, url: str) -> int | None:
    cached = (item.get("_cdn_range_sizes") or {}).get(url)
    if type(cached) is int and 0 < cached < 2**63:
        return cached
    # Ranking skips single-source and some unselected formats. Validate just
    # the track chosen by yt-dlp, without streaming an entire response on 200.
    headers = {k: v for k, v in (item.get("http_headers") or {}).items()
               if k.lower() in {"user-agent", "referer", "origin"}}
    headers.update({"Range": "bytes=0-0", "Accept-Encoding": "identity"})
    try:
        with urlopen(Request(url, headers=headers), timeout=4) as response:
            span = re.fullmatch(r"bytes 0-0/([1-9]\d{0,18})",
                                response.headers.get("Content-Range", ""), re.I)
            if (response.status == 206 and span
                    and response.headers.get("Content-Encoding", "identity").lower() == "identity"
                    and response.headers.get("Content-Length", "1") == "1"):
                size = int(span[1])
                if (size < 2**63 and (not item.get("filesize") or size == item["filesize"])
                        and len(response.read(2)) == 1):
                    return size
    except HTTPError as exc:
        exc.close()
    except (OSError, ValueError, http.client.HTTPException):
        pass
    return None


def metalink_command(args: list[str], info_path: Path) -> tuple[list[str], bytes] | None:
    # yt-dlp passes one direct URL and an explicit temporary output name.
    if args[-2:-1] != ["--"] or "--out" not in args[:-3] or "--dir" not in args[:-3]:
        return None
    url = args[-1]
    if not url.startswith(("https://", "http://")):
        return None
    index = args.index("--out")
    name = args[index + 1].removeprefix("./")
    try:
        formats = json.loads(info_path.read_bytes())["formats"]
    except (OSError, ValueError, KeyError):
        return None
    for item in formats:
        if item.get("url") != url or item.get("fragments"):
            continue
        if item.get("vcodec") != "none" and item.get("acodec") != "none":
            continue
        # A plain basename may contain printable CJK punctuation. Metalink
        # rewrites ampersands in names, so preserve --out for those names and
        # keep rejecting separators, encoded paths and control characters.
        if (name.startswith((".", " ")) or not name.endswith(".part")
                or not name.isprintable() or any(char in name for char in '/\\&%<>:"|?*')):
            print("[aria2c] 临时文件名无法用 Metalink 原样保存，使用常规 aria2 传输", flush=True)
            return None
        size = selected_track_size(item, url)
        if size is None:
            print("[aria2c] 无法验证当前轨道的 Range 和总长度，使用常规 aria2 传输", flush=True)
            return None
        print(f"[aria2c] 当前轨道已验证定长分片：{size} 字节", flush=True)
        # One URL per manifest. A retry on another CDN remains under the
        # runner's existing partial-file and signed-URL renewal policy.
        root = ET.Element("metalink", xmlns="urn:ietf:params:xml:ns:metalink")
        file = ET.SubElement(root, "file", name=name)
        ET.SubElement(file, "size").text = str(size)
        ET.SubElement(file, "url").text = url
        options = args[:index] + args[index + 2:-2]
        options.extend(["--follow-metalink=false", "--follow-torrent=false", "--metalink-file=-"])
        return options, ET.tostring(root, encoding="utf-8")
    return None


def main() -> None:
    exe = os.environ["BILI_RANGE_ARIA2"]
    args = sys.argv[1:]
    command = metalink_command(args, Path(os.environ["BILI_RANGE_INFO"]))
    if command is None:
        os.execv(exe, [exe, *args])
    options, manifest = command
    # No signed URL or manifest left on disk after cancellation. exec preserves
    # the process group used by the job's stop button.
    with tempfile.TemporaryFile() as manifest_file:
        manifest_file.write(manifest)
        manifest_file.seek(0)
        os.dup2(manifest_file.fileno(), 0)
        os.execv(exe, [exe, *options])


if __name__ == "__main__":
    main()
