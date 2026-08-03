"""IMSAI 8080 CP/M 2.2 system integration — CPU + memory + I/O + boot.

This is the runtime: it owns the CPU, memory, the 8251 USART, two floppy
drives, the BIOS port handler, and the cold-boot sequence. The headless
CLI and the GUI both use this class.

CP/M 2.2 boot flow (per skill §1 and §9):
1. Cold boot: load the pre-built CP/M 2.2 system image (CCP+BDOS) into
   memory at SYSTEM_BASE (0xE200), then overwrite the BDOS entry trampoline
   at 0x0005 to point to our stub BDOS at STUB_BDOS_BASE.
2. The stub BDOS is a hand-encoded 8080 program (see stub_bdos.py) that
   pushes registers, calls our Python handler via OUT BIOS_PORT, and
   pops registers. The Python handler is a single dispatch table that
   services both BDOS and BIOS calls.
3. BDOS function calls (C=function, DE=parameter) from CCP/user programs
   go through the stub to Python. Python may call back into BIOS for
   disk I/O, but the BDOS handles the high-level disk semantics
   (file lookup, FCB parsing, etc.) — that's M3 work.

The OUT-trap mechanism is the same pattern as the BDOS trampoline from
skill §1: the 8080 executes `OUT 0xF0, A` and Python dispatches based
on the value of A. We use the BIOS port for both BDOS and BIOS dispatch
(only one port is wired — the XEROX BDOS has its own complex dispatch
that we sidestep entirely with the stub).
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
from cpm22.stub_bdos import (
    STUB_BDOS_BASE,
    BDOS_PTERM,
    BDOS_CONIN,
    BDOS_CONOUT,
    BDOS_PRINT,
    BDOS_RBUF,
    BDOS_CONST,
    BDOS_GETVER,
    BDOS_RESET,
    BDOS_SELDSK,
    BDOS_OPEN,
    BDOS_CLOSE,
    BDOS_SFIRST,
    BDOS_SNEXT,
    BDOS_DELETE,
    BDOS_READ,
    BDOS_WRITE,
    BDOS_MAKE,
    BDOS_RENAME,
    BDOS_GETDRV,
    BDOS_DMAOFF,
    BDOS_SETVEC,
    build_stub_bdos,
    build_bios_stub as _build_bios_stub_8080,
)
from cpm22.minimal_ccp import (
    build_ccp as build_minimal_ccp,
    get_strings as get_ccp_strings,
    CCP_BASE as MINIMAL_CCP_BASE,
)

# Port for BDOS dispatch (separate from BIOS_PORT so Python can tell layers apart)
BDOS_PORT = 0xF0


class CPMSystem:
    """The IMSAI 8080 CP/M 2.2 emulator runtime.

    Owns the CPU, memory, USART, two floppy drives. The BIOS vector table
    at 0xF200 is overridden to point to OUT-trap stubs; the Python
    dispatch reads A and routes to the appropriate method.
    """

    def __init__(
        self,
        cpm_sys_path: str,
        rbuf_deadline: float = 5.0,
        use_dr_system: bool = False,
    ):
        """Create the CP/M system.

        Args:
            cpm_sys_path: path to the CPM.SYS file (currently unused since
                we install our own CCP/BDOS rather than loading the XEROX image).
            rbuf_deadline: seconds for BDOS_RBUF to wait for input before
                returning with no input. Default 5s (real CP/M waits indefinitely).
                Tests should set this to a small value (e.g. 0.5) to keep the
                test harness fast.
            use_dr_system: if True, load the authentic Digital Research CP/M 2.2
                binaries (cross-assembled from OS2CCP.ASM, OS3BDOS.ASM, OS4BIOS.ASM)
                instead of the stub BDOS + minimal CCP. This is the real deal.
        """
        self.rbuf_deadline = rbuf_deadline
        self.use_dr_system = use_dr_system
        self.mem = Memory()
        self.cpu = CPU8080(self.mem)
        self.usart = USART8251()
        self.drives: list[Optional[FloppyImage]] = [None, None]
        self.current_drive = 0
        # Per-BIOS-call state
        self._track = 0
        self._sector = 1
        self._dma = 0x0080
        # BDOS state
        self._console_output_buffer: list[int] = []
        # Choose between authentic DR CP/M 2.2 and stub BDOS + minimal CCP.
        if use_dr_system:
            self._load_dr_system()
        else:
            self._load_stub_system()
        # Wire the BDOS port handler (port 0xF0)
        self.cpu.out_port[BDOS_PORT] = self._bdos_dispatch
        # Wire the BIOS port handler (port 0xF1)
        self.cpu.out_port[BIOS_PORT] = self._bios_dispatch
        # Wire the 8251 USART to CPU ports
        self.usart.attach_to_cpu(self.cpu)

    def _load_stub_system(self):
        """Install the stub BDOS + minimal CCP at 0xE000/0xE100.

        This is the M2 baseline: a tiny CP/M that responds to a few CCP
        commands (DIR, TYPE, ERA) using the Python stub BDOS dispatch.
        """
        # Install the stub BDOS at 0xE000.
        stub = build_stub_bdos(BIOS_PORT=BDOS_PORT)
        for i, b in enumerate(stub):
            self.mem.wb(STUB_BDOS_BASE + i, b)
        # Install the minimal CCP at 0xE100
        ccp = build_minimal_ccp()
        for i, b in enumerate(ccp):
            self.mem.wb(MINIMAL_CCP_BASE + i, b)
        # Write the CCP strings
        for addr, s in get_ccp_strings().items():
            for i, b in enumerate(s):
                self.mem.wb(addr + i, b)
        # Patch the BDOS entry trampoline at 0x0005 to point to the stub BDOS
        self.mem.wb(0x0005, 0xC3)                          # JP
        self.mem.wb(0x0006, STUB_BDOS_BASE & 0xFF)          # low byte
        self.mem.wb(0x0007, (STUB_BDOS_BASE >> 8) & 0xFF)   # high byte
        # Patch the warm-boot entry at 0x0000 to jump to the CCP
        self.mem.wb(0x0000, 0xC3)                          # JP
        self.mem.wb(0x0001, MINIMAL_CCP_BASE & 0xFF)         # low byte
        self.mem.wb(0x0002, (MINIMAL_CCP_BASE >> 8) & 0xFF)  # high byte
        # Override the BIOS vector table at 0xF800 to point to our stubs
        self._install_bios_vector_table()

    def _load_dr_system(self):
        """Load the authentic Digital Research CP/M 2.2 binaries.

        Cross-assembles OS2CCP.ASM, OS3BDOS.ASM, OS4BIOS.ASM from the original
        DR source and places them at the correct memory addresses. The BIOS
        jump table at 0xF000 is patched to point at our Python-driven stubs
        (since the assembled BIOS code references real hardware we don't
        have — 8251 USART, 1771 floppy controller — but the stubs provide
        the same functional interface via port 0xF1).
        """
        from cpm22.dr_loader import build_dr_cpm_system
        from cpm22.boot_stub import build_boot_stub, BOOT_VECTORS
        sys = build_dr_cpm_system()
        # Load CCP at 0xE000
        for i, b in enumerate(sys["ccp_bytes"]):
            self.mem.wb(sys["ccp_load"] + i, b)
        # Load BDOS at 0xE800
        for i, b in enumerate(sys["bdos_bytes"]):
            self.mem.wb(sys["bdos_load"] + i, b)
        # Load BIOS at 0xF000 (jump table + DR code)
        for i, b in enumerate(sys["bios_bytes"]):
            self.mem.wb(sys["bios_load"] + i, b)
        # Load BIOS stub dispatchers at 0xC000
        for i, b in enumerate(sys["bios_stubs"]):
            self.mem.wb(sys["bios_stub_base"] + i, b)
        # Install the pre-boot ROM stub at 0x0100 (TPA — unused by CP/M).
        # When no disk is mounted, BDOS init crashes; this stub intercepts
        # the cold-boot vector and prompts the user to insert a disk.
        stub = build_boot_stub()
        for i, b in enumerate(stub):
            self.mem.wb(0x0100 + i, b)
        # Patch the boot vectors at 0x0000-0x0007
        for i, b in enumerate(BOOT_VECTORS):
            self.mem.wb(0x0000 + i, b)
        # Auto-mount the CP/M 2.2 system disk on drive A. This gives BDOS
        # something to read when CCP runs its init sequence. Without a disk,
        # BDOS init crashes (seeks to garbage because disk params are 0).
        from cpm22.floppy import FloppyImage
        from pathlib import Path
        disk_path = Path(__file__).parent.parent / "disk_images" / "CPM22_SSSD.img"
        if disk_path.exists():
            self.drives[0] = FloppyImage.from_file(str(disk_path))
            print(f"Auto-mounted {disk_path.name} on drive A")

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

    # ------------------------------------------------------------------
    # BDOS dispatch — port 0xF0 OUT
    # ------------------------------------------------------------------

    def _bdos_dispatch(self, cpu, val: int) -> None:
        """Handle OUT 0xF0, A. The 8080's A register holds the BDOS function number.

        The stub BDOS pushed BC, DE, HL before the OUT. After this handler
        returns, the stub pops HL, DE, BC. So our handler can read DE/BC for
        the function parameter (standard CP/M 2.2 calling convention:
        C = function, DE = parameter).
        """
        fn = cpu.A
        # Snapshot the parameter. The BDOS uses DE as the parameter pointer.
        de = cpu.DE()
        bc = cpu.BC()
        ret = self._bdos_call(fn, de, bc)
        # Return value in A (standard CP/M 2.2 convention)
        cpu.A = ret & 0xFF
        # If the function is CONIN, the return value is the char
        # If the function is GETDRV, the return value is the current drive (0=A, 1=B)
        # If the function is GETVER, H = version major, L = version minor (CP/M 2.2)
        # We return all results in A. The 8080 calling convention reads the
        # relevant register after the CALL returns.

    def _bdos_call(self, fn: int, de: int, bc: int) -> int:
        if fn == BDOS_PTERM:
            return self._bdos_pterm(de, bc)
        if fn == BDOS_CONIN:
            return self._bdos_conin()
        if fn == BDOS_CONOUT:
            return self._bdos_conout(de, bc)
        if fn == BDOS_PRINT:
            return self._bdos_print(de, bc)
        if fn == BDOS_RBUF:
            return self._bdos_rbuf(de, bc)
        if fn == BDOS_CONST:
            return self._bdos_const()
        if fn == BDOS_GETVER:
            return self._bdos_getver()
        if fn == BDOS_RESET:
            return self._bdos_reset(de, bc)
        if fn == BDOS_SELDSK:
            return self._bdos_seldsk(de, bc)
        if fn == BDOS_OPEN:
            return self._bdos_open(de, bc)
        if fn == BDOS_CLOSE:
            return self._bdos_close(de, bc)
        if fn == BDOS_SFIRST:
            return self._bdos_sfirst(de, bc)
        if fn == BDOS_SNEXT:
            return self._bdos_snext(de, bc)
        if fn == BDOS_DELETE:
            return self._bdos_delete(de, bc)
        if fn == BDOS_READ:
            return self._bdos_read(de, bc)
        if fn == BDOS_WRITE:
            return self._bdos_write(de, bc)
        if fn == BDOS_MAKE:
            return self._bdos_make(de, bc)
        if fn == BDOS_RENAME:
            return self._bdos_rename(de, bc)
        if fn == BDOS_GETDRV:
            return self._bdos_getdrv(de, bc)
        if fn == BDOS_DMAOFF:
            return self._bdos_dmaoff(de, bc)
        if fn == BDOS_SETVEC:
            return self._bdos_setvec(de, bc)
        # Unknown function — return 0
        return 0

    # ------------------------------------------------------------------
    # BDOS handler implementations
    # ------------------------------------------------------------------

    def _bdos_pterm(self, de: int, bc: int) -> int:
        """PTERM (BDOS 0): system reset.

        In standard CP/M, PTERM is called on program exit. The XEROX 1800
        CCP also calls PTERM as part of its command-loop initialization.
        We treat PTERM as a no-op (return 0) — the CCP continues execution
        after the CALL 5. This matches the Digital Research convention
        where PTERM is a clean exit; a true warm-boot (re-read the system
        tracks from disk) would be done by jumping to the BIOS wboot
        entry directly via the system image's JP, not via PTERM.
        """
        return 0

    def _bdos_conin(self) -> int:
        """CONIN (BDOS 1): read a char from the console (with echo)."""
        # Blocking read of one byte
        deadline = time.monotonic() + self.rbuf_deadline
        while not self.usart.has_input():
            if time.monotonic() > deadline:
                return 0
            time.sleep(0.001)
        b = self.usart._in_data(self.cpu)
        # Echo
        self.usart._out_data(self.cpu, b)
        return b

    def _bdos_conout(self, de: int, bc: int) -> int:
        """CONOUT (BDOS 2): write char in E to the console."""
        c = de & 0x7F
        self.usart._out_data(self.cpu, c)
        self._console_output_buffer.append(c)
        return 0

    def _bdos_print(self, de: int, bc: int) -> int:
        """PRINT (BDOS 9): write string at DE until '$' terminator."""
        addr = de
        while True:
            c = self.mem.rb(addr)
            if c == ord('$'):
                break
            self.usart._out_data(self.cpu, c & 0x7F)
            self._console_output_buffer.append(c & 0x7F)
            addr = (addr + 1) & 0xFFFF
        return 0

    def _bdos_rbuf(self, de: int, bc: int) -> int:
        """RBUF (BDOS 10): read a line from console into buffer at DE.

        Buffer format (per CP/M 2.2):
            DE+0: max chars (1-255, set by caller)
            DE+1: actual chars read (filled by BDOS)
            DE+2..(DE+1+actual): the chars
        Terminator: CR (0x0D) or LF (0x0A). Char count does NOT include terminator.
        Backspace (0x08) deletes the previous char.
        """
        buf_addr = de
        max_chars = self.mem.rb(buf_addr) or 1  # at least 1
        # Clear the buffer first (so empty lines don't have stale data)
        for i in range(1, max_chars + 2):
            self.mem.wb(buf_addr + i, 0)
        chars_read = 0
        deadline = time.monotonic() + self.rbuf_deadline
        while chars_read < max_chars:
            # Read one char (blocking)
            while not self.usart.has_input():
                if time.monotonic() > deadline:
                    self.mem.wb(buf_addr + 1, chars_read)
                    return 0
                time.sleep(0.001)
            c = self.usart._in_data(self.cpu)
            if c in (0x0D, 0x0A):
                # Line terminator — echo CR+LF
                self.usart._out_data(self.cpu, 0x0D)
                self.usart._out_data(self.cpu, 0x0A)
                break
            if c == 0x08:  # backspace
                if chars_read > 0:
                    chars_read -= 1
                    self.mem.wb(buf_addr + 2 + chars_read, 0)
                    # Echo backspace
                    self.usart._out_data(self.cpu, 0x08)
                    self.usart._out_data(self.cpu, ord(' '))
                    self.usart._out_data(self.cpu, 0x08)
                continue
            # Echo the char
            self.usart._out_data(self.cpu, c)
            self.mem.wb(buf_addr + 2 + chars_read, c & 0x7F)
            chars_read += 1
        self.mem.wb(buf_addr + 1, chars_read)
        return 0

    def _bdos_const(self) -> int:
        """CONST (BDOS 11): console status — 0xFF if char ready, 0 if not."""
        return 0xFF if self.usart.has_input() else 0x00

    def _bdos_getver(self) -> int:
        """GETVER (BDOS 12): return CP/M version number. 0x22 for 2.2."""
        return 0x22

    def _bdos_reset(self, de: int, bc: int) -> int:
        """RESET (BDOS 13): reset drives, jump to wboot.

        Like PTERM, the XEROX CCP calls RESET as part of its loop. We
        make it a no-op for now — the system continues from where it was.
        A true reset (reload from disk) is done by jumping to the BIOS
        wboot entry directly.
        """
        return 0

    def _bdos_seldsk(self, de: int, bc: int) -> int:
        """SELDSK (BDOS 14): select disk. E = drive (0=A, 1=B, ...)."""
        drive = de & 0xFF
        if drive < 0 or drive > 1 or self.drives[drive] is None:
            return 0
        self.current_drive = drive
        return 0  # Return DPHB address (we don't use it; 0 = OK)

    def _bdos_open(self, de: int, bc: int) -> int:
        """OPEN (BDOS 15): open file. DE = FCB address.

        Returns directory code (0-3) on success, 0xFF on failure.
        Stub: not yet implemented in M2 (this is M4 work).
        """
        return 0xFF  # not implemented

    def _bdos_close(self, de: int, bc: int) -> int:
        """CLOSE (BDOS 16): close file. Returns 0 on success, 0xFF on failure."""
        return 0xFF  # not implemented

    def _bdos_sfirst(self, de: int, bc: int) -> int:
        """SFIRST (BDOS 17): search for first. Returns 0-3 on success, 0xFF on failure."""
        return 0xFF  # not implemented

    def _bdos_snext(self, de: int, bc: int) -> int:
        """SNEXT (BDOS 18): search for next. Returns 0-3 on success, 0xFF on failure."""
        return 0xFF  # not implemented

    def _bdos_delete(self, de: int, bc: int) -> int:
        """DELETE (BDOS 19): delete file. Returns 0 on success, 0xFF on failure."""
        return 0xFF  # not implemented

    def _bdos_read(self, de: int, bc: int) -> int:
        """READ (BDOS 20): read next record. DE = FCB. Returns 0=OK, 1=EOF, 0xFF=error."""
        return 0xFF  # not implemented

    def _bdos_write(self, de: int, bc: int) -> int:
        """WRITE (BDOS 21): write next record. Returns 0=OK, 1=DIR full, 0xFF=error."""
        return 0xFF  # not implemented

    def _bdos_make(self, de: int, bc: int) -> int:
        """MAKE (BDOS 22): create file. DE = FCB. Returns 0-3 on success, 0xFF on failure."""
        return 0xFF  # not implemented

    def _bdos_rename(self, de: int, bc: int) -> int:
        """RENAME (BDOS 23): rename file. Returns 0 on success, 0xFF on failure."""
        return 0xFF  # not implemented

    def _bdos_getdrv(self, de: int, bc: int) -> int:
        """GETDRV (BDOS 25): get current drive (0=A, 1=B, ...)."""
        return self.current_drive

    def _bdos_dmaoff(self, de: int, bc: int) -> int:
        """DMAOFF (BDOS 26): set DMA address at DE."""
        self._dma = de
        return 0

    def _bdos_setvec(self, de: int, bc: int) -> int:
        """SETVEC (BDOS 30): set exception vector. Stub."""
        return 0

    # ------------------------------------------------------------------
    # BIOS dispatch — port 0xF1 OUT
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
        self.cpu.PC = MINIMAL_CCP_BASE
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
