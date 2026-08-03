"""Build a minimal CP/M 2.2 bootable disk image.

Standard CP/M 2.2 SSSD 8" floppy geometry:
  - 77 tracks (numbered 0-76)
  - 26 sectors per track (numbered 1-26)
  - 128 bytes per sector
  - Total: 77 × 26 × 128 = 256,256 bytes

Disk layout:
  - Track 0, Sector 1: System sector (CCP+BDOS jump + system image)
  - Track 0, Sectors 2-N: Directory entries (16 entries × 32 bytes = 512 bytes = 4 sectors)
  - Track 1, Sector 1 onwards: Data blocks (allocation blocks are typically 1K = 8 sectors)

For a minimal empty disk:
  - Sector 1 contains the system loader
  - Directory sectors are filled with 0xE5 (empty)
  - All other sectors are zeros
"""

import struct
from pathlib import Path


# Standard CP/M 2.2 SSSD 8" floppy geometry
TRACKS = 77
SECTORS_PER_TRACK = 26
SECTOR_SIZE = 128
DISK_SIZE = TRACKS * SECTORS_PER_TRACK * SECTOR_SIZE  # 256,256 bytes

# Sector offset in image = (track × sectors_per_track + (sector - 1)) × sector_size
def sector_offset(track: int, sector: int) -> int:
    """Return byte offset in the disk image for the given track/sector.
    Sectors are 1-indexed (CP/M convention).
    """
    return (track * SECTORS_PER_TRACK + (sector - 1)) * SECTOR_SIZE


def build_empty_sssd_image() -> bytes:
    """Build a minimal empty CP/M 2.2 SSSD disk image.

    The image has:
      - Sector 1 of track 0: empty (will be filled by boot sector loader)
      - Directory sectors (track 0, sectors 2-4): filled with 0xE5 (empty entries)
      - All other sectors: zeros (unused)
    """
    img = bytearray(DISK_SIZE)

    # Fill directory sectors (track 0, sectors 2-4) with 0xE5 = "empty"
    for sector in range(2, 5):
        off = sector_offset(0, sector)
        for i in range(SECTOR_SIZE):
            img[off + i] = 0xE5

    return bytes(img)


def write_system_to_disk(img: bytearray, ccp_bytes: bytes, bdos_bytes: bytes) -> None:
    """Write the CCP+BDOS into track 0, sector 1.

    Standard CP/M 2.2 expects the system to be loaded starting at 0xE000.
    The system image is contiguous: CCP first, then BDOS.
    """
    # Combine CCP+BDOS into a single contiguous block
    system = ccp_bytes + bdos_bytes

    # The system image spans multiple sectors starting at sector 1 of track 0
    # The boot sector (sector 1) just contains the first 128 bytes
    # For our purposes, we put it all in sector 1 (we have 256 bytes of system)
    # The BIOS loader will read additional sectors as needed.

    # Actually for simplicity, we just put the system starting at sector 1 of
    # track 0. The system is small enough (5K) to fit in track 0 + part of track 1.

    total_sectors = (len(system) + SECTOR_SIZE - 1) // SECTOR_SIZE
    for i in range(total_sectors):
        track = i // SECTORS_PER_TRACK
        sector = (i % SECTORS_PER_TRACK) + 1
        off = sector_offset(track, sector)
        chunk = system[i * SECTOR_SIZE : (i + 1) * SECTOR_SIZE]
        img[off : off + len(chunk)] = chunk


def build_cpm_disk_image(
    ccp_bytes: bytes,
    bdos_bytes: bytes,
    bios_bytes: bytes,
) -> bytes:
    """Build a complete CP/M 2.2 bootable disk image with system pre-loaded.

    Layout:
      Track 0, Sector 1: System sector (CCP + BDOS + BIOS jump)
      Track 0, Sector 2-4: Directory (16 entries, all empty = 0xE5)
      Track 0, Sector 5+: Reserved
      Track 1+: Data blocks

    The boot loader (in our boot ROM stub) reads sector 1 of track 0 into
    memory at 0xE000 and jumps to it. The CCP then takes over.
    """
    img = bytearray(build_empty_sssd_image())

    # Build the full system image: CCP + BDOS + BIOS jump table
    # This is what gets loaded into high memory
    system = ccp_bytes + bdos_bytes + bios_bytes[:51]  # First 51 bytes = jump table

    # Write the system to track 0, starting at sector 1
    # The system will be loaded at 0xE000 by the boot loader
    total_sectors = (len(system) + SECTOR_SIZE - 1) // SECTOR_SIZE
    for i in range(total_sectors):
        track = i // SECTORS_PER_TRACK
        sector = (i % SECTORS_PER_TRACK) + 1
        off = sector_offset(track, sector)
        chunk = system[i * SECTOR_SIZE : (i + 1) * SECTOR_SIZE]
        img[off : off + len(chunk)] = chunk

    # Sector 1 also contains a small boot header. The first few bytes
    # tell the boot loader where to load and jump.
    # Format: JP <load_addr> at offset 0, then system follows
    # For our purposes, we just put the system directly.

    return bytes(img)


if __name__ == "__main__":
    # Demo: build an empty disk
    img = build_empty_sssd_image()
    out = Path("disk_images/EMPTY_SSSD.img")
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(img)
    print(f"Wrote {len(img)} bytes to {out}")
    print(f"  Geometry: {TRACKS} tracks × {SECTORS_PER_TRACK} sectors × {SECTOR_SIZE} bytes")
    print(f"  Directory: track 0, sectors 2-4 (filled with 0xE5)")
