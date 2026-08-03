"""Tests for the DR-syntax 8080 cross-assembler.

Each test assembles a small known-good program and asserts the output
bytes match the expected Intel 8080 encoding. These are the same
dispatch-table tests we used for the CPU core — they catch opcode bugs
early before they corrupt larger assemblies.
"""

import pytest

from cpm22.asm8080_d import assemble_file


def _assemble(src: str, org: int = 0x100):
    """Helper: write src to a temp file and assemble."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".asm", delete=False) as f:
        f.write(src)
        path = f.name
    return assemble_file(path, org_addr=org)


# ---------------------------------------------------------------------------
# Data movement
# ---------------------------------------------------------------------------


def test_mvi_a_imm():
    code, _ = _assemble("mvi a,5\n")
    assert code == bytes([0x3E, 0x05])


def test_mvi_b_imm():
    code, _ = _assemble("mvi b,10\n")
    assert code == bytes([0x06, 0x0A])


def test_mvi_h_imm():
    code, _ = _assemble("mvi h,0ffh\n")
    assert code == bytes([0x26, 0xFF])


def test_mvi_l_imm():
    code, _ = _assemble("mvi l,127\n")
    assert code == bytes([0x2E, 0x7F])


def test_mvi_m_imm():
    code, _ = _assemble("mvi m,0\n")
    assert code == bytes([0x36, 0x00])


def test_mov_a_b():
    code, _ = _assemble("mov a,b\n")
    assert code == bytes([0x78])


def test_mov_b_a():
    code, _ = _assemble("mov b,a\n")
    assert code == bytes([0x47])


def test_mov_l_h():
    code, _ = _assemble("mov l,h\n")
    # d=L=5 (bits 5,4,3), s=H=4 (bits 2,1,0): 0x40 | (5<<3) | 4 = 0x6C
    assert code == bytes([0x6C])


def test_mov_h_l():
    code, _ = _assemble("mov h,l\n")
    # d=H=4, s=L=5: 0x40 | (4<<3) | 5 = 0x65
    assert code == bytes([0x65])


def test_mov_m_a():
    code, _ = _assemble("mov m,a\n")
    assert code == bytes([0x77])


def test_lxi_b_imm16():
    code, _ = _assemble("lxi b,1234h\n")
    assert code == bytes([0x01, 0x34, 0x12])


def test_lxi_h_imm16():
    code, _ = _assemble("lxi h,0xABCD\n")
    assert code == bytes([0x21, 0xCD, 0xAB])


def test_lxi_sp_imm16():
    code, _ = _assemble("lxi sp,0FFFFh\n")
    assert code == bytes([0x31, 0xFF, 0xFF])


def test_stax_b():
    code, _ = _assemble("stax b\n")
    assert code == bytes([0x02])


def test_stax_d():
    code, _ = _assemble("stax d\n")
    assert code == bytes([0x12])


def test_ldax_b():
    code, _ = _assemble("ldax b\n")
    assert code == bytes([0x0A])


def test_ldax_d():
    code, _ = _assemble("ldax d\n")
    assert code == bytes([0x1A])


def test_shld_addr():
    code, _ = _assemble("shld 1234h\n")
    assert code == bytes([0x22, 0x34, 0x12])


def test_lhld_addr():
    code, _ = _assemble("lhld 8000h\n")
    assert code == bytes([0x2A, 0x00, 0x80])


def test_sta_addr():
    code, _ = _assemble("sta 1000h\n")
    assert code == bytes([0x32, 0x00, 0x10])


def test_lda_addr():
    code, _ = _assemble("lda 1000h\n")
    assert code == bytes([0x3A, 0x00, 0x10])


# ---------------------------------------------------------------------------
# Arithmetic
# ---------------------------------------------------------------------------


def test_add_b():
    code, _ = _assemble("add b\n")
    assert code == bytes([0x80])


def test_add_a():
    code, _ = _assemble("add a\n")
    assert code == bytes([0x87])


def test_adc_c():
    code, _ = _assemble("adc c\n")
    assert code == bytes([0x89])


def test_adi_3():
    code, _ = _assemble("adi 3\n")
    assert code == bytes([0xC6, 0x03])


def test_aci_imm():
    code, _ = _assemble("aci 7fh\n")
    assert code == bytes([0xCE, 0x7F])


def test_sub_d():
    code, _ = _assemble("sub d\n")
    assert code == bytes([0x92])


def test_sui_imm():
    code, _ = _assemble("sui 5\n")
    assert code == bytes([0xD6, 0x05])


def test_sbb_e():
    code, _ = _assemble("sbb e\n")
    assert code == bytes([0x9B])


def test_inr_a():
    code, _ = _assemble("inr a\n")
    assert code == bytes([0x3C])


def test_dcr_b():
    code, _ = _assemble("dcr b\n")
    assert code == bytes([0x05])


def test_inx_h():
    code, _ = _assemble("inx h\n")
    assert code == bytes([0x23])


def test_dcx_d():
    code, _ = _assemble("dcx d\n")
    assert code == bytes([0x1B])


def test_dad_b():
    code, _ = _assemble("dad b\n")
    assert code == bytes([0x09])


def test_dad_sp():
    code, _ = _assemble("dad sp\n")
    assert code == bytes([0x39])


def test_xra_a():
    """xra a clears A."""
    code, _ = _assemble("xra a\n")
    assert code == bytes([0xAF])


def test_ora_b():
    code, _ = _assemble("ora b\n")
    assert code == bytes([0xB0])


def test_cmp_c():
    code, _ = _assemble("cmp c\n")
    assert code == bytes([0xB9])


def test_cpi_imm():
    code, _ = _assemble("cpi 0dh\n")
    assert code == bytes([0xFE, 0x0D])


# ---------------------------------------------------------------------------
# Branches and control flow
# ---------------------------------------------------------------------------


def test_jmp_addr():
    code, _ = _assemble("jmp 100h\n")
    assert code == bytes([0xC3, 0x00, 0x01])


def test_jmp_label():
    src = "start: nop\njmp start\n"
    code, _ = _assemble(src)
    # Layout: 0x0100: nop; 0x0101: jmp start (start=0x0100)
    assert code == bytes([0x00, 0xC3, 0x00, 0x01])


def test_call_addr():
    code, _ = _assemble("call 1234h\n")
    assert code == bytes([0xCD, 0x34, 0x12])


def test_ret():
    code, _ = _assemble("ret\n")
    assert code == bytes([0xC9])


def test_jz_addr():
    code, _ = _assemble("jz 200h\n")
    assert code == bytes([0xCA, 0x00, 0x02])


def test_jnz_addr():
    code, _ = _assemble("jnz 300h\n")
    assert code == bytes([0xC2, 0x00, 0x03])


def test_jc_addr():
    code, _ = _assemble("jc 400h\n")
    assert code == bytes([0xDA, 0x00, 0x04])


def test_jnc_addr():
    code, _ = _assemble("jnc 500h\n")
    assert code == bytes([0xD2, 0x00, 0x05])


def test_jm_addr():
    code, _ = _assemble("jm 600h\n")
    assert code == bytes([0xFA, 0x00, 0x06])


def test_jp_addr():
    code, _ = _assemble("jp 700h\n")
    assert code == bytes([0xF2, 0x00, 0x07])


def test_cz_addr():
    code, _ = _assemble("cz 1000h\n")
    assert code == bytes([0xCC, 0x00, 0x10])


def test_cnz_addr():
    code, _ = _assemble("cnz 2000h\n")
    assert code == bytes([0xC4, 0x00, 0x20])


def test_rz():
    code, _ = _assemble("rz\n")
    assert code == bytes([0xC8])


def test_rnz():
    code, _ = _assemble("rnz\n")
    assert code == bytes([0xC0])


def test_rc():
    code, _ = _assemble("rc\n")
    assert code == bytes([0xD8])


def test_pchl():
    code, _ = _assemble("pchl\n")
    assert code == bytes([0xE9])


# ---------------------------------------------------------------------------
# Stack
# ---------------------------------------------------------------------------


def test_push_b():
    code, _ = _assemble("push b\n")
    assert code == bytes([0xC5])


def test_push_d():
    code, _ = _assemble("push d\n")
    assert code == bytes([0xD5])


def test_push_h():
    code, _ = _assemble("push h\n")
    assert code == bytes([0xE5])


def test_push_psw():
    """push a is push psw in Intel 8080 syntax."""
    code, _ = _assemble("push psw\n")
    assert code == bytes([0xF5])


def test_pop_b():
    code, _ = _assemble("pop b\n")
    assert code == bytes([0xC1])


def test_pop_d():
    code, _ = _assemble("pop d\n")
    assert code == bytes([0xD1])


def test_pop_h():
    code, _ = _assemble("pop h\n")
    assert code == bytes([0xE1])


def test_pop_psw():
    code, _ = _assemble("pop psw\n")
    assert code == bytes([0xF1])


def test_sphl():
    code, _ = _assemble("sphl\n")
    assert code == bytes([0xF9])


def test_xchg():
    code, _ = _assemble("xchg\n")
    assert code == bytes([0xEB])


# ---------------------------------------------------------------------------
# I/O and misc
# ---------------------------------------------------------------------------


def test_in_port():
    code, _ = _assemble("in 10h\n")
    assert code == bytes([0xDB, 0x10])


def test_out_port():
    code, _ = _assemble("out 11h\n")
    assert code == bytes([0xD3, 0x11])


def test_ei():
    code, _ = _assemble("ei\n")
    assert code == bytes([0xFB])


def test_di():
    code, _ = _assemble("di\n")
    assert code == bytes([0xF3])


def test_hlt():
    code, _ = _assemble("hlt\n")
    assert code == bytes([0x76])


def test_nop():
    code, _ = _assemble("nop\n")
    assert code == bytes([0x00])


def test_cma():
    code, _ = _assemble("cma\n")
    assert code == bytes([0x2F])


def test_stc():
    code, _ = _assemble("stc\n")
    assert code == bytes([0x37])


def test_cmc():
    code, _ = _assemble("cmc\n")
    assert code == bytes([0x3F])


def test_daa():
    code, _ = _assemble("daa\n")
    assert code == bytes([0x27])


def test_rst_0():
    code, _ = _assemble("rst 0\n")
    assert code == bytes([0xC7])


def test_rst_7():
    code, _ = _assemble("rst 7\n")
    assert code == bytes([0xFF])


# ---------------------------------------------------------------------------
# Logical / rotate
# ---------------------------------------------------------------------------


def test_ana_b():
    code, _ = _assemble("ana b\n")
    assert code == bytes([0xA0])


def test_ora_c():
    code, _ = _assemble("ora c\n")
    assert code == bytes([0xB1])


def test_ani_imm():
    code, _ = _assemble("ani 0fh\n")
    assert code == bytes([0xE6, 0x0F])


def test_ori_imm():
    code, _ = _assemble("ori 80h\n")
    assert code == bytes([0xF6, 0x80])


def test_xri_imm():
    code, _ = _assemble("xri 0ffh\n")
    assert code == bytes([0xEE, 0xFF])


def test_rlc():
    code, _ = _assemble("rlc\n")
    assert code == bytes([0x07])


def test_rrc():
    code, _ = _assemble("rrc\n")
    assert code == bytes([0x0F])


def test_ral():
    code, _ = _assemble("ral\n")
    assert code == bytes([0x17])


def test_rar():
    code, _ = _assemble("rar\n")
    assert code == bytes([0x1F])


# ---------------------------------------------------------------------------
# Directives
# ---------------------------------------------------------------------------


def test_org_changes_addr():
    src = "org 200h\nmvi a,1\n"
    code, base = _assemble(src, org=0)
    assert base == 0x200
    assert code == bytes([0x3E, 0x01])


def test_db_byte():
    src = "db 5\n"
    code, _ = _assemble(src)
    assert code == bytes([0x05])


def test_db_bytes():
    src = "db 1,2,3,4\n"
    code, _ = _assemble(src)
    assert code == bytes([0x01, 0x02, 0x03, 0x04])


def test_db_string():
    src = "db 'hello'\n"
    code, _ = _assemble(src)
    assert code == b"hello"


def test_dw_word():
    src = "dw 1234h\n"
    code, _ = _assemble(src)
    assert code == bytes([0x34, 0x12])


def test_ds_reserves_space():
    src = "ds 10\n"
    code, _ = _assemble(src)
    assert code == bytes(10)


def test_equ_label():
    src = "x equ 42\ndb x\n"
    code, _ = _assemble(src)
    assert code == bytes([0x2A])


def test_label_in_branch():
    """Forward references must work."""
    src = "jmp start\nstart: nop\n"
    code, _ = _assemble(src)
    # Layout: 0x0100: jmp (3 bytes); 0x0103: nop; jmp points to 0x0103
    assert code == bytes([0xC3, 0x03, 0x01, 0x00])


# ---------------------------------------------------------------------------
# DR-specific syntax
# ---------------------------------------------------------------------------


def test_bang_separator():
    """DR's ! separator splits one line into multiple instructions."""
    src = "mov e,a! mvi c,2! jmp bdos\nbdos: ret\n"
    code, _ = _assemble(src)
    # mov e,a (5F), mvi c,2 (0E 02), jmp bdos (C3 06 01 -> 0x0106), ret (C9)
    assert code == bytes([0x5F, 0x0E, 0x02, 0xC3, 0x06, 0x01, 0xC9])


