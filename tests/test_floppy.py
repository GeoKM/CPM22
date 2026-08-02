"""Tests for 8" floppy image handling (M2)."""

from __future__ import annotations

import os
import tempfile

import pytest

from cpm22.floppy import (
    SECTOR_SKEW_26,
    FloppyFormat,
    FloppyImage,
    detect_format,
)


def test_sssd_format_detection():
    # SSSD = 77 * 26 * 128 = 256,256
    assert detect_format(256256) == FloppyFormat.SSSD_8


def test_ssdd_format_detection():
    # SSDD = 77 * 26 * 256 = 512,512
    assert detect_format(512512) == FloppyFormat.SSDD_8


def test_unknown_format_raises():
    with pytest.raises(ValueError):
        detect_format(123456)


def test_blank_sssd_size():
    img = FloppyImage.blank(FloppyFormat.SSSD_8)
    assert len(img.data) == 256256
    assert img.sector_size == 128
    assert img.sectors_per_track == 26
    assert img.tracks == 77


def test_blank_ssdd_size():
    img = FloppyImage.blank(FloppyFormat.SSDD_8)
    assert len(img.data) == 512512
    assert img.sector_size == 256


def test_write_read_sector():
    img = FloppyImage.blank(FloppyFormat.SSSD_8)
    # Write a pattern into track 0, sector 1
    pattern = bytes(range(128))
    img.write_sector(0, 1, pattern)
    # Read it back
    read = img.read_sector(0, 1)
    assert read == pattern


def test_write_sector_wrong_size():
    img = FloppyImage.blank(FloppyFormat.SSSD_8)
    with pytest.raises(ValueError):
        img.write_sector(0, 1, b"\x00" * 100)  # wrong size


def test_sector_out_of_range():
    img = FloppyImage.blank(FloppyFormat.SSSD_8)
    with pytest.raises(ValueError):
        img.read_sector(0, 0)  # sectors are 1-based
    with pytest.raises(ValueError):
        img.read_sector(0, 27)  # max is 26


def test_track_out_of_range():
    img = FloppyImage.blank(FloppyFormat.SSSD_8)
    with pytest.raises(ValueError):
        img.read_sector(77, 1)  # tracks 0..76


def test_sector_skew_26_canonical():
    """The skew table must be the canonical 26-element CP/M 2.2 skew."""
    assert len(SECTOR_SKEW_26) == 26
    assert sorted(SECTOR_SKEW_26) == list(range(1, 27))
    # Spot-check a few values
    assert SECTOR_SKEW_26[0] == 1     # logical 1 → physical 1
    assert SECTOR_SKEW_26[1] == 7     # logical 2 → physical 7
    assert SECTOR_SKEW_26[12] == 21   # logical 13 → physical 21
    assert SECTOR_SKEW_26[13] == 2    # logical 14 → physical 2


def test_translate_sector():
    img = FloppyImage.blank(FloppyFormat.SSSD_8)
    assert img.translate_sector(1) == 1
    assert img.translate_sector(2) == 7
    assert img.translate_sector(13) == 21
    assert img.translate_sector(14) == 2


def test_write_protect():
    img = FloppyImage.blank(FloppyFormat.SSSD_8)
    img.write_protect = True
    with pytest.raises(IOError):
        img.write_sector(0, 1, b"\x00" * 128)


def test_round_trip_file():
    img = FloppyImage.blank(FloppyFormat.SSSD_8)
    img.write_sector(0, 1, b"A" * 128)
    img.write_sector(5, 13, b"B" * 128)
    img.write_sector(76, 26, b"C" * 128)
    with tempfile.NamedTemporaryFile(suffix=".img", delete=False) as f:
        path = f.name
    try:
        img.to_file(path)
        img2 = FloppyImage.from_file(path)
        assert img2.read_sector(0, 1) == b"A" * 128
        assert img2.read_sector(5, 13) == b"B" * 128
        assert img2.read_sector(76, 26) == b"C" * 128
    finally:
        os.unlink(path)


def test_read_to_dma_via_memory():
    """Verify the DMA-style read path works against the Memory object."""
    from cpm22.memory import Memory

    img = FloppyImage.blank(FloppyFormat.SSSD_8)
    img.write_sector(0, 1, bytes([0xAA, 0xBB] + [0x00] * 126))
    mem = Memory()
    img.read_to_dma(mem, 0, 1)
    assert mem.rb(img.dma_addr) == 0xAA
    assert mem.rb(img.dma_addr + 1) == 0xBB


def test_write_from_dma_via_memory():
    from cpm22.memory import Memory

    img = FloppyImage.blank(FloppyFormat.SSSD_8)
    mem = Memory()
    mem.wb(img.dma_addr, 0xCC)
    mem.wb(img.dma_addr + 1, 0xDD)
    img.write_from_dma(mem, 0, 1)
    assert img.read_sector(0, 1)[0] == 0xCC
    assert img.read_sector(0, 1)[1] == 0xDD
