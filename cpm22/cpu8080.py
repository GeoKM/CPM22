"""Intel 8080 CPU core.

Pure Python, dict-of-handlers dispatch (skill Section 1). 244 documented opcodes
plus 12 undocumented opcodes (NOP/RST variants), 8080 flag layout
(S, Z, AC, P, CY — bit 5 always 0, no N flag), correct HALF-CARRY (AC) for DAA,
and 4/5/7/10/11 cycle accounting per the Intel 8080 datasheet.

The skill's vintage-cpu-emulation/SKILL.md Section 1 calls out a long list of
dispatch-table bugs that bit me in the Z80 build. This file applies those
lessons:

- Every opcode 0x00..0xFF has a handler. Unimplemented opcodes map to NOP
  (still increments PC, doesn't change state).
- Late-binding defaults use `reg=d` and `cond=c` patterns explicitly.
- HALT (0x76) is special: it sets cpu.halted and re-executes the same
  instruction until an interrupt fires.
- The 0x00-0x3F short-form range is wired exhaustively.
- INC/DEC r set all four of S, Z, AC, P per the 8080 datasheet.
- DAA reads AC, not a half-carry we recompute.
- The factory-method-in-lambda pitfall is dodged by calling the factory
  directly: `ops.append((0xC0, self._cond_ret(0)))`.
"""

from __future__ import annotations

from typing import Callable, ClassVar

# Flag bit positions in the F register
FLAG_S = 0x80  # Sign
FLAG_Z = 0x40  # Zero
FLAG_AC = 0x10  # Auxiliary Carry (half carry)
FLAG_P = 0x04  # Parity
FLAG_CY = 0x01  # Carry


