"""Browser-resolved Web DASH catalogues and exact-track download handoff."""

import io
import json
import re
import secrets
import time
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .naming import NameOptions, download_plan, episode_display_title, episode_facts, url_facts

MAX_BODY = 12 * 1024 * 1024
MAX_IMPORTS = 24
IMPORT_TTL = 4 * 3600
SOURCE = re.compile(r"https://www\.bilibili\.com/(?:bangumi/play/ep[0-9]{1,20}|video/(?:BV[A-Za-z0-9]{6,20}|av[0-9]{1,20})(?:\?p=[1-9][0-9]{0,4})?)")
CDN = re.compile(r"[a-z0-9][a-z0-9.-]*\.bilivideo\.(?:com|cn|net)")
TRACK_PATH = re.compile(r"/[A-Za-z0-9_/-]+\.(?:m4s|mp4)")
HEADERS = {"Referer": "https://www.bilibili.com/", "User-Agent": "Mozilla/5.0"}
EXTENSION_DIR = Path(__file__).resolve().parents[2] / "static/v2/download/assistant"
EXTENSION_FILES = (
    "manifest.json", "popup.html", "popup.js", "bilibili.js", "picker.js", "assistant.css",
    "quality-format.js", "tv.js", "md5.min.js", "md5.LICENSE.txt", "qrcode.mjs", "qrcode.LICENSE.txt",
)


def stream_deadline(value: str) -> int | None:
    query = parse_qs(urlsplit(value).query)
    deadline = query.get("deadline", [""])[0]
    return int(deadline) if re.fullmatch(r"[0-9]{1,11}", deadline) else None


class Track(BaseModel):
    url: str = Field(max_length=2048)
    format_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    dynamic_range: Literal["SDR", "HDR10", "DV", "HLG", "HDR10+"] | None = None
    quality: int = Field(ge=1, le=99999)
    codec: str = Field(pattern=r"^(?:avc[13]|hev1|hvc1|av01|mp4a|ec-3|ac-3|fLaC|flac|opus)(?:\.[A-Za-z0-9.]{1,48})?$")
    bandwidth: int = Field(ge=0, le=200_000_000)
    width: int = Field(default=0, ge=0, le=16384)
    height: int = Field(default=0, ge=0, le=16384)
    fps: float | None = Field(default=None, gt=0, le=300)
    size: int | None = Field(default=None, gt=0, le=300_000_000_000)

    @field_validator("url")
    @classmethod
    def allowed_media(cls, value):
        if any(ord(c) <= 32 or ord(c) >= 127 for c in value) or "\\" in value:
            raise ValueError("非法播放地址")
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not CDN.fullmatch(parsed.netloc)
                or not TRACK_PATH.fullmatch(parsed.path) or parsed.fragment):
            raise ValueError("播放地址不是受支持的 B站 CDN 轨道")
        if any(key.lower() in {"access_key", "access_token", "refresh_token", "sessdata"}
               for key in parse_qs(parsed.query, keep_blank_values=True)):
            raise ValueError("播放地址不得携带登录凭据")
        return value


class Episode(BaseModel):
    url: str = Field(max_length=120)
    title: str = Field(min_length=1, max_length=300)
    number: int | None = Field(default=None, gt=0, le=99999)
    duration: int | None = Field(default=None, gt=0, le=86400)
    video: list[Track] = Field(default_factory=list, max_length=40)
    audio: list[Track] = Field(default_factory=list, max_length=10)

    @field_validator("url")
    @classmethod
    def allowed_episode(cls, value):
        if not SOURCE.fullmatch(value):
            raise ValueError("不支持的 B站正片地址")
        return value

    @model_validator(mode="after")
    def track_types(self):
        if any(not track.height or not track.codec.startswith(("avc", "hev", "hvc", "av01")) for track in self.video):
            raise ValueError("视频轨参数无效")
        if any(track.height or not track.codec.lower().startswith(("mp4a", "ec-3", "ac-3", "flac", "opus")) for track in self.audio):
            raise ValueError("音轨参数无效")
        return self


