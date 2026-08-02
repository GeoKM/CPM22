"""Entry point for `python -m cpm22` and the `cpm22` console script."""

from __future__ import annotations

import argparse
import sys

from cpm22 import __version__ as VERSION


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cpm22",
        description="IMSAI 8080 CP/M 2.2 emulator",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print version and exit",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run in headless mode (no GUI; serial I/O via stdin/stdout)",
    )
    parser.add_argument(
        "--boot",
        metavar="IMG",
        help="Boot the named 8\" floppy image (skipped in M0; wired up in M3+)",
    )
    parser.add_argument(
        "--drive-b",
        metavar="IMG",
        help="Mount an image in drive B: too (M2+)",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=0,
        help="Maximum number of CPU cycles to run before exiting (headless only; 0 = unbounded)",
    )
    parser.add_argument(
        "--tests",
        action="store_true",
        help="Run the test suite via pytest and exit",
    )
    args = parser.parse_args(argv)

    if args.version:
        print(f"cpm22 {VERSION}")
        return 0

    if args.tests:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-v"],
            cwd=".",
        )
        return result.returncode

    # Real boot paths land in M3.
    if args.headless or args.boot:
        print(
            "cpm22: headless boot and floppy mounting are scheduled for M2/M3.",
            file=sys.stderr,
        )
        print(
            "cpm22: M0 only ships the package skeleton, GitHub repo, and CI.",
            file=sys.stderr,
        )
        return 2

    # GUI ships in M5. Until then, print a friendly pointer.
    print(
        f"cpm22 {VERSION}\n"
        "IMSAI 8080 CP/M 2.2 emulator\n\n"
        "This is M0 (repo skeleton). Boot and GUI come online at M2/M5.\n"
        "See PLAN.md for the roadmap.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
