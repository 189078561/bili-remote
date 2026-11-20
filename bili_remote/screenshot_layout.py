"""Saved collage design options and resolution-independent layout helpers."""

from __future__ import annotations

from collections.abc import Callable

from PIL import Image, ImageOps

COLLAGE_DESIGN_DEFAULTS = {
    "ratio": "original",
    "fit": "contain",
    "info_enabled": True,
    "info_font": "sans",
    "info_size": 15,
    "info_rows": 4,
    "info_percent": 9,
    "info_fields": ["filename", "size", "resolution", "aspect", "fps", "video", "audio", "duration"],
}
COLLAGE_DESIGN_SETTINGS = {key: f"collage_{key}" for key in COLLAGE_DESIGN_DEFAULTS}
RATIOS = {"wide": 16 / 9, "classic": 4 / 3, "square": 1.0}


MAX_CANVAS_PIXELS = 150_000_000
MAX_CANVAS_SIDE = 20_000


def collage_canvas_fits(width: int, height: int) -> bool:
    return 0 < width <= MAX_CANVAS_SIDE and 0 < height <= MAX_CANVAS_SIDE * 2 and width * height <= MAX_CANVAS_PIXELS


def resize_tile(image: Image.Image, width: int, ratio: str, fit: str) -> Image.Image:
    height = max(1, round(width / RATIOS[ratio])) if ratio in RATIOS else max(1, round(width * image.height / image.width))
    size = (width, height)
    if ratio not in RATIOS:
        return image.resize(size, Image.Resampling.LANCZOS)
    if fit == "cover":
        return ImageOps.fit(image, size, Image.Resampling.LANCZOS)
    return ImageOps.pad(image, size, Image.Resampling.LANCZOS, color="black")


def info_lines(fields: list[str], requested: int, width: int, measure: Callable[[str], float]) -> list[str]:
    # Keep each field together wherever possible. Wrap only fields wider than
    # the sheet, then partition the ordered text into non-empty rows.
    pieces = []
    for field in fields:
        part = ""
        for char in field:
            if part and measure(part + char) > width:
                pieces.append(part)
                part = ""
            part += char
        if part:
            pieces.append(part)
    count = len(pieces)
    if not count:
        return []
    groups = [["    ".join(pieces[i:j]) for j in range(i + 1, count + 1)] for i in range(count)]
    widths = [list(map(measure, row)) for row in groups]
    for rows in range(min(requested, count), count + 1):
        ideal = measure(groups[0][-1]) / rows
        scores = [{0: (0, [])}]
        for row in range(1, rows + 1):
            best = {}
            for start, (cost, lines) in scores[-1].items():
                for end in range(start + 1, count - (rows - row) + 1):
                    index = end - start - 1
                    length = widths[start][index]
                    if length > width:
                        break
                    score = cost + (length - ideal) ** 2
                    if end not in best or score < best[end][0]:
                        best[end] = (score, [*lines, groups[start][index]])
            scores.append(best)
        if count in scores[-1]:
            return scores[-1][count][1]
    return pieces
