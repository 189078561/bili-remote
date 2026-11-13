"""Stateless publication composition. No repository, draft or project dependency."""
from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from ..naming.errors import NamingError
from .markup import format_description


INTRO_LABELS = (
    ("译　　名", "title_zh"),
    ("片　　名", "original_title"),
    ("年　　代", "year"),
    ("产　　地", "region"),
    ("类　　别", "genres"),
    ("上映日期", "release_date"),
    ("豆瓣评分", "douban_rating"),
    ("豆瓣链接", "douban_url"),
    ("片　　长", "runtime"),
)


def safe_url(url):
    if not isinstance(url, str) or any(c in url for c in "\r\n\x00[]<>\""):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return (
        parsed.scheme in {"http", "https"}
        and bool(parsed.netloc)
        and not parsed.username
        and not parsed.password
    )


def field_value(item):
    if isinstance(item, dict):
        for key in ("value", "VALUE"):
            if key in item:
                return item.get(key), item
    return item, {}


def plain_text(value):
    if isinstance(value, (list, tuple)):
        return " / ".join(str(item) for item in value if item not in (None, ""))
    return "" if value is None else str(value)


def escape_text(value, output):
    if output == "html":
        return html.escape(value)
    if output == "bbcode":
        return value.replace("[", "&#91;").replace("]", "&#93;")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", html.escape(value))


def image_block(url, output, alt="媒体图片"):
    if output == "html":
        return f'<img src="{html.escape(url, quote=True)}" alt="{html.escape(alt, quote=True)}">'
    if output == "bbcode":
        return f"[img]{url}[/img]"
    return f"![{escape_text(alt, output)}]({url.replace('(', '%28').replace(')', '%29')})"


def report_block(text, output):
    if output == "html":
        return "<pre>" + html.escape(text) + "</pre>"
    if output == "bbcode":
        return "[code]" + text.replace("[", "&#91;").replace("]", "&#93;") + "[/code]"
    fence = "````"
    return f"{fence}text\n{text}\n{fence}"


def indent_overview(text):
    return "\n".join(("　　" + line) if line.strip() else "" for line in text.splitlines())


class PublishingService:
    def _tmdb_poster(self, context, warnings):
        metadata = context.get("metadata") or {}
        value, field = field_value(metadata.get("poster_url"))
        source = field.get("source")
        if value and source == "tmdb" and safe_url(value):
            return value
        if value:
            warnings.append(
                {"code": "non_tmdb_poster_ignored", "message": "已忽略非 TMDB 主封面"}
            )
        return None

    def _intro_values(self, context):
        metadata = context.get("metadata") or {}
        values = {}
        for _, key in INTRO_LABELS:
            item, _ = field_value(metadata.get(key))
            values[key] = item
            if key == "region" and isinstance(item, str):
                values[key] = [item]
        links = metadata.get("links") or {}
        if not values.get("douban_url") and safe_url(links.get("douban")):
            values["douban_url"] = links["douban"]
        runtime = values.get("runtime")
        if isinstance(runtime, (int, float)) and runtime > 0:
            values["runtime"] = f"{runtime:g} 分钟"
        return values

    def _introduction(self, context, overview, output, warnings):
        poster = self._tmdb_poster(context, warnings)
        values = self._intro_values(context)
        lines = []
        for label, key in INTRO_LABELS:
            text = plain_text(values.get(key)).strip()
            if not text:
                continue
            lines.append(f"◎{label}　{text}")
        overview = overview or ""
        if poster and output == "bbcode":
            image = f"[img]{poster}[/img]"
            if overview.lstrip().startswith(image):
                overview = overview.lstrip()[len(image):].lstrip("\r\n ")
        blocks = []
        if poster:
            blocks.append(image_block(poster, output, "主封面"))
        if lines:
            blocks.append("\n".join(escape_text(line, output) for line in lines))
        if overview.strip():
            body = format_description(overview, output, safe_url)
            if output == "html":
                blocks.append(
                    "<h3>◎简　　介</h3><p>"
                    + indent_overview(body).replace("\n", "<br>\n")
                    + "</p>"
                )
            elif output == "bbcode":
                blocks.append("◎简　　介\n\n" + indent_overview(body))
            else:
                blocks.append("### ◎简　　介\n\n" + indent_overview(body))
        return "\n\n".join(blocks), poster

    def compose(
        self,
        context,
        release_title,
        subtitle,
        overview,
        artifacts=None,
        format="bbcode",
        *,
        links=None,
        report_texts=None,
    ):
        if format not in {"bbcode", "html", "markdown"}:
            raise NamingError("invalid_format", "发布格式无效")
        release_title = release_title or ""
        subtitle = subtitle or ""
        overview = overview or ""
        errors, warnings, notices = [], [], []
        if not release_title.strip():
            notices.append(
                {"code": "missing_release_title", "field": "release_title", "message": "主标题尚未填写，不会写入发布正文"}
            )
        if not subtitle.strip():
            notices.append(
                {"code": "missing_subtitle", "field": "subtitle", "message": "副标题尚未填写，不会写入发布正文"}
            )
        if not overview.strip():
            notices.append(
                {"code": "missing_overview", "field": "overview", "message": "简介正文尚未生成"}
            )

        blocks = []
        introduction, _ = self._introduction(context, overview, format, warnings)
        if introduction:
            blocks.append(introduction)
        elif not overview.strip():
            warnings.append(
                {"code": "missing_introduction", "message": "没有可生成的简介字段"}
            )

        reports = list(report_texts or [])
        if reports:
            blocks.extend(report_block(text, format) for text in reports if text)

        images = []
        for artifact in artifacts or []:
            url = artifact.get("url")
            if not url:
                continue
            if not safe_url(url):
                warnings.append(
                    {"code": "invalid_url", "message": "已忽略不安全的图片地址"}
                )
                continue
            if artifact.get("kind") not in {"screenshot", "collage", "upload"}:
                continue
            images.append(image_block(url, format))
        blocks.extend(images)

        for url in links or []:
            if not safe_url(url):
                warnings.append(
                    {"code": "invalid_url", "message": "已忽略不安全的链接"}
                )
                continue
            if format == "html":
                blocks.append(
                    f'<p><a href="{html.escape(url, quote=True)}" rel="noreferrer">'
                    f"{html.escape(url)}</a></p>"
                )
            elif format == "bbcode":
                blocks.append("[url]" + url + "[/url]")
            else:
                blocks.append("<" + url + ">")

        content = "\n\n".join(block for block in blocks if block)
        return {
            "content": content,
            "validation": {
                "ready": bool(content) and not errors,
                "errors": errors,
                "warnings": warnings,
                "notices": notices,
            },
            "context_revision": context["revision"],
        }
