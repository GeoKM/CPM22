"""8" floppy disk image handling.

Two formats supported (skill §9, IMSAI hardware profile):
- 8" SSSD 256KB: 26 sectors × 77 tracks × 128 bytes = 253,952 bytes
- 8" SSDD 512KB: 26 sectors × 77 tracks × 256 bytes = 512,256 bytes

Auto-detect by total size. Read/write a single sector at (track, sector).
"""

from __future__ import annotations

import os
from enum import Enum


class FloppyFormat(Enum):
    SSSD_8 = "sssd_8"   # 8" Single-Sided Single-Density, 128-byte sectors
    SSDD_8 = "ssdd_8"   # 8" Single-Sided Double-Density, 256-byte sectors


# IBM 8" standard sector skew — 26 sectors.
# Without skew, sequential reads time out (CPU waits a full rotation between
# sectors because they're sequential on disk). This is the canonical skew
# that CP/M 2.2 expects.
SECTOR_SKEW_26 = [
    1, 7, 13, 19, 25, 5, 11, 17, 23, 3, 9, 15, 21,
    2, 8, 14, 20, 26, 6, 12, 18, 24, 4, 10, 16, 22,
]


def detect_format(size: int) -> FloppyFormat:
    if size == 256256:  # 77 * 26 * 128
        return FloppyFormat.SSSD_8
    if size == 512512:  # 77 * 26 * 256
        return FloppyFormat.SSDD_8
    raise ValueError(
        f"Unknown 8\" floppy size: {size} bytes "
        f"(expected 256256 for SSSD or 512512 for SSDD)"
    )


class FloppyImage:
    """In-memory or file-backed 8" floppy image.

    Track numbering: 0..76 (77 tracks total).
    Sector numbering: 1..26 (26 sectors per track, post-skew).
    """

    def __init__(self, fmt: FloppyFormat, data: bytearray | None = None):
        self.fmt = fmt
        if fmt == FloppyFormat.SSSD_8:
            self.sector_size = 128
            self.sectors_per_track = 26
            self.tracks = 77
        elif fmt == FloppyFormat.SSDD_8:
            self.sector_size = 256
            self.sectors_per_track = 26
            self.tracks = 77
        else:
            raise ValueError(f"Unsupported format: {fmt}")
        self.data = data if data is not None else bytearray(self.sector_size * self.sectors_per_track * self.tracks)
        # In-memory tracking
        self.write_protect = False
        self.motor_on = False
        self.current_track = 0
        self.current_sector = 1  # 1-based
        self.dma_addr = 0x0080  # default CP/M DMA

    # ------------------------------------------------------------------
    # Construction from file / to file
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: str) -> "FloppyImage":
        with open(path, "rb") as f:
            data = bytearray(f.read())
        fmt = detect_format(len(data))
        return cls(fmt, data)

    @classmethod
    def blank(cls, fmt: FloppyFormat) -> "FloppyImage":
        size = 256256 if fmt == FloppyFormat.SSSD_8 else 512512
        return cls(fmt, bytearray(size))

    def to_file(self, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self.data)

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def track_offset(self, track: int) -> int:
        """Byte offset of the start of a given track."""
        return track * self.sectors_per_track * self.sector_size

    def sector_offset(self, track: int, sector: int) -> int:
        """Byte offset of a (track, sector) pair. Sectors are 1-based.

        Per CP/M 2.2: sectors on disk are in skew order. SECTOR_SKEW_26 maps
        logical sector (1..26) to physical position on the track.
        """
        if not (1 <= sector <= self.sectors_per_track):
            raise ValueError(f"sector {sector} out of range 1..{self.sectors_per_track}")
        physical_index = SECTOR_SKEW_26[sector - 1] - 1
        return self.track_offset(track) + physical_index * self.sector_size

    # ------------------------------------------------------------------
    # Read / write sectors
    # ------------------------------------------------------------------

    def read_sector(self, track: int, sector: int) -> bytes:
        if not (0 <= track < self.tracks):
            raise ValueError(f"track {track} out of range 0..{self.tracks - 1}")
        off = self.sector_offset(track, sector)
        end = off + self.sector_size
        if end > len(self.data):
            raise ValueError(
                f"sector offset {off:#x} + {self.sector_size} > image size {len(self.data):#x} "
                f"(track={track}, sector={sector})"
            )
        return bytes(self.data[off:off + self.sector_size])

    def write_sector(self, track: int, sector: int, buf: bytes) -> None:
        if self.write_protect:
            raise IOError("disk is write protected")
        if len(buf) != self.sector_size:
            raise ValueError(
                f"write_sector expects {self.sector_size} bytes, got {len(buf)}"
            )
        off = self.sector_offset(track, sector)
        end = off + self.sector_size
        if end > len(self.data):
            raise ValueError(
                f"sector offset {off:#x} + {self.sector_size} > image size {len(self.data):#x} "
                f"(track={track}, sector={sector})"
            )
        self.data[off:off + self.sector_size] = buf

    # ------------------------------------------------------------------
    # DMA-style read (read a sector into the memory's DMA address)
    # ------------------------------------------------------------------

    def read_to_dma(self, mem, track: int, sector: int) -> None:
        buf = self.read_sector(track, sector)
        for i, b in enumerate(buf):
            mem.wb(self.dma_addr + i, b)

    def write_from_dma(self, mem, track: int, sector: int) -> None:
        buf = bytes(mem.rb(self.dma_addr + i) for i in range(self.sector_size))
        self.write_sector(track, sector, buf)

    # ------------------------------------------------------------------
    # Skew translation (BIOS SECTRAN call)
    # ------------------------------------------------------------------

    @staticmethod
    def translate_sector(sector: int) -> int:
        """CP/M 2.2 BIOS SECTRAN: translate logical to physical sector.

        Per Intel's standard, SECTRAN returns the physical sector for a
        given logical sector. The skew table is indexed by (logical - 1)
        in some references, by (logical) in others. The XEROX 1800 BIOS
        and most CP/M 2.2 implementations index 0-based: sector 1 → skew[0].
        For our standard skew, this is a no-op transform (the table already
        gives 1-based results), so we return the same value.

        Sector 0 is treated as sector 26 (the highest sector wraps to
        the first); this matches the behavior of real CP/M 2.2 BIOSes
        when BDOS passes a 0 sector number.
        """
        if sector == 0:
            sector = 26
        if not (1 <= sector <= 26):
            raise ValueError(f"sector {sector} out of range 1..26")
        return SECTOR_SKEW_26[sector - 1]

    def __repr__(self) -> str:
        return (
            f"FloppyImage(fmt={self.fmt.value}, tracks={self.tracks}, "
            f"sectors={self.sectors_per_track}, sector_size={self.sector_size}, "
            f"size={len(self.data)})"
        )
