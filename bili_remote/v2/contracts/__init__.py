"""Shared transport models. Services consume their JSON representation."""
from __future__ import annotations
from typing import Any, Generic, Literal, Protocol, TypeVar
from pydantic import BaseModel, ConfigDict, Field, field_validator

T = TypeVar('T')

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid')

class ResourceRef(Model):
    root_key: Literal['downloads','torrents','images','release_info']
    relative_path: str = Field(min_length=1, max_length=4096)
    kind: Literal['file','directory'] = 'file'
    fingerprint: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')

    @field_validator('relative_path')
    @classmethod
    def normalized(cls, value):
        if '\x00' in value or '\\' in value or any(p in {'','.','..'} for p in value.split('/')):
            raise ValueError('A normalized root-relative path is required')
        return value

class FieldValue(Model, Generic[T]):
    value: T | None = None
    source: Literal['bili','tmdb','bangumi','ptgen','ffprobe','mediainfo','filename','manual','default','memory']
    source_ref: str | None = None
    manual: bool = False
    confirmed: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)
    updated_at: str | None = None

class Identity(Model):
    media_type: Literal['movie','series','anime']
    tmdb_id: str | None = None
    bangumi_id: str | None = None
    selected_by: Literal['automatic','manual'] = 'automatic'
    confidence: float | None = None
    confirmed: bool = False

class EpisodeSelection(Model):
    season: FieldValue[int] | None = None
    episode: FieldValue[int] | None = None
    season_episode_count: FieldValue[int] | None = None
    scope: Literal['single','season'] = 'single'
    mapping_key: str | None = None

class DownloadOrigin(Model):
    job_id: int = Field(gt=0)
    bvid: str | None = None
    season_id: str | None = None
    ep_id: str | None = None
    cid: str | None = None
    original_title: str | None = None
    episode_index: int | None = None
    original_resource: ResourceRef
    api_mode: Literal['web','tv'] = 'web'

class PersonCredit(Model):
    person_id: str | None = None
    name: FieldValue[str]
    role: Literal['actor','voice_actor','director']
    character: str | None = None
    provider_order: int | None = None

class TitleCandidate(Model):
    id: str
    text: str
    language: str = 'und'
    kind: Literal['localized','english','original','alternative','custom']
    source: str
    source_ref: str | None = None
    selected: bool = False

class Metadata(Model):
    title_zh: FieldValue[str] | None = None
    title_zh_hant: FieldValue[str] | None = None
    title_ja: FieldValue[str] | None = None
    original_title: FieldValue[str] | None = None
    year: FieldValue[str | int] | None = None
    region: FieldValue[list[str] | str] | None = None
    overview: FieldValue[str] | None = None
    poster_url: FieldValue[str] | None = None
    genres: FieldValue[list[str]] | None = None
    release_date: FieldValue[str] | None = None
    douban_rating: FieldValue[str | float] | None = None
    douban_url: FieldValue[str] | None = None
    runtime: FieldValue[int | str] | None = None
    cast: list[PersonCredit] = Field(default_factory=list)
    voice_cast: list[PersonCredit] = Field(default_factory=list)
    directors: list[PersonCredit] = Field(default_factory=list)
    links: dict[str,str] = Field(default_factory=dict)

class EpisodeMapping(Model):
    bili_season_id: str | None = None
    bili_ep_id: str | None = None
    bili_cid: str | None = None
    tmdb_id: str | None = None
    tmdb_season: int = Field(ge=0)
    tmdb_episode: int = Field(ge=1)
    method: Literal['structured','tmdb','filename','manual','memory']
    confirmed: bool = False

class FrameRate(Model):
    numerator: int = Field(gt=0)
    denominator: int = Field(gt=0)

class MediaProbe(Model):
    width: FieldValue[int] | None = None
    height: FieldValue[int] | None = None
    resolution: FieldValue[str] | None = None
    video_codec: FieldValue[str] | None = None
    audio_codec: FieldValue[str] | None = None
    audio_channels: FieldValue[str | int] | None = None
    frame_rate: FieldValue[FrameRate] | None = None
    bit_depth: FieldValue[int] | None = None
    hdr: FieldValue[list[str]] | None = None
    duration_seconds: FieldValue[float] | None = None
    container: FieldValue[str] | None = None
    probe_tool: str | None = None
    probe_version: str | None = None
    probe_error: str | None = None

class SidecarRef(Model):
    ref: ResourceRef
    video_ref: ResourceRef
    kind: Literal['subtitle','external_audio']
    language: str | None = None
    suffix_tokens: list[str] = Field(default_factory=list)
    association: str
    confirmed: bool = False

class MediaFile(Model):
    ref: ResourceRef
    episode: EpisodeSelection = Field(default_factory=EpisodeSelection)
    probe: MediaProbe = Field(default_factory=MediaProbe)
    sidecars: list[SidecarRef] = Field(default_factory=list)

class ResourceContext(Model):
    schema_version: Literal[1] = 1
    context_id: str
    revision: int = Field(ge=0)
    resources: list[ResourceRef] = Field(default_factory=list, max_length=1000)
    origins: list[DownloadOrigin] = Field(default_factory=list, max_length=1000)
    identity: Identity | None = None
    identity_candidates: list[dict[str, Any]] = Field(default_factory=list)
    titles: list[TitleCandidate] = Field(default_factory=list)
    selected_title: FieldValue[str] | None = None
    metadata: Metadata = Field(default_factory=Metadata)
    episode: EpisodeSelection = Field(default_factory=EpisodeSelection)
    episode_mappings: list[EpisodeMapping] = Field(default_factory=list)
    files: list[MediaFile] = Field(default_factory=list)
    effective_rules: dict[str, Any] = Field(default_factory=dict)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)

class ArtifactRef(Model):
    kind: Literal['screenshot','collage','report','torrent','upload']
    id: str
    resource: ResourceRef | None = None
    url: str | None = None

class TaskRef(Model):
    title: str | None = None
    kind: Literal['download','processing']
    id: int
    task_type: str
    status: Literal['queued','running','completed','failed','cancelled','auth_required']
    progress: float | None = Field(default=None, ge=0, le=100)
    created_at: str | None = None
    finished_at: str | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    error: str | None = None
    retry_allowed: bool = False
    cancel_allowed: bool = False

class RenderRequest(Model):
    context: ResourceContext
    rule: dict[str, Any] = Field(default_factory=dict)

class PublishPreviewRequest(Model):
    context: ResourceContext
    release_title: str = Field(default='', max_length=10000)
    subtitle: str = Field(default='', max_length=10000)
    overview: str = Field(default='', max_length=1000000)
    poster: ArtifactRef | None = None
    reports: list[ArtifactRef] = Field(default_factory=list, max_length=1000)
    screenshots: list[ArtifactRef] = Field(default_factory=list, max_length=1000)
    links: list[str] = Field(default_factory=list, max_length=1000)
    format: Literal['bbcode','html','markdown'] = 'bbcode'

class NamingMemoryRepository(Protocol):
    def get(self, identity=None, season=None) -> dict: ...
    def put(self, scope, key, rules, expected_revision) -> dict: ...
