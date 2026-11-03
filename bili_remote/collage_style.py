from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_CANDIDATES = (
    Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
    Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)
_REFERENCE_WIDTH = 480
_REFERENCE_TIMESTAMP_HEIGHT = 30
_REFERENCE_TIMESTAMP_FONT = 18
_REFERENCE_SHADOW = 3
_REFERENCE_GAP = 3
_INFO_REFERENCE_WIDTH = 2000
_INFO_REFERENCE_FONT = 14
_INFO_REFERENCE_PADDING = 15

_TEXT_TOKEN = re.compile(
    r"[\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef]|"
    r"[^\s\u2e80-\u9fff\uac00-\ud7af\uff00-\uffef]+|\s+"
)


@dataclass(frozen=True, slots=True)
class CollageMediaInfo:
    filename: str
    size_bytes: int
    width: int | None
    height: int | None
    aspect_ratio: str
    fps: float | None
    video_codec: str
    audio_codec: str
    duration_seconds: float | None


@dataclass(frozen=True, slots=True)
class CollageFrame:
    path: Path
    timestamp_seconds: float


@dataclass(frozen=True, slots=True)
class StyleMetrics:
    thumbnail_width: int
    timestamp_height: int
    timestamp_font_size: int
    shadow_size: int
    shadow_blur: int
    shadow_margin: int
    gap: int


def find_cjk_font() -> Path | None:
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def clamp(value: float, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(round(value))))


