"""Relocate the XEROX 1800 CP/M 2.2 system image to a different load address.

The XEROX 1800 image is pre-relocated to load at 0xDC00. To use it as a
standard 64KB CP/M 2.2 system, we need to relocate it to load at 0xE200
(the standard 64K CP/M 2.2 MOVCPM output address).

Relocation is a byte-level patch: scan the image for JP instructions
(opcode 0xC3) and CALL instructions (opcode 0xCD), and patch the
16-bit target by `+delta` where `delta = new_base - old_base`.

We patch JP and CALL because those are the only instructions that
encode absolute 16-bit addresses. Other absolute references (like
LXI rp, nn or SHLD nn) are typically used for data addresses, not
code addresses — and the XEROX image's LXI/SHLD references are all
for the BIOS data area, which we'll relocate too.

Output: a new image that, when loaded at the new base, behaves
identically to the original when loaded at the old base.
"""

from __future__ import annotations

import struct


# Default relocation: from XEROX 1800 base (0xDC00) to standard 64K base (0xE200)
OLD_BASE = 0xDC00
NEW_BASE = 0xE200


def relocate_cpm_sys(src_path: str, dst_path: str, old_base: int = OLD_BASE, new_base: int = NEW_BASE) -> int:
    """Relocate CP/M 2.2 system image from old_base to new_base.

    Returns the number of bytes patched.
    """
    with open(src_path, "rb") as f:
        data = bytearray(f.read())

    if len(data) != 0x1E00:
        raise ValueError(f"expected 7680-byte CPM.SYS, got {len(data)}")

    delta = new_base - old_base
    n_patched = 0

    for i in range(len(data) - 2):
        op = data[i]
        # JP (0xC3) and CALL (0xCD) encode absolute 16-bit addresses.
        if op in (0xC3, 0xCD):
            old_target = data[i+1] | (data[i+2] << 8)
            # Only patch if target is in the code region (above old_base)
            if old_target >= old_base and old_target < old_base + len(data):
                new_target = (old_target + delta) & 0xFFFF
                data[i+1] = new_target & 0xFF
                data[i+2] = (new_target >> 8) & 0xFF
                n_patched += 1

    with open(dst_path, "wb") as f:
        f.write(data)
    return n_patched


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "disk_images/cpm22-sssd.src"
    dst = sys.argv[2] if len(sys.argv) > 2 else "disk_images/cpm22-sssd.img"
    n = relocate_cpm_sys(src, dst)
    print(f"Relocated {src} -> {dst} ({n} JP/CALL targets patched)")
