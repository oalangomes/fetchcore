"""Basic invariants for the source image and the optional social preview exporter."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from scripts.brand.export_social_preview import SOURCE, SOURCE_DIMENSIONS, SOURCE_SHA256, OUTPUT_DIMENSIONS


def test_approved_social_preview_source_is_intact() -> None:
    raw = SOURCE.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert raw[12:16] == b"IHDR"
    assert struct.unpack(">II", raw[16:24]) == SOURCE_DIMENSIONS
    assert hashlib.sha256(raw).hexdigest() == SOURCE_SHA256


def test_social_preview_size_is_2_to_1() -> None:
    width, height = OUTPUT_DIMENSIONS
    assert (width, height) == (1280, 640)
    assert width == 2 * height
