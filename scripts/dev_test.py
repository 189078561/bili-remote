#!/usr/bin/env python3
"""Run existing tests against the current checkout with temporary storage."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

GROUPS = {
    "integration": [],
    "naming": ["tests/test_naming.py", "tests/test_rename_media.py"],
    "subtitle": ["tests/test_assistant_api.py"],
    "workbench": ["tests/test_assistant_api.py", "tests/test_release_tools.py"],
    "download": ["tests/test_downloader.py", "tests/test_validation.py", "tests/test_bili.py"],
    "filebrowser": ["tests/test_file_service.py", "tests/test_filebrowser.py"],
    "smoke": ["tests/test_database.py", "tests/test_security.py", "tests/test_validation.py"],
    "all": ["tests"],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=GROUPS, default="smoke", nargs="?")
    parser.add_argument("--python", help="Existing interpreter with requirements-dev installed")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    candidates = [root / ".venv/bin/python", Path("/opt/bili-remote/.venv/bin/python")]
    interpreter = args.python or next((str(p) for p in candidates if p.is_file()), sys.executable)
    env = {
        k: v
        for k, v in os.environ.items()
        if not (
            k.startswith(("APP_", "ADMIN_", "MINFO_", "PTGEN_", "DOWNLOAD_", "ARIA2_", "CDN_", "V2_", "TMDB_", "BANGUMI_"))
            or k in {"PYTHONPATH", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"}
        )
    }
    with tempfile.TemporaryDirectory(prefix="bili-v2-tests-") as temporary:
        base = Path(temporary)
        for key, name in {
            "DATA_DIR": "data",
            "DOWNLOAD_ROOT_CONTAINER": "downloads",
            "IMPORTS_ROOT": "imports",
            "STAGING_ROOT": "staging",
            "RELEASES_ROOT": "releases",
            "TORRENTS_ROOT": "torrents",
            "IMAGES_ROOT": "images",
            "RELEASE_INFO_ROOT": "release-info",
        }.items():
            env[key] = str(base / name)
        env.update(
            APP_SECRET="isolated-tests-only-secret-longer-than-thirty-two-characters",
            ADMIN_PASSWORD="isolated-tests-only-password",
            SECURE_COOKIES="false",
            MINFO_BASE_URL="",
            MINFO_API_TOKEN="",
            PTGEN_BASE_URL="",
            PTGEN_API_TOKEN="",
            PYTHONDONTWRITEBYTECODE="1",
            ARIA2_ENABLED="false",
            TV_CDN_PROBE="false",
        )
        targets = list(GROUPS[args.group])
        modules = ["naming", "metadata"] if args.group == "naming" else [args.group]
        if args.group != "all":
            targets.extend(
                str(Path("tests/v2") / module)
                for module in modules
                if (root / "tests/v2" / module).is_dir()
            )
        report = root / '.dev' / f'test-results-{args.group}.xml'
        report.parent.mkdir(parents=True, exist_ok=True)
        return subprocess.run(
            [
                interpreter,
                "-m",
                "pytest",
                "-q",
                "-ra",
                f"--junitxml={report}",
                "--basetemp",
                str(base / "pytest"),
                *targets,
            ],
            cwd=root,
            env=env,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
