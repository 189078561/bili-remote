from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping
from typing import Any

from .database import Database
from .file_service import FileServiceError
from .torrents import TorrentError, TorrentService


class PublishError(FileServiceError):
    pass


def release_title(fields):
    """Use only confirmed release attributes; codec names do not identify encoders."""
    name = str(fields.get("title_en") or "").strip()
    tokens = [name, str(fields.get("year") or "")]
    if fields.get("season"):
        tokens.append("S" + str(fields["season"]).zfill(2))
    for key in ("resolution", "source", "hdr", "video_codec", "audio_codec"):
        tokens.append(str(fields.get(key) or ""))
    text = ".".join(re.sub(r"[\s.]+", ".", x.strip()).strip(".") for x in tokens if x.strip())
    if fields.get("group"):
        text += "-" + str(fields["group"]).strip()
    return text


class PublishService:
    def __init__(self, database: Database, torrents: TorrentService):
        self.database, self.torrents = database, torrents

    def _reports(self, document, selected):
        media = (document or {}).get("media") or {}
        reports = media.get("reports")
        if reports is None:  # schema-v3 records remain readable
            reports = [
                {
                    "id": v.get("relative_path"),
                    "kind": "MediaInfo" if v.get("media_info_tool") == "mediainfo" else "ffprobe",
                    "text": v.get("media_info") or "",
                    "source": v.get("relative_path"),
                    "valid": v.get("media_info_tool") == "mediainfo",
                    "details": v.get("details") or {},
                }
                for v in media.get("videos") or []
            ]
            if media.get("disc_info"):
                reports.append(
                    {
                        "id": "bdinfo",
                        "kind": "BDInfo",
                        "text": media["disc_info"],
                        "valid": media.get("disc_info_tool") == "bdinfo",
                    }
                )
        if selected is not None:
            reports = [r for r in reports if r["id"] in selected]
        return reports

    def _build(self, data):
        fields = dict(data.get("metadata") or {})
        stored = self.database.get_settings()
        fields.setdefault("report_wrapper", stored.get("publish_report_wrapper", "hide"))
        fields.setdefault("rule_profile", "pter")
        document = None
        if data.get("release_document_id"):
            document = self.database.get_release_document(int(data["release_document_id"]))
            if not document:
                raise PublishError("关联的发布信息记录不存在")
        title = str(data.get("title") or release_title(fields)).strip()
        if len(title) > 240:
            raise PublishError("发布标题不能超过 240 个字符")
        subtitle = str(
            fields.get("subtitle")
            or " ".join(
                filter(
                    None, [fields.get("title_zh") or fields.get("title_en"), fields.get("keywords")]
                )
            )
        ).strip()
        fields["subtitle"] = subtitle
        reports = self._reports(document, fields.get("report_ids"))
        errors, warnings = [], []
        if not title or not any(char.isalpha() for char in title):
            errors.append("缺少英文或字母文字的主标题资源名")
        if not data.get("title") and not fields.get("title_en"):
            errors.append("生成主标题前请填写英文名或罗马字名")
        elif re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", title):
            errors.append("主标题须使用英文或其他字母文字，中文请放入副标题")
        if not subtitle:
            errors.append("缺少以中文名（无中文名时英文名）开头的副标题")
        elif fields.get("title_zh") and not subtitle.startswith(str(fields["title_zh"])):
            errors.append("副标题须以资源中文名开头")
        for key, pattern in (
            ("imdb_url", r"https://www\.imdb\.com/title/tt\d+/"),
            ("douban_url", r"https://movie\.douban\.com/subject/\d+/"),
        ):
            if fields.get(key) and not re.fullmatch(pattern, str(fields[key])):
                errors.append("豆瓣 / IMDb 链接格式不正确（需标准 HTTPS 地址及末尾斜杠）")
        poster = str(fields.get("poster_url") or "")
        if fields.get("poster_upload_id"):
            uploaded = self.database.get_image_upload(int(fields["poster_upload_id"]))
            if uploaded and uploaded["status"] == "completed":
                poster = uploaded.get("remote_url") or ""
        if not re.match(r"https?://[^/\s]+/", poster):
            poster = ""
            errors.append("缺少可访问的海报链接")
        description = str(data.get("description") or fields.get("overview") or "")
        if not description.strip():
            errors.append("缺少资源简介")
        if len(description) > 2_000_000:
            raise PublishError("发布描述超过 2 MB 限制")
        valid = [r for r in reports if r.get("valid") and r.get("text")]
        document_media = (document or {}).get("media") or {}
        kind = document_media.get("disc_kind") or (
            "bluray" if document_media.get("disc_info_tool") == "bdinfo" else "video"
        )
        if kind == "bluray":
            if not any(r["kind"] == "BDInfo" for r in valid):
                errors.append("蓝光原盘需要有效 BDInfo")
        elif kind == "dvd":
            sources = [str(r.get("source") or "").upper() for r in valid]
            if not any(x.endswith(".IFO") for x in sources) or not any(
                x.endswith(".VOB") for x in sources
            ):
                errors.append("DVD 原盘需要同一正片标题组的 IFO 与 VOB 两份 MediaInfo")
        elif not any(r["kind"] == "MediaInfo" for r in valid):
            errors.append("视频资源缺少完整 MediaInfo；ffprobe 摘要不能代替")
        if kind == "video" and document:
            videos = (document.get("media") or {}).get("videos") or []

            def signature(details):
                return tuple(
                    str(details.get(k) or "")
                    for k in (
                        "resolution",
                        "width",
                        "height",
                        "video_codec",
                        "hdr",
                        "audio_codecs",
                        "audio_codec",
                    )
                )

            if len(
                [entry for entry in document.get("file_list") or [] if entry.get("is_video")]
            ) > len(videos):
                errors.append("媒体探测未覆盖全部视频，请拆分资源或补齐各质量组报告")
            covered = {signature(r.get("details") or {}) for r in valid if r["kind"] == "MediaInfo"}
            for video in videos:
                if video.get("status") != "completed":
                    errors.append(f"媒体探测失败：{video.get('relative_path')}")
                elif signature(video.get("details") or {}) not in covered:
                    errors.append(f"该质量组缺少代表 MediaInfo：{video.get('relative_path')}")
        uploads = []
        for upload_id in dict.fromkeys(data.get("image_upload_ids") or []):
            record = self.database.get_image_upload(int(upload_id))
            if not record or record["status"] != "completed" or not record.get("remote_url"):
                errors.append(f"截图上传记录 #{upload_id} 不可用")
            elif int(upload_id) != fields.get("poster_upload_id"):
                uploads.append(record)
        if not uploads:
            errors.append("缺少已上传成功的资源截图")
        if not fields.get("classification_confirmed"):
            errors.append("请确认类型、质量、地区及字幕 / 配音标签")
        if not fields.get("category") or not fields.get("quality") or not fields.get("region"):
            errors.append("请填写资源类型、质量及地区")
        animation = "动画" in str(fields.get("genres")) or fields.get("category") == "Animation"
        if animation and (
            fields.get("source") in {"BDrip", "BDRip"} or re.search(r"\bBDRip\b", title, re.I)
        ):
            errors.append("蓝光来源的动画片源应写作 BluRay")
        if animation and fields.get("category") != "Animation":
            errors.append("动画电影应归入 Animation 分类")
        if kind == "bluray" and fields.get("quality") != "Blu-ray":
            errors.append("蓝光原盘的质量应选择 Blu-ray")
        if re.search(r"(?:^|[. ])x26[45](?:[. -]|$)", title, re.I) and not fields.get(
            "encoder_confirmed"
        ):
            errors.append("x264 / x265 是编码器名称，需要人工确认，不能从 H264 / H265 推断")
        if fields.get("original") or fields.get("exclusive"):
            errors.append("原创 / 禁转标签需按站规另行核实，本工具不会生成这些标签")
        torrent = None
        if data.get("torrent_build_id"):
            torrent = self.database.get_torrent_build(int(data["torrent_build_id"]))
            if not torrent:
                warnings.append("关联的种子记录不存在")
            else:
                try:
                    self.torrents.file_path(torrent["id"])
                    torrent["download_url"] = f"/api/torrents/{torrent['id']}/file"
                except TorrentError as exc:
                    warnings.append(str(exc))
        else:
            warnings.append("尚未关联种子文件")
        # Do not attach unrelated artifacts just because they are the newest records.
        source_root = data.get("source_root_key") or (document or {}).get("source_root_key")
        source_path = data.get("source_relative_path") or (document or {}).get(
            "source_relative_path"
        )
        if (
            document
            and torrent
            and (document["source_root_key"], document["source_relative_path"])
            != (torrent["source_root_key"], torrent["source_relative_path"])
        ):
            errors.append("种子与媒体报告对应的源资源不同，请确认关联")
        bb, md, ht = [], [], []
        if poster:
            bb.append(f"[img]{poster}[/img]")
            md.append(f"![海报]({poster})")
            ht.append(f'<img src="{html.escape(poster, quote=True)}" alt="海报">')
        if description:
            bb.append(description)
            md.append(description)
            ht.append("<p>" + html.escape(description).replace("\n", "<br>\n") + "</p>")
        for report in reports:
            if not report.get("valid"):
                continue
            name = report["kind"]
            text = str(report.get("publish_text") or report["text"])
            wrapper = "quote" if fields["report_wrapper"] == "quote" else f"hide={name}"
            end = "quote" if wrapper == "quote" else "hide"
            bb.append(f"[{wrapper}]{text}[/{end}]")
            md.append(f"```text\n{text}\n```")
            ht.append("<pre>" + html.escape(text) + "</pre>")
        for record in uploads:
            url = record["remote_url"]
            bb.append(f"[img]{url}[/img]")
            md.append(f"![截图]({url})")
            ht.append(f'<img src="{html.escape(url, quote=True)}" alt="截图">')
        fields.update(
            {
                "checks": {"profile": "pter", "errors": errors, "warnings": warnings},
                "report_sources": [
                    {"id": r["id"], "kind": r["kind"], "source": r.get("source")} for r in reports
                ],
                "poster_url": poster,
            }
        )
        return dict(
            title=title,
            status="draft" if errors else "ready",
            source_root_key=source_root,
            source_relative_path=source_path,
            description=description,
            metadata=fields,
            image_upload_ids=[r["id"] for r in uploads],
            release_document_id=(document or {}).get("id"),
            torrent_build_id=(torrent or {}).get("id"),
            bbcode="\n\n".join(bb),
            markdown="\n\n".join(md),
            html="\n".join(ht),
            warnings=errors + warnings,
        )

    def create(self, data: Mapping[str, Any]):
        payload = self._build(data)
        record_id = self.database.create_publish_session(**payload)
        return self.get(record_id)

    def update(self, session_id, data):
        existing = self.get(session_id)
        merged = {**existing, **data}
        merged["metadata"] = {**(existing.get("metadata") or {}), **(data.get("metadata") or {})}
        payload = self._build(merged)
        fields = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "source_root_key",
                "source_relative_path",
                "metadata",
                "image_upload_ids",
                "warnings",
            }
        }
        fields.update(
            metadata_json=json.dumps(payload["metadata"], ensure_ascii=False),
            image_upload_ids_json=json.dumps(payload["image_upload_ids"]),
            warnings_json=json.dumps(payload["warnings"], ensure_ascii=False),
        )
        self.database.update_publish_session(session_id, **fields)
        return self.get(session_id)

    def get(self, session_id):
        record = self.database.get_publish_session(session_id)
        if record is None:
            raise PublishError("发布辅助会话不存在")
        record["torrent"] = (
            self.database.get_torrent_build(record["torrent_build_id"])
            if record.get("torrent_build_id")
            else None
        )
        return record

    def list_sessions(self, *, limit=100, offset=0):
        return self.database.list_publish_sessions(limit=limit, offset=offset)
