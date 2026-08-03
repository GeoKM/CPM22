"""Loader for authentic Digital Research CP/M 2.2 binaries.

Assembles OS2CCP.ASM, OS3BDOS.ASM, OS4BIOS.ASM (DR's original 8080 source)
and places them at the correct memory addresses for a CP/M 2.2 system.

Memory layout (standard CP/M 2.2, 64K RAM):
  0x0000-0x0002:  JP CCP (warm boot)
  0x0003-0x0004:  I/O byte
  0x0005:         current drive
  0x0006-0x0008:  JP BDOS (function dispatch)
  0xE000-0xE7FF:  CCP  (DRCCP.BIN, 2082 bytes)
  0xE800-0xF1FF:  BDOS (DRBDOS.BIN, 3566 bytes, sourced at logical 0x0800)
  0xF200-0xF403:  BIOS (DRBIOS.BIN, 517 bytes, sourced at logical 0x1600)

The DR source uses logical addresses (org 000h for CCP, org 0800h for BDOS,
org patch=1600h for BIOS). We assemble at those logical addresses, then place
the resulting bytes at 0xE000, 0xE800, 0xF000 respectively.

The BIOS jump table at 0xF000 has 17 jmp instructions that need to be patched
to point at our Python BIOS stubs (which live somewhere in RAM below the
CCP/BDOS/BIOS region, e.g. 0xC000).
"""

from pathlib import Path
import re

from cpm22.asm8080_d import Assembler
from cpm22.asm8080 import MVI, OUT, RET

# Memory addresses
CCP_LOAD = 0xE000     # where CCP is placed in memory
BDOS_LOAD = 0xE800    # where BDOS is placed
BIOS_LOAD = 0xF600    # where BIOS jump table starts (BDOS builds bios as ($ and 0ff00h)+100h)
BIOS_STUB_BASE = 0xC000  # where BIOS Python-stub dispatchers live

# The DR source uses logical addresses (org 000h for CCP, org 0800h for BDOS,
# patch=1600h for BIOS). We rewrite them so the assembled bytes can be loaded
# directly at our physical addresses without relocation.
CCP_ORG = 0xE000
BDOS_ORG = 0xE800
BIOS_PATCH = 0xF600

# BIOS jump table offsets (relative to BIOS_LOAD = 0xF000)
BIOS_JMP_OFFSETS = {
    "boot":     0x00,   # JP boot
    "wboot":    0x03,   # JP wboot
    "const":    0x06,
    "conin":    0x09,
    "conout":   0x0C,
    "list":     0x0F,
    "punch":    0x12,
    "reader":   0x15,
    "home":     0x18,
    "seldsk":   0x1B,
    "settrk":   0x1E,
    "setsec":   0x21,
    "setdma":   0x24,
    "read":     0x27,
    "write":    0x2A,
    "listst":   0x2D,
    "sectran":  0x30,
}

# Functions in stub order — matches FN_* in cpm_bios.py
BIOS_FUNCTIONS = [
    ("boot", 0x00), ("wboot", 0x01), ("const", 0x02), ("conin", 0x03),
    ("conout", 0x04), ("list", 0x05), ("punch", 0x06), ("reader", 0x07),
    ("home", 0x08), ("seldsk", 0x09), ("settrk", 0x0A), ("setsec", 0x0B),
    ("setdma", 0x0C), ("read", 0x0D), ("write", 0x0E), ("listst", 0x0F),
    ("sectran", 0x10),
]

BIOS_PORT = 0xF1


def _build_bios_stubs() -> bytes:
    """Build 17 BIOS stub dispatchers, each 5 bytes: MVI A, fn; OUT port; RET."""
    out = bytearray()
    for _, fn in BIOS_FUNCTIONS:
        stub = MVI("A", fn) + OUT(BIOS_PORT) + RET
        out += stub
    return bytes(out)


def _patch_bios_jmp_table(bios_bytes: bytearray, stub_base: int) -> None:
    """Patch the JP instructions in the BIOS jump table to point at our stubs.

    The DR BIOS source has 17 JP <label> instructions at offsets 0x00-0x32 of
    the BIOS region. The labels are local ('boot:', 'wboot:' etc.) which the
    assembler resolves to addresses INSIDE the BIOS region (since the BIOS
    code follows the jump table).

    But we don't have the actual BIOS implementations (no floppy controller,
    no USART driver in the assembled code). So we patch every JP in the jump
    table to point at our Python-driven stubs instead.
    """
    stub_addr = stub_base
    for _, fn in BIOS_FUNCTIONS:
        offset = BIOS_JMP_OFFSETS[BIOS_FUNCTIONS[fn][0]] if False else BIOS_JMP_OFFSETS[
            [name for name, _ in BIOS_FUNCTIONS][fn]
        ]
        # Write JP <stub_addr> at the offset
        bios_bytes[offset + 0] = 0xC3  # JP opcode
        bios_bytes[offset + 1] = stub_addr & 0xFF
        bios_bytes[offset + 2] = (stub_addr >> 8) & 0xFF
        stub_addr += 5  # each stub is 5 bytes


