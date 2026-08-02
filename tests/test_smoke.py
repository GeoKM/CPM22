"""Tests directory placeholder.

Real test suites land in M1+:

- M1: test_cpu8080.py — 50+ instruction tests against a known-good reference
- M2: test_floppy.py — SSSD/SSDD round-trip
- M3: test_asm8080.py, test_bios.py — cross-assembler and end-to-end boot
- M4: test_diskbuild.py — build image, boot, verify with STAT
- M5: test_gui.py — Tk widget construction (200ms auto-close)

M0 ships just one smoke test that confirms the package imports cleanly
and --version works.
"""

import subprocess
import sys


def test_package_imports():
    """The cpm22 package must import without error."""
    import cpm22  # noqa: F401
    assert hasattr(cpm22, "__version__")
    assert isinstance(cpm22.__version__, str)
    assert len(cpm22.__version__.split(".")) == 3


def test_version_flag():
    """`python -m cpm22 --version` prints the version and exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "cpm22", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "cpm22" in result.stdout
    assert "0.1.0" in result.stdout


def test_help():
    """`python -m cpm22 --help` prints usage and exits 0."""
    result = subprocess.run(
        [sys.executable, "-m", "cpm22", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "IMSAI 8080" in result.stdout
    assert "--headless" in result.stdout
    assert "--boot" in result.stdout
