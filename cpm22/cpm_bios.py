"""IMSAI 8080 CP/M 2.2 BIOS — hand-encoded 8080 code + Python dispatch.

The 17 CP/M 2.2 BIOS functions are implemented as small 8080 stubs in
memory. Each stub does `OUT 0xF0` with a unique function number in A,
which our Python handler dispatches. This is the OUT-trap pattern from
skill §1, "OUT trap for BDOS/BIOS calls."

We hand-encode the BIOS bytes (no cross-assembler) to avoid the entire
244-bug class the prior Z80 assembler had. Every byte is auditable
against the Intel 8080 datasheet.

Memory layout (XEROX 1800 pre-relocated image, with our adaptation):

    0xDC00  ┐
            │  Pre-built CP/M 2.2 system (7,680 bytes from CPM.SYS)
            │  - CCP at 0xDC00
            │  - BDOS at 0xE400-ish
            │  - CCP+BDOS+vector-table at offsets 0..0x1DFF of the image
    0xF9FF  ┘
    0xFA00  ┐  BIOS vector table (17 × 3 bytes = 51 bytes)
    0xFA33  ┘
    0xFA34  ┐  BIOS code (our hand-encoded stubs)
    ...     │
    0xFFFF  ┘  SP initial value

BDOS entry: 0x0005 (already in the pre-built image)
WBOOT entry: 0xDC00 (wboot is the first JMP in the image)
BOOT entry: 0xDC03 (second JMP)
"""

from __future__ import annotations

from cpm22 import asm8080 as A


# Port used for BIOS-call dispatch. Each BIOS stub writes its function
# number to A and does `OUT 0xF0`. Python's BIOS port handler reads A,
# dispatches, and returns (CPU then executes the following RET).
# We use a separate port for BDOS (BDOS_PORT) so the Python dispatcher
# can tell which layer the call is from.
BIOS_PORT = 0xF1

# Function numbers (must match cpm_bios.BIOSHandler.dispatch)
FN_BOOT = 0x00
FN_WBOOT = 0x01
FN_CONST = 0x02
FN_CONIN = 0x03
FN_CONOUT = 0x04
FN_LIST = 0x05
FN_PUNCH = 0x06
FN_READER = 0x07
FN_HOME = 0x08
FN_SELDSK = 0x09
FN_SETTRK = 0x0A
FN_SETSEC = 0x0B
FN_SETDMA = 0x0C
FN_READ = 0x0D
FN_WRITE = 0x0E
FN_LISTST = 0x0F
FN_SECTRAN = 0x10


def _stub(fn: int, port: int = BIOS_PORT) -> bytes:
    """A BIOS stub: MVI A, fn; OUT port; RET.

    All 17 BIOS functions are this exact same 5-byte pattern. The Python
    BIOS dispatch handler reads A and calls the corresponding Python method.
    """
    return (
        A.MVI("A", fn)            # 0x3E nn       (2 bytes)
        + A.OUT(port)             # 0xD3 0xF1     (2 bytes)
        + A.RET                   # 0xC9          (1 byte)
    )


# 17 BIOS stubs at the same offsets as the CP/M 2.2 jump table.
# Each stub is 5 bytes; with 17 entries that's 85 bytes. Plus a 1-byte
# alignment NOP between if we want it.
def build_bios_stubs() -> bytes:
    return b"".join(
        _stub(FN_BOOT + i) for i in range(17)
    )


