from __future__ import annotations

import asyncio
import logging
import json
import os
import re
import secrets
import signal
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from .bili import (
    BiliAPIError,
    BiliAuthService,
    BiliTVAuthError,
    BiliTVRegionError,
)
from .config import Settings
from .database import Database, utc_now
from .security import redact_sensitive
from .validation import ValidationError, canonicalize_bilibili_url


class DownloadFailure(RuntimeError):
    def __init__(self, message: str, *, auth_required: bool = False) -> None:
        super().__init__(message)
        self.auth_required = auth_required


class CancelledFailure(RuntimeError):
    pass


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]

TV_CDN_BLOCKED_MESSAGE = (
    "TV 片源 CDN 返回 403 或连接超时：当前服务器 IP 无法访问该片源。"
    "TV 线路不能解除地区限制，请切换到 Web 线路，或更换服务器出口 IP 后重试。"
)
MIN_FORMAT_HEIGHT = 1080
ARIA2_STABLE_MAX_CONNECTIONS = 8
ARIA2_STABLE_FLOOR_CONNECTIONS = 4
TV_STREAM_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/134.0.0.0 Mobile Safari/537.36"
)


@dataclass(slots=True)
class ResolvedMedia:
    title: str
    uploader: str
    duration: int | None
    webpage_url: str | None
    is_playlist: bool
    playlist_count: int | None
    formats: list[dict[str, Any]]
    episodes: list[dict[str, Any]]
    default_episode_id: str | None
    api_mode: str = "web"
    warning: str | None = None


def _parse_int(value: str) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_float(value: str) -> float | None:
    try:
        result = float(value)
        return result if result >= 0 else None
    except (TypeError, ValueError):
        return None


def _parse_fps(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value)
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        try:
            denominator_value = float(denominator)
            return float(numerator) / denominator_value if denominator_value else None
        except (TypeError, ValueError):
            return None
    return _parse_float(text)


def _parse_bandwidth(value: Any) -> float | None:
    try:
        return float(value) / 1000 if value is not None else None
    except (TypeError, ValueError):
        return None