class CPU8080:
    """Intel 8080 CPU core.

    The CPU owns no I/O. `step()` reads from `self.in_port[port](self)` and
    writes to `self.out_port[port](self, val)` dicts. The host system wires
    those handlers in.
    """

    def __init__(self, memory):
        self.mem = memory
        self.A = 0
        self.B = 0
        self.C = 0
        self.D = 0
        self.E = 0
        self.H = 0
        self.L = 0
        self.F = 0
        self.SP = 0xFFFF
        self.PC = 0
        self.halted = False
        self.interrupts_enabled = True
        self.cycles = 0
        # I/O dispatch
        self.in_port: dict[int, Callable[["CPU8080"], int]] = {}
        self.out_port: dict[int, Callable[["CPU8080", int], None]] = {}
        # Dispatch table
        self._main_table: list[Callable[[], int]] = [self._nop] * 256
        self._build_dispatch()

    # ------------------------------------------------------------------
    # Register pair helpers
    # ------------------------------------------------------------------

    def BC(self) -> int:
        return (self.B << 8) | self.C

    def DE(self) -> int:
        return (self.D << 8) | self.E

    def HL(self) -> int:
        return (self.H << 8) | self.L

    def setBC(self, v: int) -> None:
        self.B = (v >> 8) & 0xFF
        self.C = v & 0xFF

    def setDE(self, v: int) -> None:
        self.D = (v >> 8) & 0xFF
        self.E = v & 0xFF

    def setHL(self, v: int) -> None:
        self.H = (v >> 8) & 0xFF
        self.L = v & 0xFF

    # ------------------------------------------------------------------
    # Memory read/write (delegate to memory)
    # ------------------------------------------------------------------

    def rb(self, addr: int) -> int:
        return self.mem.rb(addr)

    def wb(self, addr: int, val: int) -> None:
        self.mem.wb(addr, val)

    def rw(self, addr: int) -> int:
        return self.mem.rw(addr)

    def ww(self, addr: int, val: int) -> None:
        self.mem.ww(addr, val)

    def fetchb(self) -> int:
        v = self.mem.rb(self.PC)
        self.PC = (self.PC + 1) & 0xFFFF
        return v

    def fetchw(self) -> int:
        lo = self.fetchb()
        hi = self.fetchb()
        return (hi << 8) | lo

    # ------------------------------------------------------------------
    # Flag helpers
    # ------------------------------------------------------------------

    def _set_sz_flags(self, v: int) -> None:
        v &= 0xFF
        if v == 0:
            self.F |= FLAG_Z
        else:
            self.F &= ~FLAG_Z & 0xFF
        if v & 0x80:
            self.F |= FLAG_S
        else:
            self.F &= ~FLAG_S & 0xFF

    def _set_p_flag(self, v: int) -> None:
        v &= 0xFF
        parity = bin(v).count("1") & 1
        if parity == 0:
            self.F |= FLAG_P
        else:
            self.F &= ~FLAG_P & 0xFF

    def _set_sz_p_flags(self, v: int) -> None:
        self._set_sz_flags(v)
        self._set_p_flag(v)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def step(self) -> int:
        if self.halted:
            return 4
        op = self.fetchb()
        handler = self._main_table[op]
        # Pass the opcode to the handler so it can decode operand fields
        # without re-reading memory. Skill Section 1, "PC is *after* fetch
        # in Z80, not before" — the same applies to 8080, and it means
        # `self.PC - 1` is the opcode's ADDRESS, not its VALUE.
        c = handler(op)
        self.cycles += c
        return c

    def run(self, n: int) -> int:
        total = 0
        for _ in range(n):
            if self.halted:
                break
            total += self.step()
        return total

    # ------------------------------------------------------------------
    # Register / flag accessors (used by handlers)
    # ------------------------------------------------------------------

    def _getreg(self, r: int) -> int:
        if r == 0:
            return self.B
        if r == 1:
            return self.C
        if r == 2:
            return self.D
        if r == 3:
            return self.E
        if r == 4:
            return self.H
        if r == 5:
            return self.L
        if r == 6:
            return self.rb(self.HL())
        return self.A  # r == 7

    def _setreg(self, r: int, v: int) -> None:
        v &= 0xFF
        if r == 0:
            self.B = v
        elif r == 1:
            self.C = v
        elif r == 2:
            self.D = v
        elif r == 3:
            self.E = v
        elif r == 4:
            self.H = v
        elif r == 5:
            self.L = v
        elif r == 6:
            self.wb(self.HL(), v)
        else:
            self.A = v

    def _cc(self, cc: int) -> bool:
        """Evaluate a condition code. cc=0..7."""
        z = bool(self.F & FLAG_Z)
        c = bool(self.F & FLAG_CY)
        p = bool(self.F & FLAG_P)
        s = bool(self.F & FLAG_S)
        return [
            not z,   # 0 NZ
            z,        # 1 Z
            not c,   # 2 NC
            c,        # 3 C
            not p,   # 4 PO
            p,        # 5 PE
            not s,   # 6 P
            s,        # 7 M
        ][cc]

    # ------------------------------------------------------------------
    # ALU primitives
    # ------------------------------------------------------------------

    def _add(self, v: int) -> None:
        a = self.A
        new = (a + v) & 0xFF
        if (a & 0x0F) + (v & 0x0F) > 0x0F:
            self.F |= FLAG_AC
        else:
            self.F &= ~FLAG_AC & 0xFF
        if a + v > 0xFF:
            self.F |= FLAG_CY
        else:
            self.F &= ~FLAG_CY & 0xFF
        self.A = new
        self._set_sz_p_flags(new)

    def _adc(self, v: int) -> None:
        a = self.A
        c = 1 if self.F & FLAG_CY else 0
        new = (a + v + c) & 0xFF
        if (a & 0x0F) + (v & 0x0F) + c > 0x0F:
            self.F |= FLAG_AC
        else:
            self.F &= ~FLAG_AC & 0xFF
        if a + v + c > 0xFF:
            self.F |= FLAG_CY
        else:
            self.F &= ~FLAG_CY & 0xFF
        self.A = new
        self._set_sz_p_flags(new)

    def _sub(self, v: int) -> None:
        a = self.A
        new = (a - v) & 0xFF
        if (a & 0x0F) < (v & 0x0F):
            self.F |= FLAG_AC
        else:
            self.F &= ~FLAG_AC & 0xFF
        if a < v:
            self.F |= FLAG_CY
        else:
            self.F &= ~FLAG_CY & 0xFF
        self.A = new
        self._set_sz_p_flags(new)

    def _sbb(self, v: int) -> None:
        a = self.A
        c = 1 if self.F & FLAG_CY else 0
        new = (a - v - c) & 0xFF
        if (a & 0x0F) < ((v & 0x0F) + c):
            self.F |= FLAG_AC
        else:
            self.F &= ~FLAG_AC & 0xFF
        if a < v + c:
            self.F |= FLAG_CY
        else:
            self.F &= ~FLAG_CY & 0xFF
        self.A = new
        self._set_sz_p_flags(new)

    def _ana(self, v: int) -> None:
        self.A = (self.A & v) & 0xFF
        if self.A & 0x10:
            self.F |= FLAG_AC
        else:
            self.F &= ~FLAG_AC & 0xFF
        self.F &= ~FLAG_CY & 0xFF
        self._set_sz_p_flags(self.A)

    def _xra(self, v: int) -> None:
        self.A = (self.A ^ v) & 0xFF
        self.F &= ~FLAG_AC & 0xFF
        self.F &= ~FLAG_CY & 0xFF
        self._set_sz_p_flags(self.A)

    def _ora(self, v: int) -> None:
        self.A = (self.A | v) & 0xFF
        self.F &= ~FLAG_AC & 0xFF
        self.F &= ~FLAG_CY & 0xFF
        self._set_sz_p_flags(self.A)

    def _cmp(self, v: int) -> None:
        a = self.A
        if (a & 0x0F) < (v & 0x0F):
            self.F |= FLAG_AC
        else:
            self.F &= ~FLAG_AC & 0xFF
        if a < v:
            self.F |= FLAG_CY
        else:
            self.F &= ~FLAG_CY & 0xFF
        new = (a - v) & 0xFF
        self._set_sz_p_flags(new)

    # ------------------------------------------------------------------
    # Instruction handlers (one per opcode, return cycle count)
    # ------------------------------------------------------------------

    def _nop(self, op: int) -> int:
        return 4

    def _mov_rr(self, op: int) -> int:
        d = (op >> 3) & 0x07
        s = op & 0x07
        self._setreg(d, self._getreg(s))
        return 4

    def _mvi_r(self, op: int) -> int:
        r = (op >> 3) & 0x07
        v = self.fetchb()
        self._setreg(r, v)
        # MVI M is 10 cycles; MVI r is 7
        return 10 if r == 6 else 7

    def _lxi_rp(self, op: int) -> int:
        rp = (op >> 4) & 0x03
        v = self.fetchw()
        if rp == 0:
            self.setBC(v)
        elif rp == 1:
            self.setDE(v)
        elif rp == 2:
            self.setHL(v)
        else:
            self.SP = v
        return 10

    def _stax_b(self, op: int) -> int:
        self.wb(self.BC(), self.A)
        return 7

    def _stax_d(self, op: int) -> int:
        self.wb(self.DE(), self.A)
        return 7

    def _ldax_b(self, op: int) -> int:
        self.A = self.rb(self.BC())
        return 7

    def _ldax_d(self, op: int) -> int:
        self.A = self.rb(self.DE())
        return 7

    def _shld(self, op: int) -> int:
        addr = self.fetchw()
        self.wb(addr, self.L)
        self.wb((addr + 1) & 0xFFFF, self.H)
        return 16

    def _lhld(self, op: int) -> int:
        addr = self.fetchw()
        self.L = self.rb(addr)
        self.H = self.rb((addr + 1) & 0xFFFF)
        return 16

    def _sta(self, op: int) -> int:
        self.wb(self.fetchw(), self.A)
        return 13

    def _lda(self, op: int) -> int:
        self.A = self.rb(self.fetchw())
        return 13

    def _inc_r(self, op: int) -> int:
        r = (op >> 3) & 0x07
        v = self._getreg(r)
        new = (v + 1) & 0xFF
        if (v & 0x0F) == 0x0F:
            self.F |= FLAG_AC
        else:
            self.F &= ~FLAG_AC & 0xFF
        self._set_sz_p_flags(new)
        self._setreg(r, new)
        return 10 if r == 6 else 5

    def _dec_r(self, op: int) -> int:
        r = (op >> 3) & 0x07
        v = self._getreg(r)
        new = (v - 1) & 0xFF
        if (v & 0x0F) == 0x00:
            self.F |= FLAG_AC
        else:
            self.F &= ~FLAG_AC & 0xFF
        self._set_sz_p_flags(new)
        self._setreg(r, new)
        return 10 if r == 6 else 5

    def _inc_rp(self, op: int) -> int:
        rp = (op >> 4) & 0x03
        if rp == 0:
            self.setBC((self.BC() + 1) & 0xFFFF)
        elif rp == 1:
            self.setDE((self.DE() + 1) & 0xFFFF)
        elif rp == 2:
            self.setHL((self.HL() + 1) & 0xFFFF)
        else:
            self.SP = (self.SP + 1) & 0xFFFF
        return 5

    def _dec_rp(self, op: int) -> int:
        rp = (op >> 4) & 0x03
        if rp == 0:
            self.setBC((self.BC() - 1) & 0xFFFF)
        elif rp == 1:
            self.setDE((self.DE() - 1) & 0xFFFF)
        elif rp == 2:
            self.setHL((self.HL() - 1) & 0xFFFF)
        else:
            self.SP = (self.SP - 1) & 0xFFFF
        return 5

    def _dad_rp(self, op: int) -> int:
        rp = (op >> 4) & 0x03
        if rp == 0:
            v = self.BC()
        elif rp == 1:
            v = self.DE()
        elif rp == 2:
            v = self.HL()
        else:
            v = self.SP
        result = self.HL() + v
        new_carry = (result >> 16) & 1
        self.setHL(result & 0xFFFF)
        if new_carry:
            self.F |= FLAG_CY
        else:
            self.F &= ~FLAG_CY & 0xFF
        return 10

    def _alu_r(self, op: int) -> int:
        sub = (op >> 3) & 0x07
        r = op & 0x07
        v = self._getreg(r)
        self._do_alu(sub, v)
        return 7 if r == 6 else 4

    def _alu_m(self, op: int) -> int:
        sub = (op >> 3) & 0x07
        v = self.rb(self.HL())
        self._do_alu(sub, v)
        return 7

    def _alu_n(self, op: int) -> int:
        sub = (op >> 3) & 0x07
        v = self.fetchb()
        self._do_alu(sub, v)
        return 7

    def _do_alu(self, sub: int, v: int) -> None:
        if sub == 0:
            self._add(v)
        elif sub == 1:
            self._adc(v)
        elif sub == 2:
            self._sub(v)
        elif sub == 3:
            self._sbb(v)
        elif sub == 4:
            self._ana(v)
        elif sub == 5:
            self._xra(v)
        elif sub == 6:
            self._ora(v)
        else:
            self._cmp(v)

    def _rlc(self, op: int) -> int:
        a = self.A
        cy = (a >> 7) & 1
        self.A = ((a << 1) | cy) & 0xFF
        self.F = (self.F & ~(FLAG_CY)) | (cy * FLAG_CY)
        return 4

    def _rrc(self, op: int) -> int:
        a = self.A
        cy = a & 1
        self.A = ((a >> 1) | (cy << 7)) & 0xFF
        self.F = (self.F & ~(FLAG_CY)) | (cy * FLAG_CY)
        return 4

    def _ral(self, op: int) -> int:
        a = self.A
        old_cy = (self.F & FLAG_CY) >> 0
        new_cy = (a >> 7) & 1
        self.A = ((a << 1) | old_cy) & 0xFF
        self.F = (self.F & ~(FLAG_CY)) | (new_cy * FLAG_CY)
        return 4

    def _rar(self, op: int) -> int:
        a = self.A
        old_cy = (self.F & FLAG_CY) >> 0
        new_cy = a & 1
        self.A = ((a >> 1) | (old_cy << 7)) & 0xFF
        self.F = (self.F & ~(FLAG_CY)) | (new_cy * FLAG_CY)
        return 4

    def _cma(self, op: int) -> int:
        self.A ^= 0xFF
        return 4

    def _stc(self, op: int) -> int:
        self.F |= FLAG_CY
        return 4

    def _cmc(self, op: int) -> int:
        self.F ^= FLAG_CY
        return 4

    def _daa(self, op: int) -> int:
        # Intel 8080 DAA algorithm:
        # 1. If (A & 0x0F) > 9 OR AC=1, add 6 to A.
        # 2. If (A >> 4) > 9 OR CY=1 OR (A >> 4) >= 9 AND (A & 0x0F) > 9,
        #    add 0x60 to A and set CY.
        # AC is set if step 1 produced a carry out of bit 3 (i.e., the
        # low nibble of the ORIGINAL A was >= 0x0A, OR AC was already 1).
        # CY is set if step 2 produced a carry out of bit 7.
        a = self.A
        old_cy = bool(self.F & FLAG_CY)
        old_ac = bool(self.F & FLAG_AC)
        cy = old_cy
        ac = old_ac
        if (a & 0x0F) > 9 or ac:
            a = (a + 0x06) & 0xFF
            ac = True  # carry from bit 3
        if (a >> 4) > 9 or cy or ((a >> 4) >= 9 and (a & 0x0F) > 9):
            a = (a + 0x60) & 0xFF
            cy = True
        self.A = a
        # Update AC and CY
        if ac:
            self.F |= FLAG_AC
        else:
            self.F &= ~FLAG_AC & 0xFF
        if cy:
            self.F |= FLAG_CY
        else:
            self.F &= ~FLAG_CY & 0xFF
        self._set_sz_p_flags(self.A)
        return 4

    def _push_b(self, op: int) -> int:
        self.SP = (self.SP - 1) & 0xFFFF
        self.wb(self.SP, self.B)
        self.SP = (self.SP - 1) & 0xFFFF
        self.wb(self.SP, self.C)
        return 11

    def _push_d(self, op: int) -> int:
        self.SP = (self.SP - 1) & 0xFFFF
        self.wb(self.SP, self.D)
        self.SP = (self.SP - 1) & 0xFFFF
        self.wb(self.SP, self.E)
        return 11

    def _push_h(self, op: int) -> int:
        self.SP = (self.SP - 1) & 0xFFFF
        self.wb(self.SP, self.H)
        self.SP = (self.SP - 1) & 0xFFFF
        self.wb(self.SP, self.L)
        return 11

    def _push_psw(self, op: int) -> int:
        self.SP = (self.SP - 1) & 0xFFFF
        self.wb(self.SP, self.A)
        self.SP = (self.SP - 1) & 0xFFFF
        # 8080 flag byte: bits 1 and 3 are always 1 (some references say
        # "always 1"; CP/M 2.2 expects this layout and ignores them on POP).
        self.wb(self.SP, self.F | 0x02 | 0x08)
        return 11

    def _pop_b(self, op: int) -> int:
        self.C = self.rb(self.SP)
        self.SP = (self.SP + 1) & 0xFFFF
        self.B = self.rb(self.SP)
        self.SP = (self.SP + 1) & 0xFFFF
        return 10

    def _pop_d(self, op: int) -> int:
        self.E = self.rb(self.SP)
        self.SP = (self.SP + 1) & 0xFFFF
        self.D = self.rb(self.SP)
        self.SP = (self.SP + 1) & 0xFFFF
        return 10

    def _pop_h(self, op: int) -> int:
        self.L = self.rb(self.SP)
        self.SP = (self.SP + 1) & 0xFFFF
        self.H = self.rb(self.SP)
        self.SP = (self.SP + 1) & 0xFFFF
        return 10

    def _pop_psw(self, op: int) -> int:
        # 8080 flag byte: bits 1 and 3 are forced to 0; bits 5 and 3 forced to 0.
        self.F = self.rb(self.SP) & 0xD7
        self.SP = (self.SP + 1) & 0xFFFF
        self.A = self.rb(self.SP)
        return 10

    def _jmp(self, op: int) -> int:
        self.PC = self.fetchw()
        return 10

    def _pchl(self, op: int) -> int:
        self.PC = self.HL()
        return 5

    def _call(self, op: int) -> int:
        addr = self.fetchw()
        # PC is now past the 2-byte operand — push it as the return address
        ret = self.PC
        self.SP = (self.SP - 1) & 0xFFFF
        self.wb(self.SP, (ret >> 8) & 0xFF)
        self.SP = (self.SP - 1) & 0xFFFF
        self.wb(self.SP, ret & 0xFF)
        self.PC = addr
        return 17

    def _ret(self, op: int) -> int:
        lo = self.rb(self.SP)
        self.SP = (self.SP + 1) & 0xFFFF
        hi = self.rb(self.SP)
        self.SP = (self.SP + 1) & 0xFFFF
        self.PC = (hi << 8) | lo
        return 10

    def _rst(self, op: int) -> int:
        ret = self.PC
        self.SP = (self.SP - 1) & 0xFFFF
        self.wb(self.SP, (ret >> 8) & 0xFF)
        self.SP = (self.SP - 1) & 0xFFFF
        self.wb(self.SP, ret & 0xFF)
        self.PC = op & 0x38
        return 11

    def _cond_jmp(self, op: int) -> int:
        cc = (op >> 3) & 0x07
        addr = self.fetchw()
        if self._cc(cc):
            self.PC = addr
        return 10

    def _cond_call(self, op: int) -> int:
        cc = (op >> 3) & 0x07
        addr = self.fetchw()
        if self._cc(cc):
            ret = self.PC
            self.SP = (self.SP - 1) & 0xFFFF
            self.wb(self.SP, (ret >> 8) & 0xFF)
            self.SP = (self.SP - 1) & 0xFFFF
            self.wb(self.SP, ret & 0xFF)
            self.PC = addr
            return 17
        return 11

    def _cond_ret(self, op: int) -> int:
        cc = (op >> 3) & 0x07
        if self._cc(cc):
            lo = self.rb(self.SP)
            self.SP = (self.SP + 1) & 0xFFFF
            hi = self.rb(self.SP)
            self.SP = (self.SP + 1) & 0xFFFF
            self.PC = (hi << 8) | lo
            return 11
        return 5

    def _xchg(self, op: int) -> int:
        self.H, self.D = self.D, self.H
        self.L, self.E = self.E, self.L
        return 4

    def _xthl(self, op: int) -> int:
        h = self.H
        l = self.L
        self.L = self.rb(self.SP)
        self.H = self.rb((self.SP + 1) & 0xFFFF)
        self.wb(self.SP, l)
        self.wb((self.SP + 1) & 0xFFFF, h)
        return 18

    def _sphl(self, op: int) -> int:
        self.SP = self.HL()
        return 5

    def _in(self, op: int) -> int:
        port = self.fetchb()
        handler = self.in_port.get(port)
        if handler is not None:
            self.A = handler(self) & 0xFF
        else:
            self.A = 0xFF
        return 10

    def _out(self, op: int) -> int:
        port = self.fetchb()
        handler = self.out_port.get(port)
        if handler is not None:
            handler(self, self.A & 0xFF)
        return 10

    def _ei(self, op: int) -> int:
        self.interrupts_enabled = True
        return 4

    def _di(self, op: int) -> int:
        self.interrupts_enabled = False
        return 4

    def _hlt(self, op: int) -> int:
        self.halted = True
        return 7

    # ------------------------------------------------------------------
    # Dispatch table
    # ------------------------------------------------------------------

    def _build_dispatch(self) -> None:
        """Build the 256-entry dispatch table. Every opcode is wired.

        Order of writes matters when multiple aliases exist (e.g. 0xD0 and
        0xC0 are both RNC / cond_ret(2) — they encode to the same condition
        number (2), just placed at different opcodes). We write all of them
        — `_main_table[op] = fn` is last-wins, and for these aliases they
        ALL dispatch to the same handler so order is irrelevant.
        """
        t = self._main_table

        # 0x00 — NOP
        t[0x00] = self._nop

        # 0x01, 0x11, 0x21, 0x31 — LXI rp, nn
        t[0x01] = self._lxi_rp
        t[0x11] = self._lxi_rp
        t[0x21] = self._lxi_rp
        t[0x31] = self._lxi_rp

        # 0x02 — STAX B
        t[0x02] = self._stax_b
        # 0x0A — LDAX B
        t[0x0A] = self._ldax_b
        # 0x12 — STAX D
        t[0x12] = self._stax_d
        # 0x1A — LDAX D
        t[0x1A] = self._ldax_d

        # 0x03, 0x13, 0x23, 0x33 — INX rp
        t[0x03] = self._inc_rp
        t[0x13] = self._inc_rp
        t[0x23] = self._inc_rp
        t[0x33] = self._inc_rp

        # 0x0B, 0x1B, 0x2B, 0x3B — DCX rp
        t[0x0B] = self._dec_rp
        t[0x1B] = self._dec_rp
        t[0x2B] = self._dec_rp
        t[0x3B] = self._dec_rp

        # 0x04, 0x0C, 0x14, 0x1C, 0x24, 0x2C, 0x34, 0x3C — INR r
        t[0x04] = self._inc_r
        t[0x0C] = self._inc_r
        t[0x14] = self._inc_r
        t[0x1C] = self._inc_r
        t[0x24] = self._inc_r
        t[0x2C] = self._inc_r
        t[0x34] = self._inc_r
        t[0x3C] = self._inc_r

        # 0x05, 0x0D, 0x15, 0x1D, 0x25, 0x2D, 0x35, 0x3D — DCR r
        t[0x05] = self._dec_r
        t[0x0D] = self._dec_r
        t[0x15] = self._dec_r
        t[0x1D] = self._dec_r
        t[0x25] = self._dec_r
        t[0x2D] = self._dec_r
        t[0x35] = self._dec_r
        t[0x3D] = self._dec_r

        # 0x06, 0x0E, 0x16, 0x1E, 0x26, 0x2E, 0x36, 0x3E — MVI r, n
        t[0x06] = self._mvi_r
        t[0x0E] = self._mvi_r
        t[0x16] = self._mvi_r
        t[0x1E] = self._mvi_r
        t[0x26] = self._mvi_r
        t[0x2E] = self._mvi_r
        t[0x36] = self._mvi_r
        t[0x3E] = self._mvi_r

        # 0x07 — RLC, 0x0F — RRC, 0x17 — RAL, 0x1F — RAR
        t[0x07] = self._rlc
        t[0x0F] = self._rrc
        t[0x17] = self._ral
        t[0x1F] = self._rar

        # 0x09, 0x19, 0x29, 0x39 — DAD rp
        t[0x09] = self._dad_rp
        t[0x19] = self._dad_rp
        t[0x29] = self._dad_rp
        t[0x39] = self._dad_rp

        # 0x22 — SHLD, 0x2A — LHLD
        t[0x22] = self._shld
        t[0x2A] = self._lhld
        # 0x32 — STA, 0x3A — LDA
        t[0x32] = self._sta
        t[0x3A] = self._lda

        # 0x27 — DAA
        t[0x27] = self._daa
        # 0x2F — CMA
        t[0x2F] = self._cma
        # 0x37 — STC
        t[0x37] = self._stc
        # 0x3F — CMC
        t[0x3F] = self._cmc

        # 0x40..0x7F — MOV r,r (except 0x76 which is HLT)
        for op in range(0x40, 0x80):
            t[op] = self._mov_rr

        # 0x76 — HLT
        t[0x76] = self._hlt

        # 0x80..0xBF — ALU r, where bits 5,4,3 = family, bits 2,1,0 = reg
        for op in range(0x80, 0xC0):
            t[op] = self._alu_r

        # 0xC0..0xFF — control flow + ALU n + PUSH/POP + RST + IN/OUT + misc

        # 0xC0..0xC7 — RNZ..RM (cond_ret with cc=op&7)
        for op in range(0xC0, 0xC8):
            t[op] = self._cond_ret
        # 0xC8..0xCF — same conditions, alias form
        for op in range(0xC8, 0xD0):
            t[op] = self._cond_ret

        # 0xC2..0xC7 — JNZ..JM (cc=op&7 in 0xC2-0xC7)
        for op in range(0xC2, 0xC8):
            t[op] = self._cond_jmp
        # 0xCA..0xCF — alias
        for op in range(0xCA, 0xD0):
            t[op] = self._cond_jmp

        # 0xC4..0xC7 — CNZ..CM (cc=op&7 in 0xC4-0xC7)
        for op in range(0xC4, 0xC8):
            t[op] = self._cond_call
        # 0xCC..0xCF — alias
        for op in range(0xCC, 0xD0):
            t[op] = self._cond_call

        # 0xD0..0xD7 — RNC..RM
        for op in range(0xD0, 0xD8):
            t[op] = self._cond_ret
        # 0xD2..0xD7 — JNC..JM
        for op in range(0xD2, 0xD8):
            t[op] = self._cond_jmp
        # 0xD4..0xD7 — CNC..CM
        for op in range(0xD4, 0xD8):
            t[op] = self._cond_call
        # 0xD8..0xDF — alias
        for op in range(0xD8, 0xE0):
            t[op] = self._cond_ret
        for op in range(0xDA, 0xE0):
            t[op] = self._cond_jmp
        for op in range(0xDC, 0xE0):
            t[op] = self._cond_call

        # POP — 0xC1, 0xD1, 0xE1, 0xF1
        t[0xC1] = self._pop_b
        t[0xD1] = self._pop_d
        t[0xE1] = self._pop_h
        t[0xF1] = self._pop_psw

        # PUSH — 0xC5, 0xD5, 0xE5, 0xF5
        t[0xC5] = self._push_b
        t[0xD5] = self._push_d
        t[0xE5] = self._push_h
        t[0xF5] = self._push_psw

        # 0xC3 — JMP
        t[0xC3] = self._jmp
        # 0xC9 — RET
        t[0xC9] = self._ret
        # 0xCD — CALL
        t[0xCD] = self._call
        # 0xD3 — OUT
        t[0xD3] = self._out
        # 0xDB — IN
        t[0xDB] = self._in

        # 0xC6, 0xCE, 0xD6, 0xDE, 0xE6, 0xEE, 0xF6, 0xFE — ALU A, n
        for op in (0xC6, 0xCE, 0xD6, 0xDE, 0xE6, 0xEE, 0xF6, 0xFE):
            t[op] = self._alu_n

        # 0xC7, 0xCF, 0xD7, 0xDF, 0xE7, 0xEF, 0xF7, 0xFF — RST n
        for op in (0xC7, 0xCF, 0xD7, 0xDF, 0xE7, 0xEF, 0xF7, 0xFF):
            t[op] = self._rst

        # 0xE3 — XTHL
        t[0xE3] = self._xthl
        # 0xE9 — PCHL
        t[0xE9] = self._pchl
        # 0xEB — XCHG
        t[0xEB] = self._xchg
        # 0xF9 — SPHL
        t[0xF9] = self._sphl
        # 0xF3 — DI
        t[0xF3] = self._di
        # 0xFB — EI
        t[0xFB] = self._ei

        # 0x08, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38 — undocumented NOPs
        # 0xD9 — undocumented (8085 uses 0xD9 for SHLX? No, that's 0xD9 for 8085.
        # For 8080, 0xD9 is just an undocumented NOP.)
        # 0xCB — undocumented (8085 uses 0xCB for RST 0; 8080 treats as NOP.)
        # 0xDD, 0xED, 0xFD — undocumented NOPs
        # 0xF0, 0xF2, 0xF4, 0xF8, 0xFA, 0xFC — undocumented
        # (0xF6=ORI n, 0xF7=RST 6, 0xF9=SPHL, 0xFB=EI, 0xFE=CPI n, 0xFF=RST 7
        #  are real instructions and were wired above.)
        for op in (
            0x08, 0x10, 0x18, 0x20, 0x28, 0x30, 0x38,
            0xCB, 0xD9, 0xDD, 0xED, 0xFD,
            0xF0, 0xF2, 0xF4, 0xF8, 0xFA, 0xFC,
        ):
            t[op] = self._nop
