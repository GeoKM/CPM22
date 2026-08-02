# IMSAI 8080 CP/M 2.2 Emulator — Implementation Plan

**Target:** Authentic IMSAI 8080 emulation with CP/M 2.2 boot, 2× 8" floppy drives, serial console, and a period-correct Tk front-panel GUI.

**Repo:** `/Users/keith/src/CPM22` (this directory). Remote: `github.com/GeoKM14/CPM22` (created at end of M0).

**Reference material in tree:**
- `CPM2.2SRC/OS1BOOT.ASM` — cold-boot loader (read in M2)
- `CPM2.2SRC/OS2CCP.ASM` — Console Command Processor (ported in M3)
- `CPM2.2SRC/OS3BDOS.ASM` — Basic Disk Operating System (ported in M3)
- `CPM2.2SRC/OS4BIOS.ASM` — reference BIOS, MDS-800 hardware → rewrite for emulator (M3)
- `CPM2.2SRC/SYSGEN.ASM`, `DDT*.ASM`, `ED.LIN`, `PIP.LIN`, `STAT.LIN`, `SUBMIT.LIN` — system utilities to build into the boot disk image (M4)

**Skill:** `vintage-cpu-emulation` (v0.5.0). Section 1 (CPU core), 3 (block-on-input), 4 (headless tests), 5 (test fidelity), 6 (pitfalls), 7 (worked CP/M example), 8 (retro Tk GUI) all apply.

---

## Architectural decisions (locked in)

| Decision | Choice | Rationale |
|---|---|---|
| CPU core | **Intel 8080** (not Z80) | Period-correct IMSAI; 8080 is a strict subset of Z80 so the same Z80-built CP/M 2.2 runs unchanged. |
| Floppy format | **8" SSSD 256KB + 8" SSDD 512KB**, both supported | IMSAI shipped with SSSD 8" drives; later Cromemco/IMSAI upgrades used SSDD. Auto-detect by directory entry layout. |
| Memory model | **64KB flat, bytearray** | Standard CP/M 2.2. TPA = 0x0100–user-end. BIOS = top of 64KB (jump table at 0xFA00-ish). |
| Boot sector | **Track 0, sector 1** (128 bytes) loads CCP/BDOS | Match Digital Research's loader convention from OS1BOOT.ASM. |
| I/O port model | **OUT-trap BDOS**, **CALL-trap BIOS** | 8080 has no `OUT (n),A` indexed form (only direct port), so we'll use `OUT` with a fixed port per function — same pattern as the previous Z80 work but on 8080. |
| Serial port | **Single 8251 USART** mapped to ports 0x10/0x11 | IMSAI SIO-2 had 4 ports but CP/M only uses one. |
| Front panel | **Tk Canvas** with toggle-switch imagery | Section 8 of the skill (CRT + panel patterns). |
| Repo | **Git, push to GitHub as `GeoKM14/CPM22`** | Per the user. |

---

## Milestones

### M0 — Repo skeleton, GitHub, CI scaffolding *(this PR)*
- `git init`, `.gitignore` (`__pycache__`, `*.pyc`, `.DS_Store`, `disk_images/*.img` except checked-in samples)
- Create GitHub repo `GeoKM14/CPM22` via `gh repo create`
- Top-level `README.md`, `PLAN.md`, `LICENSE` (MIT)
- `cpm22/` Python package with empty `__init__.py` and `__main__.py`
- `tests/` directory with one placeholder test
- `pyproject.toml` so `python -m cpm22` runs
- `.github/workflows/ci.yml` (run smoke tests on push)

**Acceptance:** `python -m cpm22 --version` works; `pytest tests/` runs; repo is public on GitHub.

