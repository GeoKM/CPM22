"""IMSAI 8080 CP/M 2.2 system integration — CPU + memory + I/O + boot.

This is the runtime: it owns the CPU, memory, the 8251 USART, two floppy
drives, the BIOS port handler, and the cold-boot sequence. The headless
CLI and the GUI both use this class.

CP/M 2.2 boot flow (per skill §1 and §9):
1. Cold boot: load CPM.SYS into memory at the pre-relocated base
   (XEROX 1800 image = 0xDC00), set PC=0xDC00, start the CPU.
2. CCP prints the signon banner, then issues BIOS.CONST in a loop
   waiting for keystrokes. The CCP's BDOS calls route to our Python
   handlers via the BDOS trampoline (0x0005 in the image).
3. Each BDOS call that needs BIOS work calls into the BIOS vector table
   at 0xF200, which points to XEROX BIOS code. We override the
   necessary vectors to point to OUT-trap stubs in our hand-encoded BIOS
   region (0xFA00+).
4. Our Python BIOS dispatch (BIOS_PORT 0xF0) handles the request, returns
   the result in A, and the CPU continues.

The OUT-trap mechanism is the same pattern as the BDOS trampoline from
skill §1: the 8080 executes `OUT 0xF0, A` and Python dispatches based
on the value of A.
"""

from __future__ import annotations

import signal
import time
from pathlib import Path
from typing import Optional

from cpm22.cpu8080 import CPU8080
from cpm22.floppy import FloppyImage
from cpm22.memory import Memory
from cpm22.serial import USART8251
from cpm22.cpm_bios import (
    BOOT_ENTRY,
    BIOS_PORT,
    FN_BOOT,
    FN_CONIN,
    FN_CONOUT,
    FN_CONST,
    FN_HOME,
    FN_LIST,
    FN_LISTST,
    FN_PUNCH,
    FN_READ,
    FN_READER,
    FN_SECTRAN,
    FN_SELDSK,
    FN_SETDMA,
    FN_SETSEC,
    FN_SETTRK,
    FN_WBOOT,
    FN_WRITE,
    SYSTEM_BASE,
    SYSTEM_SIZE,
    VECTOR_BASE,
    build_bios_stubs,
    load_cpm_sys_into,
)


