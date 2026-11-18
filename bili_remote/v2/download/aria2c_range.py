#!/usr/bin/env python3
"""Give aria2 the verified length of a single DASH track selected by yt-dlp."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


def metalink_command(args: list[str], info_path: Path) -> tuple[list[str], bytes] | None:
    # yt-dlp passes one direct URL and an explicit temporary output name.
    if args[-2:-1] != ["--"] or "--out" not in args[:-3] or "--dir" not in args[:-3]:
        return None
    url = args[-1]
    if not url.startswith(("https://", "http://")):
        return None
    index = args.index("--out")
    name = args[index + 1].removeprefix("./")
    # aria2's Metalink parser escapes some punctuation in filenames. Let its
    # normal --out path handle these names verbatim, as well as fragment jobs.
    if name.startswith((".", " ")) or not name.endswith(".part") or not re.fullmatch(r"[\w .+()\[\]-]+", name):
        return None
    try:
        formats = json.loads(info_path.read_bytes())["formats"]
    except (OSError, ValueError, KeyError):
        return None
    for item in formats:
        if item.get("url") != url or item.get("fragments"):
            continue
        if item.get("vcodec") != "none" and item.get("acodec") != "none":
            continue
        size = (item.get("_cdn_range_sizes") or {}).get(url)
        if type(size) is not int or not 0 < size < 2**63:
            continue
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