### M1 — 8080 CPU core
**File:** `cpm22/cpu8080.py`
- 8080 instruction set: 256 opcodes, 244 documented + 12 undocumented (NOP/RST variants handled; we don't need full undocumented for CP/M)
- Flags: S, Z, AC, P, CY (8080 layout — bit 5 always 0, no N flag)
- Registers: A, B, C, D, E, H, L; SP, PC
- Dict-of-handlers dispatch (Section 1 of skill)
- Cycle accounting (4/5/7/10/11 cycles per instruction)
- HALF-CARRY (AC) flag for DAA — easy to miss
- `HALT` opcode (0x76) — halt-and-restart-on-interrupt model

**Test:** `tests/test_cpu8080.py` — 50+ instruction tests against a textbook reference (Section 5: "don't write the test from your mental model"). Verify against the Z80 reference impl we already have for any 8080-compatible opcodes, and cross-check with the Z80 skill's diagnostic scripts.

**Acceptance:** `pytest tests/test_cpu8080.py` green; opcode-dispatch audit (Section 1, audit recipe) reports 0 unmapped opcodes in CP/M 2.2's known instruction set.

### M2 — Memory, BIOS port-map, floppy image, boot loader
**Files:** `cpm22/memory.py`, `cpm22/floppy.py`, `cpm22/boot.py`
- 64KB bytearray memory
- `FloppyImage` class: 8" SSSD (26 sectors × 77 tracks × 128 = 253,952 bytes) and 8" SSDD (26 sectors × 77 tracks × 256 = 512,256 bytes)
  - Auto-detect by sector size (read first sector; if it parses as a CP/M directory entry, infer layout)
  - `.img` file read/write; can create blank images
  - Sector skew table (CP/M 2.2 standard IBM 8" skew is `[1,7,13,19,25,5,11,17,23,3,9,15,21,2,8,14,20,26,6,12,18,24,4,10,16,22]`)
- `SerialPort` class: 8251 USART, rx/tx byte buffers, status flags, I/O ports 0x10/0x11
- BIOS port mapping:
  - `OUT 0x00` → BDOS entry (function in C)
  - `OUT 0x10` → 8251 data
  - `OUT 0x11` → 8251 control
  - `IN 0x10` / `IN 0x11` → 8251 status/data read
- Cold-boot loader (in Python): read 2 tracks from floppy A: into low memory, then jump to 0x0000 (CCP)
  - First track = boot sector (skipped) + CCP + BDOS start
  - Second track = rest of BDOS
  - Mirrors `OS1BOOT.ASM` flow

**Test:** `tests/test_floppy.py` — create blank SSSD, mount in BIOS, write a sector, read back, verify skew translation. `tests/test_boot.py` — load `disk_images/cpm22-sssd.img` (created by M4), verify CCP banner appears on serial port.

**Acceptance:** `python -m cpm22 --headless --boot disk_images/cpm22-sssd.img` prints the CP/M 2.2 banner to stdout.

### M3 — BIOS port + pre-built CP/M 2.2 binary
**Files:** `cpm22/cpm_bios.py`, `cpm22/asm8080.py` (minimal, only for the BIOS jump vector)
- **Pre-built CP/M 2.2 system image**: download from `cpm.z80.de` / classic archives — the relocatable CCP+BDOS binary that's been used by every CP/M 2.2 emulator since 1982. Run `MOVCPM 64 *` to relocate it to the top of our 64KB address space. We do NOT assemble the source. This is the "authentic bytes" path, just with the bytes already built for us.
- **BIOS**: hand-written minimal 8080 assembly for the IMSAI-specific 17 entry points. We do write a tiny assembler (or hand-encode) for ~200 bytes of BIOS — this is the only place we generate 8080 bytes ourselves, and it's small enough to test exhaustively. Source: port the structure from `OS4BIOS.ASM`, replace the MDS-800 I/O ports (0x78–0x7F) with our 8251 USART ports (0x10/0x11).
- **BDOS entry**: a 3-byte trampoline at `0x0005`: `MOV A, C; JMP 0x0005+5` — wait, that's circular. Standard CP/M 2.2 convention: `0x0005` contains `JP BDOS_ENTRY`. We just write 3 bytes there.
- **BIOS jump vector**: at top of 64KB (e.g. 0xFA00) — 17 `JMP`s, one per entry. The pre-built CP/M 2.2 calls these by hardcoded address, so the location is fixed by the relocated binary.

**Test:** `tests/test_bios.py` — boot from real `cpm22-sssd.img`, run CCP, type `DIR`, verify the directory listing comes out. Use the same busy-wait + `time.sleep(0.001)` pattern from Section 3 of the skill.

**Acceptance:** `python -m cpm22 --headless --boot disk_images/cpm22-sssd.img` reaches the `A>` prompt. `echo -e "DIR\r" | python -m cpm22 --headless --boot disk_images/cpm22-sssd.img` lists the disk contents.

### M4 — Build bootable disk images
**Files:** `cpm22/diskbuild.py`, `disk_images/`
- `cpm22-sssd.img` — 8" SSSD 256KB, single user area
- `cpm22-ssdd.img` — 8" SSDD 512KB, single user area
- Both contain: CCP, BDOS, BIOS (as assembled in M3), and standard utilities: `PIP.COM`, `STAT.COM`, `ED.COM`, `SUBMIT.COM`, `DDT.COM`, `SYSGEN.COM`, `DUMP.COM`, `MOVCPM.COM` (subset of what's in `CPM2.2SRC/`)
- Build script reads the `.COM` binaries from the source distribution and writes them onto a freshly formatted disk image with directory entries

**Test:** `tests/test_diskbuild.py` — build image, boot in emulator, run `STAT`, verify it reports the file system state.

**Acceptance:** Both images boot in the emulator, `STAT` reports the expected files, `PIP` can copy files within the disk.

### M5 — Front-panel + CRT GUI
**Files:** `cpm22/gui/panel.py`, `cpm22/gui/crt.py`, `cpm22/gui/app.py`
- **CRT:** Tk `Canvas`, 80×25 amber phosphor cells on dark CRT background, scanline overlay, rounded corners. Renders from the 8251's tx buffer (CPU thread → UI thread via `queue.Queue`).
- **Front panel:** two halves.
  - **Top half — toggle switches** (the iconic blue IMSAI face): 16 toggle switches rendered as vertical bars with circular handles. UP = on. Right-click to flip, or just click.
  - **Bottom half — LED array:** 16 address LEDs + 8 data LEDs + RUN/HALT/INTERRUPT status LEDs. Address LEDs follow the 8080's PC upper bits (or full PC, scrolling).
- **Controls:** Run/Halt, Reset, Single-step, Examine, Deposit (toggle switches → memory), Boot from A:/B: drive select.
- **Drive bay:** two 8" floppy images displayed as physical disks with sliding door animations on mount/unmount. A: and B: labels in amber.
- **Tabs:** Main (front panel + CRT), Drive A (file browser to mount .img), Drive B (same), Debug (memory/PC/register inspector).
- **Threading:** CPU on daemon thread; UI on Tk main thread; serial as the shared channel. Use the Section 8 `try/except + queue` pattern so a CPU thread crash surfaces to the user.

**Test:** `tests/test_gui.py` — construct `EmulatorApp`, assert widgets exist, auto-close in 200ms (Section 8: "Verify GUI construction without launching a window").

**Acceptance:** `python -m cpm22` opens the GUI. Clicking Run with `cpm22-sssd.img` mounted boots CP/M and the CRT shows the `A>` prompt.

### M6 — Polish & documentation
- README with screenshots, controls, supported disk formats
- Add a sample disk with a few classic CP/M programs (MBASIC, ADVENT, etc. — public domain copies)
- Performance tuning: target real-time CP/M (1 MHz 8080 equivalent)
- Save/restore CPU state (register dump to file)
- Record/replay input
- In-GUI hex memory viewer, disassembler view

---

## Test strategy (skill Sections 3, 4, 5)

Each milestone ends with a smoke test that:
1. Constructs the subsystem in isolation
2. Exercises one or two end-to-end flows
3. Bounds itself with `signal.alarm(15)` (Section 4, watchdog pattern)
4. Uses `time.sleep(0.001)` in any busy-wait so the test thread can push input
5. Breaks on a SUT-specific event (BDOS call, BDOS RBUF call count) rather than on a memory byte or PC value

For the M1 8080 tests specifically, I'll cross-check against a known-good reference (the working Z80 impl from the prior session, since Z80's 8080-mode behavior is byte-for-byte identical for the 244 documented 8080 opcodes).

---

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| 8080 vs Z80 instruction-set trap (e.g. Z80-specific DAA not used by CP/M 2.2) | Low | CP/M 2.2 was written for 8080; we just need 244 opcodes + correct flag behavior. |
| Original CP/M source has maclib dependencies (CPMOVE.ASM, DDT, etc.) | Med | The bootstrap is in OS1BOOT; the system itself is in OS2/OS3/OS4. We only need OS4 rewritten; the other source files (utilities) are standalone PL/M or .LIN binaries we just include. |
| Front-panel layout takes too long to get period-correct | Med | Use reference photos from Wikipedia; the iconic look is blue background, white switches, red LED row, and the four short toggle columns on the right. Iterate from a simple version. |
| Performance — Python interpreter vs 1 MHz 8080 | High | Profile after M3. Likely fine (Z80 build did 1MHz easily); but if not, add a `cython` or `numba` JIT for the inner CPU loop as a late-M6 optimization. |

---

## Definition of done (whole project)

- `python -m cpm22` opens the IMSAI faceplate GUI
- Mounting `cpm22-sssd.img` in drive A: and clicking Run shows the `A>` prompt on the CRT
- Typing `DIR` on the CRT keyboard lists the system files
- Typing `STAT` and `ED TEST.TXT` work
- `python -m cpm22 --headless --boot disk_images/cpm22-sssd.img` reaches `A>` non-interactively (for CI)
- All milestones shipped as separate PRs
- Repo is public, README has screenshots, CI is green