class Catalogue(BaseModel):
    source: Literal["web", "tv"] = "web"
    movie: bool = False
    title: str = Field(min_length=1, max_length=300)
    episodes: list[Episode] = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def unique_urls(self):
        if len({ep.url for ep in self.episodes}) != len(self.episodes):
            raise ValueError("解析列表中有重复剧集")
        return self


class Pick(BaseModel):
    episode: int = Field(ge=0, lt=600)
    video: int | None = Field(ge=0, lt=40)
    audio: int = Field(ge=0, lt=10)


class Picks(BaseModel):
    choices: list[Pick] = Field(min_length=1, max_length=600)


class Direct(Picks):
    catalogue: Catalogue


def presentation(catalogue, queued):
    return {
        "title": catalogue.title,
        "source": catalogue.source,
        "movie": catalogue.movie,
        "episodes": [
            {
                **ep.model_dump(exclude={"video", "audio"}),
                "video": [track.model_dump(exclude={"url"}) for track in ep.video],
                "audio": [track.model_dump(exclude={"url"}) for track in ep.audio],
            }
            for ep in catalogue.episodes
        ],
        "queued": [list(key) for key in queued],
    }


def playback_info(title, display_title, ep, video, audio, source):
    headers = HEADERS if source == "web" else {
        **HEADERS, "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/134.0.0.0 Mobile Safari/537.36",
    }
    formats = []
    for track_id, track in (("brvideo", video), ("braudio", audio)):
        if track is None:
            continue
        is_video = track_id == "brvideo"
        formats.append({
            "format_id": track_id, "url": track.url,
            "ext": "mp4" if is_video else ("flac" if track.codec.lower() == "flac" else "m4a"),
            "vcodec": track.codec if is_video else "none",
            "acodec": "none" if is_video else track.codec,
            "width": track.width or None, "height": track.height or None,
            "fps": track.fps, "tbr": track.bandwidth / 1000,
            "filesize": track.size, "http_headers": headers,
            "dynamic_range": track.dynamic_range or ({125: "HDR10", 126: "DV"}.get(track.quality, "SDR") if is_video else None),
        })
    facts = url_facts(ep.url)
    return {
        "id": facts.identifier, "title": display_title, "series": title,
        "webpage_url": ep.url, "duration": ep.duration, "formats": formats,
        "_v2_origin": {
            "bvid": facts.identifier if facts.identifier.startswith("BV") else None,
            "ep_id": facts.identifier[2:] if facts.identifier.startswith("ep") else None,
            "original_title": title, "episode_index": ep.number,
            "part_number": facts.number,
        },
    }


def build_jobs(services, catalogue, payload, *, create_log_path, submitted=()):
    # Validate the full selection before committing the batch.
    jobs = []
    keys = []
    seen = set()
    titles = [{
        "id": str(i), "title": ep.title, "display_episode_number": ep.number,
        "group": "正片",
        "movie": catalogue.movie,
    } for i, ep in enumerate(catalogue.episodes)]
    names = episode_facts(titles)
    for pick in payload.choices:
        if pick.episode in seen:
            raise HTTPException(422, "同一集只能选择一个版本")
        seen.add(pick.episode)
        key = (pick.episode, pick.video, pick.audio)
        if key in submitted:
            continue
        try:
            ep = catalogue.episodes[pick.episode]
            video = ep.video[pick.video] if pick.video is not None else None
            audio = ep.audio[pick.audio]
        except IndexError as exc:
            raise HTTPException(422, "所选剧集或档位不可用，请重新解析") from exc
        deadlines = [deadline for track in (video, audio) if track
                     if (deadline := stream_deadline(track.url)) is not None]
        expires = min([int(time.time()) + IMPORT_TTL, *deadlines])
        if expires <= time.time():
            raise HTTPException(409, f"{ep.title} 的播放地址已过期，请在扩展重新解析")
        source_facts = url_facts(ep.url)
        display_title = episode_display_title(catalogue.title, ep.title, ep.number, movie=catalogue.movie,
                                             part=source_facts.number_kind == "part")
        info = playback_info(catalogue.title, display_title, ep, video, audio, catalogue.source)
        facts = names[str(pick.episode)]
        facts.identifier = source_facts.identifier
        if source_facts.number_kind == "part":
            facts.number, facts.number_kind = source_facts.number, "part"
        plan = download_plan(catalogue.title, facts, NameOptions())
        plan["display_title"] = display_title
        plan["assistant_playback"] = services.secrets.encrypt_json(json.dumps({"expires": expires, "info": info}))
        jobs.append({
            "source_url": ep.url, "kind": "manual", "api_mode": catalogue.source,
            "title": display_title, "output_template": "%(id)s.%(ext)s",
            "output_dir": services.files.resolve_new_job_output(None),
            "format_selector": "(brvideo+braudio)" if video else "(braudio)", "all_parts": False,
            "log_path": str(create_log_path(services.settings)), "naming_plan": plan,
        })
        keys.append(key)
    return jobs, keys