class CPMSystem:
    """The IMSAI 8080 CP/M 2.2 emulator runtime.

    Owns the CPU, memory, USART, two floppy drives. The BIOS vector table
    at 0xF200 is overridden to point to OUT-trap stubs; the Python
    dispatch reads A and routes to the appropriate method.
    """

    def __init__(self, cpm_sys_path: str):
        self.mem = Memory()
        self.cpu = CPU8080(self.mem)
        self.usart = USART8251()
        self.drives: list[Optional[FloppyImage]] = [None, None]
        self.current_drive = 0
        # Per-BIOS-call state
        self._track = 0
        self._sector = 1
        self._dma = 0x0080
        # BDOS entry trampoline at 0x0005: JP 0xDC05 (into the pre-built image)
        # Already set when we load CPM.SYS. Verify after load.
        self._console_output_buffer: list[int] = []
        # Load the pre-built CP/M 2.2 system
        load_cpm_sys_into(self.mem, cpm_sys_path)
        # The CP/M 2.2 system image is pre-relocated to load at SYSTEM_BASE
        # (0xE200). Per CP/M 2.2 convention, the BDOS code starts at
        # SYSTEM_BASE + 0x06 = 0xE206. The trampoline at low-memory 0x0005
        # must be a JP to 0xE206 so the CCP (which lives at SYSTEM_BASE and
        # calls 0x0005) can reach the BDOS.
        bdos_addr = SYSTEM_BASE + 0x06
        self.mem.wb(0x0005, 0xC3)                        # JP
        self.mem.wb(0x0006, bdos_addr & 0xFF)           # low byte
        self.mem.wb(0x0007, (bdos_addr >> 8) & 0xFF)    # high byte
        # Wire the BIOS port handler
        self.cpu.out_port[BIOS_PORT] = self._bios_dispatch
        # Wire the 8251 USART to CPU ports
        self.usart.attach_to_cpu(self.cpu)
        # Override the BIOS vector table at 0xF800 to point to our stubs
        self._install_bios_vector_table()

    # ------------------------------------------------------------------
    # BIOS vector table — replace the XEROX 1800 vectors with our stubs
    # ------------------------------------------------------------------

    def _install_bios_vector_table(self) -> None:
        """Write 17 JP instructions at VECTOR_BASE, each pointing to a
        stub in our hand-encoded BIOS region.

        The CP/M 2.2 image's vector table at VECTOR_BASE (0xF800) has 17
        JPs to the XEROX BIOS code. We replace each one with a JP to the
        corresponding stub in our stub region.

        Stubs are 5 bytes each (MVI A, fn; OUT 0xF0; RET). We put them in
        unused memory at 0xDC00 (below the system image at 0xE200, so
        nothing else lives there).
        """
        stubs = build_bios_stubs()
        if len(stubs) != 17 * 5:
            raise RuntimeError(
                f"BIOS stubs size mismatch: {len(stubs)} != 85 (17 × 5)"
            )
        STUB_BASE = 0xDC00
        # Write the stubs at 0xDC00..0xDC54
        for i, b in enumerate(stubs):
            self.mem.wb(STUB_BASE + i, b)
        # Now write 17 JPs at VECTOR_BASE (0xF800), each pointing to the
        # corresponding stub.
        for i in range(17):
            stub_addr = STUB_BASE + i * 5
            vector_addr = VECTOR_BASE + i * 3
            self.mem.wb(vector_addr, 0xC3)  # JP
            self.mem.wb(vector_addr + 1, stub_addr & 0xFF)
            self.mem.wb(vector_addr + 2, (stub_addr >> 8) & 0xFF)

    # ------------------------------------------------------------------
    # Floppy drive management
    # ------------------------------------------------------------------

    def mount_drive(self, drive: int, path: str) -> None:
        if drive not in (0, 1):
            raise ValueError(f"drive {drive} out of range 0..1")
        self.drives[drive] = FloppyImage.from_file(path)

    def mount_blank(self, drive: int, fmt) -> None:
        if drive not in (0, 1):
            raise ValueError(f"drive {drive} out of range 0..1")
        from cpm22.floppy import FloppyFormat
        self.drives[drive] = FloppyImage.blank(
            fmt if isinstance(fmt, FloppyFormat) else FloppyFormat(fmt)
        )

    # ------------------------------------------------------------------
    # BIOS dispatch — port 0xF0 OUT
    # ------------------------------------------------------------------

    def _bios_dispatch(self, cpu, val: int) -> None:
        """Handle OUT 0xF0, A. The 8080's A register holds the function number."""
        fn = cpu.A
        ret = self._bios_call(fn)
        cpu.A = ret & 0xFF
        cpu.L = ret & 0xFF  # some BDOS calls return value in A or HL

    def _bios_call(self, fn: int) -> int:
        if fn == FN_BOOT:
            return self._bios_boot()
        if fn == FN_WBOOT:
            return self._bios_wboot()
        if fn == FN_CONST:
            return self._bios_const()
        if fn == FN_CONIN:
            return self._bios_conin()
        if fn == FN_CONOUT:
            return self._bios_conout()
        if fn == FN_LIST:
            return self._bios_list()
        if fn == FN_PUNCH:
            return self._bios_punch()
        if fn == FN_READER:
            return self._bios_reader()
        if fn == FN_HOME:
            return self._bios_home()
        if fn == FN_SELDSK:
            return self._bios_seldsk()
        if fn == FN_SETTRK:
            return self._bios_settrk()
        if fn == FN_SETSEC:
            return self._bios_setsec()
        if fn == FN_SETDMA:
            return self._bios_setdma()
        if fn == FN_READ:
            return self._bios_read()
        if fn == FN_WRITE:
            return self._bios_write()
        if fn == FN_LISTST:
            return self._bios_listst()
        if fn == FN_SECTRAN:
            return self._bios_sectran()
        # Unknown — return 0
        return 0

    # ------------------------------------------------------------------
    # 17 BIOS implementations
    # ------------------------------------------------------------------

    def _bios_boot(self) -> int:
        # Reload system tracks from drive A. Reuse the cold-boot path.
        return self._bios_wboot()

    def _bios_wboot(self) -> int:
        # Reload the pre-built system image from drive A and reset to CCP.
        # The host (cpm_system) loaded the image at SYSTEM_BASE; we just
        # need to set PC=BOOT_ENTRY and reset SP. Drive A is index 0.
        if self.drives[0] is None:
            return 0xFF  # no disk
        # Read tracks 0-1 into SYSTEM_BASE
        # (In our setup, SYSTEM_BASE is RAM-loaded at boot, not re-read.
        # For a real disk boot, we'd read the system tracks here. We stub.)
        self.cpu.PC = BOOT_ENTRY
        self.cpu.SP = 0xFFFF
        return 0

    def _bios_const(self) -> int:
        # Console status: 0xFF if char ready, 0x00 if not
        return 0xFF if self.usart.has_input() else 0x00

    def _bios_conin(self) -> int:
        # Console in: blocking read of one byte
        deadline = time.monotonic() + 30.0
        while not self.usart.has_input():
            if time.monotonic() > deadline:
                return 0x00  # timeout
            time.sleep(0.001)
        return self.usart._in_data(self.cpu)

    def _bios_conout(self) -> int:
        # Console out: write the char in C to the USART
        c = self.cpu.C & 0x7F  # strip high bit (CP/M convention)
        self.usart._out_data(self.cpu, c)
        self._console_output_buffer.append(c)
        return 0

    def _bios_list(self) -> int:
        # List device: stub (we don't simulate a printer)
        return 0

    def _bios_punch(self) -> int:
        # Punch: stub
        return 0

    def _bios_reader(self) -> int:
        # Reader: stub — return 0x1A (EOF / ^Z)
        return 0x1A

    def _bios_home(self) -> int:
        # Home: move to track 0
        self._track = 0
        return 0

    def _bios_seldsk(self) -> int:
        # Select disk. C = drive number (0=A, 1=B, ...).
        # We use BIOS convention: 0 = current drive (no-op), else set.
        drive = self.cpu.C
        if drive == 0:
            drive = self.current_drive
        else:
            drive -= 1
        if drive < 0 or drive > 1 or self.drives[drive] is None:
            return 0xFFFF  # no disk
        self.current_drive = drive
        return 0  # return a DPHB address; we don't use it, but spec is 0 for OK

    def _bios_settrk(self) -> int:
        self._track = self.cpu.C
        return 0

    def _bios_setsec(self) -> int:
        self._sector = self.cpu.C
        return 0

    def _bios_setdma(self) -> int:
        self._dma = self.cpu.BC()
        return 0

    def _bios_read(self) -> int:
        drive = self.drives[self.current_drive]
        if drive is None:
            return 0x01  # error
        try:
            drive.read_to_dma(self.mem, self._track, self._sector)
            return 0
        except Exception:
            return 0x01

    def _bios_write(self) -> int:
        drive = self.drives[self.current_drive]
        if drive is None:
            return 0x01
        try:
            drive.write_from_dma(self.mem, self._track, self._sector)
            return 0
        except Exception:
            return 0x01

    def _bios_listst(self) -> int:
        # List status: 0xFF if printer ready, 0 if not
        return 0

    def _bios_sectran(self) -> int:
        # SECTRAN: BC = sector, DE = skew table pointer
        # We ignore the table (always use the standard CP/M 2.2 skew)
        # and just return the translated sector.
        sector = self.cpu.C
        return FloppyImage.translate_sector(sector)

    # ------------------------------------------------------------------
    # Cold boot sequence
    # ------------------------------------------------------------------

    def cold_boot(self) -> None:
        """Set PC=CCP entry and SP=top of memory. Start the CPU."""
        self.cpu.PC = BOOT_ENTRY
        self.cpu.SP = 0xFFFF

    # ------------------------------------------------------------------
    # Console output capture (for tests / headless)
    # ------------------------------------------------------------------

    def drain_console(self) -> bytes:
        """Drain the buffered console output as bytes (for tests)."""
        out = bytes(self._console_output_buffer)
        self._console_output_buffer.clear()
        return out

    def get_console_text(self) -> str:
        """Drain and decode console output as ASCII (for tests)."""
        out = self.drain_console()
        return out.decode("ascii", errors="replace")