class YtDlpRunner:
    def __init__(
        self,
        settings: Settings,
        auth_service: BiliAuthService,
    ) -> None:
        self.settings = settings
        self.auth_service = auth_service
        self.completion_origins: dict[int, dict[str, Any]] = {}
        self.completion_paths: dict[int, list[str]] = {}
        self.completion_item_origins: dict[int, dict[str, dict]] = {}
        self.current_process: asyncio.subprocess.Process | None = None
        self.cancel_requested = False
        self._active_operations = 0
        self.aria2_executable: str | None = None
        self.refresh_aria2_executable()

    def refresh_aria2_executable(self) -> None:
        self.aria2_executable = (
            shutil.which(self.settings.aria2c_command)
            if self.settings.aria2_enabled and self.settings.aria2c_command
            else None
        )

    def remember_completion_item(self, job_id: int, payload: str) -> None:
        """Keep only structured extractor provenance, never download URLs or cookies."""
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict) or not isinstance(data.get('filepath'), str):
            return
        def identifier(name):
            value = data.get(name)
            return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) and value != '' else None
        index = data.get('episode_number') or data.get('playlist_index')
        title = data.get('series') or data.get('title')
        origin = {'bvid':identifier('bvid'), 'cid':identifier('cid'), 'season_id':identifier('season_id'),
                  'ep_id':identifier('episode_id'), 'original_title':title if isinstance(title,str) else None,
                  'episode_index':index if type(index) is int else None}
        self.completion_item_origins.setdefault(job_id,{})[data['filepath']] = origin

    def has_active_operation(self) -> bool:
        return self._active_operations > 0

    def _begin_operation(self) -> None:
        self._active_operations += 1

    def _end_operation(self) -> None:
        self._active_operations = max(0, self._active_operations - 1)

    async def resolve(self, url: str, api_mode: str = "web") -> ResolvedMedia:
        self._begin_operation()
        try:
            return await self._resolve(url, api_mode)
        finally:
            self._end_operation()

    async def _resolve(self, url: str, api_mode: str = "web") -> ResolvedMedia:
        url = await canonicalize_bilibili_url(url)
        if api_mode == "tv":
            return await self._resolve_tv(url)
        if api_mode != "web":
            raise DownloadFailure("解析线路无效")
        return await self._resolve_web(url)

    async def episode_formats(
        self, url: str, api_mode: str = "web"
    ) -> dict[str, Any]:
        self._begin_operation()
        try:
            return await self._episode_formats(url, api_mode)
        finally:
            self._end_operation()

    async def _episode_formats(
        self, url: str, api_mode: str = "web"
    ) -> dict[str, Any]:
        url = await canonicalize_bilibili_url(url)
        if api_mode == "tv":
            info = await self._tv_download_info(url, {})
            return {
                "title": info.get("title"),
                "duration": info.get("duration"),
                "webpage_url": url,
                "episode_id": info.get("id"),
                "formats": self._normalize_formats(info.get("formats") or []),
            }
        if api_mode != "web":
            raise DownloadFailure("解析线路无效")
        cookie_path = self._create_cookie_file()
        deadline = (
            asyncio.get_running_loop().time()
            + self.settings.resolve_timeout_seconds
        )
        try:
            payload = await self._resolve_payload(
                url,
                cookie_path,
                flat_playlist=False,
                deadline=deadline,
            )
        finally:
            if cookie_path:
                self._remove_file(cookie_path)
        return {
            "title": payload.get("title"),
            "duration": payload.get("duration"),
            "webpage_url": url,
            "episode_id": payload.get("id"),
            "formats": self._normalize_formats(payload.get("formats") or []),
        }

    async def batch_episode_formats(
        self,
        url: str,
        episode_ids: list[str],
        api_mode: str = "web",
        *,
        max_probe: int = 8,
    ) -> dict[str, Any]:
        self._begin_operation()
        try:
            return await self._batch_episode_formats(
                url, episode_ids, api_mode, max_probe=max_probe
            )
        finally:
            self._end_operation()

    async def _batch_episode_formats(
        self,
        url: str,
        episode_ids: list[str],
        api_mode: str = "web",
        *,
        max_probe: int = 8,
    ) -> dict[str, Any]:
        """Probe representative episodes and report profile coverage."""
        url = await canonicalize_bilibili_url(url)
        if not self._is_bangumi_url(url):
            raise DownloadFailure("该链接不支持集数格式检测")
        if api_mode not in {"web", "tv"}:
            raise DownloadFailure("解析线路无效")

        kind, item_id = self._bangumi_ids(url)
        try:
            season, sections = await self._season_bundle(kind, item_id)
        except BiliAPIError as exc:
            raise DownloadFailure(str(exc)) from exc
        episodes, _ = self._build_episodes(season, sections)
        episode_map = {str(item["id"]): item for item in episodes}

        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_id in episode_ids:
            episode_id = str(raw_id).strip()
            if not episode_id or episode_id in seen:
                continue
            episode = episode_map.get(episode_id)
            if not episode:
                raise DownloadFailure(f"集数 {episode_id} 不属于该番剧")
            seen.add(episode_id)
            selected.append(episode)
        if not selected:
            raise DownloadFailure("请至少选择一集")

        probes = self._sample_episodes(selected, max(1, min(max_probe, 12)))
        semaphore = asyncio.Semaphore(2 if api_mode == "tv" else 4)
        timeout = max(
            5.0, min(float(self.settings.resolve_timeout_seconds), 30.0)
        )

        async def probe(
            episode: dict[str, Any],
        ) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
            async with semaphore:
                try:
                    result = await asyncio.wait_for(
                        self.episode_formats(str(episode["url"]), api_mode),
                        timeout=timeout,
                    )
                except TimeoutError:
                    return episode, [], "解析超时"
                except (DownloadFailure, ValidationError) as exc:
                    return episode, [], str(exc)
                except Exception:
                    return episode, [], "格式检测失败"
                formats = result.get("formats") or []
                if not formats:
                    return episode, [], "未返回可用格式"
                return episode, formats, None

        outcomes = await asyncio.gather(*(probe(episode) for episode in probes))
        format_sets = [formats for _, formats, error in outcomes if error is None]
        failed = [
            {"id": str(episode["id"]), "title": episode.get("title"), "error": error}
            for episode, _, error in outcomes
            if error is not None
        ]
        return {
            "title": str(season.get("title") or "未命名番剧"),
            "selected_count": len(selected),
            "checked": len(format_sets),
            "sampled": len(probes) < len(selected),
            "failed": len(failed),
            "failed_episodes": failed,
            "profiles": self._aggregate_format_profiles(format_sets),
            "api_mode": api_mode,
        }

    async def _resolve_web(self, url: str) -> ResolvedMedia:
        cookie_path = self._create_cookie_file()
        deadline = (
            asyncio.get_running_loop().time()
            + self.settings.resolve_timeout_seconds
        )
        try:
            if self._is_bangumi_url(url):
                return await self._resolve_bangumi_web(
                    url, cookie_path, deadline=deadline
                )
            is_favorite = self._is_favorite_url(url)
            payload = await self._resolve_payload(
                url,
                cookie_path,
                flat_playlist=is_favorite,
                deadline=deadline,
            )
            return self._normalize_resolved(payload, is_favorite=is_favorite)
        except BiliAPIError as exc:
            raise DownloadFailure(str(exc)) from exc
        finally:
            if cookie_path:
                self._remove_file(cookie_path)

    async def _resolve_bangumi_web(
        self,
        url: str,
        cookie_path: Path | None,
        *,
        deadline: float,
    ) -> ResolvedMedia:
        kind, item_id = self._bangumi_ids(url)
        season, sections = await self._season_bundle(kind, item_id)
        episodes, main_count = self._build_episodes(season, sections)
        default_episode_id = item_id if kind == "ep" else None
        target = self._target_episode(episodes, default_episode_id)
        warning = None
        try:
            payload = await self._resolve_payload(
                target["url"],
                cookie_path,
                flat_playlist=False,
                deadline=deadline,
            )
        except DownloadFailure as exc:
            payload = {"formats": []}
            warning = f"集数已读取，但格式预解析失败：{exc}"
        return ResolvedMedia(
            title=str(season.get("title") or "未命名番剧"),
            uploader="",
            duration=target.get("duration"),
            webpage_url=url,
            is_playlist=True,
            playlist_count=main_count,
            formats=self._normalize_formats(payload.get("formats") or []),
            episodes=episodes,
            default_episode_id=default_episode_id,
            api_mode="web",
            warning=warning,
        )

    async def _resolve_tv(self, url: str) -> ResolvedMedia:
        if not self._has_tv_token():
            raise DownloadFailure("请先完成 TV 扫码登录", auth_required=True)
        try:
            if self._is_bangumi_url(url):
                kind, item_id = self._bangumi_ids(url)
                season, sections = await self._season_bundle(kind, item_id)
                episodes, main_count = self._build_episodes(season, sections)
                default_episode_id = item_id if kind == "ep" else None
                target = self._target_episode(episodes, default_episode_id)
                dash = await self.auth_service.tv_bangumi_playinfo(
                    aid=target["aid"],
                    cid=target["cid"],
                    ep_id=target["id"],
                )
                formats = await self._prepare_tv_formats(
                    self._tv_formats(dash)
                )
                return ResolvedMedia(
                    title=str(season.get("title") or "未命名番剧"),
                    uploader="",
                    duration=target.get("duration"),
                    webpage_url=url,
                    is_playlist=True,
                    playlist_count=main_count,
                    formats=self._normalize_formats(formats),
                    episodes=episodes,
                    default_episode_id=default_episode_id,
                    api_mode="tv",
                    warning=None if formats else TV_CDN_BLOCKED_MESSAGE,
                )
            video_id = self._video_id(url)
            if not video_id:
                raise DownloadFailure("TV 线路目前只支持番剧和普通视频")
            view = await self.auth_service.video_view(bvid=video_id)
            pages = view.get("pages") or []
            if len(pages) > 1:
                raise DownloadFailure(
                    "TV 线路暂不支持多 P 视频，请切换到 Web 线路"
                )
            page = pages[0] if pages else view
            dash = await self.auth_service.tv_video_playinfo(
                aid=view.get("aid"), cid=page.get("cid") or view.get("cid")
            )
            formats = await self._prepare_tv_formats(
                self._tv_formats(dash)
            )
            if not formats:
                raise DownloadFailure(TV_CDN_BLOCKED_MESSAGE)
            return ResolvedMedia(
                title=str(view.get("title") or "未命名视频"),
                uploader=str(
                    (view.get("owner") or {}).get("name")
                    or view.get("uploader")
                    or ""
                ),
                duration=int(view.get("duration") or 0) or None,
                webpage_url=url,
                is_playlist=False,
                playlist_count=None,
                formats=self._normalize_formats(formats),
                episodes=[],
                default_episode_id=None,
                api_mode="tv",
            )
        except BiliTVAuthError as exc:
            raise DownloadFailure(str(exc), auth_required=True) from exc
        except BiliTVRegionError as exc:
            raise DownloadFailure(str(exc)) from exc
        except BiliAPIError as exc:
            raise DownloadFailure(str(exc)) from exc

    async def _season_target(
        self, kind: str, item_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if kind == "ep":
            season = await self.auth_service.season_view(ep_id=item_id)
        else:
            season = await self.auth_service.season_view(season_id=item_id)
        episodes = season.get("episodes") or []
        if kind == "ep":
            for episode in episodes:
                if str(episode.get("ep_id") or episode.get("id") or "") == item_id:
                    return season, episode
        if not episodes:
            raise DownloadFailure("没有可下载的剧集")
        return season, episodes[0]

    async def _tv_download_info(
        self, url: str, job: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._has_tv_token():
            raise DownloadFailure("请先完成 TV 扫码登录", auth_required=True)
        try:
            if self._is_bangumi_url(url):
                kind, item_id = self._bangumi_ids(url)
                season, target = await self._season_target(kind, item_id)
                ep_id = str(target.get("ep_id") or target.get("id") or item_id)
                aid = target.get("aid")
                cid = target.get("cid")
                if not aid or not cid:
                    raise DownloadFailure("TV 接口缺少剧集播放参数")
                dash = await self.auth_service.tv_bangumi_playinfo(
                    aid=aid, cid=cid, ep_id=ep_id
                )
                duration_ms = target.get("duration")
                duration = (
                    int(float(duration_ms) / 1000)
                    if duration_ms and float(duration_ms) > 1000
                    else int(float(duration_ms or 0))
                )
                title = (
                    job.get("title")
                    or " ".join(
                        part
                        for part in (
                            str(target.get("title") or "").strip(),
                            str(target.get("long_title") or "").strip(),
                        )
                        if part
                    )
                    or str(season.get("title") or "未命名剧集")
                )
                uploader = str(job.get("uploader") or "")
                webpage_url = str(
                    target.get("share_url")
                    or f"https://www.bilibili.com/bangumi/play/ep{ep_id}"
                )
                item_identifier = f"ep{ep_id}"
            else:
                video_id = self._video_id(url)
                if not video_id:
                    raise DownloadFailure("TV 线路目前只支持番剧和普通视频")
                view = await self.auth_service.video_view(bvid=video_id)
                pages = view.get("pages") or []
                if len(pages) > 1:
                    raise DownloadFailure(
                        "TV 线路暂不支持多 P 视频，请切换到 Web 线路"
                    )
                page = pages[0] if pages else view
                aid = view.get("aid")
                cid = page.get("cid") or view.get("cid")
                if not aid or not cid:
                    raise DownloadFailure("TV 接口缺少视频播放参数")
                dash = await self.auth_service.tv_video_playinfo(aid=aid, cid=cid)
                title = str(job.get("title") or view.get("title") or "未命名视频")
                uploader = str(
                    job.get("uploader")
                    or (view.get("owner") or {}).get("name")
                    or view.get("uploader")
                    or ""
                )
                duration = int(view.get("duration") or 0) or None
                webpage_url = url
                item_identifier = video_id
            formats = self._tv_formats(dash)
            formats = await self._prepare_tv_formats(formats)
            if not formats:
                raise DownloadFailure(TV_CDN_BLOCKED_MESSAGE)
            origin = {"bvid": None, "season_id": None, "ep_id": None, "cid": str(cid), "original_title": None, "episode_index": None}
            if self._is_bangumi_url(url):
                origin.update(bvid=target.get("bvid"), season_id=str(season["season_id"]) if season.get("season_id") else None,
                    ep_id=str(target.get("ep_id") or target.get("id")) if target.get("ep_id") or target.get("id") else None,
                    original_title=season.get("title"),
                    episode_index=next((i for i, item in enumerate(season.get("episodes", []), 1) if item.get("cid") == cid), None))
            else:
                origin.update(bvid=view.get("bvid"), original_title=view.get("title"), episode_index=page.get("page"))
            return {
                "_v2_origin": origin,
                "_type": "video",
                "id": item_identifier,
                "title": title,
                "uploader": uploader,
                "duration": duration,
                "webpage_url": webpage_url,
                "extractor": "BiliTV",
                "extractor_key": "BiliTV",
                "formats": formats,
            }
        except BiliTVAuthError as exc:
            raise DownloadFailure(str(exc), auth_required=True) from exc
        except BiliTVRegionError as exc:
            raise DownloadFailure(str(exc)) from exc
        except BiliAPIError as exc:
            raise DownloadFailure(str(exc)) from exc

    def _has_tv_token(self) -> bool:
        try:
            return bool(self.auth_service.load_tv_token())
        except ValueError:
            return False

    async def _web_download_info(
        self,
        url: str,
        cookie_path: Path | None,
    ) -> dict[str, Any] | None:
        """Resolve once, attach Web/TV CDN candidates, and rank them by speed."""
        deadline = (
            asyncio.get_running_loop().time()
            + self.settings.resolve_timeout_seconds
        )
        payload = await self._resolve_payload(
            url,
            cookie_path,
            flat_playlist=False,
            deadline=deadline,
        )
        formats = list(payload.get("formats") or [])
        if not any(item.get("url") for item in formats):
            return None

        cid = payload.get("cid")
        bvid = self._video_id(url)
        aid = payload.get("aid")
        is_bangumi = self._is_bangumi_url(url)
        ep_id: str | None = None
        if is_bangumi:
            kind, item_id = self._bangumi_ids(url)
            if not aid or not cid:
                season, target = await self._season_target(kind, item_id)
                aid = aid or target.get("aid")
                cid = cid or target.get("cid")
                ep_id = str(target.get("ep_id") or target.get("id") or item_id)
            elif kind == "ep":
                ep_id = item_id
            if not aid or not cid:
                return None
            if not ep_id:
                season = await self.auth_service.season_view(season_id=item_id)
                ep_id = str(
                    (season.get("episodes") or [{}])[0].get("ep_id")
                    or (season.get("episodes") or [{}])[0].get("id")
                    or item_id
                )
            web_dash = await self.auth_service.pgc_web_playinfo(
                aid=aid, cid=cid, ep_id=ep_id
            )
        else:
            if not bvid and aid is None:
                return None
            if not cid or aid is None:
                view = await self.auth_service.video_view(bvid=bvid, aid=aid)
                aid = aid or view.get("aid")
                if not cid:
                    pages = view.get("pages") or []
                    page_index = 0
                    try:
                        page_index = max(
                            0,
                            int(parse_qs(urlsplit(url).query).get("p", ["1"])[0])
                            - 1,
                        )
                    except (TypeError, ValueError):
                        page_index = 0
                    if pages and page_index < len(pages):
                        cid = pages[page_index].get("cid")
                    cid = cid or view.get("cid")
            if not cid:
                return None
            web_dash = await self.auth_service.web_playinfo(
                bvid=bvid, aid=aid, cid=cid
            )
        self._merge_line_candidates(
            formats, web_dash, candidate_key="_cdn_candidates"
        )

        if self.settings.line_compare_enabled and self._has_tv_token():
            try:
                if is_bangumi and aid and cid and ep_id:
                    tv_dash = await self.auth_service.tv_bangumi_playinfo(
                        aid=aid, cid=cid, ep_id=ep_id
                    )
                elif cid and (bvid or aid is not None):
                    tv_dash = await self.auth_service.tv_video_playinfo(
                        aid=aid or 0, cid=cid
                    )
                else:
                    tv_dash = None
                if tv_dash:
                    self._merge_line_candidates(
                        formats, tv_dash, candidate_key="_cdn_candidates"
                    )
            except BiliAPIError:
                pass

        await self._rank_cdn_candidates(
            formats,
            candidate_key="_cdn_candidates",
            drop_unavailable=False,
            max_items=12,
        )
        origin = {"bvid": payload.get("bvid"), "cid": str(cid) if cid else None,
                  "season_id": str(payload["season_id"]) if payload.get("season_id") else None,
                  "ep_id": str(payload["episode_id"]) if payload.get("episode_id") else None,
                  "original_title": payload.get("series") or payload.get("title"),
                  "episode_index": payload.get("episode_number") or payload.get("playlist_index")}
        if "target" in locals():
            origin.update(bvid=target.get("bvid"), season_id=str(season["season_id"]) if season.get("season_id") else None,
                ep_id=str(target.get("ep_id") or target.get("id")) if target.get("ep_id") or target.get("id") else None,
                original_title=season.get("title"),
                episode_index=next((i for i, item in enumerate(season.get("episodes", []), 1) if item.get("cid") == cid), None))
        elif "view" in locals():
            origin.update(bvid=view.get("bvid"), original_title=view.get("title"),
                episode_index=next((item.get("page") for item in view.get("pages", []) if item.get("cid") == cid), None))
        payload["_v2_origin"] = origin
        return payload

    @staticmethod
    def _write_info_json(path: Path, info: dict[str, Any]) -> None:
        path.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _advance_cdn_info(info: dict[str, Any]) -> bool:
        changed = False
        for item in info.get("formats") or []:
            candidates = item.get("_cdn_candidates") or item.get("_tv_candidates")
            if not isinstance(candidates, list) or len(candidates) < 2:
                continue
            candidates = [url for url in candidates if isinstance(url, str) and url]
            if len(candidates) < 2:
                continue
            current = item.get("url")
            try:
                index = candidates.index(current)
            except ValueError:
                index = 0
            item["url"] = candidates[(index + 1) % len(candidates)]
            changed = True
        return changed

    @staticmethod
    def _is_cdn_failure(message: str) -> bool:
        text = message.lower()
        return YtDlpRunner._has_retryable_cdn_status(message) or any(
            pattern in text
            for pattern in (
                "http error 403",
                "403: forbidden",
                "403 forbidden",
                "http error 404",
                "404: not found",
                "did not get any data blocks",
                "remote end closed connection",
                "connection reset by peer",
                "connection aborted",
                "unable to download video data",
            )
        )

    @staticmethod
    def _has_retryable_cdn_status(message: str) -> bool:
        text = message.lower()
        retryable_status = (
            r"(?:429|5(?:00|01|02|03|04|20|21|22|23|24|25|26|27)|567)"
        )
        if re.search(
            rf"(?:\bhttp(?:\s+error)?(?:\s+status)?|\berrorcode|"
            rf"\bstatus(?:\s+code)?)\s*[:=/]?\s*{retryable_status}\b",
            text,
        ):
            return True
        if re.search(
            rf"\bhttp/\d(?:\.\d)?\s+{retryable_status}\b",
            text,
        ):
            return True
        return bool(
            re.search(
                rf"\b{retryable_status}\b\s*:?\s*"
                r"(?:too many requests|internal server error|bad gateway|"
                r"service unavailable|gateway timeout|web server is down|"
                r"connection timed out|origin is unreachable)",
                text,
            )
        )

    @staticmethod
    def _should_rotate_cdn(message: str) -> bool:
        """Keep resumable downloads on one CDN unless the node itself is bad."""
        text = message.lower()
        if (
            YtDlpRunner._has_retryable_cdn_status(message)
            or "too many requests" in text
            or "rate limit" in text
        ):
            return False
        return any(
            pattern in text
            for pattern in (
                "http error 403",
                "403: forbidden",
                "403 forbidden",
                "http error 404",
                "404: not found",
                "did not get any data blocks",
                "remote end closed connection",
                "connection reset by peer",
                "connection aborted",
                "unable to download video data",
            )
        )

    async def _season_bundle(
        self, kind: str, item_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if kind == "ep":
            season = await self.auth_service.season_view(ep_id=item_id)
        else:
            season = await self.auth_service.season_view(season_id=item_id)
        season_id = season.get("season_id") or item_id
        sections = await self.auth_service.season_sections(season_id)
        return season, sections

    def _build_episodes(
        self, season: dict[str, Any], sections: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], int]:
        details = {
            str(item.get("ep_id") or item.get("id")): item
            for item in season.get("episodes") or []
        }
        main = (sections.get("main_section") or {}).get("episodes") or []
        groups: list[tuple[str, list[dict[str, Any]]]] = [("正片", main)]
        for section in sections.get("section") or []:
            episodes = section.get("episodes") or []
            if episodes:
                groups.append((str(section.get("title") or "其他"), episodes))

        result: list[dict[str, Any]] = []
        for group_title, items in groups:
            for group_index, item in enumerate(items, 1):
                ep_id = str(item.get("id") or "")
                if not ep_id:
                    continue
                detail = details.get(ep_id) or {}
                duration_ms = detail.get("duration") or item.get("duration")
                duration = (
                    int(float(duration_ms) / 1000)
                    if duration_ms and float(duration_ms) > 1000
                    else int(float(duration_ms or 0))
                )
                title = " ".join(
                    part
                    for part in (
                        str(item.get("title") or detail.get("title") or "").strip(),
                        str(
                            item.get("long_title")
                            or detail.get("long_title")
                            or ""
                        ).strip(),
                    )
                    if part
                )
                result.append(
                    {
                        "id": ep_id,
                        "index": len(result) + 1,
                        "group": group_title,
                        "group_index": group_index,
                        "title": title or f"第{len(result) + 1}话",
                        "duration": duration or None,
                        "url": item.get("share_url")
                        or detail.get("share_url")
                        or f"https://www.bilibili.com/bangumi/play/ep{ep_id}",
                        "badge": item.get("badge") or detail.get("badge") or "",
                        "aid": item.get("aid") or detail.get("aid"),
                        "cid": item.get("cid") or detail.get("cid"),
                    }
                )
        return result, len(main)

    @staticmethod
    def _target_episode(
        episodes: list[dict[str, Any]], default_episode_id: str | None
    ) -> dict[str, Any]:
        if default_episode_id:
            for episode in episodes:
                if episode["id"] == default_episode_id:
                    return episode
        for episode in episodes:
            if episode.get("group") == "正片":
                return episode
        if episodes:
            return episodes[0]
        raise DownloadFailure("没有可下载的剧集")

    async def _resolve_payload(
        self,
        url: str,
        cookie_path: Path | None,
        *,
        flat_playlist: bool,
        deadline: float,
    ) -> dict[str, Any]:
        command = [*self.settings.ytdlp_command, "-J", "--no-warnings"]
        if cookie_path:
            command.extend(["--cookies", str(cookie_path)])
        command.append("--flat-playlist" if flat_playlist else "--no-playlist")
        command.append(url)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=max(
                    1.0, deadline - asyncio.get_running_loop().time()
                ),
            )
        except TimeoutError as exc:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise DownloadFailure("解析超时，请稍后重试") from exc

        if process.returncode != 0:
            message = self._clean_error(stderr.decode("utf-8", errors="replace"))
            raise DownloadFailure(
                message or "yt-dlp 无法解析该链接",
                auth_required=self._looks_like_auth_error(message),
            )
        try:
            payload = json.loads(stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise DownloadFailure("yt-dlp 返回的解析结果无法读取") from exc
        return payload

    @staticmethod
    def _tv_url_candidates(item: dict[str, Any]) -> list[str]:
        urls: list[str] = []
        for key in ("base_url", "baseUrl"):
            value = item.get(key)
            if isinstance(value, str) and value and value not in urls:
                urls.append(value)
        backup = item.get("backup_url") or item.get("backupUrl") or []
        if isinstance(backup, str):
            backup = [backup]
        if isinstance(backup, list):
            for value in backup:
                if isinstance(value, str) and value and value not in urls:
                    urls.append(value)
        return urls

    @staticmethod
    def _codec_family(value: Any) -> str:
        codecs = str(value or "").lower()
        if "av01" in codecs:
            return "av1"
        if "hvc" in codecs or "hev" in codecs:
            return "hevc"
        if "avc" in codecs or "h264" in codecs:
            return "avc"
        return codecs

    @staticmethod
    def _dynamic_range_label(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return "any"
        if "dolby" in text or "杜比" in text or text in {"dv", "dovi"}:
            return "DV"
        if "hlg" in text:
            return "HLG"
        if "hdr" in text:
            return "HDR10"
        if "sdr" in text:
            return "SDR"
        return "any"

    @classmethod
    def _batch_format_profile(cls, item: dict[str, Any]) -> dict[str, Any] | None:
        try:
            height = int(item.get("height") or 0)
        except (TypeError, ValueError):
            return None
        vcodec = str(item.get("vcodec") or "")
        if height < MIN_FORMAT_HEIGHT or not vcodec or vcodec == "none":
            return None
        codec = cls._codec_family(vcodec)
        if codec not in {"avc", "hevc", "av1"}:
            codec = "any"
        fps = item.get("fps")
        try:
            fps_high = None if fps is None or fps == "" else float(fps) >= 50
        except (TypeError, ValueError):
            fps_high = None
        return {
            "height": height,
            "fps_high": fps_high,
            "vcodec": codec,
            "dynamic_range": cls._dynamic_range_label(item.get("dynamic_range")),
        }

    @staticmethod
    def _sample_episodes(
        episodes: list[dict[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        if limit <= 0 or len(episodes) <= limit:
            return list(episodes)
        if limit == 1:
            return [episodes[0]]
        indexes = sorted(
            {
                round(index * (len(episodes) - 1) / (limit - 1))
                for index in range(limit)
            }
        )
        return [episodes[index] for index in indexes]

    @classmethod
    def _aggregate_format_profiles(
        cls, format_sets: list[list[dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        profiles: dict[tuple[Any, ...], dict[str, Any]] = {}
        for formats in format_sets:
            seen: set[tuple[Any, ...]] = set()
            for item in formats:
                profile = cls._batch_format_profile(item)
                if not profile:
                    continue
                key = (
                    profile["height"],
                    profile["fps_high"],
                    profile["vcodec"],
                    profile["dynamic_range"],
                )
                entry = profiles.get(key)
                if entry is None:
                    entry = {
                        "profile": profile,
                        "coverage": 0,
                        "sample": item,
                    }
                    profiles[key] = entry
                elif cls._format_quality(item) > cls._format_quality(entry["sample"]):
                    entry["sample"] = item
                if key not in seen:
                    entry["coverage"] += 1
                    seen.add(key)

        result: list[dict[str, Any]] = []
        for entry in profiles.values():
            sample = entry["sample"]
            result.append(
                {
                    **entry["profile"],
                    "coverage": entry["coverage"],
                    "sample": {
                        "width": sample.get("width"),
                        "height": sample.get("height"),
                        "fps": sample.get("fps"),
                        "vcodec": sample.get("vcodec"),
                        "dynamic_range": sample.get("dynamic_range"),
                        "format_note": sample.get("format_note"),
                        "tbr": sample.get("tbr"),
                    },
                }
            )
        result.sort(
            key=lambda item: (
                item["height"],
                2 if item["fps_high"] is True else 1 if item["fps_high"] is False else 0,
                item["coverage"],
                cls._format_quality(item["sample"]),
            ),
            reverse=True,
        )
        return result

    @staticmethod
    def _format_quality(item: dict[str, Any]) -> tuple[float, float, float]:
        try:
            tbr = float(item.get("tbr") or 0)
        except (TypeError, ValueError):
            tbr = 0.0
        try:
            fps = float(item.get("fps") or 0)
        except (TypeError, ValueError):
            fps = 0.0
        try:
            height = float(item.get("height") or 0)
        except (TypeError, ValueError):
            height = 0.0
        return tbr, fps, height

    @classmethod
    def _stream_candidate_map(
        cls, dash: dict[str, Any]
    ) -> dict[tuple[str, str, str], list[str]]:
        result: dict[tuple[str, str, str], list[str]] = {}
        for item in dash.get("video") or []:
            key = (
                "v",
                str(item.get("id") or ""),
                cls._codec_family(item.get("codecs")),
            )
            candidates = result.setdefault(key, [])
            for url in cls._tv_url_candidates(item):
                if url not in candidates:
                    candidates.append(url)
        audio_items = list(dash.get("audio") or [])
        audio_items.extend(list((dash.get("dolby") or {}).get("audio") or []))
        audio_items.extend(list((dash.get("flac") or {}).get("audio") or []))
        for item in audio_items:
            key = ("a", str(item.get("id") or ""), "")
            candidates = result.setdefault(key, [])
            for url in cls._tv_url_candidates(item):
                if url not in candidates:
                    candidates.append(url)
        return result

    @classmethod
    def _format_stream_key(cls, item: dict[str, Any]) -> tuple[str, str, str]:
        format_id = str(item.get("format_id") or "")
        acodec = str(item.get("acodec") or "")
        if format_id.startswith("tva"):
            audio_id = format_id[3:]
            audio_id = audio_id.removeprefix("dolby-").removeprefix("flac-")
            return ("a", audio_id, "")
        if acodec and acodec != "none" and format_id.isdigit():
            return ("a", format_id, "")
        quality = item.get("quality")
        if quality is not None:
            return (
                "v",
                str(quality),
                cls._codec_family(item.get("vcodec")),
            )
        if format_id.startswith("tv"):
            quality_id = format_id[2:].split("-", 1)[0]
            return ("v", quality_id, cls._codec_family(item.get("vcodec")))
        return ("v", format_id, cls._codec_family(item.get("vcodec")))

    @classmethod
    def _merge_line_candidates(
        cls,
        formats: list[dict[str, Any]],
        other_dash: dict[str, Any],
        *,
        candidate_key: str,
    ) -> None:
        source = cls._stream_candidate_map(other_dash)
        for item in formats:
            candidates = source.get(cls._format_stream_key(item)) or []
            if not candidates and not str(item.get("format_id") or "").startswith("tv"):
                continue
            current = item.get(candidate_key)
            if not isinstance(current, list):
                current = [item.get("url")]
            for url in candidates:
                if url and url not in current:
                    current.append(url)
            item[candidate_key] = [url for url in current if url]

    @staticmethod
    def _tv_formats(dash: dict[str, Any]) -> list[dict[str, Any]]:
        support = {
            int(item.get("quality") or 0): item
            for item in dash.get("support_formats") or []
        }
        headers = {
            "User-Agent": TV_STREAM_USER_AGENT,
        }
        formats: list[dict[str, Any]] = []
        by_id: dict[str, dict[str, Any]] = {}
        videos = list(dash.get("video") or [])
        audio_groups = [
            ("", "m4a", list(dash.get("audio") or [])),
            ("dolby-", "m4a", list((dash.get("dolby") or {}).get("audio") or [])),
            ("flac-", "flac", list((dash.get("flac") or {}).get("audio") or [])),
        ]
        for item in videos:
            candidates = YtDlpRunner._tv_url_candidates(item)
            if not candidates:
                continue
            quality = int(item.get("id") or 0)
            codecs = str(item.get("codecs") or "")
            lowered = codecs.lower()
            if "av01" in lowered:
                codec = "av1"
            elif "hvc" in lowered or "hev" in lowered:
                codec = "hevc"
            else:
                codec = "avc"
            format_id = f"tv{quality}-{codec}"
            existing = by_id.get(format_id)
            if existing is not None:
                for url in candidates:
                    if url not in existing["_tv_candidates"]:
                        existing["_tv_candidates"].append(url)
                continue
            info = support.get(quality) or {}
            note = str(
                info.get("display_desc")
                or info.get("new_description")
                or info.get("description")
                or ""
            )
            dynamic_range = (
                YtDlpRunner._dynamic_range_label(note) if note else "SDR"
            )
            if dynamic_range == "any":
                dynamic_range = "SDR"
            format_info = {
                "format_id": format_id,
                "url": candidates[0],
                "_tv_candidates": candidates,
                "ext": "mp4",
                "vcodec": codecs or "none",
                "acodec": "none",
                "width": item.get("width"),
                "height": item.get("height"),
                "fps": _parse_fps(item.get("frame_rate")),
                "tbr": _parse_bandwidth(item.get("bandwidth")),
                "filesize": item.get("size"),
                "format_note": note,
                    "dynamic_range": dynamic_range,
                "http_headers": headers,
            }
            by_id[format_id] = format_info
            formats.append(format_info)
        for prefix, ext, items in audio_groups:
            for item in items:
                audio_id = str(item.get("id") or "")
                candidates = YtDlpRunner._tv_url_candidates(item)
                if not audio_id or not candidates:
                    continue
                format_id = f"tva{prefix}{audio_id}"
                existing = by_id.get(format_id)
                if existing is not None:
                    for url in candidates:
                        if url not in existing["_tv_candidates"]:
                            existing["_tv_candidates"].append(url)
                    continue
                format_info = {
                    "format_id": format_id,
                    "url": candidates[0],
                    "_tv_candidates": candidates,
                    "ext": ext,
                    "vcodec": "none",
                    "acodec": str(item.get("codecs") or "none"),
                    "tbr": _parse_bandwidth(item.get("bandwidth")),
                    "filesize": item.get("size"),
                    "format_note": "Dolby" if prefix == "dolby-" else ("FLAC" if prefix == "flac-" else "Audio"),
                    "http_headers": headers,
                }
                by_id[format_id] = format_info
                formats.append(format_info)
        return formats

    async def _prepare_tv_formats(
        self, formats: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return await self._rank_cdn_candidates(
            formats, candidate_key="_tv_candidates", drop_unavailable=True
        )

    async def _rank_cdn_candidates(
        self,
        formats: list[dict[str, Any]],
        *,
        candidate_key: str,
        drop_unavailable: bool,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        if not formats or not self.settings.tv_cdn_probe_enabled:
            return formats
        selected = formats
        if max_items is not None and len(formats) > max_items:
            ranked = sorted(
                formats,
                key=lambda item: (
                    item.get("height") or 0,
                    item.get("tbr") or 0,
                    item.get("fps") or 0,
                ),
                reverse=True,
            )
            selected = ranked[:max_items]
            selected.extend(
                item
                for item in formats
                if item not in selected
                and str(item.get("acodec") or "none") != "none"
            )
        semaphore = asyncio.Semaphore(self.settings.cdn_probe_connections)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.cdn_probe_timeout_seconds, connect=2.5),
            follow_redirects=True,
        ) as client:
            async def choose(item: dict[str, Any]) -> bool:
                current = item.get(candidate_key)
                if not isinstance(current, list):
                    current = [item.get("url")]
                candidates = [url for url in current if isinstance(url, str) and url]
                deduped: list[str] = []
                for url in candidates:
                    if url not in deduped:
                        deduped.append(url)
                original = item.get("url") or (deduped[0] if deduped else None)
                if not deduped:
                    return False
                headers = dict(item.get("http_headers") or {})

                async def probe(url: str) -> float | None:
                    async with semaphore:
                        return await self._probe_tv_url(client, url, headers)

                results = await asyncio.gather(*(probe(url) for url in deduped))
                speeds: list[tuple[str, float]] = []
                for url, speed in zip(deduped, results):
                    if isinstance(speed, bool):
                        speed = 1.0 if speed else None
                    if speed is not None:
                        speeds.append((url, float(speed)))
                if not speeds:
                    if drop_unavailable:
                        return False
                    item[candidate_key] = deduped
                    return True
                speeds.sort(key=lambda pair: pair[1], reverse=True)
                ordered = [url for url, _ in speeds]
                if original and original not in ordered:
                    ordered.append(original)
                for url in deduped:
                    if url not in ordered:
                        ordered.append(url)
                item["url"] = ordered[0]
                item[candidate_key] = ordered
                return True

            available = await asyncio.gather(*(choose(item) for item in selected))
        if not drop_unavailable:
            return formats
        available_ids = {
            id(item) for item, ok in zip(selected, available) if ok
        }
        return [item for item in formats if id(item) in available_ids]

    async def _probe_tv_url(
        self, client: httpx.AsyncClient, url: str, headers: dict[str, str]
    ) -> float | None:
        return await self._probe_url_speed(
            client,
            url,
            headers,
            sample_bytes=self.settings.cdn_probe_sample_bytes,
            timeout=self.settings.cdn_probe_timeout_seconds,
        )

    @staticmethod
    async def _probe_url_speed(
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        *,
        sample_bytes: int,
        timeout: float,
    ) -> float | None:
        started = time.perf_counter()
        received = 0
        probe_headers = dict(headers)
        probe_headers["Range"] = f"bytes=0-{max(0, sample_bytes - 1)}"
        try:
            async with client.stream("GET", url, headers=probe_headers) as response:
                if response.status_code not in {200, 206}:
                    return None
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if (
                        received >= sample_bytes
                        or time.perf_counter() - started >= timeout
                    ):
                        break
        except (httpx.HTTPError, OSError, asyncio.TimeoutError):
            return None
        elapsed = max(0.001, time.perf_counter() - started)
        if received < 1024:
            return None
        return received / elapsed

    @staticmethod
    def _fallback_selectors(selector: str) -> list[str]:
        selector = selector.strip()
        if "+" not in selector and not selector.startswith(("bv*", "b")):
            return []
        return [
            "bv*[dynamic_range=SDR]+ba/b",
            "bv*[height<=1080]+ba/b",
        ]

    @staticmethod
    def _tv_fallback_selectors(info: dict[str, Any]) -> list[str]:
        formats = list(info.get("formats") or [])
        videos = sorted(
            (
                item
                for item in formats
                if str(item.get("vcodec") or "none") != "none"
            ),
            key=lambda item: (item.get("height") or 0, item.get("tbr") or 0),
            reverse=True,
        )
        audio = max(
            (
                item
                for item in formats
                if str(item.get("acodec") or "none") != "none"
            ),
            key=lambda item: item.get("tbr") or 0,
            default=None,
        )
        audio_id = str((audio or {}).get("format_id") or "ba")
        selectors: list[str] = []
        for video in videos:
            selector = f"{video['format_id']}+{audio_id}"
            if selector not in selectors:
                selectors.append(selector)
        return selectors[:8]

    @staticmethod
    def _should_fallback(message: str) -> bool:
        return YtDlpRunner._is_cdn_failure(message) or (
            "requested format is not available" in message.lower()
        )

    def _aria2_connection_plan(self) -> tuple[int, ...]:
        attempts = 1 + self.settings.aria2_resume_retries
        connections = min(
            max(1, self.settings.aria2_connections),
            ARIA2_STABLE_MAX_CONNECTIONS,
        )
        plan: list[int] = []
        for _ in range(attempts):
            plan.append(max(1, connections))
            if connections <= ARIA2_STABLE_FLOOR_CONNECTIONS:
                connections = max(1, connections - 1)
            else:
                connections = max(
                    ARIA2_STABLE_FLOOR_CONNECTIONS,
                    connections - 2,
                )
        return tuple(plan)

    def _aria2_args(self, connections: int | None = None) -> str:
        connections = connections or self.settings.aria2_connections
        return (
            "aria2c:--max-concurrent-downloads=1 "
            f"--max-connection-per-server={connections} --split={connections} "
            f"--min-split-size={self.settings.aria2_min_split_size} "
            f"--piece-length={self.settings.aria2_piece_size} "
            "--max-tries=5 --retry-wait=1 --connect-timeout=15 --timeout=30 "
            "--summary-interval=1 --show-console-readout=true"
        )

    async def download(
        self,
        job: dict[str, Any],
        output_root: Path,
        log_path: Path,
        progress_callback: ProgressCallback,
    ) -> str | None:
        self._begin_operation()
        try:
            return await self._download(job, output_root, log_path, progress_callback)
        except BaseException:
            self.completion_paths.pop(job['id'], None)
            self.completion_origins.pop(job['id'], None)
            self.completion_item_origins.pop(job['id'], None)
            raise
        finally:
            self._end_operation()

    async def _download(
        self,
        job: dict[str, Any],
        output_root: Path,
        log_path: Path,
        progress_callback: ProgressCallback,
    ) -> str | None:
        self.cancel_requested = False
        api_mode = job.get("api_mode") or "web"
        cookie_path: Path | None = None
        info_path: Path | None = None
        info_payload: dict[str, Any] | None = None
        cdn_warning: str | None = None
        if api_mode == "tv":
            info_payload = await self._tv_download_info(job["source_url"], job)
            info_path = (
                self.settings.runtime_dir
                / f"tv-info-{secrets.token_hex(12)}.json"
            )
            self._write_info_json(info_path, info_payload)
        elif api_mode == "web":
            cookie_path = self._create_cookie_file(required=False)
            if not job.get("all_parts"):
                try:
                    info_payload = await self._web_download_info(
                        job["source_url"], cookie_path
                    )
                    if info_payload:
                        info_path = (
                            self.settings.runtime_dir
                            / f"web-info-{secrets.token_hex(12)}.json"
                        )
                        self._write_info_json(info_path, info_payload)
                except (DownloadFailure, BiliAPIError) as exc:
                    cdn_warning = f"CDN 测速未启用，使用 yt-dlp 默认线路：{exc}"
        else:
            raise DownloadFailure("下载线路无效")

        self.completion_paths[job["id"]] = []
        self.completion_item_origins[job["id"]] = {}
        self.completion_origins.pop(job["id"], None)
        if info_payload and info_payload.get("_v2_origin"):
            self.completion_origins[job["id"]] = info_payload["_v2_origin"]

        def build_command(
            selector: str,
            use_aria2: bool,
            connections: int | None = None,
        ) -> list[str]:
            command = [
                *self.settings.ytdlp_command,
                "--newline",
                "--color",
                "never",
                "--no-write-info-json",
                "--no-write-subs",
                "--no-write-auto-subs",
                "--no-overwrites",
                "--retries",
                "20",
                "--file-access-retries",
                "10",
                "--retry-sleep",
                "1",
                "--http-chunk-size",
                "4M",
                "--paths",
                f"home:{output_root}",
                "--output",
                job["output_template"],
                "--format",
                selector,
                "--progress-template",
                (
                    "download:__PROGRESS__%(progress._percent_str)s|"
                    "%(progress.downloaded_bytes)s|"
                    "%(progress.total_bytes)s|"
                    "%(progress.total_bytes_estimate)s|"
                    "%(progress.speed)s|%(progress.eta)s"
                ),
                "--print",
                "before_dl:__TITLE__%(title)s",
                "--print",
                "before_dl:__UPLOADER__%(uploader,creator,channel,uploader_id)s",
                "--no-quiet",
                "--print",
                "after_move:__RESULT__%(filepath)s",
                "--print",
                "after_move:__V2_ORIGIN__%(.{filepath,bvid,cid,season_id,episode_id,series,title,episode_number,playlist_index})j",
            ]
            if use_aria2:
                command.extend(
                    [
                        "--downloader",
                        "aria2c",
                        "--downloader-args",
                        self._aria2_args(connections),
                    ]
                )
            if info_path:
                command.extend(["--load-info-json", str(info_path)])
                if cookie_path:
                    command.extend(["--cookies", str(cookie_path)])
            else:
                if cookie_path:
                    command.extend(["--cookies", str(cookie_path)])
                if not job.get("all_parts"):
                    command.append("--no-playlist")
                command.append(job["source_url"])
            return command

        if self.cancel_requested:
            if info_path:
                self._remove_file(info_path)
            if cookie_path:
                self._remove_file(cookie_path)
            raise CancelledFailure("任务已取消")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if api_mode == "tv" and info_payload:
            fallback_selectors = self._tv_fallback_selectors(info_payload)
        else:
            fallback_selectors = self._fallback_selectors(
                job["format_selector"]
            )
        selectors = [job["format_selector"], *fallback_selectors]
        selectors = [
            selector
            for index, selector in enumerate(selectors)
            if selector and selector not in selectors[:index]
        ]
        connection_plan = self._aria2_connection_plan()
        attempts: list[tuple[str, bool, int | None]] = []
        for selector in selectors:
            if self.aria2_executable:
                attempts.extend(
                    (selector, True, connections)
                    for connections in connection_plan
                )
            attempts.append((selector, False, None))
        try:
            attempt_messages: list[str] = []
            result_path: str | None = None
            errors: list[str] = []
            previous_selector: str | None = None
            aria2_partial_file: Path | None = None
            with log_path.open("a", encoding="utf-8") as log_file:
                if self.aria2_executable:
                    connection_plan_text = " → ".join(
                        f"x{connections}" for connections in connection_plan
                    )
                    log_file.write(
                        f"\n[{utc_now()}] aria2c 多连接下载已启用："
                        f"配置 {self.settings.aria2_connections} 连接，"
                        f"当前 {connection_plan[0]} 连接，"
                        f"分片 {self.settings.aria2_piece_size}，"
                        f"最小分片 {self.settings.aria2_min_split_size}，"
                        f"断点续传重试 {self.settings.aria2_resume_retries} 次；"
                        f"CDN 限流时按 {connection_plan_text} 自动降档\n"
                    )
                elif self.settings.aria2_enabled:
                    log_file.write(
                        f"\n[{utc_now()}] 未检测到 aria2c，使用 yt-dlp 原生下载\n"
                    )
                if cdn_warning:
                    log_file.write(f"[{utc_now()}] {cdn_warning}\n")
                log_file.flush()
            for index, (selector, use_aria2, connections) in enumerate(attempts):
                if (
                    index > 0
                    and info_payload
                    and info_path
                    and selector == previous_selector
                    and attempt_messages
                    and self._should_rotate_cdn(attempt_messages[-1])
                    and self._advance_cdn_info(info_payload)
                ):
                    self._remove_aria2_partial(aria2_partial_file, output_root)
                    aria2_partial_file = None
                    self._write_info_json(info_path, info_payload)
                    with log_path.open("a", encoding="utf-8") as log_file:
                        next_engine = (
                            f"aria2c x{connections}"
                            if use_aria2
                            else "yt-dlp 原生下载"
                        )
                        log_file.write(
                            f"\n[{utc_now()}] CDN 节点发生不可恢复错误，"
                            "已切换到下一个备用节点；"
                            f"下一次尝试使用 {next_engine}\n"
                        )
                        log_file.flush()
                if selector != previous_selector:
                    aria2_partial_file = None
                    if previous_selector is not None:
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(
                                f"\n[{utc_now()}] 首选格式下载失败，"
                                f"自动回退到 {selector}\n"
                            )
                            log_file.flush()
                        await progress_callback(
                            {
                                "format_selector": selector,
                                "progress": 0,
                                "downloaded_bytes": 0,
                            }
                        )
                    else:
                        await progress_callback({"format_selector": selector})
                    previous_selector = selector
                elif use_aria2 and attempts[index - 1][1]:
                    with log_path.open("a", encoding="utf-8") as log_file:
                        log_file.write(
                            f"\n[{utc_now()}] aria2c 下载失败，"
                            "使用断点续传重试"
                            f"（连接数调整为 x{connections}）\n"
                        )
                        log_file.flush()
                elif not use_aria2 and attempts[index - 1][1]:
                    with log_path.open("a", encoding="utf-8") as log_file:
                        log_file.write(
                            f"\n[{utc_now()}] aria2c 连续中断，"
                            "已清理临时分片，"
                            "回退到 yt-dlp 原生下载\n"
                        )
                        log_file.flush()

                command = build_command(selector, use_aria2, connections)
                result_path = None
                errors = []
                with log_path.open("a", encoding="utf-8") as log_file:
                    engine = (
                        f"aria2c x{connections}"
                        if use_aria2
                        else "yt-dlp native"
                    )
                    log_file.write(
                        f"\n[{utc_now()}] Starting job {job['id']} "
                        f"with format {selector} via {engine}\n"
                    )
                    log_file.flush()
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        start_new_session=os.name == "posix",
                    )
                    self.current_process = process

                    async def consume(
                        stream: asyncio.StreamReader | None, is_stderr: bool
                    ) -> None:
                        nonlocal result_path, aria2_partial_file
                        if stream is None:
                            return
                        while True:
                            raw = await stream.readline()
                            if not raw:
                                break
                            line = raw.decode("utf-8", errors="replace").rstrip()
                            clean_line = redact_sensitive(line)
                            log_file.write(clean_line + "\n")
                            log_file.flush()
                            if line.startswith("__PROGRESS__"):
                                update = self._parse_progress(line)
                                if update:
                                    await progress_callback(update)
                                continue
                            if line.startswith("__TITLE__"):
                                title = line.removeprefix("__TITLE__").strip()
                                if title and title != "NA":
                                    await progress_callback({"title": title})
                                continue
                            if line.startswith("__UPLOADER__"):
                                uploader = line.removeprefix("__UPLOADER__").strip()
                                if uploader and uploader != "NA":
                                    await progress_callback({"uploader": uploader})
                                continue
                            if line.startswith("__V2_ORIGIN__"):
                                self.remember_completion_item(job['id'], line.removeprefix('__V2_ORIGIN__'))
                                continue
                            if line.startswith("__RESULT__"):
                                result_path = line.removeprefix("__RESULT__").strip()
                                if result_path:
                                    paths = self.completion_paths[job["id"]]
                                    if result_path not in paths:
                                        paths.append(result_path)
                                    await progress_callback(
                                        {"result_path": result_path}
                                    )
                                continue
                            if use_aria2 and line.startswith("FILE:"):
                                aria2_partial_file = Path(
                                    line.removeprefix("FILE:").strip()
                                )
                                continue
                            aria_update = self._parse_aria2_progress(line)
                            if aria_update:
                                await progress_callback(aria_update)
                                continue
                            if is_stderr:
                                errors.append(clean_line)

                    await asyncio.gather(
                        consume(process.stdout, False),
                        consume(process.stderr, True),
                    )
                    return_code = await process.wait()
                    self.current_process = None

                if self.cancel_requested:
                    raise CancelledFailure("任务已取消")
                if return_code == 0:
                    return result_path

                message = redact_sensitive("\n".join(errors[-20:]).strip())
                attempt_messages.append(message or f"状态码 {return_code}")
                if self._looks_like_auth_error(message):
                    self._remove_aria2_partial(aria2_partial_file, output_root)
                    break
                if use_aria2:
                    next_is_aria2 = (
                        index + 1 < len(attempts)
                        and attempts[index + 1][0] == selector
                        and attempts[index + 1][1]
                    )
                    if next_is_aria2:
                        continue
                    self._remove_aria2_partial(aria2_partial_file, output_root)
                    aria2_partial_file = None
                    continue
                if not self._should_fallback(message) or index == len(attempts) - 1:
                    break

            combined = "\n".join(attempt_messages)
            if api_mode == "tv" and (
                "HTTP Error 403" in combined
                or "403: Forbidden" in combined
                or "403 Forbidden" in combined
            ):
                raise DownloadFailure(TV_CDN_BLOCKED_MESSAGE)
            if api_mode == "tv" and "Requested format is not available" in combined:
                raise DownloadFailure(
                    "所选 TV 格式当前不可用，可能是片源 CDN 变化或格式已下线；"
                    "请重新解析并选择其他格式，或使用默认格式。"
                )
            raise DownloadFailure(
                attempt_messages[-1] if attempt_messages else "yt-dlp 下载失败",
                auth_required=self._looks_like_auth_error(combined),
            )
        finally:
            self.current_process = None
            if cookie_path:
                self._remove_file(cookie_path)
            if info_path:
                self._remove_file(info_path)

    async def cancel(self) -> None:
        self.cancel_requested = True
        process = self.current_process
        if not process or process.returncode is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            await asyncio.wait_for(process.wait(), timeout=8)
        except (ProcessLookupError, TimeoutError):
            try:
                process.kill()
            except ProcessLookupError:
                pass

    def _create_cookie_file(self, required: bool = False) -> Path | None:
        cookies = self.auth_service.load_cookies()
        if not cookies:
            if required:
                raise DownloadFailure(
                    "需要登录 B 站后才能继续", auth_required=True
                )
            return None
        path = self.settings.runtime_dir / f"cookies-{secrets.token_hex(12)}.txt"
        self.auth_service.write_cookie_file(path, cookies)
        return path

    @staticmethod
    def _remove_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    @classmethod
    def _remove_aria2_partial(cls, path: Path | None, output_root: Path) -> None:
        if path is None:
            return
        try:
            partial = path.resolve()
            if not partial.is_relative_to(output_root.resolve()):
                return
            cls._remove_file(partial)
            cls._remove_file(partial.with_name(partial.name + ".aria2"))
        except OSError:
            pass

    @staticmethod
    def _is_favorite_url(url: str) -> bool:
        parsed = urlsplit(url)
        return "favlist" in parsed.path or "/medialist/detail/" in parsed.path

    @staticmethod
    def _is_bangumi_url(url: str) -> bool:
        return bool(
            re.fullmatch(
                r"/bangumi/play/(?:ep|ss)\d+/?", urlsplit(url).path, re.I
            )
        )

    @staticmethod
    def _bangumi_ids(url: str) -> tuple[str, str]:
        path = urlsplit(url).path
        episode = re.fullmatch(r"/bangumi/play/ep(\d+)/?", path, re.I)
        if episode:
            return "ep", episode.group(1)
        season = re.fullmatch(r"/bangumi/play/ss(\d+)/?", path, re.I)
        if season:
            return "ss", season.group(1)
        raise DownloadFailure("无法识别番剧链接")

    @staticmethod
    def _video_id(url: str) -> str | None:
        match = re.search(r"/(?:video/)?(BV[A-Za-z0-9]+)", url, re.I)
        return match.group(1) if match else None

    @staticmethod
    def _clean_error(message: str) -> str:
        lines = [
            redact_sensitive(line.strip())
            for line in message.splitlines()
            if line.strip() and not line.startswith("[debug]")
        ]
        return lines[-1] if lines else ""

    @staticmethod
    def _looks_like_auth_error(message: str) -> bool:
        lowered = message.lower()
        patterns = (
            "login",
            "login required",
            "log in",
            "logged in",
            "authentication",
            "cookies are no longer valid",
            "private favorites",
            "需要登录",
            "请先登录",
            "大会员",
            "会员专享",
            "账号未登录",
        )
        return any(pattern in lowered for pattern in patterns)

    @staticmethod
    def _parse_progress(line: str) -> dict[str, Any] | None:
        parts = line.removeprefix("__PROGRESS__").split("|")
        if len(parts) not in {5, 6}:
            return None
        percent_text = parts[0].replace("%", "").strip()
        downloaded = _parse_int(parts[1]) or 0
        if len(parts) == 6:
            total = _parse_int(parts[2]) or _parse_int(parts[3])
            speed = _parse_float(parts[4])
            eta = _parse_int(parts[5])
        else:
            total = _parse_int(parts[2])
            speed = _parse_float(parts[3])
            eta = _parse_int(parts[4])
        percent = _parse_float(percent_text)
        if percent is None and total:
            percent = min(100.0, max(0.0, downloaded * 100.0 / total))
        return {
            "progress": percent or 0.0,
            "downloaded_bytes": downloaded,
            "total_bytes": total,
            "speed": speed,
            "eta": eta,
        }

    @staticmethod
    def _parse_aria2_progress(line: str) -> dict[str, Any] | None:
        match = re.search(
            r"\[#\S+\s+([\d.]+)\s*([KMGTP]?i?B)/"
            r"([\d.]+)\s*([KMGTP]?i?B)\s*\((\d+(?:\.\d+)?)%\)",
            line,
            re.I,
        )
        if not match:
            return None
        factors = {
            "B": 1,
            "KIB": 1024,
            "MIB": 1024**2,
            "GIB": 1024**3,
            "TIB": 1024**4,
            "PIB": 1024**5,
            "KB": 1000,
            "MB": 1000**2,
            "GB": 1000**3,
            "TB": 1000**4,
            "PB": 1000**5,
        }
        downloaded = int(
            float(match.group(1)) * factors[match.group(2).upper()]
        )
        total = int(float(match.group(3)) * factors[match.group(4).upper()])
        percent = _parse_float(match.group(5))
        speed_match = re.search(r"DL:([\d.]+)\s*([KMGTP]?i?B)", line, re.I)
        speed = (
            float(speed_match.group(1)) * factors[speed_match.group(2).upper()]
            if speed_match
            else None
        )
        eta = None
        eta_match = re.search(r"ETA:([^\]\s]+)", line, re.I)
        if eta_match:
            parts = re.findall(r"(\d+)([hms])", eta_match.group(1).lower())
            if parts:
                multipliers = {"h": 3600, "m": 60, "s": 1}
                eta = sum(int(value) * multipliers[unit] for value, unit in parts)
        return {
            "progress": percent or 0.0,
            "downloaded_bytes": downloaded,
            "total_bytes": total,
            "speed": speed,
            "eta": eta,
        }

    @staticmethod
    def _normalize_formats(formats: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized_formats: list[dict[str, Any]] = []
        for item in formats:
            format_id = str(item.get("format_id") or "")
            if not format_id or item.get("has_drm"):
                continue
            acodec = item.get("acodec")
            selector = format_id if acodec and acodec != "none" else f"{format_id}+ba"
            height = item.get("height")
            width = item.get("width")
            try:
                height_value = int(height or 0)
            except (TypeError, ValueError):
                height_value = 0
            if height_value and height_value < MIN_FORMAT_HEIGHT:
                continue
            resolution = item.get("resolution") or (
                f"{width}x{height}" if width and height else "audio only"
            )
            normalized_formats.append(
                {
                    "format_id": format_id,
                    "selector": selector,
                    "ext": item.get("ext"),
                    "resolution": resolution,
                    "width": width,
                    "height": height,
                    "fps": item.get("fps"),
                    "vcodec": item.get("vcodec"),
                    "acodec": acodec,
                    "dynamic_range": item.get("dynamic_range"),
                    "format_note": item.get("format_note"),
                    "filesize": item.get("filesize") or item.get("filesize_approx"),
                    "tbr": item.get("tbr"),
                }
            )
        normalized_formats.sort(
            key=lambda item: (
                item.get("height") or 0,
                item.get("tbr") or 0,
                item.get("fps") or 0,
            ),
            reverse=True,
        )
        return normalized_formats

    @staticmethod
    def _normalize_resolved(payload: dict[str, Any], *, is_favorite: bool) -> ResolvedMedia:
        entries = payload.get("entries") or []
        formats = payload.get("formats") or []
        title = payload.get("title") or payload.get("playlist_title") or "未命名视频"
        uploader = (
            payload.get("uploader")
            or payload.get("creator")
            or payload.get("channel")
            or payload.get("uploader_id")
            or ""
        )
        return ResolvedMedia(
            title=str(title),
            uploader=str(uploader),
            duration=payload.get("duration"),
            webpage_url=payload.get("webpage_url"),
            is_playlist=bool(payload.get("_type") == "playlist" or is_favorite),
            playlist_count=payload.get("playlist_count") or len(entries) or None,
            formats=YtDlpRunner._normalize_formats(formats)[:100],
            episodes=[],
            default_episode_id=None,
            api_mode="web",
        )


class DownloadManager:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        runner: YtDlpRunner,
    ) -> None:
        self.settings = settings
        self.database = database
        self.runner = runner
        self.completion_hook = None
        self.current_job_id: int | None = None
        self._wake = asyncio.Event()
        self._stopping = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if not self._task:
            self._task = asyncio.create_task(self._run_loop(), name="download-manager")

    async def stop(self) -> None:
        self._stopping = True
        await self.runner.cancel()
        self._wake.set()
        if self._task:
            await self._task
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    async def cancel_job(self, job_id: int) -> bool:
        job = self.database.get_job(job_id)
        if not job or job["status"] not in {"queued", "running"}:
            return False
        if job["status"] == "queued":
            self.database.update_job(job_id, status="cancelled", finished_at=utc_now())
            return True
        if self.current_job_id == job_id:
            await self.runner.cancel()
            return True
        return False

    async def _run_loop(self) -> None:
        while not self._stopping:
            job = self.database.next_queued_job()
            if not job:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=2)
                except TimeoutError:
                    pass
                self._wake.clear()
                continue
            if not self.database.claim_job(job["id"]):
                continue
            self.current_job_id = job["id"]
            job = self.database.get_job(job["id"]) or job
            try:
                if job.get("kind") != "manual":
                    self.database.update_job(
                        job["id"],
                        status="cancelled",
                        error="收藏夹追更功能已停用，请手动创建下载任务",
                        finished_at=utc_now(),
                    )
                    continue
                await self._execute_download(job)
            except CancelledFailure:
                self.database.update_job(
                    job["id"], status="cancelled", finished_at=utc_now()
                )
            except DownloadFailure as exc:
                status = "auth_required" if exc.auth_required else "failed"
                self.database.update_job(
                    job["id"],
                    status=status,
                    error=str(exc)[:4000],
                    finished_at=utc_now(),
                )
            except Exception as exc:
                message = redact_sensitive(f"内部错误: {exc}")
                self.database.update_job(
                    job["id"],
                    status="failed",
                    error=message[:4000],
                    finished_at=utc_now(),
                )
            finally:
                self.current_job_id = None

    async def _execute_download(self, job: dict[str, Any]) -> None:
        output_root = self.settings.download_root
        if job.get("output_dir"):
            configured_output = Path(job["output_dir"])
            output_root = (
                configured_output
                if configured_output.is_absolute()
                else output_root / configured_output
            )
        log_path = (
            Path(job["log_path"])
            if job.get("log_path")
            else self.settings.logs_dir / f"job-{job['id']}.log"
        )
        last_progress_write = 0.0
        progress_fields = {
            "progress",
            "downloaded_bytes",
            "total_bytes",
            "speed",
            "eta",
        }

        async def update(values: dict[str, Any]) -> None:
            nonlocal last_progress_write
            clean = {key: value for key, value in values.items() if value is not None}
            if not clean:
                return
            if set(clean) <= progress_fields:
                now = asyncio.get_running_loop().time()
                final_progress = float(clean.get("progress") or 0) >= 100
                if not final_progress and now - last_progress_write < 1.0:
                    return
                last_progress_write = now
            self.database.update_job(job["id"], **clean)

        result = await self.runner.download(job, output_root, log_path, update)
        self.database.update_job(
            job["id"],
            status="completed",
            progress=100,
            result_path=result,
            finished_at=utc_now(),
        )
        if self.completion_hook:
            try:
                await asyncio.wait_for(self.completion_hook(self.database.get_job(job["id"])), timeout=30)
            except Exception:
                # Recognition is optional; the completed download remains completed.
                logging.getLogger(__name__).warning("V2 recognition unavailable for completed download %s", job["id"])



class MaintenanceScheduler:
    def __init__(self, settings: Settings, database: Database) -> None:
        self.settings = settings
        self.database = database
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._last_cleanup: datetime | None = None
        self._wake = asyncio.Event()

    def start(self) -> None:
        if not self._task:
            self._task = asyncio.create_task(self._loop(), name="maintenance-scheduler")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task:
            await self._task
            self._task = None

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                self._cleanup_logs()
            except Exception:
                # Maintenance failures must not stop the service.
                pass
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=3600)
            except TimeoutError:
                pass
            self._wake.clear()

    def _cleanup_logs(self) -> None:
        now = datetime.now(UTC)
        if self._last_cleanup and now - self._last_cleanup < timedelta(hours=24):
            return
        self._last_cleanup = now
        cutoff = (now - timedelta(days=self.settings.log_retention_days)).replace(
            microsecond=0
        ).isoformat()
        for raw_path in self.database.old_log_paths(cutoff):
            try:
                Path(raw_path).unlink(missing_ok=True)
            except OSError:
                pass


def create_job_log_path(settings: Settings) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    token = secrets.token_hex(4)
    return settings.logs_dir / f"job-{stamp}-{token}.log"
