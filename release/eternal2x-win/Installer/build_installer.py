"""
Compile the Eternal2x GUI installer into a standalone executable.

Usage:
    pip install pyinstaller
    python Installer/build_installer.py

Produces:
    dist/Eternal2xInstaller.exe  (Windows)
    dist/Eternal2xInstaller      (macOS/Linux)

The compiled exe should be placed in the root of the release zip
so buyers can double-click it alongside the plugin folder contents.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    script = repo_root / "Installer" / "gui_installer.py"

    if not script.exists():
        print(f"Missing installer script: {script}")
        return 1

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed.")
        print("Run:  pip install pyinstaller")
        return 1

    icon_arg: list[str] = []
    icon = repo_root / "Installer" / "icon.ico"
    if icon.exists():
        icon_arg = ["--icon", str(icon)]

    name = "Eternal2xInstaller"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", name,
        "--distpath", str(repo_root / "dist"),
        "--workpath", str(repo_root / "build"),
        "--specpath", str(repo_root / "build"),
        *icon_arg,
        str(script),
    ]

    print(f"Building {name}...")
    print(f"  Command: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode == 0:
        ext = ".exe" if sys.platform.startswith("win") else ""
        output = repo_root / "dist" / f"{name}{ext}"
        print(f"\nBuild complete: {output}")
        print(f"Size: {output.stat().st_size / 1024 / 1024:.1f} MB")
    else:
        print("\nBuild failed. Check the output above for errors.")

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
