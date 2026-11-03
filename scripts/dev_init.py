#!/usr/bin/env python3
"""Prepare empty worktree-local storage and development credentials; start nothing."""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    if (root / "data" / "bili-remote.sqlite3").exists():
        raise SystemExit("Refusing to initialize a checkout containing the production database.")
    os.umask(0o077)
    for name in (
        "data",
        "downloads",
        "imports",
        "staging",
        "releases",
        "torrents",
        "images",
        "release-info",
        "test-artifacts",
    ):
        (root / ".dev" / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    config = root / ".env.dev"
    if config.exists():
        print("Existing .env.dev preserved; no credentials changed.")
        return
    text = (root / ".env.dev.example").read_text()
    text = text.replace(
        "replace-with-generated-development-secret-at-least-32-characters",
        secrets.token_urlsafe(48),
    )
    text = text.replace("replace-with-generated-development-password", secrets.token_urlsafe(24))
    with config.open("x") as handle:
        handle.write(text)
    config.chmod(0o600)
    print("Created private .env.dev and empty .dev roots. No database copied; no service started.")


if __name__ == "__main__":
    main()
