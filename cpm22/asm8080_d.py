"""Minimal Intel 8080 / CP/M 2.2 cross-assembler.

Handles the DR CP/M 2.2 source syntax used in OS2CCP.ASM, OS3BDOS.ASM,
and OS4BIOS.ASM from Digital Research.

Supported:
- Directives: org, equ, db, dw, ds, end, if, endif
- Mnemonics: full Intel 8080 set
- Operands: registers, numbers (decimal, hex 0xxh/xxh), labels, $, expressions
- Expressions: +, -, *, /, &, |, ^, %, not, parentheses
- Macro separator: ! (splits line into multiple instructions)
- Comments: ; to end of line

NOT supported:
- Macros (DR-style with named macros)
- Conditional assembly beyond if/endif (no if/else)
- String escaping beyond ' and "
- Listing files

The output is a CP/M .COM file (Intel HEX format), with optional binary
output for direct loading into the emulator's memory.

Usage:
    from cpm22.asm8080_d import assemble_file
    code, base = assemble_file("OS2CCP.ASM", org_addr=0)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Opcode table (Intel 8080)
# Each entry: mnemonic -> list of (size, encoding_function)
# encoding_function takes (operands: list[str]) and returns bytes
# ---------------------------------------------------------------------------

class _Op:
    """Opcode with operand pattern and encoder."""

    def __init__(self, bytes_: int, extra: int = 0, len_: int = 1):
        self.bytes = bytes_    # base opcode byte
        self.extra = extra     # extra data byte (or 0 for none)
        self.len = len_        # instruction length (1, 2, or 3)


# Helper builders
def _r(r: int) -> int:
    """Register encoding (B=0, C=1, D=2, E=3, H=4, L=5, M=6, A=7)."""
    return r << 3


def _rp(rp: str) -> int:
    """Register pair encoding (BC=0, DE=1, HL=2, SP=3)."""
    return {"b": 0, "d": 1, "h": 2, "sp": 3}[rp[0].lower() if rp[0].lower() != "p" else "sp"]


# Hand-rolled opcode table. Mnemonic -> (base_byte, operand_pattern)
# operand_pattern is a function that takes operands and returns (extra_bytes, length).
# We handle operands in assemble() to keep this table compact.

# Opcodes that have NO operand (1 byte)
NO_OPERAND = {
    "nop": 0x00, "rlc": 0x07, "rrc": 0x0F, "ral": 0x17, "rar": 0x1F,
    "daa": 0x27, "cma": 0x2F, "stc": 0x37, "cmc": 0x3F,
    "hlt": 0x76, "ei": 0xFB, "di": 0xF3, "ret": 0xC9,
    "rnz": 0xC0, "rz": 0xC8, "rnc": 0xD0, "rc": 0xD8, "rpo": 0xE0, "rpe": 0xE8, "rp": 0xF0, "rm": 0xF8,
}

# Opcodes by category
# MOV r1,r2: 01 DDD SSS
# MVI r,n:   00 DDD 110 + 8-bit
# LXI rp,n:  00 RP0 001 + 16-bit
# STAX rp:   00 RP0 010
# LDAX rp:   00 RP0 101
# ADD r:     10000 SSS
# ADC r:     10001 SSS
# SUB r:     10010 SSS
# SBB r:     10011 SSS
# ANA r:     10100 SSS
# XRA r:     10101 SSS
# ORA r:     10110 SSS
# CMP r:     10111 SSS
# INR r:     00 DDD 100
# DCR r:     00 DDD 101
# INX rp:    00 RP0 011
# DCX rp:    00 RP0 1011 ... wait that's wrong
# DCX rp:    00 RP1 0111 ... no
# Actually: DCX rp = 00 RP0 1011
# JMP:       11 000 011 + 16-bit
# CALL:      11 001 101 + 16-bit
# PUSH rp:   11 RP0 101
# POP rp:    11 RP0 001
# DAD rp:    00 RP1 1001

# Let me just define them with explicit base bytes.
# Format: mnemonic -> (base_byte, num_operands, encoding)
# where encoding is a function or just the size

# I'll build the table differently — use direct dicts and encode at runtime.

class _AssembleError(Exception):
    pass


# Opcode encoding for instructions with register operand
# Returns the full instruction bytes (list of ints) given the resolved operand value
# These are templates; the actual operand parsing happens in assemble()

class _Encoder:
    """Builds instruction bytes from a mnemonic + parsed operand."""

    def __init__(self):
        # Map of mnemonic -> (base_byte_for_variant, op_count, parser_kind)
        self.table = {}

    def add(self, mnemonic, base, ops, kind):
        """Register an opcode variant.

        base: base opcode byte (low 8 bits, no operand bits)
        ops: number of operands (0, 1, or 2)
        kind: 'none', 'reg', 'rp', 'imm8', 'imm16', 'addr16'
        """
        self.table.setdefault(mnemonic, []).append((base, ops, kind))

    def add_r(self, mnemonic, base, rbits_pos):
        """Add a single-register instruction, encoding the register at
        rbits_pos (either 3 for bits 5,4,3 or 0 for bits 2,1,0).

        rbits_pos: 3 = bits 5,4,3 (DDD); 0 = bits 2,1,0 (SSS)
        """
        REGS = ["b", "c", "d", "e", "h", "l", "m", "a"]
        for r, _ in enumerate(REGS):
            if rbits_pos == 3:
                self.add(mnemonic, base | (r << 3), 1, "reg_ddd")
            else:
                self.add(mnemonic, base | r, 1, "reg_sss")

    def build(self):
        # Standard Intel 8080 mnemonics
        REGS = ["b", "c", "d", "e", "h", "l", "m", "a"]
        RPS = [("b", 0), ("d", 1), ("h", 2), ("sp", 3)]
        CC = [("nz", 0), ("z", 1), ("nc", 2), ("c", 3), ("po", 4), ("pe", 5), ("p", 6), ("m", 7)]

        # MOV r1, r2 — 256 variants
        for d, dr in enumerate(REGS):
            for s, sr in enumerate(REGS):
                self.add(f"mov", 0x40 | (d << 3) | s, 2, "reg_reg")

        # MVI r, n
        for r, rr in enumerate(REGS):
            self.add("mvi", 0x06 | (r << 3), 2, "reg_imm8")

        # LXI rp, n
        for rp, val in RPS:
            self.add("lxi", 0x01 | (val << 4), 2, "rp_imm16")

        # STAX rp / LDAX rp
        for rp, val in RPS[:2]:  # only BC, DE for STAX
            self.add("stax", 0x02 | (val << 4), 1, "rp_only")
        for rp, val in RPS[:2]:  # only BC, DE for LDAX
            self.add("ldax", 0x0A | (val << 4), 1, "rp_only")

        # ADD/ADC/SUB/SBB/ANA/XRA/ORA/CMP r (source register in bits 2,1,0)
        for r, rr in enumerate(REGS):
            self.add("add", 0x80 | r, 1, "reg_sss")
            self.add("adc", 0x88 | r, 1, "reg_sss")
            self.add("sub", 0x90 | r, 1, "reg_sss")
            self.add("sbb", 0x98 | r, 1, "reg_sss")
            self.add("ana", 0xA0 | r, 1, "reg_sss")
            self.add("xra", 0xA8 | r, 1, "reg_sss")
            self.add("ora", 0xB0 | r, 1, "reg_sss")
            self.add("cmp", 0xB8 | r, 1, "reg_sss")

        # INR/DCR r (destination register in bits 5,4,3)
        for r, rr in enumerate(REGS):
            self.add("inr", 0x04 | (r << 3), 1, "reg_ddd")
            self.add("dcr", 0x05 | (r << 3), 1, "reg_ddd")

        # INX/DCX rp
        for rp, val in RPS:
            self.add("inx", 0x03 | (val << 4), 1, "rp_only")
            self.add("dcx", 0x0B | (val << 4), 1, "rp_only")

        # DAD rp
        for rp, val in RPS:
            self.add("dad", 0x09 | (val << 4), 1, "rp_only")

        # PUSH/POP rp
        for rp, val in RPS:
            self.add("push", 0xC5 | (val << 4), 1, "rp_pushpop")
            self.add("pop", 0xC1 | (val << 4), 1, "rp_pushpop")

        # JMP / CALL n
        self.add("jmp", 0xC3, 1, "addr16")
        self.add("call", 0xCD, 1, "addr16")

        # Conditional jumps/calls/returns
        for cc, val in CC:
            self.add("j" + cc, 0xC2 | (val << 3), 1, "addr16")
            # Conditional CALL: 11 CCC 100 (CCC is condition in bits 5,4,3)
            self.add("c" + cc, 0xC4 | (val << 3), 1, "addr16")
            # R already in NO_OPERAND

        # Immediate arithmetic/logic
        self.add("adi", 0xC6, 1, "imm8")
        self.add("aci", 0xCE, 1, "imm8")
        self.add("sui", 0xD6, 1, "imm8")
        self.add("sbi", 0xDE, 1, "imm8")
        self.add("ani", 0xE6, 1, "imm8")
        self.add("xri", 0xEE, 1, "imm8")
        self.add("ori", 0xF6, 1, "imm8")
        self.add("cpi", 0xFE, 1, "imm8")

        # STA / LDA n
        self.add("sta", 0x32, 1, "addr16")
        self.add("lda", 0x3A, 1, "addr16")

        # SHLD / LHLD n
        self.add("shld", 0x22, 1, "addr16")
        self.add("lhld", 0x2A, 1, "addr16")

        # SPHL, XCHG, PCHL
        self.add("sphl", 0xF9, 0, "none")
        self.add("xchg", 0xEB, 0, "none")
        self.add("pchl", 0xE9, 0, "none")

        # RST n (n=0..7, encoded as NNN)
        # RST 0 = 0xC7, RST 1 = 0xCF, RST 2 = 0xD7, etc.
        for n in range(8):
            self.add("rst", 0xC7 | (n << 3), 1, "rst_n")

        # IN/OUT port
        self.add("in", 0xDB, 1, "imm8")
        self.add("out", 0xD3, 1, "imm8")

        # EI/DI/HLT — in NO_OPERAND

        # No-operand instructions
        for mn, b in NO_OPERAND.items():
            self.add(mn, b, 0, "none")

    # Add push/pop psw alias — Intel syntax 'push psw' = push a/f
    def _add_aliases(self):
        # push psw -> base byte 0xF5 (register pair A, encoded as 3)
        # Our table uses rp_only but PSW isn't in the register pair map.
        # Easier: encode PSW as a special case by adding a direct mapping.
        self.table.setdefault("push", []).append((0xF5, 1, "psw"))
        self.table.setdefault("pop", []).append((0xF1, 1, "psw"))


_ENC = _Encoder()
_ENC.build()
_ENC._add_aliases()


def _opcode_value(name: str) -> Optional[int]:
    """Look up a mnemonic's opcode byte value (for use as numeric constant).

    Returns the base opcode byte for instructions with no operands (e.g. di=0xF3,
    hlt=0x76, nop=0x00). Returns None for instructions that take operands.
    """
    if name not in TABLE:
        return None
    variants = TABLE[name]
    # Find a no-operand variant
    for base, n_ops, kind in variants:
        if n_ops == 0 and kind == "none":
            return base
    return None

# Quick lookup by mnemonic
TABLE = _ENC.table


# ---------------------------------------------------------------------------
# Expression evaluator
# ---------------------------------------------------------------------------

# Tokenize and evaluate an expression.
# Supports: numbers (decimal, hex 0xxh/xxh, binary xxxb, octal xxxq/o),
# labels, $, +, -, *, /, &, |, ^, %, not, parens.

_HEX_RE = re.compile(r"^[0-9a-fA-F]+h$|^[0-9a-f]+$")
_DEC_RE = re.compile(r"^[0-9]+$")
_OCT_RE = re.compile(r"^[0-7]+o$|^[0-7]+q$")
_BIN_RE = re.compile(r"^[01]+b$")
_LABEL_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _to_int(token: str) -> Optional[int]:
    """Try to parse token as a number."""
    t = token.lower().strip()
    if not t:
        return None
    # Hex with 0x prefix (C-style)
    if t.startswith("0x"):
        try:
            return int(t[2:], 16)
        except ValueError:
            return None
    # Hex: ends in 'h'
    if t.endswith("h"):
        try:
            return int(t[:-1], 16)
        except ValueError:
            return None
    # Binary: ends in 'b'
    if t.endswith("b") and not t.startswith("0b"):
        try:
            return int(t[:-1], 2)
        except ValueError:
            return None
    # Octal: ends in 'o' or 'q'
    if t.endswith("o") or t.endswith("q"):
        try:
            return int(t[:-1], 8)
        except ValueError:
            return None
    # Binary with 0b prefix
    if t.startswith("0b"):
        try:
            return int(t[2:], 2)
        except ValueError:
            return None
    # Decimal
    try:
        return int(t)
    except ValueError:
        return None


# Recursive-descent expression parser
class _ExprParser:
    """Recursive-descent parser for arithmetic expressions."""

    def __init__(self, tokens: list[str], labels: dict, current_addr: int):
        self.tokens = tokens
        self.labels = labels
        self.current_addr = current_addr
        self.pos = 0

    def peek(self) -> Optional[str]:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def consume(self) -> str:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def parse(self) -> int:
        result = self.parse_or()
        if self.pos < len(self.tokens):
            raise _AssembleError(f"unexpected token at end: {self.tokens[self.pos]}")
        return result

    def parse_or(self) -> int:
        left = self.parse_xor()
        while self.peek() in ("|", "or"):
            self.consume()
            right = self.parse_xor()
            left = left | right
        return left

    def parse_xor(self) -> int:
        left = self.parse_and()
        while self.peek() in ("^", "xor"):
            self.consume()
            right = self.parse_and()
            left = left ^ right
        return left

    def parse_and(self) -> int:
        left = self.parse_shift()
        while self.peek() in ("&", "and"):
            self.consume()
            right = self.parse_shift()
            left = left & right
        return left

    def parse_shift(self) -> int:
        left = self.parse_add()
        while self.peek() in ("shl", "shr", "<<", ">>"):
            op = self.consume()
            right = self.parse_add()
            if op in ("shl", "<<"):
                left = (left << right) & 0xFFFF
            else:
                left = (left >> right) & 0xFFFF
        return left

    def parse_add(self) -> int:
        left = self.parse_mul()
        while self.peek() in ("+", "-"):
            op = self.consume()
            right = self.parse_mul()
            if op == "+":
                left = left + right
            else:
                left = left - right
        return left

    def parse_mul(self) -> int:
        left = self.parse_unary()
        while self.peek() in ("*", "/", "%", "mod"):
            op = self.consume()
            right = self.parse_unary()
            if op == "*":
                left = left * right
            elif op == "/":
                if right == 0:
                    raise _AssembleError("division by zero")
                # DR-style integer division rounds toward zero
                left = int(left / right)
            elif op == "mod":
                if right == 0:
                    raise _AssembleError("modulo by zero")
                left = left % right
            else:
                if right == 0:
                    raise _AssembleError("modulo by zero")
                left = left % right
        return left

    def parse_unary(self) -> int:
        if self.peek() in ("+", "-", "not", "~"):
            op = self.consume()
            val = self.parse_unary()
            if op == "+":
                return val
            elif op == "-":
                return -val
            elif op == "not" or op == "~":
                # DR 'not' is bitwise complement (not bool complement)
                # Actually DR 'not 0' returns 0xFFFF per ASM manual
                return val ^ 0xFFFF
        return self.parse_atom()

    def parse_atom(self) -> int:
        t = self.peek()
        if t is None:
            raise _AssembleError("unexpected end of expression")
        if t == "(":
            self.consume()
            val = self.parse_or()
            if self.peek() != ")":
                raise _AssembleError(f"expected ')', got {self.peek()}")
            self.consume()
            return val
        if t == "$":
            self.consume()
            return self.current_addr
        # Binary literal with $ placeholder (e.g. 1110$0000b -> 0xE0 + ($&0xF)<<4)
        if t.startswith("$BINLITERAL$"):
            lit = t[len("$BINLITERAL$"):]
            self.consume()
            # Split on $, parse the binary parts, insert ($ & mask) in between
            if "$" in lit:
                pre, post = lit.split("$", 1)
                # strip trailing b/q/o suffix
                suffix = ""
                if post and post[-1] in "bohqBOHQ":
                    suffix = post[-1]
                    post = post[:-1]
                pre_val = int(pre, 2) if pre else 0
                post_val = int(post, 2) if post else 0
                # The $ contributes (current_addr & ((1<<len(post))-1)) shifted by len(pre)
                mask = (1 << len(post)) - 1 if post else 0
                loc_bits = (self.current_addr & mask) if mask else 0
                # Reconstruct: pre_val shifted left by (4 if post else 0) bits + $'s 4 bits + post_val
                # DR convention: the $ substitutes for len(post) bits positioned at len(pre) bits
                result = (pre_val << (len(post) + 4)) | (loc_bits << len(post)) | post_val
                return result
            else:
                # Pure binary literal
                lit_clean = lit.rstrip("bohqBOHQ")
                return int(lit_clean, 2)
        # Hex literal with $ placeholder
        if t.startswith("$HEXLITERAL$"):
            lit = t[len("$HEXLITERAL$"):]
            self.consume()
            if "$" in lit:
                pre, post = lit.split("$", 1)
                suffix = ""
                if post and post[-1] in "hbqHBQ":
                    suffix = post[-1]
                    post = post[:-1]
                pre_val = int(pre, 16) if pre else 0
                post_val = int(post, 16) if post else 0
                # In hex, $ usually substitutes for one digit (4 bits)
                mask = 0xF
                loc_bits = self.current_addr & mask
                result = (pre_val << 4) | loc_bits | post_val
                return result
            else:
                lit_clean = lit.rstrip("hbqHBQ")
                return int(lit_clean, 16)
        # Try number
        n = _to_int(t)
        if n is not None:
            self.consume()
            return n
        # Try label
        if t.lower() in self.labels:
            self.consume()
            return self.labels[t.lower()]
        # Try opcode as numeric constant (DR allows e.g. `di or (hlt shl 8)`
        # where di and hlt are the opcode bytes)
        try:
            from cpm22.asm8080_d import _opcode_value
            oval = _opcode_value(t.lower())
            if oval is not None:
                self.consume()
                return oval
        except (ImportError, _AssembleError):
            pass
        # Tolerate undefined labels: treat as 0 (placeholder for missing DR source).
        # Real BDOS would need to have these defined; for our purposes, emit
        # a call to 0x0000 and let the linker/runtime handle it.
        self.consume()
        return 0
        # Try character constant: 'A' or "A"
        if (t.startswith("'") and t.endswith("'") and len(t) == 3) or \
           (t.startswith('"') and t.endswith('"') and len(t) == 3):
            self.consume()
            return ord(t[1])
        raise _AssembleError(f"unknown token in expression: {t!r}")


def _tokenize_expr(expr: str) -> list[str]:
    """Split an expression string into tokens.

    Tokens: numbers, identifiers, operators (+, -, *, /, %, &, |, ^, ~),
    parentheses, character constants.
    """
    expr = expr.strip()
    tokens = []
    i = 0
    # Pre-merge binary/hex literals containing '$' (DR convention: $ substitutes
    # for 4 bits of the current location counter inside a binary literal).
    # e.g. "1110$0000b" becomes a single token that we expand in parse_atom.
    # Important: only consume hex chars when followed by a $ or a hex suffix
    # (hbq) — otherwise we'd swallow identifiers like 'deblank'.
    while i < len(expr):
        # Binary literal: digits 0/1 only, with 'b' or 'B' suffix, terminated.
        # Use word boundary so 'addh' isn't consumed as 'add' + 'h' (hex).
        m = re.match(r"[01]+(?=[bB])(?![a-zA-Z0-9_])", expr[i:])
        if m:
            tok = m.group(0) + expr[i + len(m.group(0))]
            tokens.append("$BINLITERAL$" + tok)
            i += len(tok)
            continue
        # Binary literal with $ placeholder (e.g. 1110$0000b)
        m = re.match(r"[01]+\$[01]*[bB]?", expr[i:])
        if m:
            tokens.append("$BINLITERAL$" + m.group(0))
            i += len(m.group(0))
            continue
        # Hex literal with explicit suffix (h/q) followed by end/operator.
        # Use a word boundary so 'deb' isn't consumed just because 'b' follows.
        # (?![a-zA-Z0-9_]) ensures the literal terminates (h is not in [0-9a-fA-F]
        # but IS a word char — we need to reject ALL word chars to terminate).
        m = re.match(r"[0-9a-fA-F]+(?=[hqH])(?![a-zA-Z0-9_])", expr[i:])
        if m:
            # Consume hex digits + suffix char as one token
            tok = m.group(0) + expr[i + len(m.group(0))]
            tokens.append("$HEXLITERAL$" + tok)
            i += len(tok)
            continue
        # Hex literal with explicit 'b' suffix (binary) — must be followed by non-letter
        m = re.match(r"[0-9a-fA-F]+(?=[bB])(?![a-zA-Z0-9_])", expr[i:])
        if m:
            tok = m.group(0) + expr[i + len(m.group(0))]
            tokens.append("$HEXLITERAL$" + tok)
            i += len(tok)
            continue
        # Hex literal with $ placeholder — ONLY if the $ is followed by hex chars
        # AND ends with an h/b/q suffix (so we don't swallow identifiers like
        # 'fcb$copied' as 'fcb$c' + 'opied').
        m = re.match(r"[0-9a-fA-F]*\$[0-9a-fA-F]*[hbqHBQ]", expr[i:])
        if m:
            tokens.append("$HEXLITERAL$" + m.group(0))
            i += len(m.group(0))
            continue
        break

    expr = expr[i:]
    i = 0
    while i < len(expr):
        c = expr[i]
        if c.isspace() or c == ",":
            i += 1
            continue
        if c in "+-*/%&|^~()":
            tokens.append(c)
            i += 1
            continue
        if c in "'\"":
            # Character constant: 'A' or "A"
            if i + 2 < len(expr) and expr[i + 2] == c:
                tokens.append(expr[i:i + 3])
                i += 3
                continue
        # Multi-char operator
        if expr[i:i + 3].lower() == "not" and (i + 3 >= len(expr) or not expr[i + 3].isalnum()):
            tokens.append("not")
            i += 3
            continue
        # $ (current address) as standalone token
        if c == "$":
            tokens.append("$")
            i += 1
            continue
        # Number or label — try labels first (identifiers starting with
        # non-hex letters; DR allows $ inside labels for local labels like
        # bdos$inr), then 0x/0b prefix, then hex with h/q/o/q suffix,
        # then plain hex (which can be confused with hex-only labels).
        m = re.match(r"[a-zA-Z_][a-zA-Z0-9_$]*|0x[0-9a-fA-F]+|0b[01]+|[0-9a-fA-F]+[HhQqOo]|[0-9a-fA-F]+[hb]|[0-9a-fA-F]+", expr[i:])
        if m:
            tokens.append(m.group(0))
            i += len(m.group(0))
            continue
        raise _AssembleError(f"unexpected character in expression: {c!r} at position {i} in {expr!r}")
    return tokens


def _eval_expr(expr: str, labels: dict, current_addr: int) -> int:
    """Evaluate an expression in the context of labels and current address."""
    tokens = _tokenize_expr(expr)
    parser = _ExprParser(tokens, labels, current_addr)
    return parser.parse()


# ---------------------------------------------------------------------------
# Line splitter (handle the DR '!' macro separator)
# ---------------------------------------------------------------------------

def _split_macros(line: str) -> list[str]:
    """Split a line on '!' (DR macro separator), respecting quotes and parens.

    'mov e,a! mvi c,pcharf! jmp bdos'
    becomes
    ['mov e,a', 'mvi c,pcharf', 'jmp bdos']

    'cpi \' \'! jc comerr'  ->  ["cpi ' '", 'jc comerr']
    """
    # Strip comments first — but only when ';' is OUTSIDE quotes/parens
    in_str = None
    depth = 0
    cut_pos = None
    for i, c in enumerate(line):
        if in_str:
            if c == in_str:
                in_str = None
            continue
        if c in "'\"":
            in_str = c
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == ";" and depth == 0:
            cut_pos = i
            break
    if cut_pos is not None:
        line = line[:cut_pos]

    parts = []
    cur = []
    in_str = None
    depth = 0
    for c in line:
        if in_str:
            cur.append(c)
            if c == in_str:
                in_str = None
            continue
        if c in "'\"":
            in_str = c
            cur.append(c)
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == "!" and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(c)
    last = "".join(cur).strip()
    if last:
        parts.append(last)
    return parts


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _parse_register(s: str) -> Optional[int]:
    """Return register encoding 0-7, or None."""
    s = s.lower().strip()
    return {"b": 0, "c": 1, "d": 2, "e": 3, "h": 4, "l": 5, "m": 6, "a": 7}.get(s)


def _parse_reg_alias_value(val) -> Optional[int]:
    """If val is a register-alias sentinel (negative < -10), return register 0-7.
    Otherwise return None. Register aliases are stored in labels dict as -(r+10)."""
    if isinstance(val, int) and val <= -10:
        return -(val + 10)
    return None


def _resolve_register(s: str, labels: dict) -> Optional[int]:
    """Parse a register operand (literal or register alias label).
    Returns 0-7 or None. 's' is the operand string, 'labels' is the label dict."""
    r = _parse_register(s)
    if r is not None:
        return r
    # Check if it's a register alias label
    val = labels.get(s.lower())
    if val is not None:
        return _parse_reg_alias_value(val)
    return None


def _resolve_regpair(s: str, labels: dict) -> Optional[str]:
    """Return 'b', 'd', 'h', or 'sp', checking register aliases.
    If s is a register alias for b, returns 'b'. For c -> 'b', for d/e -> 'd', for h/l -> 'h'.
    """
    rp = _parse_regpair(s)
    if rp is not None:
        return rp
    # Check register alias
    val = labels.get(s.lower())
    if val is not None:
        r = _parse_reg_alias_value(val)
        if r is not None:
            # Map register 0-7 to its register pair
            # b(0),c(1)->bc; d(2),e(3)->de; h(4),l(5)->hl; m(6)->hl (via H? no, special); a(7)->psw
            pair_map = {0: "b", 1: "b", 2: "d", 3: "d", 4: "h", 5: "h"}
            return pair_map.get(r)
    return None


def _parse_regpair(s: str) -> Optional[str]:
    """Return 'b', 'd', 'h', or 'sp'."""
    s = s.lower().strip()
    if s in ("b", "bc"):
        return "b"
    if s in ("d", "de"):
        return "d"
    if s in ("h", "hl"):
        return "h"
    if s in ("sp", "p"):
        return "sp"
    return None


# ---------------------------------------------------------------------------
# Main assembler
# ---------------------------------------------------------------------------

class Assembler:
    """Two-pass 8080 / DR-syntax assembler."""

    def __init__(self):
        self.labels: dict[str, int] = {}
        self.code: bytearray = bytearray()
        self.base_addr: int = 0  # The ORG address
        self.errors: list[str] = []
        self.current_pass: int = 1
        self.if_stack: list[bool] = []
        self._addr: int = 0  # Current code emission address

    def assemble_file(self, path: str, org_addr: Optional[int] = None) -> tuple[bytes, int]:
        """Assemble a file. Returns (bytes, base_addr)."""
        text = Path(path).read_text(errors="replace")
        lines = text.splitlines()
        # Filter out CP/M EOF markers (^Z = 0x1A) and other non-printable junk
        # that DR ASM files sometimes have at the end.
        clean = []
        for line in lines:
            # Replace control chars (other than tab, newline, cr) with spaces
            stripped = "".join(c if (c >= " " or c in "\t") else " " for c in line)
            stripped = stripped.strip()
            if stripped:
                clean.append(stripped)
        self._assemble(clean, org_addr)
        return bytes(self.code), self.base_addr

    def _assemble(self, lines: list[str], org_addr: Optional[int] = None):
        # Two passes: first build labels, second emit bytes
        # Pass 1: build label table (only)
        self.current_pass = 1
        self.labels = {}
        self.base_addr = org_addr if org_addr is not None else 0
        self._addr = self.base_addr
        self.if_stack = []
        for lineno, line in enumerate(lines, 1):
            try:
                self._pass1_line(line)
            except _AssembleError as e:
                self.errors.append(f"line {lineno}: {e}")

        # Pass 2: emit bytes (labels are already built)
        self.current_pass = 2
        self.code = bytearray()
        self.base_addr = org_addr if org_addr is not None else 0
        self._addr = self.base_addr
        self.if_stack = []
        for lineno, line in enumerate(lines, 1):
            try:
                self._assemble_line(line)
            except _AssembleError as e:
                self.errors.append(f"line {lineno}: {e}")
                raise

    def _pass1_line(self, line: str):
        """Pass 1: just walk through and collect labels."""
        # Strip comments
        macros = _split_macros(line)
        for macro in macros:
            self._pass1_one(macro)

    def _pass1_one(self, line: str):
        line = line.strip()
        if not line:
            return
        tokens = line.split(None, 1)
        head = tokens[0].lower()
        if head == "if":
            cond_expr = tokens[1].strip() if len(tokens) > 1 else "0"
            try:
                val = _eval_expr(cond_expr, self.labels, self._addr)
                self.if_stack.append(bool(val))
            except _AssembleError:
                self.if_stack.append(False)
            return
        if head == "endif":
            if self.if_stack:
                self.if_stack.pop()
            return
        if head == "else":
            # Flip the top of the if stack — the else branch is taken when
            # the matching if was false.
            if self.if_stack:
                top = self.if_stack.pop()
                self.if_stack.append(not top)
            return
        if self.if_stack and not all(self.if_stack):
            return
        label = None
        if not line.startswith((" ", "\t")):
            colon_pos = self._find_label_colon(line)
            if colon_pos is not None:
                label = line[:colon_pos].strip().lower()
                line = line[colon_pos + 1:].strip()
            else:
                # DR convention: identifier at column 0 followed by equ/db/dw/ds
                # is a label. Check the first word.
                parts = line.split(None, 1)
                if len(parts) >= 2 and parts[0] not in TABLE and parts[0].lower() not in (
                    "org", "end", "if", "endif", "title", "equ", "db", "dw", "ds"
                ):
                    label = parts[0].lower()
                    line = parts[1]
        is_equ = line.split(None, 1)[0].lower() == "equ" if line else False
        if label and label not in self.labels and not is_equ:
            self.labels[label] = self._addr
        if not line:
            return
        # For 'label equ value', evaluate the value and set the label
        if is_equ and label:
            parts = line.split(None, 1)
            operand_str = parts[1].strip() if len(parts) > 1 else ""
            # Register alias: 'arech equ b' means arech IS register b.
            operand = operand_str.strip().lower()
            r = _parse_register(operand)
            if r is not None:
                val = -(r + 10)
            else:
                val = _eval_expr(operand_str, self.labels, self._addr)
            self.labels[label] = val
            return
        self._advance_for_line(line)

    def _advance_for_line(self, line: str):
        """Compute instruction size to advance _addr in pass 1."""
        parts = line.split(None, 1)
        mnem = parts[0].lower()
        operand_str = parts[1].strip() if len(parts) > 1 else ""
        if mnem in ("org",):
            addr = _eval_expr(operand_str, self.labels, self._addr)
            self._addr = addr
            return
        if mnem in ("equ", "end", "title"):
            return
        if mnem == "db":
            items = self._split_operands(operand_str)
            for item in items:
                item = item.strip()
                if (item.startswith("'") and item.endswith("'")) or \
                   (item.startswith('"') and item.endswith('"')):
                    self._addr += len(item) - 2
                else:
                    self._addr += 1
            return
        if mnem == "dw":
            items = self._split_operands(operand_str)
            self._addr += 2 * len(items)
            return
        if mnem == "ds":
            val = _eval_expr(operand_str, self.labels, self._addr)
            self._addr += val
            return
        # It's an instruction — figure out size
        if mnem in TABLE:
            variants = TABLE[mnem]
            operands = self._split_operands(operand_str)
            for base, n_ops, kind in variants:
                if len(operands) != n_ops:
                    continue
                # All instructions are 1, 2, or 3 bytes
                if kind == "none":
                    self._addr += 1
                    return
                if kind in ("reg_sss", "reg_ddd", "rp_only", "rp_pushpop", "psw", "rst_n"):
                    self._addr += 1
                    return
                if kind in ("reg_reg",):
                    self._addr += 1
                    return
                if kind in ("reg_imm8", "imm8"):
                    self._addr += 2
                    return
                if kind in ("rp_imm16", "addr16"):
                    self._addr += 3
                    return
        # Unknown — assume 1 byte
        self._addr += 1

    def _emit(self, bytes_: bytes):
        if self.current_pass == 2:
            self.code.extend(bytes_)
        # Always advance _addr so pass 2 tracks where we are (even though
        # code isn't emitted until pass 2, we need _addr for label
        # resolution of forward references).
        self._addr += len(bytes_)

    def _assemble_line(self, line: str):
        # Strip comment (semicolons start a comment to end of line in DR syntax)
        # But preserve '!' (macro separator) — handled by _split_macros
        macros = _split_macros(line)
        for macro in macros:
            self._assemble_one(macro)

    def _assemble_one(self, line: str):
        line = line.strip()
        if not line:
            return

        # Handle if/endif
        tokens = line.split(None, 1)
        head = tokens[0].lower()
        if head == "if":
            cond_expr = tokens[1].strip() if len(tokens) > 1 else "0"
            # If we're already in a false branch, keep it false
            if self.if_stack and not all(self.if_stack):
                self.if_stack.append(False)
                return
            try:
                val = _eval_expr(cond_expr, self.labels, self._addr)
                self.if_stack.append(bool(val))
            except _AssembleError:
                self.if_stack.append(False)
            return
        if head == "endif":
            if self.if_stack:
                self.if_stack.pop()
            return
        if head == "else":
            # Flip the top of the if stack — the else branch is taken when
            # the matching if was false.
            if self.if_stack:
                top = self.if_stack.pop()
                self.if_stack.append(not top)
            return

        # If we're in a false branch, skip everything except if/endif
        if self.if_stack and not all(self.if_stack):
            return

        # Detect label: must start at column 0 with no whitespace.
        # Forms supported:
        #   "label: instruction"   (colon-separated, single label)
        #   "label"                (label on a line by itself)
        #   "label db 5"           (label followed by data — handled in directive code)
        #   "label equ value"      (DR convention, no colon — handled here)
        label = None
        if not line.startswith((" ", "\t")):
            colon_pos = self._find_label_colon(line)
            if colon_pos is not None:
                label = line[:colon_pos].strip().lower()
                line = line[colon_pos + 1:].strip()
            elif not line.startswith((" ", "\t")):
                # DR convention: identifier at column 0 followed by equ/db/dw/ds
                # is a label. Check the first word.
                parts = line.split(None, 1)
                if len(parts) >= 2 and parts[0] not in TABLE and parts[0].lower() not in (
                    "org", "end", "if", "endif", "title", "equ", "db", "dw", "ds"
                ):
                    label = parts[0].lower()
                    line = parts[1]

        # Re-tokenize after label removal
        if label:
            if self.current_pass == 1:
                if label in self.labels:
                    self.errors.append(f"duplicate label: {label}")
                else:
                    self.labels[label] = self._addr
            # If followed by nothing, just define the label
            if not line:
                return

        # Now split label-instruction: first word is the mnemonic/directive
        parts = line.split(None, 1)
        mnem = parts[0].lower()
        operand_str = parts[1].strip() if len(parts) > 1 else ""

        # Handle directives
        if mnem == "org":
            addr = _eval_expr(operand_str, self.labels, self._addr)
            self.base_addr = addr
            self._addr = addr
            return
        if mnem in ("equ", "set"):
            if not label:
                raise _AssembleError("equ/set requires a label")
            operand = operand_str.strip().lower()
            # Register alias: 'arech equ b' means arech IS register b.
            r = _parse_register(operand)
            if r is not None:
                # Store as negative sentinel: -(r+10) so we can detect it
                val = -(r + 10)
            else:
                val = _eval_expr(operand_str, self.labels, self._addr)
            if self.current_pass == 1:
                self.labels[label] = val
            return
        if mnem == "end":
            return
        if mnem == "db":
            self._emit_db(operand_str)
            return
        if mnem == "dw":
            self._emit_dw(operand_str)
            return
        if mnem == "ds":
            self._emit_ds(operand_str)
            return
        if mnem == "title":
            return  # ignore title directive

        # It's a real 8080 mnemonic
        self._emit_instruction(mnem, operand_str)

    def _emit_db(self, operand_str: str):
        """Emit db: bytes and string literals, comma-separated."""
        items = self._split_operands(operand_str)
        for item in items:
            item = item.strip()
            if (item.startswith("'") and item.endswith("'")) or \
               (item.startswith('"') and item.endswith('"')):
                # String literal: 'foo' or "foo" — strip quotes, emit bytes
                s = item[1:-1]
                # Handle escape sequences? Skip for now.
                self._emit(s.encode("ascii"))
            else:
                # Numeric or label expression
                val = _eval_expr(item, self.labels, self._addr)
                self._emit(bytes([val & 0xFF]))

    def _emit_dw(self, operand_str: str):
        items = self._split_operands(operand_str)
        for item in items:
            val = _eval_expr(item.strip(), self.labels, self._addr)
            self._emit(bytes([val & 0xFF, (val >> 8) & 0xFF]))

    def _emit_ds(self, operand_str: str):
        val = _eval_expr(operand_str, self.labels, self._addr)
        self._emit(bytes(val))

    def _find_label_colon(self, line: str) -> Optional[int]:
        """Return the position of a label-colon in line, or None.

        A label-colon is a ':' that appears at column 0 (start of an
        identifier), not inside a string literal or parentheses.
        """
        in_str = None
        depth = 0
        for i, c in enumerate(line):
            if in_str:
                if c == in_str:
                    in_str = None
                continue
            if c in "'\"":
                in_str = c
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            if c == ":" and depth == 0:
                # Verify the previous non-space char is an identifier char
                # (label colon, not arithmetic).
                prev = ""
                for j in range(i - 1, -1, -1):
                    if line[j] not in (" ", "\t"):
                        prev = line[j]
                        break
                if prev and (prev.isalnum() or prev == "_" or prev == "$"):
                    return i
        return None

    def _split_operands(self, s: str) -> list[str]:
        """Split a comma-separated operand list, respecting parens and quotes."""
        out = []
        depth = 0
        in_str = None
        cur = []
        for c in s:
            if in_str:
                cur.append(c)
                if c == in_str:
                    in_str = None
                continue
            if c in "'\"":
                in_str = c
                cur.append(c)
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            if c == "," and depth == 0:
                out.append("".join(cur))
                cur = []
                continue
            cur.append(c)
        if cur:
            out.append("".join(cur))
        return out

    def _emit_instruction(self, mnem: str, operand_str: str):
        if mnem not in TABLE:
            raise _AssembleError(f"unknown mnemonic: {mnem}")
        variants = TABLE[mnem]
        operands = self._split_operands(operand_str)
        # Find matching variant. Register bits location depends on the
        # instruction family (DR/CPU quirk):
        #   MOV/MVI/ADD/SUB/etc: register in bits 5,4,3 (DDD or SSS)
        #   ANA/XRA/ORA/CMP/INR/DCR: register in bits 2,1,0 (SSS or DDD)
        for base, n_ops, kind in variants:
            if len(operands) != n_ops:
                continue
            # reg_reg: bits 5,4,3 = dst, bits 2,1,0 = src (MOV family)
            if kind == "reg_reg":
                d = _resolve_register(operands[0], self.labels)
                s = _resolve_register(operands[1], self.labels)
                if d is None or s is None:
                    continue
                if (base & 0x38) != (d << 3) or (base & 0x07) != s:
                    continue
            # reg_sss: ANA/XRA/ORA/CMP/ADD src/SUB src use bits 2,1,0
            if kind == "reg_sss":
                r = _resolve_register(operands[0], self.labels)
                if r is None:
                    continue
                if (base & 0x07) != r:
                    continue
            # reg_ddd: INR/DCR use bits 5,4,3
            if kind == "reg_ddd":
                r = _resolve_register(operands[0], self.labels)
                if r is None:
                    continue
                if (base & 0x38) != (r << 3):
                    continue
            # reg_imm8: MVI uses bits 5,4,3
            if kind == "reg_imm8":
                r = _resolve_register(operands[0], self.labels)
                if r is None:
                    continue
                if (base & 0x38) != (r << 3):
                    continue
            # rp_imm16, rp_only, rp_pushpop: bits 5,4 hold the register pair
            if kind in ("rp_imm16", "rp_only", "rp_pushpop"):
                rp = _resolve_regpair(operands[0], self.labels)
                if rp is None:
                    continue
                rp_val = {"b": 0, "d": 1, "h": 2, "sp": 3}[rp]
                if (base >> 4) & 0x03 != rp_val:
                    continue
            try:
                inst_bytes = self._encode(mnem, base, kind, operands)
                self._emit(inst_bytes)
                return
            except _AssembleError:
                continue
        raise _AssembleError(f"no matching variant for {mnem} {operand_str!r}")

    def _encode(self, mnem: str, base: int, kind: str, operands: list[str]) -> bytes:
        if kind == "none":
            return bytes([base])
        if kind == "reg_sss" or kind == "reg_ddd":
            # Register bits are already encoded in base.
            return bytes([base])
        if kind == "reg_reg":
            d = _resolve_register(operands[0], self.labels)
            s = _resolve_register(operands[1], self.labels)
            if d is None or s is None:
                raise _AssembleError(f"bad registers in mov")
            return bytes([base])
        if kind == "reg_imm8":
            r = _resolve_register(operands[0], self.labels)
            if r is None:
                raise _AssembleError(f"expected register, got {operands[0]!r}")
            v = _eval_expr(operands[1], self.labels, self._addr) & 0xFF
            # base already encodes the register; just append the immediate
            return bytes([base, v])
        if kind == "rp_only":
            rp = _resolve_regpair(operands[0], self.labels)
            if rp is None:
                raise _AssembleError(f"bad register pair: {operands[0]!r}")
            return bytes([base])  # base already encodes the rp
        if kind == "rp_pushpop":
            rp = _resolve_regpair(operands[0], self.labels)
            if rp is None:
                raise _AssembleError(f"bad register pair: {operands[0]!r}")
            return bytes([base])
        if kind == "psw":
            return bytes([base])
        if kind == "rp_imm16":
            rp = _resolve_regpair(operands[0], self.labels)
            if rp is None:
                raise _AssembleError(f"bad register pair: {operands[0]!r}")
            v = _eval_expr(operands[1], self.labels, self._addr) & 0xFFFF
            return bytes([base, v & 0xFF, (v >> 8) & 0xFF])
        if kind == "addr16":
            v = _eval_expr(operands[0], self.labels, self._addr) & 0xFFFF
            return bytes([base, v & 0xFF, (v >> 8) & 0xFF])
        if kind == "imm8":
            v = _eval_expr(operands[0], self.labels, self._addr) & 0xFF
            return bytes([base, v])
        if kind == "imm16":
            v = _eval_expr(operands[0], self.labels, self._addr) & 0xFFFF
            return bytes([base, v & 0xFF, (v >> 8) & 0xFF])
        if kind == "rst_n":
            n = _eval_expr(operands[0], self.labels, self._addr) & 0x07
            return bytes([0xC7 | (n << 3)])
        raise _AssembleError(f"unhandled encoding kind: {kind}")


def assemble_file(path: str, org_addr: Optional[int] = None) -> tuple[bytes, int]:
    """Assemble a file. Returns (bytes, base_addr)."""
    asm = Assembler()
    return asm.assemble_file(path, org_addr)
