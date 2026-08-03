"""CP/M 2.2 Disk Parameter Header (DPH) and Disk Parameter Block (DPB).

CP/M 2.2 disk geometry data structures. These are read by BDOS during
disk initialization (via the BIOS seldsk function).

When BIOS seldsk is called, it returns a pointer (in HL) to a 16-byte
Disk Parameter Header (DPH). The DPH contains pointers to:
  - sector translation table (.tran)
  - scratch words (.cdrmax, .curtrk, .currec, .buffa)
  - Disk Parameter Block (.dpbaddr)

The DPB (15 bytes) contains the actual disk geometry:
  spt  - sectors per track
  bsh  - block shift factor (3 = 1024 byte blocks)
  blm  - block mask (7 = 1024 byte blocks)
  exm  - extent mask (0 = 1024 byte blocks)
  dsm  - total disk size in blocks - 1
  drm  - max directory entry number (0-based)
  al0, al1 - allocation vector (first 2 bytes)
  cks  - checksum vector size (0 = fixed, 2 = removable)
  off  - reserved tracks offset
  psh  - physical sector shift (7 = 128 byte sectors)
  phm  - physical sector mask

For 8" SSSD CP/M 2.2:
  spt=26, bsh=3, blm=7, exm=0, dsm=242 (243 blocks of 1K = 248KB)
  drm=63 (64 directory entries), al0=11000000b, al1=0
  cks=0 (fixed disk), off=2 (2 reserved tracks)
  psh=7, phm=127 (128 byte sectors)

For more details, see Digital Research CP/M 2.2 Interface Guide, Section 4.
"""

from dataclasses import dataclass


@dataclass
class DiskGeometry:
    """8" SSSD floppy disk geometry for CP/M 2.2."""

    spt: int   # sectors per track
    bsh: int   # block shift factor
    blm: int   # block mask
    exm: int   # extent mask
    dsm: int   # disk size - 1 (in blocks)
    drm: int   # directory max (0-based; 64 entries = drm=63)
    al0: int   # allocation vector byte 0
    al1: int   # allocation vector byte 1
    cks: int   # checksum size (0=fixed, 2=removable)
    off: int   # reserved tracks
    psh: int   # sector shift (7 = 128 byte sectors)
    phm: int   # sector mask


# Standard 8" SSDD single-density floppy geometry for CP/M 2.2
# (matches Digital Research's CP/M 2.2 distribution disk)
SSSD_8INCH = DiskGeometry(
    spt=26,    # 26 sectors per track
    bsh=3,     # 1024-byte allocation blocks (2^3)
    blm=7,     # block mask = 2^bsh - 1
    exm=0,     # extent mask = 0 for 1024-byte blocks
    dsm=242,   # 243 blocks of 1K = 248KB total
    drm=63,    # 64 directory entries (0-63)
    al0=0xC0,  # 11000000b = first 2 directory entries reserved
    al1=0x00,  # no more reserved
    cks=0,     # fixed disk (no checksum vector needed)
    off=2,     # 2 reserved tracks (system + directory)
    psh=0,     # standard 128-byte sectors (no shift needed)
    phm=0,     # standard 128-byte sectors (no mask needed)
)


def build_dph(geom: DiskGeometry, scratch_base: int, dpb_base: int,
              tran_base: int, dir_buf_base: int) -> bytes:
    """Build a 16-byte DPH structure.

    Args:
        geom: disk geometry
        scratch_base: base address of 4 scratch words (8 bytes) for BDOS
                     to fill with: cdrmax, curtrk, currec, (reserved).
                     In standard CP/M, these are filled by BDOS at runtime.
        dpb_base: address of 16-byte DPB
        tran_base: address of sector translation table (spt entries × 2 bytes)
        dir_buf_base: address of 128-byte directory scratch buffer

    Returns:
        16-byte DPH structure.
    """
    dph = bytearray(16)
    # +0,1: .tran - sector translate table pointer
    dph[0] = tran_base & 0xFF
    dph[1] = (tran_base >> 8) & 0xFF
    # +2,3: scratch word 1 (filled by BDOS with max dir entry)
    dph[2] = scratch_base & 0xFF
    dph[3] = (scratch_base >> 8) & 0xFF
    # +4,5: scratch word 2 (current track)
    dph[4] = (scratch_base + 2) & 0xFF
    dph[5] = ((scratch_base + 2) >> 8) & 0xFF
    # +6,7: scratch word 3 (current record)
    dph[6] = (scratch_base + 4) & 0xFF
    dph[7] = ((scratch_base + 4) >> 8) & 0xFF
    # +8,9: .buffa - pointer to disk DMA buffer (128 bytes)
    # Standard CP/M uses 0x0080 (the default DMA buffer). CCP/BDOS set
    # this via BDOS function 26 (SETDMA) before reading/writing sectors.
    dph[8] = 0x80
    dph[9] = 0x00
    # +10,11: .dpbaddr - DPB pointer
    dph[10] = dpb_base & 0xFF
    dph[11] = (dpb_base >> 8) & 0xFF
    # +12,13: .csv - checksum vector base (0 for fixed disk)
    dph[12] = 0
    dph[13] = 0
    # +14,15: .alv - allocation vector base
    dph[14] = dir_buf_base & 0xFF
    dph[15] = (dir_buf_base >> 8) & 0xFF
    return bytes(dph)


