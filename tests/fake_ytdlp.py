from __future__ import annotations

import json
import sys
from pathlib import Path


def option_value(name: str) -> str | None:
    try:
        return sys.argv[sys.argv.index(name) + 1]
    except (ValueError, IndexError):
        return None


def render_template(template: str) -> str:
    rendered = (
        template.replace("%(season_title)s", "Test Season")
        .replace("%(playlist_title)s", "Test Season")
        .replace("%(playlist_index)02d", "01")
        .replace("%(episode_number)02d", "01")
        .replace("%(playlist_index)s", "1")
        .replace("%(episode_number)s", "1")
        .replace("%(uploader)s", "Tester")
        .replace("%(title)s", "Test Video")
        .replace("%(id)s", "BV1TEST")
        .replace("%(ext)s", "mp4")
    )
    return rendered.replace("%%", "%")


def main() -> int:
    url = sys.argv[-1]
    if option_value("--downloader") == "aria2c":
        print("__ARIA__using external downloader", file=sys.stderr, flush=True)
        downloader_args = option_value("--downloader-args") or ""
        if "ariadownshift" in url:
            if "--split=16" in downloader_args:
                print(
                    "ERROR: unable to download video data: HTTP Error 567",
                    file=sys.stderr,
                    flush=True,
                )
                return 1
            if "--split=8" in downloader_args:
                print(
                    "ERROR: HTTP Error 429: Too Many Requests",
                    file=sys.stderr,
                    flush=True,
                )
                return 1
            if "--split=4" not in downloader_args:
                print("ERROR: unexpected adaptive connection plan", file=sys.stderr)
                return 1
        if "ariaresume" in url:
            paths = option_value("--paths") or "home:."
            root = Path(paths.removeprefix("home:"))
            template = option_value("--output") or "%(title)s.%(ext)s"
            result = root / render_template(template)
            result.parent.mkdir(parents=True, exist_ok=True)
            partial = Path(str(result) + ".part")
            control = Path(str(partial) + ".aria2")
            marker = root / ".aria-resume-state"
            if not marker.exists():
                marker.write_text("started", encoding="utf-8")
                partial.write_bytes(b"sparse partial")
                control.write_text("control", encoding="utf-8")
                print(f"FILE: {partial}", flush=True)
                print("ERROR: aria2c exited with code 1", file=sys.stderr, flush=True)
                return 1
            marker.unlink()
            partial.unlink(missing_ok=True)
            control.unlink(missing_ok=True)
        if "ariapartial" in url:
            paths = option_value("--paths") or "home:."
            root = Path(paths.removeprefix("home:"))
            template = option_value("--output") or "%(title)s.%(ext)s"
            result = root / render_template(template)
            result.parent.mkdir(parents=True, exist_ok=True)
            partial = Path(str(result) + ".part")
            partial.write_bytes(b"sparse partial")
            Path(str(partial) + ".aria2").write_text("control", encoding="utf-8")
            print(f"FILE: {partial}", flush=True)
            print("ERROR: aria2c exited with code 1", file=sys.stderr, flush=True)
            return 1
        if "ariafail" in url:
            print("ERROR: aria2c exited with code 1", file=sys.stderr, flush=True)
            return 1
    if "FAILHDR" in (option_value("--format") or ""):
        print("ERROR: Did not get any data blocks", file=sys.stderr)
        return 1
    if "authfail" in url:
        print("ERROR: This video requires login", file=sys.stderr)
        return 1
    info_path = option_value("--load-info-json")
    info: dict = {}
    if info_path:
        try:
            info = json.loads(Path(info_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: cannot read info json: {exc}", file=sys.stderr)
            return 1
        if not info.get("formats"):
            print("ERROR: info json has no formats", file=sys.stderr)
            return 1
        if info.get("title") == "__TV403__":
            print(
                "ERROR: unable to download video data: HTTP Error 403: Forbidden",
                file=sys.stderr,
            )
            return 1
        print("__INFO__Loaded TV info JSON", flush=True)
    if "-J" in sys.argv:
        if "--flat-playlist" in sys.argv:
            payload = {
                "_type": "playlist",
                "id": "123",
                "title": "Test Playlist",
                "playlist_count": 2,
                "entries": [{"id": "BV1TEST"}, {"id": "BV2TEST"}],
            }
        else:
            payload = {
                "id": "BV1TEST",
                "title": "Test Video",
                "uploader": "Tester",
                "duration": 95,
                "webpage_url": url,
                "formats": [
                    {
                        "format_id": "30280",
                        "ext": "m4a",
                        "resolution": "audio only",
                        "vcodec": "none",
                        "acodec": "mp4a.40.2",
                        "tbr": 192,
                    },
                    {
                        "format_id": "126",
                        "ext": "mp4",
                        "width": 3840,
                        "height": 2160,
                        "fps": 60,
                        "vcodec": "hev1.2.4.L153",
                        "acodec": "none",
                        "dynamic_range": "DV",
                        "format_note": "HDR",
                        "tbr": 18000,
                    },
                ],
            }
        print(json.dumps(payload))
        return 0

    paths = option_value("--paths") or "home:."
    root = Path(paths.removeprefix("home:"))
    template = option_value("--output") or "%(title)s.%(ext)s"
    result = root / render_template(template)
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text("fake media", encoding="utf-8")
    title = info.get("title") or "Test Video"
    uploader = info.get("uploader") or "Tester"
    print(f"__TITLE__{title}", flush=True)
    print(f"__UPLOADER__{uploader}", flush=True)
    suppress_progress = "--print" in sys.argv and "--no-quiet" not in sys.argv
    if not suppress_progress:
        print("__PROGRESS__ 50.0%|500|1000|1200|125000|4", flush=True)
    print(f"__RESULT__{result}", flush=True)
    if not suppress_progress:
        print("__PROGRESS__100.0%|1000|1000|1200|0|0", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
