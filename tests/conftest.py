from __future__ import annotations

import sys
from pathlib import Path

import pytest

from bili_remote.config import Settings

FAKE_YTDLP = Path(__file__).with_name("fake_ytdlp.py")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        download_root=tmp_path / "downloads",
        imports_root=tmp_path / "imports",
        staging_root=tmp_path / "staging",
        releases_root=tmp_path / "releases",
        torrents_root=tmp_path / "torrents",
        images_root=tmp_path / "images",
        release_info_root=tmp_path / "release-info",
        app_secret="test-secret-that-is-definitely-longer-than-thirty-two-characters",
        admin_password="test-password",
        secure_cookies=False,
        ytdlp_command=(sys.executable, str(FAKE_YTDLP)),
        resolve_timeout_seconds=10,
        tv_cdn_probe_enabled=False,
    )
