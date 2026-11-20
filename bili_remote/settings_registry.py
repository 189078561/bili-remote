from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from .screenshot_layout import COLLAGE_DESIGN_DEFAULTS
from .config import Settings
from .validation import ValidationError, validate_output_directory, validate_output_template

Validator = Callable[[Any, Settings], Any]

SOURCE_DEFAULTS = ("WEB-DL", "WEBRip", "BluRay", "HDTV", "DVD")
_APP_VIEWS = (
    "home",
    "files",
    "download",
    "uploads",
    "screenshots",
    "release-info",
    "torrents",
    "publish",
)
_FILE_SORTS = ("natural", "name", "mtime", "size")
_FILE_DIRECTIONS = ("asc", "desc")
_SIZE_VALUE = re.compile(r"^\d+[KMG]$", re.IGNORECASE)
_RUNTIME_APPLY_SCOPE = "保存后用于下一次解析或下载；正在进行的解析/下载不会被修改。"
_BROWSER_APPLY_SCOPE = "保存后用于新打开的文件浏览器；当前已打开的列表保持不变。"


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    key: str
    label: str
    control: str
    default: Any = None
    description: str = ""
    choices: tuple[tuple[str, str], ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str = ""
    advanced: bool = False
    apply_scope: str = ""
    runtime: bool = False
    runtime_attr: str | None = None
    runtime_scale: float = 1.0
    read_only: bool = False
    requires_idle: bool = False
    validator: Validator | None = None
    placeholder: str = ""
    directory_root: str = ""
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class SettingSection:
    key: str
    title: str
    description: str = ""
    fields: tuple[SettingDefinition, ...] = ()
    renderer: str = "fields"
    advanced: bool = False


@dataclass(frozen=True, slots=True)
class SettingGroup:
    key: str
    title: str
    description: str
    sections: tuple[SettingSection, ...] = field(default_factory=tuple)


def _choice(value: Any, definition: SettingDefinition) -> str:
    text = str(value).strip()
    allowed = {item[0] for item in definition.choices}
    if text not in allowed:
        raise ValidationError(f"{definition.label}的选项无效")
    return text


def _integer(value: Any, definition: SettingDefinition) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"{definition.label}必须是数字")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{definition.label}必须是数字") from exc
    if isinstance(value, float) and not value.is_integer():
        raise ValidationError(f"{definition.label}必须是整数")
    if definition.minimum is not None and parsed < definition.minimum:
        raise ValidationError(f"{definition.label}不能小于 {definition.minimum:g}")
    if definition.maximum is not None and parsed > definition.maximum:
        raise ValidationError(f"{definition.label}不能大于 {definition.maximum:g}")
    return parsed


def _float(value: Any, definition: SettingDefinition) -> float:
    if isinstance(value, bool):
        raise ValidationError(f"{definition.label}必须是数字")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{definition.label}必须是数字") from exc
    if definition.minimum is not None and parsed < definition.minimum:
        raise ValidationError(f"{definition.label}不能小于 {definition.minimum:g}")
    if definition.maximum is not None and parsed > definition.maximum:
        raise ValidationError(f"{definition.label}不能大于 {definition.maximum:g}")
    return parsed


def _boolean(value: Any, definition: SettingDefinition) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ValidationError(f"{definition.label}必须是开关值")


def _text(value: Any, definition: SettingDefinition) -> str:
    text = "" if value is None else str(value).strip()
    if len(text) > 1000:
        raise ValidationError(f"{definition.label}过长")
    if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        raise ValidationError(f"{definition.label}包含非法字符")
    return text


def _collage_fields(value):
    if not isinstance(value, (list, tuple)):
        raise ValidationError("拼图信息栏字段必须为列表")
    allowed = COLLAGE_DESIGN_DEFAULTS["info_fields"]
    if any(not isinstance(item, str) or item not in allowed for item in value):
        raise ValidationError("拼图信息栏包含未知字段")
    return list(dict.fromkeys(value))


def _screenshot_times(value):
    # Import lazily: the screenshot engine itself consumes this registry.
    from .screenshots import ScreenshotError, parse_timestamps

    items = re.split(r"[\r\n,]+", value) if isinstance(value, str) else value
    try:
        parse_timestamps(items)
    except (ValueError, ScreenshotError) as exc:
        raise ValidationError(str(exc)) from exc
    return [str(item).strip() for item in (items or []) if str(item).strip()]


def _templates(value: Any, definition: SettingDefinition) -> str:
    return validate_output_template(str(value or "").strip())


def _default_output(value: Any, definition: SettingDefinition) -> str:
    return validate_output_directory(str(value or ""))


def _season(value: Any, definition: SettingDefinition) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{1,3}", text):
        raise ValidationError("默认季号必须是 0 到 999 的数字")
    return text.zfill(2)


def _source(value: Any, definition: SettingDefinition) -> str:
    text = str(value or "").strip()
    if len(text) > 60 or any(ord(char) < 32 for char in text):
        raise ValidationError("默认片源类型无效")
    return text


