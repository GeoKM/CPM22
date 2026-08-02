"""8080 opcode reference — every instruction's opcode byte.

Used by the BIOS hand-encoder (cpm22/cpm_bios.py) so we can write 8080 code
without bringing in a cross-assembler. Each entry is the single opcode byte
(or list of bytes) that encodes the given instruction.

Why this is safe (per skill §1, §2 pitfalls):
- 8080 has no CB/ED/DD/FD prefixes — every instruction is 1, 2, or 3 bytes.
- The opcodes are stable, documented, and not changing.
- Hand-encoding ~200 bytes of BIOS is auditable line-by-line against the
  Intel 8080 datasheet in 10 minutes.
- This is exactly the pattern the skill recommends for "tiny assembler for
  BIOS only" (skill §1 — "8080 caveat: 8080 only has OUT n, not OUT (C),A")
  and avoids the entire 244-bug class of pitfalls the prior Z80 assembler
  had.

The dict is built once at import time. Add new opcodes by appending to
OPCODE_TABLE — the table is a flat list of (name, bytes) so we can iterate
in opcode order if we need a complete reference.
"""

from __future__ import annotations


def _b(*xs: int) -> bytes:
    """Helper to build instruction bytes."""
    return bytes(xs)


# 8080 opcode reference. Each entry: (mnemonic, encoding_function).
# The encoding function takes args and returns the byte sequence.
# The point of this table is NOT to be a complete assembler — it's a
# reference for hand-encoding the BIOS.

# === 8-bit data movement ===
# MVI r, n: 0x06+r*8  then immediate byte
def MVI(r: str, n: int) -> bytes:
    rmap = {"B": 0, "C": 1, "D": 2, "E": 3, "H": 4, "L": 5, "M": 6, "A": 7}
    return _b(0x06 + rmap[r] * 8, n & 0xFF)


# MOV r1, r2: 0x40 + dst*8 + src
def MOV(dst: str, src: str) -> bytes:
    rmap = {"B": 0, "C": 1, "D": 2, "E": 3, "H": 4, "L": 5, "M": 6, "A": 7}
    return _b(0x40 + rmap[dst] * 8 + rmap[src])


# STAX B / STAX D
STAX_B = _b(0x02)
STAX_D = _b(0x12)
# LDAX B / LDAX D
LDAX_B = _b(0x0A)
LDAX_D = _b(0x1A)
# SHLD nn
def SHLD(addr: int) -> bytes:
    return _b(0x22, addr & 0xFF, (addr >> 8) & 0xFF)
# LHLD nn
def LHLD(addr: int) -> bytes:
    return _b(0x2A, addr & 0xFF, (addr >> 8) & 0xFF)
# STA nn
def STA(addr: int) -> bytes:
    return _b(0x32, addr & 0xFF, (addr >> 8) & 0xFF)
# LDA nn
def LDA(addr: int) -> bytes:
    return _b(0x3A, addr & 0xFF, (addr >> 8) & 0xFF)


# === 16-bit data movement ===
# LXI rp, nn: 0x01 + rp*0x10  then lo, hi
def LXI(rp: str, nn: int) -> bytes:
    rmap = {"B": 0, "D": 1, "H": 2, "SP": 3}
    return _b(0x01 + rmap[rp] * 0x10, nn & 0xFF, (nn >> 8) & 0xFF)
# INX rp / DCX rp
def INX(rp: str) -> bytes:
    rmap = {"B": 0, "D": 1, "H": 2, "SP": 3}
    return _b(0x03 + rmap[rp] * 0x10)


def DCX(rp: str) -> bytes:
    rmap = {"B": 0, "D": 1, "H": 2, "SP": 3}
    return _b(0x0B + rmap[rp] * 0x10)


# PCHL
PCHL = _b(0xE9)
# SPHL
SPHL = _b(0xF9)
# XCHG
XCHG = _b(0xEB)
# XTHL
XTHL = _b(0xE3)


# === Arithmetic / logic (8-bit) ===
# ADD r / ADC r / SUB r / SBB r / ANA r / XRA r / ORA r / CMP r
def ADD(r: str) -> bytes:
    rmap = {"B": 0, "C": 1, "D": 2, "E": 3, "H": 4, "L": 5, "M": 6, "A": 7}
    return _b(0x80 + rmap[r])


def ADC(r: str) -> bytes:
    rmap = {"B": 0, "C": 1, "D": 2, "E": 3, "H": 4, "L": 5, "M": 6, "A": 7}
    return _b(0x88 + rmap[r])


def SUB(r: str) -> bytes:
    rmap = {"B": 0, "C": 1, "D": 2, "E": 3, "H": 4, "L": 5, "M": 6, "A": 7}
    return _b(0x90 + rmap[r])


def SBB(r: str) -> bytes:
    rmap = {"B": 0, "C": 1, "D": 2, "E": 3, "H": 4, "L": 5, "M": 6, "A": 7}
    return _b(0x98 + rmap[r])


