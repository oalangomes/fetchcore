"""Export the approved FetchCore 2:1 social preview for GitHub (1280x640).

Run from anywhere:
    python -m pip install Pillow
    python scripts/brand/export_social_preview.py --output /tmp/fetchcore-social-preview.png

Requires Pillow only for this optional branding operation; no runtime dependency.
The 1774x887 source is never modified.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

SOURCE_SHA256 = "c932512501d69d5ec83a30cd0dc014c7c4bdb97fad8d36faa48491b0a3675196"
SOURCE_DIMENSIONS = (1774, 887)
OUTPUT_DIMENSIONS = (1280, 640)
SOURCE = Path(__file__).resolve().parents[2] / "assets" / "launch" / "github" / "social-preview.png"


def export(output: Path) -> None:
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        raise ValueError("The approved source PNG has changed: refusing silent replacement")

    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required only for this export: pip install Pillow") from exc

    with Image.open(SOURCE) as im:
        if im.size != SOURCE_DIMENSIONS:
            raise ValueError(f"Unexpected source dimensions: {im.size!r}")
        if im.format != "PNG":
            raise ValueError("Expected approved PNG source")
        image = im.convert("RGB").resize(OUTPUT_DIMENSIONS, Image.Resampling.LANCZOS)
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="PNG", optimize=True)
        print(f"Exported {output} ({OUTPUT_DIMENSIONS[0]}x{OUTPUT_DIMENSIONS[1]})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="PNG destination, outside tracked source")
    args = parser.parse_args()
    export(args.output)


if __name__ == "__main__":
    main()