# Boot ROM — small 8080 program that runs at power-on.
# Loads the system tracks from floppy A: into 0xDC00, then jumps to
# the CCP entry point.
#
# Layout (in our address space):
#   0x0000  : Jump to BOOT_ENTRY (so PC=0 at reset goes to boot ROM)
#   0x0003  : HLT (unreached; reserved for warm-boot return)
#   0x0005  : BDOS trampoline (in the pre-built CP/M image — leave alone)
#   ...
#   0xF200  : BIOS vector table in the pre-built image
#
# Our adaptation: we override the boot vector (which points to the XEROX
# BIOS) with a small loader that reads the system tracks from floppy A:
# and then jumps to the actual wboot entry (0xDC00).
#
# The XEROX 1800 boot sequence: at offset 0, JP wboot (0xDF58) and at
# offset 3, JP boot (0xDF5C). We replace offset 0 with a JP to OUR boot
# loader, and let the BIOS handle the rest.
#
# Our boot loader (hand-encoded):
#
# BOOT_LOADER:
#   LXI  SP, 0xFFFF         ; init stack
#   MVI  A, 0               ; drive A
#   OUT  0xF0               ; BIOS_SELDSK
#   MVI  C, 0               ; track 0
#   MVI  A, FN_SETTRK
#   OUT  0xF0
#   MVI  C, 1               ; sector 1
#   MVI  A, FN_SETSEC
#   OUT  0xF0
#   LXI  D, 0xDC00          ; DMA = load address
#   MVI  A, FN_SETDMA
#   OUT  0xF0
#   MVI  A, FN_READ
#   OUT  0xF0
#   MVI  C, 2               ; sector 2
#   MVI  A, FN_SETSEC
#   OUT  0xF0
#   LXI  D, 0xDC80
#   MVI  A, FN_SETDMA
#   OUT  0xF0
#   MVI  A, FN_READ
#   OUT  0xF0
#   ... (loop for remaining sectors on tracks 0 and 1)
#   JP   0xDC00             ; jump to the CP/M CCP
#
# This is getting verbose. The simpler approach: don't override the XEROX
# image at all. Just have the Python host call BIOS.READ directly to load
# the CPM.SYS image at 0xDC00 on cold boot, then set PC=0xDC00 and go.

# ----------------------------------------------------------------------
# The function below is what the host (CPMSystem) calls on cold-boot to
# load CPM.SYS into the memory at the pre-relocated base address. We
# don't hand-encode a 8080 boot loader — we do it in Python because
# (a) it's the only code that runs at boot, (b) it never gets executed
# by the CPU, and (c) it lets us avoid the XEROX-specific quirks.
# ----------------------------------------------------------------------

# Pre-relocated CP/M 2.2 system image base address in our 64KB address space.
# After relocation (see cpm22/relocate.py), the standard 64K CP/M 2.2 image
# loads at 0xE200. CCP entry is 0xE200. BDOS is at 0xE206. BIOS vector table
# is at 0xF800 (file offset 0x1600 + 0xE200).
SYSTEM_BASE = 0xE200
SYSTEM_SIZE = 0x1E00     # 7,680 bytes
VECTOR_BASE = 0xF800     # BIOS vector table in the relocated image
BOOT_ENTRY = 0xE000      # CCP starts here in DR CP/M 2.2 (JP to ccpstart at 0xE350)
WBOOT_ENTRY = 0xE000     # Warm boot also re-enters CCP via the same JP


def load_cpm_sys_into(mem, cpm_sys_path: str) -> int:
    """Load the pre-built CP/M 2.2 system image into memory at SYSTEM_BASE.

    Returns the number of bytes loaded.

    Note: we leave the image's internal JP structure intact. The XEROX
    image's first two JMPs (wboot at offset 0, boot at offset 3) point
    to the XEROX BIOS within the image, which we DON'T need because our
    Python BIOS handlers will be wired in via the BIOS port map. We just
    need to set PC=0xDC00 (the CCP) and let the BDOS trampoline at
    0x0005 (also in the image) route calls to our Python handlers.
    """
    with open(cpm_sys_path, "rb") as f:
        data = f.read()
    if len(data) != SYSTEM_SIZE:
        raise ValueError(
            f"CPM.SYS size {len(data)} != expected {SYSTEM_SIZE}"
        )
    for i, b in enumerate(data):
        mem.wb(SYSTEM_BASE + i, b)
    return len(data)
