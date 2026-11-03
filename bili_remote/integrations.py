from __future__ import annotations

from typing import Any

from .config import Settings


def integration_status(settings: Settings) -> dict[str, Any]:
    services = (
        ("minfo", settings.minfo_base_url, settings.minfo_api_token),
        ("ptgen", settings.ptgen_base_url, settings.ptgen_api_token),
    )
    result: dict[str, Any] = {}
    for name, base_url, token in services:
        configured = bool(base_url and token)
        result[name] = {
            "configured": configured,
            "connected": False,
            "status": "未连接" if configured else "未配置",
            "base_url": base_url,
        }
    return {"services": result, "read_only": True}
