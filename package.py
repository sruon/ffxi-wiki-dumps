"""Gzip the dumps in a directory and write manifest.json + notes.md beside them."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def package(dist: Path) -> list[dict]:
    files = []
    for src in sorted(dist.glob("*.jsonl")):
        with src.open("rb") as f:
            lines = sum(1 for _ in f)
        dst = src.with_suffix(".jsonl.gz")
        with src.open("rb") as f, gzip.open(dst, "wb", compresslevel=9) as out:
            shutil.copyfileobj(f, out)
        src.unlink()
        files.append({
            "name": dst.name,
            "lines": lines,
            "bytes": dst.stat().st_size,
            "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
        })
    return files


def main() -> None:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    files = package(dist)
    if not files:
        sys.exit(f"No *.jsonl found in {dist}")

    manifest = {
        "corpus": "ffxi-wiki-dumps",
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
    }
    (dist / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    rows = "\n".join(
        "| `{name}` | {lines:,} | {mb:.1f} MB |".format(mb=f["bytes"] / 1048576, **f)
        for f in files
    )
    Path("notes.md").write_text(
        "Full re-scrape of all three wikis. Decompress before use: `gunzip *.gz`\n\n"
        "| File | Lines | Size |\n|---|---:|---:|\n"
        f"{rows}\n\nPer-file SHA-256 and exact byte counts: `manifest.json`.\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
