"""Rebuild optional FetchCore PNG brand exports from canonical SVG assets.

Usage: python scripts/brand/export.py
Optional dependency (not a runtime requirement): python -m pip install cairosvg
"""

from __future__ import annotations

from pathlib import Path


def main() -> None:
    try:
        import cairosvg
    except ImportError as exc:
        raise SystemExit("Install the optional export dependency: pip install cairosvg") from exc

    root = Path(__file__).resolve().parents[2] / "assets" / "brand"
    targets = [
        ("avatar.svg", "avatar-256.png", 256),
        ("avatar.svg", "avatar-512.png", 512),
        ("mark.svg", "mark-128.png", 128),
        ("mark-inverse.svg", "mark-inverse-128.png", 128),
    ]
    for source, target, size in targets:
        cairosvg.svg2png(
            bytestring=(root / source).read_bytes(),
            write_to=str(root / target),
            output_width=size,
            output_height=size,
        )
        print(f"{source} -> {target} ({size}px)")


if __name__ == "__main__":
    main()
