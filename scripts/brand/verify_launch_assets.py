"""Validate the exact seven approved FetchCore launch PNG assets.

Run after PNGs from the launch-image bundle have been added to assets/launch/.
This is an explicit import check, not a CI gate before the images are committed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from struct import unpack

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "assets" / "launch" / "manifest.json"


def main() -> None:
    assets = json.loads(MANIFEST.read_text(encoding="utf-8"))["assets"]
    failures = []
    for item in assets:
        path = REPO / item["path"]
        if not path.is_file():
            failures.append(f"MISSING: {item['path']}")
            continue
        data = path.read_bytes()
        if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            failures.append(f"INVALID PNG: {item['path']}")
            continue
        shape = unpack(">II", data[16:24])
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != item["size_bytes"] or list(shape) != item["dimensions_px"] or digest != item["sha256"]:
            failures.append(f"MISMATCH: {item['path']}")
        else:
            print(f"OK: {item['path']} ({shape[0]}x{shape[1]})")
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"Validated {len(assets)} launch assets")


if __name__ == "__main__":
    main()
