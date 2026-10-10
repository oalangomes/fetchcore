"""Deterministic checks for the source-controlled visual identity kit."""

from __future__ import annotations

import struct
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1] / "assets" / "brand"
SOURCES = (
    "mark.svg",
    "mark-inverse.svg",
    "mark-mono.svg",
    "mark-mono-inverse.svg",
    "lockup-horizontal.svg",
    "lockup-horizontal-inverse.svg",
    "avatar.svg",
    "preview.svg",
)
PNG = {
    "avatar-256.png": (256, 256),
    "mark-128.png": (128, 128),
    "mark-inverse-128.png": (128, 128),
}


def test_brand_svg_assets_have_local_shapes_and_accessible_titles() -> None:
    for name in SOURCES:
        text = (ROOT / name).read_text(encoding="utf-8")
        element = ElementTree.fromstring(text)
        assert element.tag.endswith("svg"), name
        assert "viewBox" in element.attrib, name
        assert element.find("{http://www.w3.org/2000/svg}title") is not None, name
        assert "<script" not in text.lower(), name
        assert "http://" not in text.replace('xmlns="http://www.w3.org/2000/svg"', ""), name
        assert "https://" not in text, name
        assert "<image " not in text, name


def test_brand_png_exports_have_expected_dimensions() -> None:
    for name, expected in PNG.items():
        data = (ROOT / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
        assert data[12:16] == b"IHDR", name
        assert struct.unpack(">II", data[16:24]) == expected, name
