"""Color-managed still extraction; never treat DV Profile 5 as HDR10."""

from __future__ import annotations

import functools

from .file_service import FileServiceError
from .media_tools import run_tool


@functools.lru_cache(maxsize=8)
def rendering_capabilities(command: tuple[str, ...]):
    try:
        result = run_tool([*command, "-hide_banner", "-filters"], timeout=10)
        filters = result.stdout.decode(errors="replace")
        has_placebo = "libplacebo" in filters
        ready = False
        if has_placebo:
            test = run_tool(
                [
                    *command,
                    "-v",
                    "error",
                    "-init_hw_device",
                    "vulkan=bili:llvmpipe",
                    "-filter_hw_device",
                    "bili",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=size=16x16:duration=0.1",
                    "-vf",
                    "libplacebo=colorspace=gbr:color_primaries=bt709:color_trc=iec61966-2-1:format=rgb24:peak_detect=false",
                    "-frames:v",
                    "1",
                    "-f",
                    "null",
                    "-",
                ],
                timeout=30,
            )
            ready = test.returncode == 0
        return {
            "libplacebo": ready,
            "libplacebo_filter": has_placebo,
            "zscale": "zscale" in filters and "tonemap " in filters,
        }
    except (OSError, FileServiceError):
        return {"libplacebo": False, "libplacebo_filter": False, "zscale": False}


def color_metadata(payload):
    stream = next((s for s in payload.get("streams", []) if s.get("codec_type") == "video"), {})
    dovi = next(
        (
            s
            for s in stream.get("side_data_list", [])
            if "dovi" in str(s.get("side_data_type", "")).lower()
        ),
        {},
    )
    return {
        key: stream.get(key)
        for key in ("color_space", "color_transfer", "color_primaries", "color_range")
    } | {
        "dv_profile": dovi.get("dv_profile"),
        "dv_bl_signal_compatibility_id": dovi.get("dv_bl_signal_compatibility_id"),
        "dv_bl_present_flag": dovi.get("bl_present_flag"),
    }


def conversion_filter(media, processor, capabilities):
    transfer = media.get("color_transfer")
    dv = media.get("dv_profile") is not None
    hdr = transfer in {"smpte2084", "arib-std-b67"}
    if not dv and not hdr and media.get("color_primaries") != "bt2020":
        return "", "sdr"
    if processor in {"auto", "libplacebo"} and capabilities.get("libplacebo"):
        return (
            "libplacebo=colorspace=gbr:color_primaries=bt709:color_trc=iec61966-2-1:range=pc:format=rgb24:peak_detect=false"
            + (":apply_dolbyvision=true" if dv else "")
        ), "libplacebo"
    if processor == "libplacebo":
        raise FileServiceError("libplacebo 实际渲染不可用，请检查 Vulkan 驱动")
    if dv:
        compatible = media.get("dv_bl_signal_compatibility_id") in {1, 2, 4, 6}
        if not compatible or int(media.get("dv_profile") or 0) == 5:
            raise FileServiceError("该 DV 素材没有已确认的兼容基础层，必须使用 libplacebo")
        if transfer not in {"smpte2084", "arib-std-b67", "bt709"}:
            raise FileServiceError("DV 基础层色彩元数据不完整，不能安全回退")
    if not capabilities.get("zscale"):
        raise FileServiceError("HDR 转换依赖不可用：需要 libplacebo 或 zscale/tonemap")
    if not hdr:
        return "zscale=p=bt709:t=bt709:m=bt709,format=rgb24", "zscale"
    return (
        "zscale=t=linear:npl=203,format=gbrpf32le,tonemap=mobius:param=0.3:desat=2,"
        "zscale=p=bt709:t=bt709:m=bt709:r=full,format=rgb24"
    ), "zscale-base-layer" if dv else "zscale"
