#!/usr/bin/env python3
"""Build a remote plugin catalog from the plugins in catalog/.

    python tools/build_catalog.py out/                      # zips + index.json with relative urls
    python tools/build_catalog.py out/ --base-url https://example.com/hexcast/

Upload the contents of out/ anywhere that serves static files, then put the address of
index.json under "catalogs" in config/plugins.json on any Hexcast that should offer these
plugins (see docs/plugins.md -> Catalogs). Zips are byte-for-byte reproducible, so an unchanged
plugin keeps its checksum from one build to the next.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hexcast_core.catalog import IGNORE_NAMES, IGNORE_SUFFIXES  # noqa: E402
from hexcast_core.manifest import API_VERSION, ManifestError, load_manifest  # noqa: E402


def build_zip(folder: Path, dest: Path) -> str:
    """Zip a plugin folder with fixed timestamps; returns the zip's sha-256."""
    files = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if not path.is_file() or any(part in IGNORE_NAMES for part in rel.parts) or path.name.endswith(IGNORE_SUFFIXES):
            continue
        files.append((rel.as_posix(), path))
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, path in files:
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return hashlib.sha256(dest.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", type=Path, help="folder to write the zips and index.json into")
    ap.add_argument("--base-url", default="", help="where the zips will be served from (default: next to index.json)")
    ap.add_argument("--catalog", type=Path, default=ROOT / "catalog", help="plugins folder (default: catalog/)")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip("/") + "/" if args.base_url else ""
    plugins, failed = [], 0
    for folder in sorted(p for p in args.catalog.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
        try:
            manifest = load_manifest(folder)
        except ManifestError as exc:
            print(f"  skipped {folder.name}: {exc}", file=sys.stderr)
            failed += 1
            continue
        name = f"{manifest.id}-{manifest.version}.zip"
        sha = build_zip(folder, args.out / name)
        plugins.append({"manifest": manifest.raw, "url": base + name, "sha256": sha})
        print(f"  {name}  {sha[:12]}")
    (args.out / "index.json").write_text(json.dumps({"api": API_VERSION, "plugins": plugins}, indent=2), encoding="utf-8")
    print(f"wrote {len(plugins)} plugins to {args.out / 'index.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
