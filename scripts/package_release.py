#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tomllib
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "bili-remote"
INCLUDE_PATHS = (
    "bili_remote",
    "tests",
    "scripts",
    "deploy",
    "docs",
    "README.md",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "Dockerfile",
    "docker-compose.yml",
    ".dockerignore",
    ".gitignore",
    ".env.example",
)
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "data",
        "dist",
        "downloads",
        "images",
        "imports",
        "release-info",
        "releases",
        "staging",
        "torrents",
        "work",
        "outputs",
    }
)
EXCLUDED_FILE_NAMES = frozenset({".DS_Store"})
EXCLUDED_FILE_SUFFIXES = frozenset({".log", ".pyc", ".pyo", ".sha256", ".sqlite3", ".tmp", ".zip"})


class PackageError(RuntimeError):
    pass


def _project_version() -> str:
    pyproject = PROJECT_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]*", version):
        raise PackageError("pyproject.toml 中缺少有效的项目版本号")
    return version


def _resolved_relative(path: Path) -> Path:
    if path.is_symlink():
        raise PackageError(f"不允许打包符号链接：{path.relative_to(PROJECT_ROOT)}")
    resolved = path.resolve(strict=True)
    try:
        return resolved.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise PackageError(f"路径超出项目目录：{path}") from exc


def _is_excluded(path: Path) -> bool:
    relative = path.relative_to(PROJECT_ROOT)
    if any(part in EXCLUDED_DIRECTORY_NAMES for part in relative.parts[:-1]):
        return True
    if path.name.startswith(".env") and path.name != ".env.example":
        return True
    return path.name in EXCLUDED_FILE_NAMES or path.suffix.lower() in EXCLUDED_FILE_SUFFIXES


def _collect_files() -> list[Path]:
    files: set[Path] = set()
    for include in INCLUDE_PATHS:
        path = PROJECT_ROOT / include
        if not path.exists():
            raise PackageError(f"缺少打包文件：{include}")
        _resolved_relative(path)
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for candidate in candidates:
            if candidate.is_symlink():
                relative = candidate.relative_to(PROJECT_ROOT)
                raise PackageError(f"不允许打包符号链接：{relative}")
            if not candidate.is_file() or _is_excluded(candidate):
                continue
            _resolved_relative(candidate)
            files.add(candidate.relative_to(PROJECT_ROOT))
    if not files:
        raise PackageError("没有可打包的文件")
    return [PROJECT_ROOT / relative for relative in sorted(files, key=lambda item: item.as_posix())]


def _write_archive(destination: Path, files: list[Path]) -> None:
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for source in files:
                relative = source.relative_to(PROJECT_ROOT).as_posix()
                archive.write(source, arcname=f"{PACKAGE_NAME}/{relative}")
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_checksum(destination: Path) -> Path:
    checksum = destination.with_name(f"{destination.name}.sha256")
    temporary = checksum.with_name(f".{checksum.name}.tmp")
    temporary.write_text(f"{_sha256(destination)}  {destination.name}\n", encoding="utf-8")
    temporary.replace(checksum)
    return checksum


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.2f} MiB"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成不含密钥和运行数据的 Bili Remote 源码分享包"
    )
    parser.add_argument(
        "--output-dir",
        default="dist",
        help="压缩包输出目录，相对路径按项目根目录解析（默认：dist）",
    )
    args = parser.parse_args()

    try:
        version = _project_version()
        output_dir = Path(args.output_dir)
        if not output_dir.is_absolute():
            output_dir = PROJECT_ROOT / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        destination = output_dir / f"{PACKAGE_NAME}-{version}-{timestamp}.zip"
        if destination.exists():
            raise PackageError(f"目标文件已存在：{destination}")

        files = _collect_files()
        _write_archive(destination, files)
        try:
            checksum = _write_checksum(destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

        digest = _sha256(destination)
        print(f"压缩包：{destination}")
        print(f"校验文件：{checksum}")
        print(f"文件数：{len(files)}")
        size = destination.stat().st_size
        print(f"大小：{_format_size(size)}（{size} 字节）")
        print(f"SHA256：{digest}")
    except (OSError, PackageError, tomllib.TOMLDecodeError, zipfile.BadZipFile) as exc:
        print(f"打包失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