def ANA(r: str) -> bytes:
    rmap = {"B": 0, "C": 1, "D": 2, "E": 3, "H": 4, "L": 5, "M": 6, "A": 7}
    return _b(0xA0 + rmap[r])


def XRA(r: str) -> bytes:
    rmap = {"B": 0, "C": 1, "D": 2, "E": 3, "H": 4, "L": 5, "M": 6, "A": 7}
    return _b(0xA8 + rmap[r])


def ORA(r: str) -> bytes:
    rmap = {"B": 0, "C": 1, "D": 2, "E": 3, "H": 4, "L": 5, "M": 6, "A": 7}
    return _b(0xB0 + rmap[r])


def CMP(r: str) -> bytes:
    rmap = {"B": 0, "C": 1, "D": 2, "E": 3, "H": 4, "L": 5, "M": 6, "A": 7}
    return _b(0xB8 + rmap[r])


# ADI n / ACI n / SUI n / SBI n / ANI n / XRI n / ORI n / CPI n
def ADI(n: int) -> bytes:
    return _b(0xC6, n & 0xFF)


def ACI(n: int) -> bytes:
    return _b(0xCE, n & 0xFF)


def SUI(n: int) -> bytes:
    return _b(0xD6, n & 0xFF)


def SBI(n: int) -> bytes:
    return _b(0xDE, n & 0xFF)


def ANI(n: int) -> bytes:
    return _b(0xE6, n & 0xFF)


def XRI(n: int) -> bytes:
    return _b(0xEE, n & 0xFF)


def ORI(n: int) -> bytes:
    return _b(0xF6, n & 0xFF)


def CPI(n: int) -> bytes:
    return _b(0xFE, n & 0xFF)


# INR r / DCR r
def INR(r: str) -> bytes:
    rmap = {"B": 0, "C": 1, "D": 2, "E": 3, "H": 4, "L": 5, "M": 6, "A": 7}
    return _b(0x04 + rmap[r] * 8)


def DCR(r: str) -> bytes:
    rmap = {"B": 0, "C": 1, "D": 2, "E": 3, "H": 4, "L": 5, "M": 6, "A": 7}
    return _b(0x05 + rmap[r] * 8)


# DAD rp
def DAD(rp: str) -> bytes:
    rmap = {"B": 0, "D": 1, "H": 2, "SP": 3}
    return _b(0x09 + rmap[rp] * 0x10)


# DAA
DAA = _b(0x27)


# === Rotate / acc ops ===
RLC = _b(0x07)  # rotate A left circular
RRC = _b(0x0F)
RAL = _b(0x17)
RAR = _b(0x1F)
CMA = _b(0x2F)  # A = ~A
STC = _b(0x37)  # CY = 1
CMC = _b(0x3F)  # CY = ~CY


# === Stack / PUSH / POP ===
PUSH_B = _b(0xC5)
PUSH_D = _b(0xD5)
PUSH_H = _b(0xE5)
PUSH_PSW = _b(0xF5)
POP_B = _b(0xC1)
POP_D = _b(0xD1)
POP_H = _b(0xE1)
POP_PSW = _b(0xF1)


# === Control flow ===
# JMP nn
def JMP(addr: int) -> bytes:
    return _b(0xC3, addr & 0xFF, (addr >> 8) & 0xFF)


# Jcond nn
def JNZ(addr: int) -> bytes:
    return _b(0xC2, addr & 0xFF, (addr >> 8) & 0xFF)


def JZ(addr: int) -> bytes:
    return _b(0xCA, addr & 0xFF, (addr >> 8) & 0xFF)


def JNC(addr: int) -> bytes:
    return _b(0xD2, addr & 0xFF, (addr >> 8) & 0xFF)


def JC(addr: int) -> bytes:
    return _b(0xDA, addr & 0xFF, (addr >> 8) & 0xFF)


# CALL nn
def CALL(addr: int) -> bytes:
    return _b(0xCD, addr & 0xFF, (addr >> 8) & 0xFF)


# Ccond nn
def CNZ(addr: int) -> bytes:
    return _b(0xC4, addr & 0xFF, (addr >> 8) & 0xFF)


def CZ(addr: int) -> bytes:
    return _b(0xCC, addr & 0xFF, (addr >> 8) & 0xFF)


def CNC(addr: int) -> bytes:
    return _b(0xD4, addr & 0xFF, (addr >> 8) & 0xFF)


def CC(addr: int) -> bytes:
    return _b(0xDC, addr & 0xFF, (addr >> 8) & 0xFF)


# RET
RET = _b(0xC9)
# Rcond
RNZ = _b(0xC0)
RZ = _b(0xC8)
RNC = _b(0xD0)
RC = _b(0xD8)


# RST n
def RST(n: int) -> bytes:
    return _b(0xC7 + n * 8)


# === I/O and control ===
def IN(port: int) -> bytes:
    return _b(0xDB, port & 0xFF)


def OUT(port: int) -> bytes:
    return _b(0xD3, port & 0xFF)


EI = _b(0xFB)
DI = _b(0xF3)
HLT = _b(0x76)
NOP = _b(0x00)