def format_timestamp(seconds: float) -> str:
    total = max(0, int(math.floor(float(seconds) + 1e-6)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "未知"
    return format_timestamp(seconds)


def format_size(size_bytes: int) -> str:
    if size_bytes <= 0:
        return "未知"
    size = float(size_bytes)
    if size >= 1024**3:
        return f"{size / 1024**3:.2f} GB"
    if size >= 1024**2:
        return f"{size / 1024**2:.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{int(size)} B"


def style_metrics(thumbnail_width: int) -> StyleMetrics:
    scale = thumbnail_width / _REFERENCE_WIDTH
    shadow = clamp(_REFERENCE_SHADOW * scale, 2, 10)
    return StyleMetrics(
        thumbnail_width=thumbnail_width,
        timestamp_height=clamp(_REFERENCE_TIMESTAMP_HEIGHT * scale, 24, 72),
        timestamp_font_size=clamp(_REFERENCE_TIMESTAMP_FONT * scale, 12, 40),
        shadow_size=shadow,
        shadow_blur=shadow,
        shadow_margin=shadow * 3,
        gap=clamp(_REFERENCE_GAP * max(scale, 0.8), 3, 8),
    )


def information_font_size(grid_width: int) -> int:
    return clamp(
        _INFO_REFERENCE_FONT * grid_width / _INFO_REFERENCE_WIDTH,
        8,
        24,
    )


def information_padding(grid_width: int) -> int:
    return clamp(
        _INFO_REFERENCE_PADDING * grid_width / _INFO_REFERENCE_WIDTH,
        5,
        25,
    )


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(path), size=size)
    except OSError as exc:
        raise RuntimeError(f"无法加载中文字体：{path}") from exc


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> float:
    return float(draw.textlength(text, font=font))


def _wrap_long_token(
    token: str,
    current: str,
    draw: ImageDraw.ImageDraw,
    font,
    max_width: int,
) -> tuple[list[str], str]:
    lines: list[str] = []
    rest = token
    while rest:
        if current and _text_width(draw, current + rest[0], font) > max_width:
            lines.append(current.rstrip())
            current = ""
            continue
        candidate = current
        consumed = 0
        for index, char in enumerate(rest):
            if candidate and _text_width(draw, candidate + char, font) > max_width:
                break
            candidate += char
            consumed = index + 1
        if consumed == 0:
            if current:
                lines.append(current.rstrip())
                current = ""
                continue
            lines.append(rest[0])
            rest = rest[1:]
            continue
        current = candidate
        rest = rest[consumed:]
        if rest or (current and _text_width(draw, current, font) >= max_width):
            lines.append(current.rstrip())
            current = ""
    return lines, current


def _wrap_tokens(
    text: str,
    draw: ImageDraw.ImageDraw,
    font,
    max_width: int,
) -> list[str]:
    tokens = _TEXT_TOKEN.findall(text) or [text]
    lines: list[str] = []
    current = ""
    for token in tokens:
        if token.isspace():
            if current:
                current += " "
            continue
        if _text_width(draw, token, font) > max_width:
            long_lines, current = _wrap_long_token(
                token, current, draw, font, max_width
            )
            lines.extend(long_lines)
            continue
        trial = current + token
        if current and _text_width(draw, trial, font) > max_width:
            lines.append(current.rstrip())
            current = token
        else:
            current = trial
    if current.strip():
        lines.append(current.rstrip())
    return lines or [""]


def _ellipsis_lines(
    text: str,
    draw: ImageDraw.ImageDraw,
    font,
    max_width: int,
    max_lines: int,
) -> list[str]:
    lines = _wrap_tokens(text, draw, font, max_width)
    if len(lines) <= max_lines:
        return lines
    result = lines[:max_lines]
    last = result[-1]
    while last and _text_width(draw, f"{last}…", font) > max_width:
        last = last[:-1]
    result[-1] = f"{last}…" if last else "…"
    return result


def _line_height(font) -> int:
    box = font.getbbox("国Ag")
    return max(1, box[3] - box[1])


def _compose_thumbnail(
    thumbnail: Image.Image,
    timestamp: str,
    metrics: StyleMetrics,
    font_path: Path,
) -> Image.Image:
    width, height = thumbnail.size
    bar_height = metrics.timestamp_height
    content = Image.new("RGB", (width, height + bar_height), "black")
    content.paste(thumbnail.convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(content)
    font = _font(font_path, metrics.timestamp_font_size)
    text_width = _text_width(draw, timestamp, font)
    text_height = _line_height(font)
    draw.text(
        (
            max(0.0, (width - text_width) / 2),
            height + max(0.0, (bar_height - text_height) / 2),
        ),
        timestamp,
        fill="white",
        font=font,
    )

    margin = metrics.shadow_margin
    canvas = Image.new(
        "RGBA",
        (width + margin * 2, height + bar_height + margin * 2),
        (255, 255, 255, 0),
    )
    shadow_mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(shadow_mask).rectangle(
        (
            margin + metrics.shadow_size,
            margin + metrics.shadow_size,
            margin + metrics.shadow_size + width - 1,
            margin + metrics.shadow_size + height + bar_height - 1,
        ),
        fill=128,
    )
    shadow_mask = shadow_mask.filter(ImageFilter.GaussianBlur(metrics.shadow_blur))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow.putalpha(shadow_mask)
    canvas.alpha_composite(shadow)
    canvas.paste(content, (margin, margin))
    return canvas


def _info_lines(
    info: CollageMediaInfo,
    draw: ImageDraw.ImageDraw,
    font,
    max_width: int,
) -> list[str]:
    filename_lines = _ellipsis_lines(
        f"文件名：{info.filename or '未知'}",
        draw,
        font,
        max_width,
        3,
    )
    resolution = (
        f"{info.width}x{info.height}" if info.width and info.height else "未知"
    )
    fps = f"{info.fps:.2f}" if info.fps is not None else "未知"
    lines = list(filename_lines)
    lines.extend(
        [
            f"大小：{format_size(info.size_bytes)}"
            f"（{max(0, info.size_bytes):,} bytes）",
            f"分辨率：{resolution}（{info.aspect_ratio or '未知'}）fps {fps}",
            f"视频编码：{info.video_codec or '未知'}，"
            f"音频编码：{info.audio_codec or '未知'}",
            f"时长：{format_duration(info.duration_seconds)}",
        ]
    )
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(_wrap_tokens(line, draw, font, max_width))
    return wrapped


def _compose_information(
    info: CollageMediaInfo,
    width: int,
    font_path: Path,
) -> Image.Image:
    # Scale the information with the complete sheet so it stays readable when
    # fitted to the result pane, including a single-frame sheet on a phone.
    font_size = clamp(width * 0.018, 12, 36)
    padding = clamp(width * 0.02, 12, 32)
    font = _font(font_path, font_size)
    title_font = _font(font_path, clamp(width * 0.028, 18, 56))
    probe = Image.new("RGB", (max(1, width), 8), "white")
    draw = ImageDraw.Draw(probe)
    available = max(1, width - padding * 2)
    title_lines = _ellipsis_lines(info.filename or "未知文件", draw, title_font, available, 3)
    resolution = f"{info.width} × {info.height}" if info.width and info.height else "未知"
    fps = f"{info.fps:.3f}" if info.fps is not None else "未知"
    lines = []
    for text in (
        f"大小：{format_size(info.size_bytes)}   分辨率：{resolution}   "
        f"画面比例：{info.aspect_ratio or '未知'}",
        f"时长：{format_duration(info.duration_seconds)}   视频：{info.video_codec or '未知'}   "
        f"音频：{info.audio_codec or '未知'}   帧率：{fps} FPS",
    ):
        lines.extend(_wrap_tokens(text, draw, font, available))
    title_height = _line_height(title_font) + max(6, font_size // 2)
    line_height = _line_height(font) + max(5, font_size // 3)
    height = padding * 2 + len(title_lines) * title_height + len(lines) * line_height
    image = Image.new("RGB", (width, height), "white")
    out = ImageDraw.Draw(image)
    y = padding
    for line in title_lines:
        out.text((padding, y), line, fill="#121826", font=title_font,
                 stroke_width=1 if title_font.size >= 24 else 0)
        y += title_height
    for line in lines:
        out.text((padding, y), line, fill="#425575", font=font)
        y += line_height
    return image


def _compose_script_thumbnail(image, timestamp, font_path):
    # ImageMagick -shadow 50x3+3+3 extends 3px left/top and 9px right/bottom.
    metrics = StyleMetrics(image.width, 30, 18, 3, 3, 9, 3)
    tile = _compose_thumbnail(image, timestamp, metrics, font_path)
    return tile.crop((6, 6, tile.width, tile.height))


def render_collage(
    frames: Iterable[CollageFrame],
    info: CollageMediaInfo,
    *,
    columns: int,
    rows: int,
    thumbnail_width: int,
    output_path: Path,
    font_path: Path | None = None,
    gap: int | None = None,
) -> dict[str, object]:
    if columns < 1 or rows < 1:
        raise ValueError("拼图网格无效")
    selected_font = font_path or find_cjk_font()
    if selected_font is None:
        raise RuntimeError("缺少中文字体，无法生成缩略图信息栏")
    frame_list = list(frames)
    if not frame_list:
        raise ValueError("没有可用于拼接的画面")
    if len(frame_list) > columns * rows:
        raise ValueError("缩略图数量超过网格容量")

    metrics = style_metrics(thumbnail_width)
    if gap is not None:
        if isinstance(gap, bool) or not isinstance(gap, int) or not 0 <= gap <= 40:
            raise ValueError("缩略图间距必须在 0 到 40 像素之间")
        metrics = StyleMetrics(
            thumbnail_width=metrics.thumbnail_width,
            timestamp_height=metrics.timestamp_height,
            timestamp_font_size=metrics.timestamp_font_size,
            shadow_size=metrics.shadow_size,
            shadow_blur=metrics.shadow_blur,
            shadow_margin=metrics.shadow_margin,
            gap=gap,
        )
    thumbnails: list[tuple[Image.Image, str]] = []
    for frame in frame_list:
        try:
            with Image.open(frame.path) as source:
                image = source.convert("RGB")
        except OSError as exc:
            raise RuntimeError(f"无法读取截图：{frame.path.name}") from exc
        target_height = max(1, int(round(thumbnail_width * image.height / image.width)))
        if image.width != thumbnail_width or image.height != target_height:
            image = image.resize(
                (thumbnail_width, target_height),
                Image.Resampling.LANCZOS,
            )
        thumbnails.append((image, format_timestamp(frame.timestamp_seconds)))

    decorated = [
        _compose_script_thumbnail(image, timestamp, selected_font)
        for image, timestamp in thumbnails
    ]
    cell_width = max(tile.width for tile in decorated)
    cell_height = max(tile.height for tile in decorated)
    cell_gap = metrics.gap
    grid_width = columns * (cell_width + cell_gap * 2)
    grid_height = rows * (cell_height + cell_gap * 2)
    grid = Image.new("RGB", (grid_width, grid_height), "white")
    for index, tile in enumerate(decorated):
        row, column = divmod(index, columns)
        x = cell_gap + column * (cell_width + cell_gap * 2) + (cell_width - tile.width) // 2
        y = cell_gap + row * (cell_height + cell_gap * 2) + (cell_height - tile.height) // 2
        grid.paste(tile, (x, y), tile)

    information = _compose_information(info, grid_width, selected_font)
    final = Image.new(
        "RGB",
        (grid_width, information.height + grid_height),
        "white",
    )
    final.paste(information, (0, 0))
    final.paste(grid, (0, information.height))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == ".png":
        final.save(output_path, "PNG", optimize=True)
    else:
        final.save(output_path, "JPEG", quality=95, subsampling=0, optimize=True)
    return {
        "width": final.width,
        "height": final.height,
        "columns": columns,
        "rows": rows,
        "thumbnail_width": thumbnail_width,
        "gap": metrics.gap,
        "font": str(selected_font),
        "timestamps": [format_timestamp(frame.timestamp_seconds) for frame in frame_list],
    }


def render_still(source: Path, output: Path, timestamp: float) -> None:
    """Same timestamp bar and shadow as pt-slt; no scaling of the source pixels."""
    font = find_cjk_font()
    if font is None:
        raise RuntimeError("缺少中文字体")
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    # The attached shell script uses a fixed 30px bar / 18pt text / 3px shadow.
    tile = _compose_script_thumbnail(image, format_timestamp(timestamp), font)
    canvas = Image.new("RGB", (tile.width + 6, tile.height + 6), "white")
    canvas.paste(tile, (3, 3), tile)
    canvas.save(output, "PNG" if output.suffix.lower() == ".png" else "JPEG",
                **({"quality": 95, "subsampling": 0} if output.suffix.lower() != ".png" else {}))


__all__ = [
    "CollageFrame",
    "CollageMediaInfo",
    "FONT_CANDIDATES",
    "StyleMetrics",
    "find_cjk_font",
    "format_duration",
    "format_size",
    "format_timestamp",
    "information_font_size",
    "information_padding",
    "render_collage",
    "style_metrics",
]
