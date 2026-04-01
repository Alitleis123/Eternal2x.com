from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

DEFAULT_UPDATE_URL = (
    "https://raw.githubusercontent.com/"
    "Alitleis123/Eternal2x.com/main/update/latest.json"
)


def _resolve_comp_dir() -> Path:
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("APPDATA is not set.")
        return (
            Path(appdata)
            / "Blackmagic Design"
            / "DaVinci Resolve"
            / "Fusion"
            / "Scripts"
            / "Comp"
        )
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Blackmagic Design"
            / "DaVinci Resolve"
            / "Fusion"
            / "Scripts"
            / "Comp"
        )
    return (
        Path.home()
        / ".local"
        / "share"
        / "DaVinciResolve"
        / "Fusion"
        / "Scripts"
        / "Comp"
    )


def _pick_python(repo_root: Path) -> str:
    if sys.platform.startswith("win"):
        venv_python = repo_root / ".venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return str(venv_python)
        return shutil.which("python") or sys.executable or "python"
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return shutil.which("python3") or shutil.which("python") or sys.executable or "python3"


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    src_lua = repo_root / "Installer" / "Eternal2xLauncher.lua"

    if not src_lua.exists():
        print(f"Missing launcher script: {src_lua}")
        return 1

    dest_dir = _resolve_comp_dir()

    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_lua = dest_dir / "Eternal2x.lua"
    shutil.copy2(src_lua, dest_lua)

    python_path = _pick_python(repo_root)

    conf_path = dest_dir / "Eternal2x.conf"
    conf_path.write_text(
        f"repo_root={repo_root}\n"
        f"python={python_path}\n"
        f"update_url={DEFAULT_UPDATE_URL}\n"
        "auto_update=true\n",
        encoding="utf-8",
    )

    print("Installed Eternal2x launcher.")
    print(f"Launcher: {dest_lua}")
    print(f"Config: {conf_path}")
    print("Restart Resolve to see Workspace > Scripts > Eternal2x.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
