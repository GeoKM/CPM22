"""Memory abstraction for the IMSAI 8080 CP/M 2.2 emulator.

64KB flat address space, bytearray-backed. Provides byte and word read/write
with 8080's little-endian word layout.
"""


class Memory:
    def __init__(self, size: int = 0x10000):
        if size > 0x10000:
            raise ValueError(f"8080 address space is 16 bits (max 0x10000); got 0x{size:x}")
        self.ram = bytearray(size)
        self.size = size

    def rb(self, addr: int) -> int:
        return self.ram[addr & 0xFFFF]

    def wb(self, addr: int, val: int) -> None:
        self.ram[addr & 0xFFFF] = val & 0xFF

    def rw(self, addr: int) -> int:
        """Read little-endian word at addr."""
        lo = self.ram[addr & 0xFFFF]
        hi = self.ram[(addr + 1) & 0xFFFF]
        return (hi << 8) | lo

    def ww(self, addr: int, val: int) -> None:
        """Write little-endian word at addr."""
        self.ram[addr & 0xFFFF] = val & 0xFF
        self.ram[(addr + 1) & 0xFFFF] = (val >> 8) & 0xFF

    def load(self, addr: int, data: bytes | bytearray) -> None:
        for i, b in enumerate(data):
            self.ram[(addr + i) & 0xFFFF] = b
