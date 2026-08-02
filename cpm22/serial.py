"""8251 USART — IMSAI SIO-2 serial chip emulation.

CP/M 2.2 only uses one serial port (port 0), per skill §9. The 8251 has
two addresses per port: data register and control/status register.

In our emulator, we map the 8251 to ports 0x10 (data) and 0x11 (control).
The CPU's IN/OUT instructions on these ports call into the handlers in
this class.

CP/M 2.2 calls BIOS.CONST (status) and BIOS.CONIN/CONOUT (read/write).
We expose:
- has_input() — for CPU thread to busy-wait on (skill §3)
- read() — pull a byte from the rx buffer
- write(b) — push a byte to the tx buffer
- tx_bytes_pending — for the UI thread to poll and display
- rx_push(b) — for the UI thread to push a keystroke into the rx buffer
"""

from __future__ import annotations

from collections import deque
from typing import Optional


class USART8251:
    """Single 8251 USART, mapped to data port 0x10 and control port 0x11.

    The 8251 has 4 control registers (mode, sync1, sync2, command) and
    a status register. We implement the minimum that CP/M 2.2 needs:
    mode register (8N1 async), command register (TX enable, RX enable),
    and status register (TX ready, RX ready).
    """

    DATA_PORT = 0x10
    CONTROL_PORT = 0x11

    # Status register bits (read from control port)
    STATUS_TX_READY = 0x01   # bit 0: transmitter ready for next byte
    STATUS_RX_READY = 0x02   # bit 1: receiver has a byte ready
    STATUS_TX_EMPTY = 0x04   # bit 2: transmitter shift register empty
    STATUS_PARITY_ERR = 0x08
    STATUS_OVERRUN_ERR = 0x10
    STATUS_FRAMING_ERR = 0x20

    # Command register bits (write to control port)
    CMD_TX_ENABLE = 0x01
    CMD_DTR = 0x02
    CMD_RX_ENABLE = 0x04
    CMD_BREAK = 0x08
    CMD_ER = 0x10  # error reset
    CMD_RTS = 0x20
    CMD_RESET = 0x40
    CMD_EH = 0x80  # enter hunt mode

    def __init__(self, console_out=None):
        # Buffer of bytes the host (UI) has written to the rx side
        self.rx_buf: deque[int] = deque()
        # Buffer of bytes the CPU has transmitted (UI polls this)
        self.tx_buf: deque[int] = deque()
        # Mode register: 8N1 async default
        self.mode = 0x4E  # 8 bits, no parity, 1 stop, 16x clock
        # Command register
        self.command = self.CMD_TX_ENABLE | self.CMD_RX_ENABLE | self.CMD_RTS
        # Status register
        self.status = self.STATUS_TX_READY | self.STATUS_TX_EMPTY
        # Optional console-out callback (used by the headless CLI to print to stdout)
        self.console_out = console_out

    def attach_to_cpu(self, cpu) -> None:
        """Wire this USART's IN/OUT handlers to the CPU."""
        cpu.in_port[self.DATA_PORT] = self._in_data
        cpu.out_port[self.DATA_PORT] = self._out_data
        cpu.in_port[self.CONTROL_PORT] = self._in_status
        cpu.out_port[self.CONTROL_PORT] = self._out_command

    # ------------------------------------------------------------------
    # CPU-facing I/O handlers
    # ------------------------------------------------------------------

    def _in_data(self, cpu) -> int:
        """Read a byte from the RX buffer (CPU reads from data port)."""
        if not self.rx_buf:
            # 8251 returns last byte on overrun. We just return 0 for "no data".
            return 0
        b = self.rx_buf.popleft()
        if not self.rx_buf:
            self.status &= ~self.STATUS_RX_READY
        return b

    def _out_data(self, cpu, val: int) -> None:
        """Write a byte to the TX buffer (CPU writes to data port)."""
        b = val & 0xFF
        self.tx_buf.append(b)
        if self.console_out is not None:
            self.console_out(b)
        # TX always ready after a write
        self.status |= self.STATUS_TX_READY | self.STATUS_TX_EMPTY

    def _in_status(self, cpu) -> int:
        """Read the status register (CPU reads from control port)."""
        return self.status

    def _out_command(self, cpu, val: int) -> None:
        """Write to the command register (CPU writes to control port)."""
        self.command = val & 0xFF
        if val & self.CMD_RESET:
            # Soft reset: clear command bits but keep TX ready
            self.command = self.CMD_TX_ENABLE | self.CMD_RX_ENABLE
        if not (val & self.CMD_TX_ENABLE):
            self.status &= ~self.STATUS_TX_READY
        if not (val & self.CMD_RX_ENABLE):
            self.status &= ~self.STATUS_RX_READY
            self.rx_buf.clear()

    # ------------------------------------------------------------------
    # Host-facing API (UI thread / test code)
    # ------------------------------------------------------------------

    def rx_push(self, b: int) -> None:
        """Push a byte into the RX buffer (UI thread, simulating keystroke)."""
        self.rx_buf.append(b & 0xFF)
        self.status |= self.STATUS_RX_READY

    def has_input(self) -> bool:
        return bool(self.rx_buf)

    def has_output(self) -> bool:
        return bool(self.tx_buf)

    def read_output(self) -> Optional[int]:
        """Drain one byte from the TX buffer (UI thread)."""
        if not self.tx_buf:
            return None
        return self.tx_buf.popleft()
