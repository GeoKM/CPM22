"""Pre-boot ROM stub that prints 'No disk — insert boot disk' before CP/M boots.

When the DR CP/M 2.2 system is loaded without a disk mounted, BDOS init fails
because it can't read the directory. This stub intercepts the cold boot vector
at 0x0000 and:

  1. Prints 'No disk in drive A.' + 'Insert boot disk and press any key.'
  2. Waits for keyboard input via the BIOS const + conin functions.
  3. Echoes the key, prints a newline, and jumps to CCP at 0xE000.

The stub lives at 0x0100 (start of TPA, unused by CP/M). The vectors at
0x0000-0x0007 are patched to:
  0x0000: DI; JMP 0x0100   (cold boot → stub)
  0x0006: JMP 0xE800        (BDOS entry — unchanged)

The stub calls BIOS via port 0xF1 (BIOS dispatch) and BDOS via port 0xF0.
"""

from cpm22.asm8080_d import Assembler
from pathlib import Path
import re
import tempfile


STUB_SOURCE = r"""
        org     0100h
stub:
        lxi     sp, 0200h
        ; Check the 'disk-mounted' flag at 0x007F. If non-zero, skip the
        ; prompt and jump straight to CCP. The loader sets this flag
        ; (via self.mem.wb(0x007F, 0x01)) when a disk is auto-mounted.
        ; NOTE: 0x007F is used instead of 0x0080 because 0x0080 is the
        ; CP/M DMA buffer and BDOS overwrites it during directory reads.
        lda     007Fh
        ora     a
        jnz     skipmsg

        lxi     h, msg
        call    printstr
waitkey:
        mvi     a, 2            ; BIOS const
        out     0F1h
        ora     a
        jz      waitkey
        mvi     a, 3            ; BIOS conin
        out     0F1h
        mov     e, a
        mvi     a, 2            ; BDOS pchar
        out     0F0h
        mvi     a, 0Dh
        mov     e, a
        mvi     a, 2
        out     0F0h
        mvi     a, 0Ah
        mov     e, a
        mvi     a, 2
        out     0F0h
skipmsg:
        ; Set C = 0 (drive A, user 0) before jumping to CCP.
        ; CCP's ccpstart reads C to extract the initial disk and user code.
        mvi     c, 0
        jmp     0E000h          ; to CCP
printstr:
        mov     a, m
        ora     a
        rz
        mov     e, a
        mvi     a, 2            ; BDOS pchar
        out     0F0h
        inx     h
        jmp     printstr
msg:    db      'No disk in drive A.', 13, 10
        db      'Insert boot disk and press any key.', 13, 10, 0
"""

# Vectors at 0x0000-0x0007 (BDOS stub that handles return for JMP-based callers)
# Layout:
#   0x0000: cold-boot entry → DI; JMP 0x0100 (boot stub)
#   0x0005: BDOS entry → CALL 0xE800; RET
# When CCP does `JMP bdos` (= JMP 0x0005), this stub CALLs BDOS so a
# return address is pushed, then RETs back to the CCP after BDOS finishes.
# Without this, BDOS's `retmon` pops garbage off the stack (typically 0x0000,
# the cold-boot vector) and the system loops.
BOOT_VECTORS = bytes([
    0xF3,                     # 0x0000: DI
    0xC3, 0x00, 0x01,         # 0x0001: JMP 0x0100 (to stub)
    0x00,                     # 0x0004: (padding)
    0xCD, 0x00, 0xE8,         # 0x0005: CALL 0xE800 (BDOS entry)
    0xC9,                     # 0x0008: RET (back to CCP after BDOS returns)
])


def build_boot_stub() -> bytes:
    """Assemble the pre-boot stub and return its bytes (at offset 0).

    Returns the stub code starting at logical offset 0. To install:
      - memcpy BOOT_VECTORS into memory at 0x0000
      - memcpy the returned bytes into memory at 0x0100
    """
    asm = Assembler()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".asm", delete=False) as f:
        f.write(STUB_SOURCE)
        tmp = f.name
    code, _ = asm.assemble_file(tmp, org_addr=0)
    return bytes(code)


if __name__ == "__main__":
    stub = build_boot_stub()
    print(f"Stub: {len(stub)} bytes")
    print(f"Vectors (6 bytes): {BOOT_VECTORS.hex()}")
    print(f"First 16 bytes of stub: {stub[:16].hex()}")