def _custom_sources(value: Any, definition: SettingDefinition) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = re.split(r"[\r\n,]+", value)
    elif isinstance(value, list):
        values = value
    else:
        raise ValidationError("自定义片源必须是字符串列表")
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        text = str(raw).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        if len(text) > 60 or any(ord(char) < 32 for char in text):
            raise ValidationError("自定义片源类型无效")
        seen.add(key)
        result.append(text)
    if len(result) > 50:
        raise ValidationError("自定义片源最多保存 50 项")
    return result


def _size(value: Any, definition: SettingDefinition) -> str:
    text = str(value or "").strip().upper()
    if not _SIZE_VALUE.fullmatch(text):
        raise ValidationError(f"{definition.label}必须使用类似 20M 的格式")
    return text


def _trackers(value: Any, definition: SettingDefinition) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = re.split(r"[\r\n,]+", value)
    elif isinstance(value, list):
        values = value
    else:
        raise ValidationError("Tracker 必须是字符串列表")
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw).strip()
        if not text or text in seen:
            continue
        if len(text) > 2000 or not re.fullmatch(r"(?:https?|udp)://[^\s]+", text):
            raise ValidationError("Tracker 地址必须以 http://、https:// 或 udp:// 开头")
        seen.add(text)
        result.append(text)
    if len(result) > 20:
        raise ValidationError("Tracker 最多保存 20 个")
    return result


def _piece_length(value: Any, definition: SettingDefinition) -> str:
    text = str(value or "auto").strip().lower()
    if text == "auto":
        return "auto"
    match = re.fullmatch(r"(\d+)([km]i?b?|b)?", text)
    if not match:
        raise ValidationError("piece 大小必须为 auto 或类似 1M 的值")
    amount = int(match.group(1))
    unit = (match.group(2) or "b").lower()
    multiplier = {
        "b": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": 1024**2,
        "mb": 1024**2,
        "mib": 1024**2,
    }[unit]
    size = amount * multiplier
    if not 16 * 1024 <= size <= 16 * 1024**2 or size & (size - 1):
        raise ValidationError("piece 大小必须是 16 KiB 到 16 MiB 之间的 2 的幂")
    return f"{size // 1024}K" if size < 1024**2 else f"{size // 1024**2}M"


def _optional_secret(value: Any, definition: SettingDefinition) -> str:
    text = "" if value is None else str(value)
    if len(text) > 4096 or any(ord(char) < 32 and char not in "\t" for char in text):
        raise ValidationError(f"{definition.label}格式无效")
    return text


def _app_default_view(value: Any, _settings: Settings) -> str:
    text = str(value or "").strip()
    if text not in _APP_VIEWS:
        raise ValidationError("默认打开页面无效")
    return text


def parse_setting_value(definition: SettingDefinition, value: Any, settings: Settings) -> Any:
    if definition.validator:
        return definition.validator(value, settings)
    if definition.control == "switch":
        return _boolean(value, definition)
    if definition.control == "number":
        if definition.step is not None and not float(definition.step).is_integer():
            return _float(value, definition)
        return _integer(value, definition)
    if definition.control == "select":
        return _choice(value, definition)
    return _text(value, definition)


def serialize_setting_value(definition: SettingDefinition, value: Any) -> str:
    if definition.control == "switch":
        return "true" if bool(value) else "false"
    if definition.control in {"list"}:
        return json.dumps(value, ensure_ascii=False)
    if definition.control == "number":
        return str(value)
    return str(value)


def deserialize_setting_value(
    definition: SettingDefinition, raw: str | None, settings: Settings
) -> Any:
    if raw is None:
        if definition.runtime and definition.runtime_attr:
            base = getattr(settings, definition.runtime_attr)
            return base / definition.runtime_scale if definition.runtime_scale != 1 else base
        return definition.default
    if definition.control == "list":
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{definition.label}存储格式无效") from exc
    elif definition.control == "switch":
        parsed = raw
    else:
        parsed = raw
    return parse_setting_value(definition, parsed, settings)