def _assemble_dr_source(src_path: str, org_overrides: dict = {},
                        return_labels: bool = False):
    """Assemble a DR source file at its native logical addresses.

    The source files contain their own 'org' directives that override the
    org_addr parameter. We pass org_addr=0 anyway for clarity.

    org_overrides: optional dict of {regex_pattern: replacement} applied to
    the source before assembly, to rewrite org directives to physical
    addresses instead of logical ones.
    return_labels: if True, return (code, labels_dict) tuple.
    """
    asm = Assembler()
    text = Path(src_path).read_text(errors="replace")
    # Strip macro-library directives that we don't have
    text = re.sub(r"^\s*maclib\s+.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*diskdef\s+.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*disks\s+.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*endef.*$", "", text, flags=re.MULTILINE)
    # Apply org overrides
    if org_overrides:
        for pattern, repl in org_overrides.items():
            text = re.sub(pattern, repl, text, flags=re.MULTILINE)
    # Write to a temp file and assemble
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".asm", delete=False) as f:
        f.write(text)
        tmp = f.name
    code, _ = asm.assemble_file(tmp, org_addr=0)
    if return_labels:
        # Adjust labels by BDOS_ORG since we assembled at logical address
        # (the source 'org' was rewritten to BDOS_ORG = 0xE800)
        adjusted = {n: addr for n, addr in asm.labels.items()}
        return code, adjusted
    return code


def build_dr_cpm_system() -> dict:
    """Build the authentic DR CP/M 2.2 system image.

    Returns a dict with keys:
      'ccp_bytes':    bytes to place at CCP_LOAD (0xE000)
      'bdos_bytes':   bytes to place at BDOS_LOAD (0xE800)
      'bios_bytes':   bytes to place at BIOS_LOAD (0xF000)
      'bios_stubs':   bytes for the Python BIOS stub dispatchers
      'bios_stub_base': address where stubs go
      'cold_boot_vec': 3 bytes (JP) for the cold-boot entry at 0x0000
      'bdos_vec':     3 bytes (JP) for the BDOS entry at 0x0006
    """
    src_dir = Path(__file__).parent.parent / "CPM2.2SRC"

    # Rewrite the source 'org' directives to physical addresses so the
    # assembled bytes load directly at our memory locations.
    ccp_overrides = {
        r"^\s*org\s+3400h\s*$": f"\torg\t{CCP_ORG:04x}h",
        r"^\s*org\s+000h\s*$": f"\torg\t{CCP_ORG:04x}h",
    }
    bdos_overrides = {
        r"^\s*org\s+0dc00h\s*$": f"\torg\t{BDOS_ORG + 0x500:04x}h",  # testing branch
        # Rewrite the original 'org 0800h' to our BDOS base AND inject a
        # `bios equ 0xF600` line so the `set` directives near the top of
        # the source (which build bootf, wbootf, etc. as `bios+3*N`)
        # resolve correctly. The source's own `bios equ ($ and 0ff00h)+100h`
        # at the end evaluates to the same value (0xF600 for BDOS at 0xE800).
        r"^\s*org\s+0800h\s*$": f"\torg\t{BDOS_ORG:04x}h\nbios\tequ\t{BDOS_ORG + 0xE00:04x}h",
    }
    bios_overrides = {
        r"^patch\s+equ\s+1600h\s*$": f"patch\tequ\t{BIOS_PATCH:04x}h",
    }

    # Assemble CCP
    ccp = bytearray(_assemble_dr_source(str(src_dir / "OS2CCP.ASM"), ccp_overrides))
    # Assemble BDOS (capture labels so we can zero out uninitialized variables
    # at their actual addresses, not hardcoded addresses that may collide with
    # live code if the source layout shifts).
    bdos_code, bdos_labels = _assemble_dr_source(
        str(src_dir / "OS3BDOS.ASM"), bdos_overrides, return_labels=True)
    bdos = bytearray(bdos_code)
    # Assemble BIOS
    bios = bytearray(_assemble_dr_source(str(src_dir / "OS4BIOS.ASM"), bios_overrides))

    # Patch BIOS jump table to point at our Python stubs
    stubs = _build_bios_stubs()
    _patch_bios_jmp_table(bios, BIOS_STUB_BASE)

    # Build the cold-boot vector at 0x0000 (JP CCP_LOAD)
    cold_boot_vec = bytes([0xC3, CCP_LOAD & 0xFF, (CCP_LOAD >> 8) & 0xFF])
    # Build the BDOS vector at 0x0006 (JP BDOS_LOAD)
    bdos_vec = bytes([0xC3, BDOS_LOAD & 0xFF, (BDOS_LOAD >> 8) & 0xFF])

    return {
        "ccp_bytes": bytes(ccp),
        "bdos_bytes": bytes(bdos),
        "bios_bytes": bytes(bios),
        "bios_stubs": stubs,
        "bios_stub_base": BIOS_STUB_BASE,
        "ccp_load": CCP_LOAD,
        "bdos_load": BDOS_LOAD,
        "bios_load": BIOS_LOAD,
        "cold_boot_vec": cold_boot_vec,
        "bdos_vec": bdos_vec,
        "bdos_labels": bdos_labels,
    }