def build_dpb(geom: DiskGeometry) -> bytes:
    """Build a 17-byte DPB (Disk Parameter Block).

    The Digital Research CP/M 2.2 BDOS has a quirk: its `sectpt` local
    variable is declared as `ds word` (2 bytes) and is loaded with LHLD,
    so it grabs 2 bytes from the DPB. For 8" SSSD with SPT=26, only the
    low byte is meaningful. The standard DRI DPB layout places a 1-byte
    padding between SPT and BSH to keep `sectpt` = SPT (not SPT+BSH*256).

    The actual DPB layout that aligns with BDOS's sectpt labels:
      +0:  SPT  (1 byte)  - sectors per track
      +1:  0    (1 byte)  - padding (NOT BSH! sectpt word needs high byte = 0)
      +2:  BSH  (1 byte)  - block shift
      +3:  BLM  (1 byte)  - block mask
      +4:  EXM  (1 byte)  - extent mask
      +5:  DSM  (2 bytes LE) - aligned with BDOS maxall word at sectpt+5,6
      +7:  DRM  (2 bytes LE)
      +9:  AL0  (1 byte)
      +10: AL1  (1 byte)
      +11: CKS  (2 bytes LE)
      +13: OFF  (2 bytes LE)
      +15: PSH  (1 byte)
      +16: PHM  (1 byte)

    BDOS copies 15 bytes (dpblist) of this into sectpt:
      sectpt+0,1 = (word): SPT, 0          (SPT as word)
      sectpt+2 = BSH                      (BDOS blkshf)
      sectpt+3 = BLM                      (BDOS blkmsk)
      sectpt+4 = EXM                      (BDOS extmsk)
      sectpt+5,6 = DSM                    (BDOS maxall — correct!)
      sectpt+7,8 = DRM                    (BDOS dirmax)
      sectpt+9,10 = AL0, AL1              (BDOS dirblk)
      sectpt+11,12 = CKS                  (BDOS chksiz)
      sectpt+13,14 = OFF                  (BDOS offset)
    """
    return bytes([
        geom.spt & 0xFF,         # +0:  SPT
        0,                       # +1:  padding (NOT bsh!)
        geom.bsh & 0xFF,         # +2:  BSH
        geom.blm & 0xFF,         # +3:  BLM
        geom.exm & 0xFF,         # +4:  EXM
        geom.dsm & 0xFF,         # +5:  DSM low
        (geom.dsm >> 8) & 0xFF,  # +6:  DSM high
        geom.drm & 0xFF,         # +7:  DRM low
        (geom.drm >> 8) & 0xFF,  # +8:  DRM high
        geom.al0 & 0xFF,         # +9:  AL0
        geom.al1 & 0xFF,         # +10: AL1
        geom.cks & 0xFF,         # +11: CKS low
        (geom.cks >> 8) & 0xFF,  # +12: CKS high
        geom.off & 0xFF,         # +13: OFF low
        (geom.off >> 8) & 0xFF,  # +14: OFF high
        geom.psh & 0xFF,         # +15: PSH
        geom.phm & 0xFF,         # +16: PHM
    ])


def build_dpb_with_phm(geom: DiskGeometry) -> bytes:
    """16-byte DPB including physical sector mask at the end."""
    dpb = bytearray(build_dpb(geom))
    dpb.append(geom.phm & 0xFF)
    return bytes(dpb)


