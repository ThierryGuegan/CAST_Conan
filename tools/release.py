#!/usr/bin/env python3
"""Build a deterministic source ZIP and its SHA-256 sidecar."""

import hashlib
import re
import stat
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build"}


def main():
    text = (ROOT / "cast_offline_collector.py").read_text(encoding="utf-8")
    match = re.search(r'^COLLECTOR_VERSION = "([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("COLLECTOR_VERSION not found")
    version = match.group(1)
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    archive = destination / f"cast-imaging-offline-collector-{version}.zip"
    prefix = f"cast-imaging-offline-collector-{version}"
    files = sorted(
        path for path in ROOT.rglob("*")
        if path.is_file() and not any(part in EXCLUDED_PARTS for part in path.relative_to(ROOT).parts)
    )
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(f"{prefix}/{relative}", date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if path.suffix == ".py" and path.read_bytes().startswith(b"#!") else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            output.writestr(info, path.read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(archive)
    print(checksum)


if __name__ == "__main__":
    main()
