from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from bili_remote.collage_style import (
    CollageFrame,
    CollageMediaInfo,
    find_cjk_font,
    format_size,
    format_timestamp,
    render_collage,
    style_metrics,
)


def make_frame(path: Path, color: tuple[int, int, int], size=(320, 180)) -> Path:
    Image.new("RGB", size, color).save(path, "PNG")
    return path


def test_timestamp_size_and_scaled_style() -> None:
    assert format_timestamp(0) == "00:00:00"
    assert format_timestamp(59.999) == "00:00:59"
    assert format_timestamp(3661.9) == "01:01:01"
    assert format_size(1024**2) == "1.00 MB"
    small = style_metrics(240)
    default = style_metrics(480)
    large = style_metrics(1200)
    assert small.timestamp_height < default.timestamp_height < large.timestamp_height
    assert small.gap <= default.gap <= large.gap


def test_render_collage_keeps_pt_slt_visual_features(tmp_path: Path) -> None:
    font = find_cjk_font()
    if font is None:
        pytest.skip("系统没有中文字体")
    frames = [
        CollageFrame(make_frame(tmp_path / "one.png", (210, 30, 30)), 0.0),
        CollageFrame(
            make_frame(tmp_path / "two.png", (30, 120, 210), (180, 320)),
            12.0,
        ),
    ]
    output = tmp_path / "collage.jpg"
    result = render_collage(
        frames,
        CollageMediaInfo(
            filename="一个非常长的中文测试视频文件名用于验证换行与省略处理_" + "x" * 80,
            size_bytes=123456789,
            width=1920,
            height=1080,
            aspect_ratio="16:9",
            fps=23.976,
            video_codec="H.265",
            audio_codec="AAC",
            duration_seconds=3661.2,
        ),
        columns=2,
        rows=1,
        thumbnail_width=480,
        output_path=output,
        font_path=font,
    )
    assert output.is_file() and output.stat().st_size > 0
    assert int(result["width"]) > 0
    assert result["gap"] == style_metrics(480).gap
    with Image.open(output) as image:
        rgb = image.convert("RGB")
        assert rgb.width > 900
        assert rgb.height > 300
        assert rgb.getpixel((0, 0))[0] >= 250
        assert rgb.getpixel((rgb.width - 1, 0))[0] >= 250
        assert rgb.getpixel((0, rgb.height - 1))[0] >= 250
        black_pixels = sum(
            1
            for y in range(rgb.height // 2, rgb.height)
            for x in range(0, rgb.width, 4)
            if max(rgb.getpixel((x, y))) < 25
        )
        assert black_pixels > 200
        colors = rgb.getcolors(maxcolors=rgb.width * rgb.height)
        assert colors is not None
        assert any(20 < max(color) < 245 for _, color in colors)

    customized = tmp_path / "collage-gap.jpg"
    result = render_collage(
        frames,
        CollageMediaInfo(
            filename="test.mkv",
            size_bytes=100,
            width=320,
            height=180,
            aspect_ratio="16:9",
            fps=10.0,
            video_codec="H.264",
            audio_codec="AAC",
            duration_seconds=20.0,
        ),
        columns=2,
        rows=1,
        thumbnail_width=480,
        output_path=customized,
        font_path=font,
        gap=11,
    )
    assert customized.is_file()
    assert result["gap"] == 11


def test_render_collage_rejects_too_many_frames(tmp_path: Path) -> None:
    font = find_cjk_font()
    if font is None:
        pytest.skip("系统没有中文字体")
    frame = make_frame(tmp_path / "one.png", (100, 100, 100))
    info = CollageMediaInfo(
        filename="test.mkv",
        size_bytes=1,
        width=320,
        height=180,
        aspect_ratio="16:9",
        fps=10.0,
        video_codec="H.264",
        audio_codec="AAC",
        duration_seconds=10.0,
    )
    with pytest.raises(ValueError, match="超过网格容量"):
        render_collage(
            [CollageFrame(frame, 1.0), CollageFrame(frame, 2.0)],
            info,
            columns=1,
            rows=1,
            thumbnail_width=240,
            output_path=tmp_path / "bad.jpg",
            font_path=font,
        )


def test_info_rows_preserve_all_fields_and_order():
    from bili_remote.screenshot_layout import info_lines

    fields = ["文件名：测试", "大小：1 GB", "分辨率：1920 × 1080", "画面比例：16:9",
              "FPS：24", "视频：H264", "音频：AAC", "时长：02:00:00"]
    for count in range(1, 9):
        lines = info_lines(fields, count, 240, len)
        assert len(lines) == count and all(lines)
        assert "    ".join(lines).split("    ") == fields
    assert info_lines(fields[:2], 8, 240, len) == fields[:2]


@pytest.mark.parametrize("enabled, expected_height", [(False, 284), (True, 316)])
def test_render_square_collage_with_optional_compact_header(tmp_path, enabled, expected_height):
    font = find_cjk_font()
    if font is None:
        pytest.skip("系统没有中文字体")
    frame = make_frame(tmp_path / "wide.png", (220, 30, 30))
    info = CollageMediaInfo("sample.mp4", 1024, 320, 180, "16:9", 24, "H264", "AAC", 12)
    target = tmp_path / "preview.png"
    result = render_collage(
        [CollageFrame(frame, 1)], info, columns=1, rows=1, thumbnail_width=240,
        gap=1, output_path=target, font_path=font,
        design={"ratio": "square", "fit": "contain", "info_enabled": enabled,
                "info_fields": ["size"], "info_percent": 10},
    )
    assert (result["width"], result["height"]) == (254, expected_height)
    with Image.open(target) as image:
        offset = expected_height - 284
        assert image.getpixel((120, offset + 6)) == (0, 0, 0)
        assert image.getpixel((120, offset + 122)) == (220, 30, 30)
