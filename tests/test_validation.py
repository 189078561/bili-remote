from __future__ import annotations

import pytest

from bili_remote.validation import (
    ValidationError,
    compile_format_profile,
    validate_api_mode,
    validate_bilibili_url,
    validate_format_selector,
    validate_output_directory,
    validate_output_template,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bilibili.com/video/BV1xx411c7mD",
        "https://b23.tv/abc123",
        "https://space.bilibili.com/123/favlist?fid=456",
        "https://bangumi.bilibili.com/anime/123",
    ],
)
def test_bilibili_urls_are_accepted(url: str) -> None:
    assert validate_bilibili_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://example.com/video",
        "https://bilibili.com.example.com/video",
        "https://user:pass@www.bilibili.com/video/BV1",
        "https://www.bilibili.com:8443/video/BV1",
    ],
)
def test_unsafe_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValidationError):
        validate_bilibili_url(url)


@pytest.mark.parametrize(
    "template",
    ["../escape/%(title)s.%(ext)s", "/tmp/%(title)s.%(ext)s", "C:/tmp/%(ext)s"],
)
def test_template_traversal_is_rejected(template: str) -> None:
    with pytest.raises(ValidationError):
        validate_output_template(template)


def test_template_requires_extension() -> None:
    with pytest.raises(ValidationError):
        validate_output_template("Bilibili/%(title)s")
    assert (
        validate_output_template("Bilibili/%(uploader)s/%(title)s.%(ext)s")
        == "Bilibili/%(uploader)s/%(title)s.%(ext)s"
    )


def test_output_directory_validation() -> None:
    assert validate_output_directory("Movies/Bilibili") == "Movies/Bilibili"
    assert validate_output_directory("") == ""
    with pytest.raises(ValidationError):
        validate_output_directory("../../etc")


def test_format_selector_validation() -> None:
    assert validate_format_selector("bv*+ba/b") == "bv*+ba/b"
    assert validate_format_selector("bv*[height<=1080][vcodec^=hev]+ba/b") == (
        "bv*[height<=1080][vcodec^=hev]+ba/b"
    )
    with pytest.raises(ValidationError):
        validate_format_selector("bv*+ba/b\n--exec rm")


def test_compile_format_profile_has_nearby_fallbacks() -> None:
    selector = compile_format_profile(
        height=2160,
        fps_high=True,
        vcodec="hevc",
        dynamic_range="HDR10",
    )
    assert "bv*[height=2160][fps>=50][dynamic_range=HDR10][vcodec^=hev]+ba" in selector
    assert "bv*[height=2160][vcodec^=hvc]+ba" in selector
    assert "bv*[height=2160]+ba" in selector
    assert "bv*[height<=2160][vcodec^=hev]+ba" in selector
    assert "bv*[height<=2160]+ba" in selector
    assert selector.endswith("/bv*+ba/b")
    validate_format_selector(selector)


def test_compile_format_profile_uses_av1_prefix() -> None:
    selector = compile_format_profile(
        height=1080,
        fps_high=False,
        vcodec="av1",
        dynamic_range="SDR",
    )
    assert "bv*[height=1080][fps<50][dynamic_range=SDR][vcodec^=av01]+ba" in selector
    assert selector.endswith("/bv*+ba/b")


def test_api_mode_validation() -> None:
    assert validate_api_mode("web") == "web"
    assert validate_api_mode("TV") == "tv"
    with pytest.raises(ValidationError):
        validate_api_mode("proxy")
