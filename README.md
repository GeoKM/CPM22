# IMSAI 8080 CP/M 2.2 Emulator

An authentic Python+TK emulation of the **IMSAI 8080** microcomputer running **CP/M 2.2**, with:

- **Intel 8080 CPU** at 2 MHz (period-correct for the IMSAI)
- **64KB RAM** with the standard CP/M memory map
- **Two 8" floppy drives** (A: and B:), supporting both **SSSD 256KB** and **SSDD 512KB** formats
- **Serial console** via 8251 USART
- **Bootable CP/M 2.2** — boots the real Digital Research CCP + BDOS, cross-assembled from the original source
- **Period-correct Tk front panel** — the iconic blue IMSAI faceplate with toggle switches and LED array
- **CRT terminal** with amber phosphor and scanline overlay

See [`PLAN.md`](./PLAN.md) for the full design and milestone breakdown.

## Status

🚧 **Under active development** — see [PLAN.md](./PLAN.md) for current milestone.

| Milestone | Description | Status |
|---|---|---|
| M0 | Repo skeleton, GitHub, CI | ✅ |
| M1 | 8080 CPU core | ⏳ |
| M2 | Memory, floppy, USART, boot | ⏳ |
| M3 | Cross-assemble OS2CCP+OS3BDOS, port OS4BIOS | ⏳ |
| M4 | Build bootable disk images | ⏳ |
| M5 | Front panel + CRT GUI | ⏳ |
| M6 | Polish & docs | ⏳ |

## Quick start (once M3 ships)

```bash
# Headless boot (CI-friendly)
python -m cpm22 --headless --boot disk_images/cpm22-sssd.img

# Interactive GUI
python -m cpm22

# Tests
pytest tests/
```

## Source material

The original Digital Research CP/M 2.2 source is in `CPM2.2SRC/`:

- `OS1BOOT.ASM` — cold-boot loader
- `OS2CCP.ASM` — Console Command Processor
- `OS3BDOS.ASM` — Basic Disk Operating System
- `OS4BIOS.ASM` — reference BIOS (MDS-800) — we port the structure for the IMSAI
- `PIP.LIN`, `STAT.LIN`, `ED.LIN`, `SUBMIT.LIN`, `DDT*.ASM`, `SYSGEN.ASM`, `MOVCPM.ASM`, `CPMOVE.ASM` — system utilities

## Architecture

```
cpm22/
├── cpu8080.py     # Intel 8080 CPU core (244 opcodes, dict-of-handlers dispatch)
├── memory.py      # 64KB flat RAM
├── floppy.py      # 8" SSSD/SSDD floppy image (IBM sector skew)
├── serial.py      # 8251 USART (one port, CP/M only uses one)
├── bios.py        # 17-entry BIOS jump vector, port-mapped
├── cpm_bdos.py    # BDOS entry (Python trampoline)
├── cpm_ccp.py     # CCP entry (Python trampoline)
├── asm8080.py     # 8080 cross-assembler (assembles OS2/OS3/OS4)
├── diskbuild.py   # Build bootable 8" disk images with system files
├── boot.py        # Cold-boot loader (read tracks 0-1, jump to CCP)
├── headless.py    # CLI entry for non-GUI boot
└── gui/
    ├── app.py     # Tk app, threading, drive bay
    ├── panel.py   # Front panel (toggle switches, LEDs)
    └── crt.py     # CRT terminal (Canvas, scanlines)

tests/
├── test_cpu8080.py    # 50+ instruction tests vs known-good reference
├── test_floppy.py     # SSSD/SSDD round-trip
├── test_asm8080.py    # Cross-assembler tests
├── test_bios.py       # End-to-end boot to A> prompt
└── test_diskbuild.py  # Build image, boot, verify with STAT
```

## References

- [IMSAI 8080 — Wikipedia](https://en.wikipedia.org/wiki/IMSAI_8080)
- Intel 8080 Assembly Language Programming Manual
- Zilog Z80 User Manual (for cross-reference; 8080 is a strict subset)
- Digital Research CP/M 2.2 Interface Guide
- "Vintage CPU Emulation" skill (`~/.hermes/skills/software-development/vintage-cpu-emulation`)

## License

MIT for the emulator code. CP/M 2.2 source is © Digital Research, used here under their original 1980s-era license terms for non-commercial research/educational use.