async def read_body(request, model):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            raise HTTPException(413, "解析列表太大，请缩小本次推送范围")
        body.extend(chunk)
    try:
        return model.model_validate_json(bytes(body))
    except ValidationError as exc:
        # Pydantic input values include signed playback URLs.
        raise HTTPException(422, "解析助手资料无效，或包含不支持的片源") from exc


def register(app, *, get_session, require_csrf, get_services, create_log_path):
    imports = {}

    def prune():
        now = time.time()
        for key, (_, expires, _) in list(imports.items()):
            if expires <= now:
                del imports[key]

    @app.get("/api/parse-assistant/package")
    def package(_: object = Depends(get_session)):
        output = io.BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            for name in EXTENSION_FILES:
                archive.write(EXTENSION_DIR / name, arcname=name)
        return Response(output.getvalue(), media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="bili-parse-assistant.zip"',
            "Cache-Control": "no-store",
        })

    @app.get("/api/parse-assistant")
    async def state(_: object = Depends(get_session)):
        prune()
        return {"version": 1, "features": ["local-tv", "audio-only", "movie-title"], "imports": [
            {"id": key, "title": catalogue.title, "count": len(catalogue.episodes), "source": catalogue.source}
            for key, (catalogue, _, _) in reversed(imports.items())
        ]}

    @app.post("/api/parse-assistant/imports")
    async def accept(request: Request, _: object = Depends(require_csrf)):
        catalogue = await read_body(request, Catalogue)
        prune()
        if len(imports) == MAX_IMPORTS:
            del imports[next(iter(imports))]
        key = secrets.token_urlsafe(16)
        imports[key] = (catalogue, time.time() + IMPORT_TTL, set())
        return {"id": key, "count": len(catalogue.episodes)}

    @app.get("/api/parse-assistant/imports/{key}")
    async def details(key: str, _: object = Depends(get_session)):
        prune()
        if key not in imports:
            raise HTTPException(404, "列表已失效，请从解析助手重新推送")
        catalogue, _, queued = imports[key]
        return presentation(catalogue, queued)

    @app.post("/api/parse-assistant/imports/{key}/jobs")
    async def enqueue(key: str, request: Request, _: object = Depends(require_csrf), services=Depends(get_services)):
        payload = await read_body(request, Picks)
        prune()
        if key not in imports:
            raise HTTPException(404, "列表已失效，请从解析助手重新推送")
        catalogue, _, queued = imports[key]
        jobs, keys = build_jobs(services, catalogue, payload, create_log_path=create_log_path, submitted=queued)
        ids = services.database.create_jobs(jobs)
        queued.update(keys)
        services.manager.wake()
        return {"ids": ids}

    @app.post("/api/parse-assistant/jobs")
    async def direct(request: Request, _: object = Depends(require_csrf), services=Depends(get_services)):
        payload = await read_body(request, Direct)
        jobs, _ = build_jobs(services, payload.catalogue, payload, create_log_path=create_log_path)
        ids = services.database.create_jobs(jobs)
        services.manager.wake()
        return {"ids": ids}
