"""End-to-end boot tests — verify the CP/M 2.2 system actually boots,
prints its signon, reads input, and dispatches commands via the minimal
CCP + stub BDOS.

Strategy: pre-load the USART rx buffer BEFORE cold-boot. The CCP's
first BDOS_RBUF call picks up the queued input immediately, so the
tests don't hang on busy-wait timeouts.
"""

from __future__ import annotations

import signal
import time

import pytest

from cpm22.cpm_system import CPMSystem

CPM_SYS = "disk_images/cpm22-sssd.img"


class _Watchdog:
    """macOS-friendly watchdog via SIGALRM."""

    def __init__(self, seconds: int = 60):
        self.seconds = seconds

    def __enter__(self):
        def _alrm(*_):
            raise SystemExit(1)
        signal.signal(signal.SIGALRM, _alrm)
        signal.alarm(self.seconds)
        return self

    def __exit__(self, *args):
        signal.signal(signal.SIGALRM, signal.SIG_DFL)


def _make_system(rx_bytes: bytes = b"", rbuf_deadline: float = 0.3) -> CPMSystem:
    """Create a CPMSystem with optional pre-queued RX bytes."""
    sys = CPMSystem(CPM_SYS, rbuf_deadline=rbuf_deadline)
    for b in rx_bytes:
        sys.usart.rx_push(b)
    return sys


def _run_until(sys: CPMSystem, predicate, max_steps: int = 200_000) -> bool:
    """Run the CPU until predicate(buffer) is true or max_steps reached."""
    for _ in range(max_steps):
        sys.cpu.step()
        out = sys.drain_console()
        if out and predicate(bytes(out)):
            return True
    return False


def test_signon_banner_prints():
    """The minimal CCP should print 'CP/M 2.2 EMULATOR\\r\\n' on cold boot."""
    with _Watchdog(30):
        sys = _make_system()
        sys.cold_boot()
        # Run until signon appears
        ran = _run_until(sys, lambda buf: b"CP/M 2.2 EMULATOR" in buf, max_steps=50_000)
        assert ran, f"signon never printed; buffer={bytes(sys._console_output_buffer)!r}"


def test_prompt_prints_arrow():
    """After the signon, the CCP should print '>' (prompt)."""
    with _Watchdog(30):
        sys = _make_system()
        sys.cold_boot()
        ran = _run_until(sys, lambda buf: b">" in buf, max_steps=100_000)
        assert ran, f"prompt never appeared; buffer={bytes(sys._console_output_buffer)!r}"


def test_unknown_command_prints_question():
    """Typing 'X' should print '?\\r\\n'."""
    with _Watchdog(30):
        # Pre-queue X+CR — the CCP's first RBUF picks it up
        sys = _make_system(b"X\r", rbuf_deadline=0.1)
        sys.cold_boot()
        ran = _run_until(sys, lambda buf: b"?" in buf, max_steps=200_000)
        assert ran, f"'?' never appeared; buffer={bytes(sys._console_output_buffer)!r}"


def test_dir_command_dispatches_to_dir_handler():
    """Typing 'D' should trigger the DIR handler."""
    with _Watchdog(30):
        sys = _make_system(b"D\r", rbuf_deadline=0.1)
        sys.cold_boot()
        ran = _run_until(sys, lambda buf: b"DIR" in buf, max_steps=200_000)
        assert ran, f"DIR handler not triggered; buffer={bytes(sys._console_output_buffer)!r}"


def test_type_command_dispatches_to_type_handler():
    """Typing 'T' should trigger the TYPE handler."""
    with _Watchdog(30):
        sys = _make_system(b"T\r", rbuf_deadline=0.1)
        sys.cold_boot()
        ran = _run_until(sys, lambda buf: b"TYPE" in buf, max_steps=200_000)
        assert ran, f"TYPE handler not triggered; buffer={bytes(sys._console_output_buffer)!r}"


def test_era_command_dispatches_to_era_handler():
    """Typing 'E' should trigger the ERA handler."""
    with _Watchdog(30):
        sys = _make_system(b"E\r", rbuf_deadline=0.1)
        sys.cold_boot()
        ran = _run_until(sys, lambda buf: b"ERA" in buf, max_steps=200_000)
        assert ran, f"ERA handler not triggered; buffer={bytes(sys._console_output_buffer)!r}"


# ---------------------------------------------------------------------------
# Pure-BDOS unit tests (don't run the CPU)
# ---------------------------------------------------------------------------


def test_bdos_getver_returns_22():
    """BDOS_GETVER (12) should return 0x22 for CP/M 2.2."""
    sys = CPMSystem(CPM_SYS, rbuf_deadline=0.1)
    rv = sys._bdos_call(12, 0, 0)
    assert rv == 0x22


def test_bdos_const_no_input():
    """BDOS_CONST with no input should return 0."""
    sys = CPMSystem(CPM_SYS, rbuf_deadline=0.1)
    rv = sys._bdos_call(11, 0, 0)
    assert rv == 0x00


def test_bdos_const_with_input():
    """BDOS_CONST with input queued should return 0xFF."""
    sys = CPMSystem(CPM_SYS, rbuf_deadline=0.1)
    sys.usart.rx_push(0x41)
    rv = sys._bdos_call(11, 0, 0)
    assert rv == 0xFF


def test_bdos_pterm_is_noop():
    """BDOS_PTERM should return 0 (no-op)."""
    sys = CPMSystem(CPM_SYS, rbuf_deadline=0.1)
    pc_before = sys.cpu.PC
    rv = sys._bdos_call(0, 0, 0)
    assert rv == 0
    assert sys.cpu.PC == pc_before


def test_bdos_reset_is_noop():
    """BDOS_RESET should return 0 (no-op)."""
    sys = CPMSystem(CPM_SYS, rbuf_deadline=0.1)
    pc_before = sys.cpu.PC
    rv = sys._bdos_call(13, 0, 0)
    assert rv == 0
    assert sys.cpu.PC == pc_before


def test_bdos_print_writes_signon():
    """BDOS_PRINT with the signon string should put CP/M 2.2 in the buffer."""
    sys = CPMSystem(CPM_SYS, rbuf_deadline=0.1)
    # Write signon string into memory
    signon = b"CP/M 2.2 EMULATOR\r\n$"
    for i, b in enumerate(signon):
        sys.mem.wb(0x0100 + i, b)
    sys._bdos_call(9, 0x0100, 0)
    buf = bytes(sys._console_output_buffer)
    assert b"CP/M 2.2 EMULATOR" in buf


def test_bdos_conout_writes_char():
    """BDOS_CONOUT with E='A' should append 'A' to the buffer."""
    sys = CPMSystem(CPM_SYS, rbuf_deadline=0.1)
    sys._bdos_call(2, ord("A"), 0)
    buf = bytes(sys._console_output_buffer)
    assert b"A" in buf