def build_tran_table(geom: DiskGeometry) -> bytes:
    """Build sector translation table (skew of spt entries).

    The default is identity (sector N → sector N). For real 8" drives
    you'd typically use a 6-sector skew (1,7,13,19,25,5,11,17,23,3,...).
    For simplicity, we use identity.
    """
    return bytes(range(1, geom.spt + 1))  # 1-indexed sectors


def build_csv(geom: DiskGeometry) -> bytes:
    """Build checksum vector (only for removable disks with cks>0).

    For fixed disks (cks=0), this is empty.
    """
    if geom.cks == 0:
        return bytes()
    # Checksum vector size = cks * (dsm+1) / 4 bytes (one bit per block)
    total_blocks = geom.dsm + 1
    csv_size = (geom.cks * total_blocks) // 4
    return bytes(csv_size)


def build_alv(geom: DiskGeometry) -> bytes:
    """Build allocation vector (one bit per block).

    For 8" SSSD: 243 blocks = 31 bytes (rounded up).
    """
    total_blocks = geom.dsm + 1
    alv_size = (total_blocks + 7) // 8
    # Initialize all blocks as free (1 = free, 0 = used)
    return bytes([0xFF] * alv_size)


# ---------------------------------------------------------------------------
# Convenience: build everything for an 8" SSSD geometry into a contiguous block
# ---------------------------------------------------------------------------

def build_disk_layout(geom: DiskGeometry = SSSD_8INCH) -> dict:
    """Build all disk structures and return their layout.

    Returns a dict with:
      'dph': 16 bytes (Disk Parameter Header)
      'dpb': 15 bytes (Disk Parameter Block)
      'tran': spt bytes (sector translation table)
      'csv': n bytes (checksum vector; empty for fixed disks)
      'alv': n bytes (allocation vector)
      'scratch': 8 bytes (BDOS scratch words)
      'addresses': dict with the absolute address for each structure
    """
    # We'll place these at high memory, below the BIOS jump table
    # BIOS jump table is at 0xF000, BDOS variables are at 0xEB00-0xEB60
    # Place disk structures in the 0xE900-0xEAFF range (free area)
    base = 0xE900

    # Sector translation table (spt bytes, 2 bytes each = spt*2)
    tran_bytes = build_tran_table(geom)
    tran_table = bytearray()
    for s in tran_bytes:
        tran_table.append(s & 0xFF)
        tran_table.append((s >> 8) & 0xFF)
    tran_base = base
    base += len(tran_table)

    # DPB
    dpb = build_dpb_with_phm(geom)
    dpb_base = base
    base += len(dpb)

    # Checksum vector (empty for fixed disk)
    csv = build_csv(geom)
    csv_base = base if csv else 0
    base += len(csv)

    # Allocation vector
    alv = build_alv(geom)
    alv_base = base
    base += len(alv)

    # BDOS scratch words (8 bytes for .cdrmax, .curtrk, .currec, .buffa)
    scratch_base = base
    base += 8

    # Directory buffer (128 bytes)
    dir_buf_base = base
    base += 128

    # Build the DPH pointing to all the above
    dph = build_dph(geom, scratch_base, dpb_base, tran_base, dir_buf_base)

    return {
        'dph': dph,
        'dpb': dpb,
        'tran': bytes(tran_table),
        'csv': csv,
        'alv': alv,
        'scratch': bytes(8),
        'addresses': {
            'tran_base': tran_base,
            'dpb_base': dpb_base,
            'csv_base': csv_base,
            'alv_base': alv_base,
            'scratch_base': scratch_base,
            'dir_buf_base': dir_buf_base,
        }
    }


if __name__ == "__main__":
    layout = build_disk_layout()
    print(f"DPH: {len(layout['dph'])} bytes")
    print(f"DPB: {len(layout['dpb'])} bytes")
    print(f"Tran: {len(layout['tran'])} bytes")
    print(f"CSV: {len(layout['csv'])} bytes")
    print(f"ALV: {len(layout['alv'])} bytes")
    print(f"Addrs: {layout['addresses']}")
    print(f"\nDPH bytes: {layout['dph'].hex()}")
    print(f"DPB bytes: {layout['dpb'].hex()}")