def test_dollar_current_address():
    """$ should evaluate to current code address."""
    src = "org 100h\ndb $\n"
    code, _ = _assemble(src)
    assert code == bytes([0x00])  # $ at org 100h is 0x100, low byte 0x00


def test_dollar_plus_offset():
    """$+N should evaluate to current address + N."""
    src = "org 100h\ndb $+5\n"
    code, _ = _assemble(src)
    assert code == bytes([0x05])  # $ = 0x100, low byte = 0x00, +5 = 0x05


def test_hex_literal_lowercase_h():
    src = "mvi a,0abh\n"
    code, _ = _assemble(src)
    assert code == bytes([0x3E, 0xAB])


def test_hex_literal_uppercase_h():
    src = "mvi a,0ABH\n"
    code, _ = _assemble(src)
    assert code == bytes([0x3E, 0xAB])


def test_not_expression():
    """DR's 'not' is bitwise complement."""
    src = "x equ not 0\ndb x\n"
    code, _ = _assemble(src)
    # not 0 = 0xFFFF (per ASM manual), low byte = 0xFF
    assert code == bytes([0xFF])


def test_subtract_expression():
    src = "x equ 100h - 80h\ndb x\n"
    code, _ = _assemble(src)
    assert code == bytes([0x80])


def test_multiplication():
    src = "x equ 5*3\ndb x\n"
    code, _ = _assemble(src)
    assert code == bytes([0x0F])


