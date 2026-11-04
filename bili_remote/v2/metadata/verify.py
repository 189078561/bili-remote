"""Explicit read-only development capability verification.

Run with python -m bili_remote.v2.metadata.verify --output <module docs file>.
Optional --config is an explicitly supplied development JSON file containing
tmdb_token, ptgen_endpoint, ptgen_api_key, bangumi_token. Never auto-load .env.
Only schema/type information and redacted capability states are written.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .providers import TMDBProvider, BangumiProvider, PTGenProvider


async def verify(config):
    providers = [TMDBProvider(config.get("tmdb_token", "")), BangumiProvider(config.get("bangumi_token", "")),
                 PTGenProvider(config.get("ptgen_endpoint", ""), config.get("ptgen_api_key", ""))]
    selections = [{"id": "603", "media_type": "movie"}, {"id": "8", "media_type": "anime"}, {"id": "1291843", "media_type": "movie"}]
    samples = {}
    for provider, selection, query in zip(providers, selections, ["The Matrix", "カウボーイビバップ", "黑客帝国"]):
        schema = await provider.verify(selection, query)
        samples[provider.name] = {"capabilities": provider.capabilities(), "sample_schema": schema}
    return {"verification_kind": "real_read_only_http", "service_version": "not_exposed", "providers": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text()) if args.config else {}
    if not isinstance(config, dict) or set(config) - {"tmdb_token", "bangumi_token", "ptgen_endpoint", "ptgen_api_key"}:
        raise SystemExit("无效的独立开发配置")
    result = asyncio.run(verify(config))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    for name, row in result["providers"].items():
        print(name + ": " + row["capabilities"]["state"])


if __name__ == "__main__":
    main()