def _choices(values: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((value, value) for value in values)


GENERAL_GROUP = SettingGroup(
    "general",
    "常规",
    "控制应用打开方式与常用默认行为。",
    (
        SettingSection(
            "startup",
            "启动行为",
            "这些选项只影响本应用界面，不改变已有下载或文件。",
            (
                SettingDefinition(
                    "app_default_view",
                    "默认打开页面",
                    "select",
                    "files",
                    "没有明确返回页面时，打开应用默认进入的业务页面。",
                    choices=(
                        ("home", "首页"),
                        ("files", "文件管理"),
                        ("download", "B站下载"),
                        ("uploads", "文件工具：上传记录"),
                        ("screenshots", "文件工具：缩略图记录"),
                        ("release-info", "文件工具：媒体报告"),
                        ("torrents", "文件工具：种子记录"),
                        ("publish", "发布资料"),
                    ),
                    validator=_app_default_view,
                ),
            ),
        ),
    ),
)

FILE_GROUP = SettingGroup(
    "files",
    "目录与文件管理",
    "管理四类文件入口和文件浏览器的默认显示方式。",
    (
        SettingSection(
            "directories",
            "目录入口",
            "入口保存为挂载根标识与相对路径；修改不会移动或删除已有文件。",
            (
                SettingDefinition(
                    "directories.downloads",
                    "资源下载目录",
                    "directory",
                    {"mount_key": "downloads", "relative_path": ""},
                    "B 站下载文件和媒体资源所在目录。",
                ),
                SettingDefinition(
                    "directories.torrents",
                    "种子目录",
                    "directory",
                    {"mount_key": "torrents", "relative_path": ""},
                    "种子文件保存目录。",
                ),
                SettingDefinition(
                    "directories.images",
                    "图片目录",
                    "directory",
                    {"mount_key": "images", "relative_path": ""},
                    "截图和预览图目录。",
                ),
                SettingDefinition(
                    "directories.release_info",
                    "发布信息目录",
                    "directory",
                    {"mount_key": "release_info", "relative_path": ""},
                    "MediaInfo、BDInfo 和发布说明目录。",
                ),
            ),
        ),
        SettingSection(
            "browser",
            "浏览默认值",
            _BROWSER_APPLY_SCOPE,
            (
                SettingDefinition(
                    "file_browser_page_size",
                    "每页项目数",
                    "number",
                    100,
                    "文件列表每页显示的目录和文件数量。",
                    minimum=20,
                    maximum=1000,
                    step=10,
                ),
                SettingDefinition(
                    "file_browser_default_sort",
                    "默认排序",
                    "select",
                    "natural",
                    "打开文件浏览器时使用的排序方式。",
                    choices=(
                        ("natural", "自然排序"),
                        ("name", "名称"),
                        ("mtime", "修改时间"),
                        ("size", "文件大小"),
                    ),
                ),
                SettingDefinition(
                    "file_browser_default_direction",
                    "默认排序方向",
                    "select",
                    "asc",
                    "打开文件浏览器时的升降序。",
                    choices=(("asc", "升序"), ("desc", "降序")),
                ),
            ),
        ),
    ),
)

DOWNLOAD_GROUP = SettingGroup(
    "downloads",
    "B 站下载",
    "管理新任务的默认输出位置、命名模板和下载线路参数。",
    (
        SettingSection(
            "defaults",
            "任务默认值",
            "只影响之后创建的任务；已排队任务保留创建时的模板和输出路径。",
            (
                SettingDefinition(
                    "single_output_template",
                    "单条视频默认模板",
                    "template",
                    "Bilibili/%(uploader)s/%(title)s [%(id)s].%(ext)s",
                    validator=_templates,
                    description="必须包含 %(ext)s。",
                    placeholder="Bilibili/%(uploader)s/%(title)s [%(id)s].%(ext)s",
                ),
                SettingDefinition(
                    "playlist_output_template",
                    "番剧 / 合集默认模板",
                    "template",
                    (
                        "Bilibili/%(playlist_title)s/S%(playlist_index)02d - "
                        "%(title)s [%(id)s].%(ext)s"
                    ),
                    validator=_templates,
                    description="必须包含 %(ext)s。",
                    placeholder=(
                        "Bilibili/%(playlist_title)s/S%(playlist_index)02d - "
                        "%(title)s [%(id)s].%(ext)s"
                    ),
                ),
                SettingDefinition(
                    "download_default_output_dir",
                    "默认保存子目录",
                    "relative-directory",
                    "",
                    "相对于资源下载目录；留空表示根目录。",
                    validator=_default_output,
                ),
            ),
        ),
        SettingSection(
            "engine",
            "下载引擎与线路探测",
            _RUNTIME_APPLY_SCOPE,
            (
                SettingDefinition(
                    "aria2_enabled",
                    "启用 aria2",
                    "switch",
                    True,
                    "检测到 aria2c 时使用多连接下载。",
                    runtime=True,
                    runtime_attr="aria2_enabled",
                    requires_idle=True,
                    apply_scope=_RUNTIME_APPLY_SCOPE,
                ),
                SettingDefinition(
                    "aria2_connections",
                    "aria2 连接数",
                    "number",
                    8,
                    "单个任务每台服务器的并发连接上限。默认 8 路，遇到限流会自动降低。",
                    minimum=1,
                    maximum=16,
                    step=1,
                    runtime=True,
                    runtime_attr="aria2_connections",
                    requires_idle=True,
                    apply_scope=_RUNTIME_APPLY_SCOPE,
                ),
                SettingDefinition(
                    "aria2_resume_retries",
                    "aria2 续传重试",
                    "number",
                    2,
                    "断点续传失败后的额外尝试次数。",
                    minimum=0,
                    maximum=3,
                    step=1,
                    runtime=True,
                    runtime_attr="aria2_resume_retries",
                    requires_idle=True,
                    apply_scope=_RUNTIME_APPLY_SCOPE,
                ),
                SettingDefinition(
                    "aria2_min_split_size",
                    "最小分片大小",
                    "text",
                    "4M",
                    "默认 4M。较小的音视频轨也能并发传输。",
                    runtime=True,
                    runtime_attr="aria2_min_split_size",
                    requires_idle=True,
                    validator=_size,
                    apply_scope=_RUNTIME_APPLY_SCOPE,
                ),
                SettingDefinition(
                    "aria2_piece_size",
                    "piece 大小",
                    "text",
                    "4M",
                    "默认 4M，可按需调整。",
                    runtime=True,
                    runtime_attr="aria2_piece_size",
                    requires_idle=True,
                    validator=_size,
                    apply_scope=_RUNTIME_APPLY_SCOPE,
                ),
                SettingDefinition(
                    "resolve_timeout_seconds",
                    "解析超时（秒）",
                    "number",
                    120,
                    "单次 B 站解析的最长等待时间。",
                    minimum=10,
                    maximum=600,
                    step=5,
                    runtime=True,
                    runtime_attr="resolve_timeout_seconds",
                    requires_idle=True,
                    advanced=True,
                    apply_scope=_RUNTIME_APPLY_SCOPE,
                ),
                SettingDefinition(
                    "tv_cdn_probe_enabled",
                    "启用 TV CDN 测速",
                    "switch",
                    True,
                    "解析 TV 线路时测试候选 CDN 速度。",
                    runtime=True,
                    runtime_attr="tv_cdn_probe_enabled",
                    requires_idle=True,
                    advanced=True,
                    apply_scope=_RUNTIME_APPLY_SCOPE,
                ),
                SettingDefinition(
                    "cdn_probe_timeout_seconds",
                    "测速超时（秒）",
                    "number",
                    3,
                    "单个 CDN 候选的最大测速时间。",
                    minimum=0.5,
                    maximum=10,
                    step=0.5,
                    runtime=True,
                    runtime_attr="cdn_probe_timeout_seconds",
                    requires_idle=True,
                    advanced=True,
                    apply_scope=_RUNTIME_APPLY_SCOPE,
                ),
                SettingDefinition(
                    "cdn_probe_sample_size_kib",
                    "测速采样大小（KiB）",
                    "number",
                    512,
                    "范围 64 到 8192 KiB。",
                    minimum=64,
                    maximum=8192,
                    step=64,
                    runtime=True,
                    runtime_attr="cdn_probe_sample_bytes",
                    runtime_scale=1024,
                    requires_idle=True,
                    advanced=True,
                    apply_scope=_RUNTIME_APPLY_SCOPE,
                ),
                SettingDefinition(
                    "cdn_probe_connections",
                    "测速并发数",
                    "number",
                    8,
                    "同时进行的 CDN 测速数量。",
                    minimum=1,
                    maximum=16,
                    step=1,
                    runtime=True,
                    runtime_attr="cdn_probe_connections",
                    requires_idle=True,
                    advanced=True,
                    apply_scope=_RUNTIME_APPLY_SCOPE,
                ),
                SettingDefinition(
                    "line_compare_enabled",
                    "比较 Web / TV 线路",
                    "switch",
                    True,
                    "解析时同时比较可用的 Web 与 TV 片源。",
                    runtime=True,
                    runtime_attr="line_compare_enabled",
                    requires_idle=True,
                    advanced=True,
                    apply_scope=_RUNTIME_APPLY_SCOPE,
                ),
            ),
            advanced=True,
        ),
    ),
)

RENAME_GROUP = SettingGroup(
    "rename",
    "剧集重命名",
    "管理逐文件重命名的默认值和命名预设；集数递增/递减仍默认关闭。",
    (
        SettingSection(
            "defaults",
            "默认值",
            "这些值只作为新预览的初始值，可在每个文件中单独修改。",
            (
                SettingDefinition(
                    "rename_default_season",
                    "默认季号",
                    "text",
                    "01",
                    "默认 01，可输入 0 到 999。",
                    validator=_season,
                    placeholder="01",
                ),
                SettingDefinition(
                    "rename_default_source",
                    "默认片源",
                    "text",
                    "",
                    "留空则不推断；可从内置或自定义片源中选择。",
                    validator=_source,
                    placeholder="留空则不推断",
                ),
                SettingDefinition(
                    "rename_custom_sources",
                    "自定义片源",
                    "list",
                    [],
                    "每行一个，最多 50 项；会自动去重。",
                    validator=_custom_sources,
                ),
            ),
        ),
        SettingSection(
            "profiles",
            "命名预设",
            "每种媒体类型至少保留一个有效默认预设。",
            renderer="naming-profiles",
        ),
    ),
)

COLLAGE_GROUP = SettingGroup(
    "collage",
    "视频截图与拼图",
    "选择生成缩略图按钮的输出模式、取样、排布与色彩处理。",
    (
        SettingSection(
            "layout",
            "布局与尺寸",
            "点击生成按钮后直接使用以下参数，不再在进度弹窗中重复配置。",
            (
                SettingDefinition(
                    "screenshot_output_mode",
                    "生成缩略图的输出模式",
                    "select",
                    "single",
                    "文件管理只有一个生成按钮，直接按此模式输出。",
                    choices=(("single", "独立单图"), ("collage", "拼图（按下方布局）")),
                ),
                SettingDefinition(
                    "collage_default_layout_mode",
                    "布局模式",
                    "select",
                    "grid",
                    "按行列生成固定网格，或按总截图数自动计算行数。",
                    choices=(("grid", "按列 × 行"), ("count", "按总截图数")),
                ),
                SettingDefinition(
                    "collage_default_columns",
                    "列数",
                    "number",
                    5,
                    "横向缩略图数量。",
                    minimum=1,
                    maximum=10,
                    step=1,
                ),
                SettingDefinition(
                    "collage_default_rows",
                    "行数",
                    "number",
                    4,
                    "按列 × 行布局时使用；总数为列数乘行数。",
                    minimum=1,
                    maximum=12,
                    step=1,
                ),
                SettingDefinition(
                    "collage_default_total",
                    "默认截图数量",
                    "number",
                    20,
                    "单图或自动排布时使用；固定拼图按列数乘行数取样。",
                    minimum=1,
                    maximum=120,
                    step=1,
                    unit="张",
                ),
                SettingDefinition(
                    "collage_default_width",
                    "拼图缩略图宽度",
                    "number",
                    480,
                    "生成拼图时每张画面的宽度；高度会按原比例计算。",
                    minimum=240,
                    maximum=1920,
                    step=20,
                    unit="像素",
                ),
                SettingDefinition(
                    "collage_default_gap",
                    "缩略图间距",
                    "number",
                    3,
                    "拼图网格中缩略图之间的白色间距。",
                    minimum=0,
                    maximum=40,
                    step=1,
                    unit="像素",
                ),
                SettingDefinition("collage_ratio", "画面比例", "select", "original",
                                  choices=(("original", "跟随原片"), ("wide", "16∶9"), ("classic", "4∶3"), ("square", "1∶1"))),
                SettingDefinition("collage_fit", "画面适配", "select", "contain",
                                  choices=(("contain", "完整画面 · 补黑边"), ("cover", "居中裁切"))),
                SettingDefinition(
                    "screenshot_default_width",
                    "独立截图宽度",
                    "number",
                    1920,
                    "独立截图的目标像素宽度；0 表示保留源分辨率。",
                    minimum=0,
                    maximum=3840,
                    step=20,
                    unit="像素",
                ),
            ),
        ),
        SettingSection(
            "information",
            "拼图信息栏",
            "只用于拼图。开启后可选择字段、调整排列顺序和文字布局。",
            (
                SettingDefinition("collage_info_enabled", "显示信息栏", "switch", True),
                SettingDefinition("collage_info_font", "信息栏字体", "select", "sans",
                                  choices=(("sans", "黑体"), ("serif", "宋体"), ("mono", "等宽"))),
                SettingDefinition("collage_info_size", "信息栏字号", "number", 15,
                                  minimum=12, maximum=32, step=1, unit="px"),
                SettingDefinition("collage_info_rows", "信息栏行数", "number", 4,
                                  minimum=1, maximum=8, step=1),
                SettingDefinition("collage_info_percent", "目标高度占比", "number", 9,
                                  minimum=2, maximum=25, step=1, unit="%"),
                SettingDefinition("collage_info_fields", "字段与顺序", "list",
                                  COLLAGE_DESIGN_DEFAULTS["info_fields"], validator=lambda value, _: _collage_fields(value)),
            ),
        ),
        SettingSection(
            "sampling",
            "取帧范围",
            "内置内容类型使用推荐跳过时长；选择“自定义”时使用下面的片头片尾值。",
            (
                SettingDefinition(
                    "collage_default_content_type",
                    "内容类型",
                    "select",
                    "anime",
                    "电影跳过 90/120 秒，电视剧跳过 90/180 秒，动漫跳过 90/90 秒。",
                    choices=(
                        ("movie", "电影"),
                        ("series", "电视剧"),
                        ("anime", "动漫"),
                        ("custom", "自定义"),
                    ),
                ),
                SettingDefinition(
                    "collage_custom_skip_start",
                    "自定义跳过片头",
                    "number",
                    0,
                    "仅在内容类型选择“自定义”时生效。",
                    minimum=0,
                    maximum=86400,
                    step=1,
                    unit="秒",
                ),
                SettingDefinition(
                    "collage_custom_skip_end",
                    "自定义跳过片尾",
                    "number",
                    0,
                    "仅在内容类型选择“自定义”时生效。",
                    minimum=0,
                    maximum=86400,
                    step=1,
                    unit="秒",
                ),
            ),
        ),
        SettingSection(
            "output",
            "输出目录",
            "截图会话和最终拼图始终位于当前图片目录内；留空表示图片目录根。",
            (
                SettingDefinition(
                    "screenshot_output_relative_path",
                    "截图输出子目录",
                    "relative-directory",
                    "",
                    "每个截图会话会在该目录下自动创建独立子目录。",
                    validator=_default_output,
                    placeholder="留空表示图片目录根目录",
                    directory_root="images",
                ),
                SettingDefinition(
                    "collage_output_relative_path",
                    "拼图输出子目录",
                    "relative-directory",
                    "",
                    "相对于设置中的图片目录；目录必须已存在且可写。",
                    validator=_default_output,
                    placeholder="留空表示图片目录根目录",
                    directory_root="images",
                ),
            ),
        ),
    ),
)

IMAGE_HOST_GROUP = SettingGroup(
    "image_host",
    "图床上传",
    "配置真实图片上传服务。密钥只加密保存在服务端，不会通过普通接口返回。",
    (
        SettingSection(
            "provider",
            "上传服务",
            "Catbox 无需账号；ImgBB 需要有效的 API Key。上传会把所选图片发送到对应外部服务。",
            (
                SettingDefinition(
                    "image_host_provider",
                    "图床服务",
                    "select",
                    "catbox",
                    "用于文件和截图上传。",
                    choices=(
                        ("catbox", "Catbox（无需密钥）"),
                        ("imgbb", "ImgBB（需要 API Key）"),
                        ("pixhost", "Pixhost（无需密钥）"),
                    ),
                ),
                SettingDefinition(
                    "image_host_imgbb_api_key",
                    "ImgBB API Key",
                    "password",
                    "",
                    "仅在选择 ImgBB 时使用；留空不会覆盖已保存值。",
                    sensitive=True,
                    validator=_optional_secret,
                    placeholder="已保存时留空保持不变",
                ),
                SettingDefinition(
                    "image_host_timeout_seconds",
                    "上传超时（秒）",
                    "number",
                    60,
                    "单张图片上传的最长等待时间。",
                    minimum=5,
                    maximum=300,
                    step=5,
                ),
            ),
        ),
    ),
)

TORRENT_GROUP = SettingGroup(
    "torrents",
    "种子制作",
    "配置本地种子文件生成的默认参数。",
    (
        SettingSection(
            "torrent_defaults",
            "种子默认值",
            "只影响之后创建的种子文件，不会修改已有种子。",
            (
                SettingDefinition(
                    "torrent_default_trackers",
                    "默认 Tracker",
                    "list",
                    [],
                    "每行一个 http://、https:// 或 udp:// 地址，最多 20 个。",
                    validator=_trackers,
                ),
                SettingDefinition(
                    "torrent_default_private",
                    "默认创建私有种子",
                    "switch",
                    True,
                    "私有种子会写入 private 标记，请按目标站点要求选择。",
                ),
                SettingDefinition(
                    "torrent_default_piece_length",
                    "默认 piece 大小",
                    "text",
                    "auto",
                    "auto 或 16K、256K、1M、4M、16M 等 2 的幂。",
                    validator=_piece_length,
                    placeholder="auto",
                ),
                SettingDefinition(
                    "torrent_default_source",
                    "默认 source 标记",
                    "text",
                    "",
                    "可选，写入种子 info.source；留空表示不设置。",
                    placeholder="例如 BiliRemote",
                ),
            ),
        ),
    ),
)

RELEASE_GROUP = SettingGroup(
    "release",
    "影视资料与发布",
    "配置发布资料生成和 PT 发布辅助的默认格式；所有发布内容仍需人工检查。",
    (
        SettingSection(
            "release_info",
            "发布信息",
            "媒体参数来自实际 ffprobe 探测；MediaInfo 可用时生成完整文本。",
            (
                SettingDefinition(
                    "release_info_default_format",
                    "默认资料格式",
                    "select",
                    "markdown",
                    "新建发布资料时使用的可编辑格式。",
                    choices=(
                        ("markdown", "Markdown"),
                        ("bbcode", "BBCode"),
                        ("text", "纯文本"),
                    ),
                ),
                SettingDefinition(
                    "release_info_include_mediainfo",
                    "优先使用 MediaInfo",
                    "switch",
                    True,
                    "工具可用时使用 MediaInfo；不可用时自动回退到 ffprobe。",
                ),
            ),
        ),
    ),
)

MAINTENANCE_GROUP = SettingGroup(
    "maintenance",
    "日志与系统",
    "查看当前运行环境，并管理可安全在线调整的维护参数。",
    (
        SettingSection(
            "logs",
            "日志维护",
            "保存后在下一轮维护任务生效。",
            (
                SettingDefinition(
                    "log_retention_days",
                    "日志保留天数",
                    "number",
                    30,
                    "超过保留期的任务日志会在维护任务中清理。",
                    minimum=1,
                    maximum=3650,
                    step=1,
                    runtime=True,
                    runtime_attr="log_retention_days",
                    apply_scope="保存后在下一轮维护任务生效。",
                ),
            ),
        ),
        SettingSection(
            "diagnostics",
            "系统信息",
            "以下信息只读，由当前运行环境和部署配置决定。",
            (
                SettingDefinition("ytdlp_version", "yt-dlp 版本", "readonly", read_only=True),
                SettingDefinition("ffprobe_status", "ffprobe", "readonly", read_only=True),
                SettingDefinition("ffmpeg_status", "ffmpeg", "readonly", read_only=True),
                SettingDefinition(
                    "collage_font_status", "拼图中文字体", "readonly", read_only=True
                ),
                SettingDefinition("mediainfo_status", "MediaInfo", "readonly", read_only=True),
                SettingDefinition("bdinfo_status", "BDInfo", "readonly", read_only=True),
                SettingDefinition("aria2_status", "aria2c", "readonly", read_only=True),
                SettingDefinition("timezone", "时区", "readonly", read_only=True),
                SettingDefinition("mount_paths", "挂载路径", "readonly", read_only=True),
            ),
        ),
    ),
)

# Media tools extend existing categories so saved settings keep their meaning.
COLLAGE_GROUP = replace(
    COLLAGE_GROUP,
    sections=COLLAGE_GROUP.sections
    + (
        SettingSection(
            "rendering",
            "截图样式与色彩",
            fields=(
                SettingDefinition(
                    "screenshot_default_sampling",
                    "默认取样",
                    "select",
                    "random",
                    choices=(
                        ("random", "分段随机"),
                        ("uniform", "均匀取样"),
                        ("timestamps", "指定时间点"),
                    ),
                ),
                SettingDefinition(
                    "screenshot_default_timestamps",
                    "默认时间点",
                    "list",
                    [],
                    "指定时间点时使用，每行 MM:SS 或 HH:MM:SS，可带毫秒。",
                    validator=lambda value, settings: _screenshot_times(value),
                ),
                SettingDefinition(
                    "screenshot_default_format",
                    "截图格式",
                    "select",
                    "png",
                    choices=(("png", "PNG 无损"), ("jpg", "JPG")),
                ),
                SettingDefinition(
                    "screenshot_default_style",
                    "单张样式",
                    "select",
                    "script",
                    choices=(("script", "脚本风格（时间栏与阴影）"), ("plain", "纯画面")),
                ),
                SettingDefinition(
                    "screenshot_hdr_processor",
                    "HDR / DV 转换",
                    "select",
                    "auto",
                    "自动优先 libplacebo；仅 HDR 或有兼容基础层的 DV 可回退。",
                    choices=(
                        ("auto", "自动"),
                        ("libplacebo", "libplacebo"),
                        ("zscale", "zscale 兼容"),
                    ),
                ),
                SettingDefinition("screenshot_auto_upload", "生成后上传", "switch", False),
                SettingDefinition(
                    "screenshot_upload_variant",
                    "上传版本",
                    "select",
                    "styled",
                    choices=(("styled", "所选风格"), ("raw", "纯画面")),
                ),
                SettingDefinition(
                    "screenshot_render_status", "HDR / DV 渲染能力", "readonly", read_only=True
                ),
            ),
        ),
    ),
)
TORRENT_GROUP = replace(
    TORRENT_GROUP,
    sections=TORRENT_GROUP.sections
    + (
        SettingSection(
            "output",
            "种子输出",
            fields=(
                SettingDefinition(
                    "torrent_output_relative_path",
                    "默认输出子目录",
                    "relative-directory",
                    "",
                    validator=_default_output,
                    directory_root="torrents",
                ),
            ),
        ),
    ),
)
RELEASE_GROUP = replace(
    RELEASE_GROUP,
    sections=RELEASE_GROUP.sections
    + (
        SettingSection(
            "metadata",
            "TMDB 与 PT-Gen",
            "搜索并确认作品后采用资料；重新获取不会自动覆盖手工内容。",
            fields=(
                SettingDefinition("v2_bangumi_enabled", "启用 Bangumi 资料补充", "switch", False),
                SettingDefinition("v2_bangumi_api_key", "Bangumi Access Token（可选）", "password", "", validator=_optional_secret, sensitive=True),
                SettingDefinition(
                    "tmdb_api_key",
                    "TMDB API Key 或 Read Access Token",
                    "password",
                    "",
                    validator=_optional_secret,
                    sensitive=True,
                ),
                SettingDefinition(
                    "ptgen_url",
                    "PT-Gen 服务地址",
                    "text",
                    "",
                    "填写返回 JSON 的接口完整地址；pt-gen-universal 通常为 http://主机:端口/api/v1/info。",
                ),
                SettingDefinition(
                    "ptgen_api_key",
                    "PT-Gen API Key",
                    "password",
                    "",
                    validator=_optional_secret,
                    sensitive=True,
                ),
                SettingDefinition(
                    "metadata_timeout_seconds", "资料请求超时", "number", 20, minimum=3, maximum=120
                ),
            ),
        ),
        SettingSection(
            "reports",
            "报告与发布规则",
            fields=(
                SettingDefinition(
                    "release_info_output_relative_path",
                    "报告输出子目录",
                    "relative-directory",
                    "",
                    validator=_default_output,
                    directory_root="release_info",
                ),
                SettingDefinition(
                    "bdinfo_timeout_seconds",
                    "BDInfo 扫描超时",
                    "number",
                    1800,
                    minimum=60,
                    maximum=14400,
                    unit="秒",
                ),
                SettingDefinition(
                    "publish_default_format",
                    "默认复制格式",
                    "select",
                    "bbcode",
                    choices=(("bbcode", "BBCode"), ("markdown", "Markdown"), ("html", "HTML")),
                ),
                SettingDefinition(
                    "publish_report_wrapper",
                    "报告排版",
                    "select",
                    "hide",
                    choices=(("hide", "hide=MediaInfo / BDInfo"), ("quote", "quote")),
                ),
                SettingDefinition(
                    "publish_rule_profile",
                    "发布规则",
                    "select",
                    "pter",
                    choices=(("pter", "猫站规则（按提供的要求）"),),
                ),
            ),
        ),
    ),
)

GROUPS = (
    GENERAL_GROUP,
    FILE_GROUP,
    DOWNLOAD_GROUP,
    RENAME_GROUP,
    COLLAGE_GROUP,
    IMAGE_HOST_GROUP,
    TORRENT_GROUP,
    RELEASE_GROUP,
    MAINTENANCE_GROUP,
)


def all_definitions() -> tuple[SettingDefinition, ...]:
    return tuple(
        definition
        for group in GROUPS
        for section in group.sections
        for definition in section.fields
    )


DEFINITIONS_BY_KEY = {
    definition.key: definition
    for definition in all_definitions()
    if not definition.read_only and definition.control != "directory"
}
RUNTIME_DEFINITIONS = {
    definition.key: definition for definition in DEFINITIONS_BY_KEY.values() if definition.runtime
}
STATIC_DEFINITIONS = {
    definition.key: definition
    for definition in DEFINITIONS_BY_KEY.values()
    if not definition.runtime
}
RUNTIME_SETTING_KEYS = frozenset(RUNTIME_DEFINITIONS)
RUNTIME_IDLE_KEYS = frozenset(
    key for key, definition in RUNTIME_DEFINITIONS.items() if definition.requires_idle
)
SECRET_SETTING_KEYS = frozenset(
    key for key, definition in DEFINITIONS_BY_KEY.items() if definition.sensitive
)


def schema_payload(runtime_defaults: dict[str, Any] | None = None) -> dict[str, Any]:
    groups: list[dict[str, Any]] = []
    for group in GROUPS:
        sections: list[dict[str, Any]] = []
        for section in group.sections:
            fields = []
            for definition in section.fields:
                item = {
                    "key": definition.key,
                    "label": definition.label,
                    "control": definition.control,
                    "description": definition.description,
                    "default": (
                        runtime_defaults.get(definition.key, definition.default)
                        if definition.runtime and runtime_defaults
                        else definition.default
                    ),
                    "advanced": definition.advanced,
                    "apply_scope": definition.apply_scope,
                    "read_only": definition.read_only,
                    "runtime": definition.runtime,
                    "requires_idle": definition.requires_idle,
                    "placeholder": definition.placeholder,
                    "sensitive": definition.sensitive,
                }
                if definition.choices:
                    item["choices"] = [
                        {"value": value, "label": label} for value, label in definition.choices
                    ]
                if definition.minimum is not None:
                    item["minimum"] = definition.minimum
                if definition.maximum is not None:
                    item["maximum"] = definition.maximum
                if definition.step is not None:
                    item["step"] = definition.step
                if definition.unit:
                    item["unit"] = definition.unit
                if definition.directory_root:
                    item["directory_root"] = definition.directory_root
                fields.append(item)
            sections.append(
                {
                    "key": section.key,
                    "title": section.title,
                    "description": section.description,
                    "advanced": section.advanced,
                    "renderer": section.renderer,
                    "fields": fields,
                }
            )
        groups.append(
            {
                "key": group.key,
                "title": group.title,
                "description": group.description,
                "sections": sections,
            }
        )
    return {"groups": groups, "source_defaults": list(SOURCE_DEFAULTS)}


def default_values() -> dict[str, Any]:
    return {
        key: definition.default
        for key, definition in STATIC_DEFINITIONS.items()
        if definition.control != "directory"
    }


def runtime_env_defaults(settings: Settings) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, definition in RUNTIME_DEFINITIONS.items():
        attr = definition.runtime_attr or key
        raw = getattr(settings, attr)
        values[key] = raw / definition.runtime_scale if definition.runtime_scale != 1 else raw
    return values


def runtime_values(settings: Settings) -> dict[str, Any]:
    return runtime_env_defaults(settings)


def static_values(stored: dict[str, str], settings: Settings) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, definition in STATIC_DEFINITIONS.items():
        if definition.control in {"directory", "readonly"}:
            continue
        if definition.sensitive:
            continue
        raw = stored.get(key)
        if raw is None:
            values[key] = definition.default
        else:
            values[key] = deserialize_setting_value(definition, raw, settings)
    return values


def parse_stored_values(values: dict[str, Any], settings: Settings) -> dict[str, str]:
    stored: dict[str, str] = {}
    for key, raw in values.items():
        definition = DEFINITIONS_BY_KEY.get(key)
        if definition is None:
            raise ValidationError(f"未知设置项：{key}")
        parsed = parse_setting_value(definition, raw, settings)
        if definition.control == "list":
            stored[key] = serialize_setting_value(definition, parsed)
        elif definition.control == "switch":
            stored[key] = serialize_setting_value(definition, parsed)
        else:
            stored[key] = serialize_setting_value(definition, parsed)
    return stored
