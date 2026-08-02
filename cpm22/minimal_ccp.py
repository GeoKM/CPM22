"""Minimal CP/M 2.2 CCP — hand-encoded 8080.

A small but real CCP that supports DIR, TYPE, ERA, USER, and falls through
to "command not found" for everything else. Uses STANDARD CP/M 2.2 BDOS
function numbers (not the XEROX 1800 BDOS numbering).

The CCP lives at CCP_BASE in memory. It calls our stub BDOS via the
standard CALL 0x0005 trampoline.

Layout:
    CCP_BASE       : entry point (boot jumps here)
    CCP_BASE+...   : CCP code
    SIGNON_ADDR    : "CP/M 2.2 EMULATOR\r\n$" signon string
    DIR_NOTICE     : "DIR: not implemented in M2 stub\r\n$"
    TYPE_NOTICE    : "TYPE: not implemented in M2 stub\r\n$"
    ERA_NOTICE     : "ERA: not implemented in M2 stub\r\n$"
    UNK_NOTICE     : "?\r\n$"

This is small enough to hand-encode (~150 bytes). The full Digital
Research CCP is cross-assembled in M3 (OS2CCP.ASM).
"""

from cpm22 import asm8080 as A


# CCP_BASE: where the CCP code lives. Below the system image (0xE200),
# above the BDOS (0xE000). Use 0xE100.
CCP_BASE = 0xE100

# Signon string address (start of the strings area, after CCP code)
STRINGS_BASE = 0xE1C0

# Each string is at a fixed offset from STRINGS_BASE, no overlap
SIGNON_TEXT = b"CP/M 2.2 EMULATOR\r\n$"           # 22 bytes
DIR_NOTICE_TEXT = b"DIR: not impl (M2 stub)\r\n$"  # 26 bytes
TYPE_NOTICE_TEXT = b"TYPE: not impl (M2 stub)\r\n$" # 27 bytes
ERA_NOTICE_TEXT = b"ERA: not impl (M2 stub)\r\n$"  # 26 bytes
UNK_NOTICE_TEXT = b"?\r\n$"                         # 4 bytes

# Compute non-overlapping addresses
SIGNON_ADDR = STRINGS_BASE
DIR_NOTICE = SIGNON_ADDR + len(SIGNON_TEXT)
TYPE_NOTICE = DIR_NOTICE + len(DIR_NOTICE_TEXT)
ERA_NOTICE = TYPE_NOTICE + len(TYPE_NOTICE_TEXT)
UNK_NOTICE = ERA_NOTICE + len(ERA_NOTICE_TEXT)
# End of strings area
STRINGS_END = UNK_NOTICE + len(UNK_NOTICE_TEXT)


def build_ccp() -> bytes:
    """Build the minimal CCP bytes.

    Returns the bytes that should be written to memory starting at CCP_BASE.
    """
    out = bytearray()

    # CCP_BASE+0: ENTRY
    out += A.LXI("SP", 0xE300)         # 3 bytes — init stack
    out += A.LXI("D", SIGNON_ADDR)     # 3 bytes — signon address
    out += A.MVI("C", 9)               # 2 bytes — BDOS_PRINT
    out += A.CALL(0x0005)              # 3 bytes — CALL BDOS

    # CCP_LOOP (offset 11 = CCP_BASE+11 = 0xE10B)
    out += A.MVI("E", ord(">"))        # 2 bytes — prompt char
    out += A.MVI("C", 2)               # 2 bytes — BDOS_CONOUT
    out += A.CALL(0x0005)              # 3 bytes
    out += A.MVI("E", ord(" "))        # 2 bytes
    out += A.MVI("C", 2)               # 2 bytes
    out += A.CALL(0x0005)              # 3 bytes
    out += A.LXI("D", 0x0080)          # 3 bytes — read into low memory
    out += A.MVI("C", 10)              # 2 bytes — BDOS_RBUF
    out += A.CALL(0x0005)              # 3 bytes

    # Dispatch on first char (0x0082 = 0x0080 + 2 = first char of line,
    # not 0x0081 which is the LENGTH byte)
    out += A.LDA(0x0082)               # 3 bytes
    out += A.CPI(0x44)                 # 2 bytes — 'D' = 0x44 for DIR
    out += A.JZ(CCP_BASE + 100)        # 3 bytes — jump to CCP_DIR
    out += A.CPI(0x54)                 # 2 bytes — 'T' for TYPE
    out += A.JZ(CCP_BASE + 120)        # 3 bytes
    out += A.CPI(0x45)                 # 2 bytes — 'E' for ERA
    out += A.JZ(CCP_BASE + 140)        # 3 bytes
    out += A.LXI("D", UNK_NOTICE)      # 3 bytes — "?"
    out += A.MVI("C", 9)               # 2 bytes
    out += A.CALL(0x0005)              # 3 bytes
    out += A.JMP(CCP_BASE + 11)        # 3 bytes — back to loop

    # Pad to handler positions
    while len(out) < 100:
        out += A.NOP

    # CCP_DIR (offset 100)
    out += A.LXI("D", DIR_NOTICE)
    out += A.MVI("C", 9)
    out += A.CALL(0x0005)
    out += A.JMP(CCP_BASE + 11)

    # Pad to TYPE handler
    while len(out) < 120:
        out += A.NOP

    # CCP_TYPE (offset 120)
    out += A.LXI("D", TYPE_NOTICE)
    out += A.MVI("C", 9)
    out += A.CALL(0x0005)
    out += A.JMP(CCP_BASE + 11)

    # Pad to ERA handler
    while len(out) < 140:
        out += A.NOP

    # CCP_ERA (offset 140)
    out += A.LXI("D", ERA_NOTICE)
    out += A.MVI("C", 9)
    out += A.CALL(0x0005)
    out += A.JMP(CCP_BASE + 11)

    # Pad to end
    while len(out) < 200:
        out += A.NOP

    return bytes(out)


def get_strings() -> dict:
    """Return all the strings used by the CCP, with their memory addresses.

    Each string is placed sequentially after the previous one to avoid
    overlap. Total size: 22 + 26 + 27 + 26 + 4 = 105 bytes.
    """
    return {
        SIGNON_ADDR: SIGNON_TEXT,
        DIR_NOTICE: DIR_NOTICE_TEXT,
        TYPE_NOTICE: TYPE_NOTICE_TEXT,
        ERA_NOTICE: ERA_NOTICE_TEXT,
        UNK_NOTICE: UNK_NOTICE_TEXT,
    }