def test_comment_stripped():
    src = "mvi a,5 ; this is a comment\n"
    code, _ = _assemble(src)
    assert code == bytes([0x3E, 0x05])


def test_title_ignored():
    """The title directive should not produce any bytes."""
    src = "title 'test'\nmvi a,5\n"
    code, _ = _assemble(src)
    assert code == bytes([0x3E, 0x05])


def test_if_endif_skips():
    """if 0 ... endif should skip the body."""
    src = "if 0\nmvi a,5\nendif\nmvi a,6\n"
    code, _ = _assemble(src)
    assert code == bytes([0x3E, 0x06])


def test_if_endif_includes():
    """if 1 ... endif should include the body."""
    src = "if 1\nmvi a,5\nendif\nmvi a,6\n"
    code, _ = _assemble(src)
    assert code == bytes([0x3E, 0x05, 0x3E, 0x06])


# ---------------------------------------------------------------------------
# The full test program from /tmp/test1.asm
# ---------------------------------------------------------------------------


def test_full_program():
    src = "org 100h\nstart: mvi a,5\nmvi b,10\nadi 3\nmov b,a\njmp start\n"
    code, base = _assemble(src, org=0)
    assert base == 0x100
    # 3E 05 06 0A C6 03 47 C3 00 01
    assert code == bytes([0x3E, 0x05, 0x06, 0x0A, 0xC6, 0x03, 0x47, 0xC3, 0x00, 0x01])
