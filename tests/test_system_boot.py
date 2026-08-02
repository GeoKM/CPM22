"""M2 system-level smoke test — boot the pre-built CP/M 2.2 image and
verify the CCP prints its signon banner.

This test exercises the full wiring: CPU + memory + USART + BIOS port
map + the pre-built CP/M 2.2 system image. Per skill §3, the test
runs the CPU for a bounded number of cycles and breaks on the first
console output (the CCP's first CONOUT call).
"""

from __future__ import annotations

import signal
import time

import pytest

from cpm22.cpm_system import CPMSystem
from cpm22.cpm_bios import BOOT_ENTRY, VECTOR_BASE, SYSTEM_BASE

CPM_SYS = "disk_images/cpm22-sssd.img"
SIGNON_PREFIX = "CP/M"


# Watchdog: terminate the test after 30 seconds. macOS doesn't ship
# the `timeout` command (skill §4).
# the `timeout` command (skill §4).
class _Watchdog:
    def __init__(self, seconds: int = 30):
        self.seconds = seconds

    def __enter__(self):
        def _alrm(*_):
            raise SystemExit(1)
        signal.signal(signal.SIGALRM, _alrm)
        signal.alarm(self.seconds)
        return self

    def __exit__(self, *args):
        signal.signal(signal.SIGALRM, signal.SIG_DFL)


def test_boot_prints_cpm_banner():
    """Boot CP/M, run until the banner appears, verify it's the signon message."""
    with _Watchdog(30):
        sys = CPMSystem(CPM_SYS)
        sys.cold_boot()
        # Run for up to 2,000,000 cycles looking for the banner.
        # CP/M 2.2 prints "CP/M VERS 2.2" or similar on cold boot.
        deadline = time.monotonic() + 10.0
        banner = b""
        for _ in range(2_000_000):
            sys.cpu.step()
            out = sys.drain_console()
            if out:
                banner += out
                if SIGNON_PREFIX.encode() in banner:
                    break
            if time.monotonic() > deadline:
                break
        # We may not get the banner if the BDOS trampoline at 0x0005 is wrong.
        # For now, just assert SOMETHING came out (the CCP at minimum prints a
        # signon message and then a prompt).
        # If nothing came out, print diagnostic info.
        if b"CP/M" not in banner and b"cp/m" not in banner.lower():
            print(f"\n[DIAG] banner so far: {banner!r}")
            print(f"[DIAG] PC=0x{sys.cpu.PC:04x}, SP=0x{sys.cpu.SP:04x}")
            # Look at bytes 0..0x100 of memory
            print("[DIAG] mem 0x0000..0x0020:", " ".join(f"{b:02x}" for b in (sys.mem.rb(i) for i in range(0x20))))
            # The BDOS trampoline at 0x0005 should be a JP to the BDOS
            bdos_tramp = bytes(sys.mem.rb(0x05 + i) for i in range(3))
            print(f"[DIAG] mem[0x0005..0x0007] = {bdos_tramp.hex()}")
            # Check 0xDC00 (CCP)
            ccp0 = bytes(sys.mem.rb(0xDC00 + i) for i in range(16))
            print(f"[DIAG] mem[0xDC00..0xDC0F] = {ccp0.hex()}")
        assert b"CP/M" in banner or b"cp/m" in banner.lower(), (
            f"expected CP/M banner, got {banner!r}"
        )


def test_system_does_not_crash_on_init():
    """Just constructing and cold-booting the system shouldn't crash."""
    sys = CPMSystem(CPM_SYS)
    sys.cold_boot()
    assert sys.cpu.PC == BOOT_ENTRY  # CCP entry
    assert sys.cpu.SP == 0xFFFF


def test_bios_vector_table_at_correct_address():
    """The 17 BIOS JPs at VECTOR_BASE should each point to a stub at 0xDC00+i*5."""
    sys = CPMSystem(CPM_SYS)
    for i in range(17):
        off = VECTOR_BASE + i * 3
        b0 = sys.mem.rb(off)
        b1 = sys.mem.rb(off + 1)
        b2 = sys.mem.rb(off + 2)
        assert b0 == 0xC3, f"vector[{i}] not JP, got 0x{b0:02x}"
        target = b1 | (b2 << 8)
        expected = 0xDC00 + i * 5
        assert target == expected, (
            f"vector[{i}] points to 0x{target:04x}, expected 0x{expected:04x}"
        )


def test_bios_stub_format():
    """Each stub is MVI A, fn; OUT 0xF0; RET."""
    sys = CPMSystem(CPM_SYS)
    for i in range(17):
        off = 0xDC00 + i * 5
        # 0x3E nn (MVI A, nn)
        assert sys.mem.rb(off) == 0x3E, f"stub[{i}] not MVI A"
        assert sys.mem.rb(off + 2) == 0xD3, f"stub[{i}] not OUT"
        assert sys.mem.rb(off + 3) == 0xF0, f"stub[{i}] not port 0xF0"
        assert sys.mem.rb(off + 4) == 0xC9, f"stub[{i}] not RET"
        # fn is in byte off+1
        fn = sys.mem.rb(off + 1)
        assert fn == i, f"stub[{i}] has fn={fn}, expected {i}"


def test_bios_call_const_no_input():
    """BIOS.CONST with no input should return 0x00."""
    sys = CPMSystem(CPM_SYS)
    sys.cpu.A = 0
    sys.cpu.C = 0
    sys._bios_dispatch(sys.cpu, 0)  # no-op for OUT
    # _bios_dispatch reads A and dispatches. A=0 = FN_BOOT.
    # But we don't want to call boot. Let's call _bios_call directly.
    rv = sys._bios_call(0x02)  # FN_CONST
    assert rv == 0x00


def test_bios_call_const_with_input():
    """BIOS.CONST with input should return 0xFF."""
    sys = CPMSystem(CPM_SYS)
    sys.usart.rx_push(0x41)
    rv = sys._bios_call(0x02)  # FN_CONST
    assert rv == 0xFF


def test_bios_conout_writes_to_usart():
    """BIOS.CONOUT should put the byte in C into the USART tx buffer."""
    sys = CPMSystem(CPM_SYS)
    sys.cpu.C = 0x41  # 'A'
    sys._bios_call(0x04)  # FN_CONOUT
    assert sys.usart.has_output()
    assert sys.usart.read_output() == 0x41


def test_bios_seldsk_no_disk():
    """BIOS.SELDSK with no disk mounted should return 0xFFFF."""
    sys = CPMSystem(CPM_SYS)
    # C=1 → drive A. No floppy mounted, should return 0xFFFF
    sys.cpu.C = 1
    rv = sys._bios_call(0x09)
    assert rv == 0xFFFF


def test_bios_seldsk_drive_a_mounted():
    """BIOS.SELDSK for an SSSD image mounted in drive A should return 0."""
    sys = CPMSystem(CPM_SYS)
    # Create a blank SSSD image for drive A
    from cpm22.floppy import FloppyFormat
    sys.mount_blank(0, FloppyFormat.SSSD_8)
    sys.cpu.C = 1  # C=1 → drive A (0-based offset)
    rv = sys._bios_call(0x09)
    assert rv == 0
    sys.cpu.C = 2  # C=2 → drive B, not mounted
    rv = sys._bios_call(0x09)
    assert rv == 0xFFFF
