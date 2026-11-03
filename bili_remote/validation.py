from __future__ import annotations

import posixpath
import re
from urllib.parse import urljoin, urlsplit

import httpx


class ValidationError(ValueError):
    """Raised when user input violates an application boundary."""


_ALLOWED_EXACT_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"}
_FORMAT_PATTERN = re.compile(r"^[A-Za-z0-9_+*.,:/?<>=!()\[\]^-]+$")
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_EPISODE_FIELD = re.compile(
    r"%\((playlist_title|season_title|playlist_index|episode_number|title|id)\)"
    r"(?:([0-9]*d)|s)?"
)


def validate_bilibili_url(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 2048:
        raise ValidationError("请输入有效的 B 站链接")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ValidationError("链接格式无效") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("链接必须使用 http 或 https")
    if parsed.username or parsed.password:
        raise ValidationError("链接不能包含账号信息")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _ALLOWED_EXACT_HOSTS and not host.endswith(".bilibili.com"):
        raise ValidationError("只允许 B 站域名")
    if parsed.port not in {None, 80, 443}:
        raise ValidationError("链接端口不允许")
    return value


async def canonicalize_bilibili_url(value: str) -> str:
    """Validate a URL and safely follow b23.tv redirects while staying on Bilibili."""
    current = validate_bilibili_url(value)
    if (urlsplit(current).hostname or "").lower() != "b23.tv":
        return current
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(15.0, connect=8.0),
        headers={"User-Agent": "Mozilla/5.0"},
    ) as client:
        for _ in range(6):
            try:
                response = await client.get(current)
            except httpx.HTTPError as exc:
                raise ValidationError("无法解析 B 站短链接") from exc
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    break
                current = validate_bilibili_url(urljoin(current, location))
                if (urlsplit(current).hostname or "").lower() != "b23.tv":
                    return current
                continue
            return current
    raise ValidationError("B 站短链接重定向次数过多或目标无效")


def validate_relative_path(value: str, *, field_name: str) -> str:
    value = value.strip().replace("\\", "/")
    if not value:
        raise ValidationError(f"{field_name}不能为空")
    if len(value) > 240:
        raise ValidationError(f"{field_name}过长")
    if "\x00" in value:
        raise ValidationError(f"{field_name}包含非法字符")
    if value.startswith("/") or _DRIVE_PREFIX.match(value):
        raise ValidationError(f"{field_name}必须是相对路径")
    if posixpath.isabs(value):
        raise ValidationError(f"{field_name}必须是相对路径")
    parts = [part for part in value.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValidationError(f"{field_name}不能越过下载目录")
    return "/".join(parts)


def validate_output_template(value: str) -> str:
    template = validate_relative_path(value, field_name="命名模板")
    if "%(ext)s" not in template:
        raise ValidationError("命名模板必须包含 %(ext)s")
    if template.count("%(") > 20:
        raise ValidationError("命名模板包含过多字段")
    return template


def validate_output_directory(value: str) -> str:
    if not value.strip():
        return ""
    return validate_relative_path(value, field_name="保存子目录")


def validate_format_selector(value: str) -> str:
    selector = value.strip()
    if not selector:
        return "bv*+ba/b"
    if len(selector) > 1000 or not _FORMAT_PATTERN.fullmatch(selector):
        raise ValidationError("格式选择器无效")
    return selector


def compile_format_profile(
    *,
    height: int,
    fps_high: bool | None,
    vcodec: str,
    dynamic_range: str,
) -> str:
    """Compile a batch format preference into a resilient yt-dlp selector."""
    codec_filters = {
        "avc": ("[vcodec^=avc]",),
        "hevc": ("[vcodec^=hev]", "[vcodec^=hvc]"),
        "av1": ("[vcodec^=av01]",),
        "any": ("",),
    }.get(vcodec, ("",))
    fps_filter = (
        "[fps>=50]" if fps_high is True else "[fps<50]" if fps_high is False else ""
    )
    dr_filter = (
        f"[dynamic_range={dynamic_range}]"
        if dynamic_range and dynamic_range != "any"
        else ""
    )
    exact = f"[height={height}]"
    lower = f"[height<={height}]"
    alternatives = [
        f"bv*{exact}{fps_filter}{dr_filter}{codec_filter}+ba"
        for codec_filter in codec_filters
    ]
    alternatives.extend(
        f"bv*{exact}{codec_filter}+ba" for codec_filter in codec_filters
    )
    alternatives.append(f"bv*{exact}+ba")
    alternatives.extend(
        f"bv*{lower}{codec_filter}+ba" for codec_filter in codec_filters
    )
    alternatives.append(f"bv*{lower}+ba")
    alternatives.append("bv*+ba")

    unique: list[str] = []
    for selector in alternatives:
        if selector not in unique:
            unique.append(selector)
    return "/".join(unique) + "/b"


def validate_api_mode(value: str) -> str:
    mode = value.strip().lower() or "web"
    if mode not in {"web", "tv"}:
        raise ValidationError("解析线路必须是 web 或 tv")
    return mode


def _template_value(value: object) -> str:
    text = str(value).replace("\x00", "").replace("\r", " ").replace("\n", " ")
    return text.replace("%", "%%").replace("/", "／").replace("\\", "＼")


def render_episode_template(
    template: str,
    *,
    season_title: str,
    index: int,
    title: str,
    episode_id: str,
) -> str:
    """Render one episode into a literal yt-dlp output template.

    Values are escaped so titles containing percent signs or path separators
    cannot inject another template field or create extra directories.
    """

    validated = validate_output_template(template)
    values = {
        "playlist_title": _template_value(season_title),
        "season_title": _template_value(season_title),
        "playlist_index": str(index),
        "episode_number": str(index),
        "title": _template_value(title),
        "id": _template_value(episode_id),
    }

    def replace(match: re.Match[str]) -> str:
        key, number_format = match.group(1), match.group(2)
        if key in {"playlist_index", "episode_number"}:
            if number_format:
                width = int(number_format[:-1] or 0)
                return f"{int(values[key]):0{width}d}"
            return values[key]
        return values[key]

    rendered = _EPISODE_FIELD.sub(replace, validated)
    if "%(ext)s" not in rendered:
        raise ValidationError("命名模板必须包含 %(ext)s")
    return rendered
