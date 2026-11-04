"""Buildless frontend gate; no package installation or production access."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_frontend_build_and_transport_contract():
    subprocess.run([sys.executable, str(Path(__file__).with_name("build_frontend.py")), "--check"], check=True)
    for source in (ROOT / "bili_remote/static/v2/workbench").glob("*.js"):
        subprocess.run(["node", "--check", str(source)], check=True)
    subprocess.run(["node", "--test", str(Path(__file__).with_name("transport.test.cjs"))], check=True)


def test_frontend_has_no_persistence_or_backend_implementation():
    sources = "\n".join(p.read_text() for p in (ROOT / "bili_remote/static/v2/workbench").glob("*.js"))
    for forbidden in ["localStorage", "sessionStorage", "indexedDB", "/api/publish/sessions", "/api/releases", "/api/media/metadata", "/api/release-info/jobs"]:
        assert forbidden not in sources
