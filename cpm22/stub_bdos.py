"""Hand-encoded minimal CP/M 2.2 BDOS.

This is a stub BDOS — a small, real-8080 BDOS hand-coded to dispatch the
core CP/M 2.2 functions to our Python BIOS. It lives in memory at
STUB_BDOS_BASE and is invoked via CALL 5 (the standard CP/M 2.2 entry).

Why a stub BDOS (and not the full Digital Research source)?  The DR
source is 30+ KB of complex 8080 — cross-assembling it cleanly without
a battle-tested assembler is the trap the prior project fell into.
The stub is small enough to hand-encode in a day, gives us a working
CP/M 2.2 baseline for tests, and serves as a reference for what the
cross-assembled version must produce.

The stub BDOS is NOT CP/M 2.2 compatible in the strict sense. It
implements the most common functions (PTERM, CONIN, CONOUT, PRINT,
RBUF, OPEN, CLOSE, MAKE, DELETE, READ, WRITE, RENAME, SELDSK,
RESET, GETDRV, DMAOFF, SETVEC, GETVEC) and stubs the rest. A real
CP/M 2.2 .COM program that only uses the common subset will run.
A program that uses uncommon functions (e.g. PARSE, BITS) will see
unexpected behavior.

The full authentic BDOS is layered on top in M3 via cross-assembly
of OS3BDOS.ASM, replacing this stub.

Layout:
    0x0000-0x0004 : Boot JP, Boot JP (3 bytes each, 2 JPs total)
    0x0005        : JP STUB_BDOS_BASE (3-byte trampoline)
    0x0008-0x00FF : CCP command buffer / FCB scratch (CCP uses this)
    0x0100-0x???? : TPA — user programs load here
    0x????        : Stub BDOS (in unused memory below the system image)
    0xF800        : BIOS vector table (17 JPs, replaced by _install_bios_vector_table)
    0xDC00        : BIOS stubs (5 bytes each, 17 stubs = 85 bytes)

STUB_BDOS_BASE is in unused memory below the system image (which now
lives at 0xE200 after relocation). The XEROX 1800 image's BIOS
data area is at 0xE207 etc., so the stub BDOS lives at 0xE100-ish,
where the XEROX image has no critical data.

Wait — actually the XEROX image's CCP+BDOS+BIOS occupies 0xE200-0xFFFF.
We want a stub BDOS that REPLACES the XEROX BDOS, not lives alongside it.
The simplest approach: at boot, we patch the 0x0005 trampoline to jump
to OUR stub BDOS (not the XEROX BDOS at 0xE206). The XEROX BDOS code
is then dead but harmless. The stub BDOS lives in low memory in the
CCP's "unused after CCP" area.

Actually, the cleanest approach: load the XEROX system image as before,
but the trampoline at 0x0005 jumps to OUR stub BDOS at 0xE100. The
stub BDOS has a complete BDOS function dispatcher in 8080, and its
BIOS calls go through the vector table at 0xF800 (which we control).

This is a much smaller surface to hand-encode than the full 30K DR
BDOS — we only need the dispatcher + the 16-20 most common functions,
each as a small handler.
"""

from __future__ import annotations

from cpm22 import asm8080 as A


# Address where the stub BDOS lives in memory. Below the system image
# (0xE200), above the CCP (which lives at 0xE200 in the XEROX image but
# is replaced by our stub at 0x0000). 0xE000-0xE1FF is unused by the
# XEROX image (the BIOS data area starts at 0xE200).
STUB_BDOS_BASE = 0xE000


def build_stub_bdos(BIOS_PORT: int = 0xF0) -> bytes:
    """Build the hand-encoded stub BDOS bytes.

    The BDOS entry is at STUB_BDOS_BASE. The caller does:
        MOV C, func
        CALL 5       ; trampoline at 0x0005 = JP STUB_BDOS_BASE
    The trampoline jumps here.

    Layout:
        STUB_BDOS_BASE+0: PUSH BC
        STUB_BDOS_BASE+1: PUSH DE
        STUB_BDOS_BASE+2: PUSH HL
        STUB_BDOS_BASE+3: MOV A, C      ; function number
        STUB_BDOS_BASE+4: OUT BIOS_PORT ; trigger Python dispatch
        STUB_BDOS_BASE+6: POP HL
        STUB_BDOS_BASE+7: POP DE
        STUB_BDOS_BASE+8: POP BC
        STUB_BDOS_BASE+9: RET
    """
    prologue = (
        A.PUSH_B           # 0xC5
        + A.PUSH_D         # 0xD5
        + A.PUSH_H         # 0xE5
        + A.MOV("A", "C")  # 0x79  (function in C → A for OUT)
        + A.OUT(BIOS_PORT) # 0xD3 0xF0
        + A.POP_H          # 0xE1
        + A.POP_D          # 0xD1
        + A.POP_B          # 0xC1
        + A.RET            # 0xC9
    )
    return prologue


# CP/M 2.2 BDOS function numbers (only the ones the stub implements)
BDOS_PTERM = 0     # system reset
BDOS_RDFLUSH = 1   # not in 2.2; reserved
BDOS_CONIN = 1     # console input (with echo in 2.2)
BDOS_CONOUT = 2    # console output
BDOS_RDRIN = 3     # reader input
BDOS_PUNOUT = 4    # punch output
BDOS_LSTOUT = 5    # list output
BDOS_DCIO = 6      # direct console I/O
BDOS_GETIOBYTE = 7 # get I/O byte
BDOS_SETIOBYTE = 8 # set I/O byte
BDOS_PRINT = 9     # print string (until $)
BDOS_RBUF = 10     # read console buffer
BDOS_CONST = 11    # console status
BDOS_GETVER = 12   # return version number
BDOS_RESET = 13    # reset drives
BDOS_SELDSK = 14   # select disk
BDOS_OPEN = 15     # open file
BDOS_CLOSE = 16    # close file
BDOS_SFIRST = 17   # search for first
BDOS_SNEXT = 18    # search for next
BDOS_DELETE = 19   # delete file
BDOS_READ = 20     # read record
BDOS_WRITE = 21    # write record
BDOS_MAKE = 22     # create file
BDOS_RENAME = 23   # rename file
BDOS_GETDRV = 25   # get current drive
BDOS_DMAOFF = 26   # set DMA address
BDOS_GETALV = 27   # get allocation vector
BDOS_WPROT = 28    # write-protect vector
BDOS_GETROV = 29   # get read-only vector
BDOS_SETVEC = 30   # set exception vector
BDOS_GETVEC = 31   # get exception vector
BDOS_DPB = 31      # get DPB
BDOS_SETDPA = 32   # set DMA address (alternate)


def build_bios_stub(fn: int, BIOS_PORT: int = 0xF1) -> bytes:
    """Build a single BIOS stub at a specified port.

    Same shape as the BDOS stub but uses a different port so the Python
    dispatcher can tell which layer the call is from.
    """
    return (
        A.MVI("A", fn)
        + A.OUT(BIOS_PORT)
        + A.RET
    )
