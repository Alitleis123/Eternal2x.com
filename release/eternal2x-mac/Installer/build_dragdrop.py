from __future__ import annotations

from pathlib import Path
import shutil


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    dist_root = repo_root / "dist" / "Eternal2x"

    if dist_root.exists():
        shutil.rmtree(dist_root)

    dist_root.mkdir(parents=True, exist_ok=True)

    shutil.copy2(repo_root / "Installer" / "Eternal2xLauncher.lua", dist_root / "Eternal2x.lua")
    shutil.copy2(repo_root / "Installer" / "Eternal2x.lua", dist_root / "Eternal2x_ui.lua")
    shutil.copytree(repo_root / "Stages", dist_root / "Stages")
    shutil.copytree(repo_root / "Pipeline", dist_root / "Pipeline")

    conf = dist_root / "Eternal2x.conf"
    conf.write_text(
        f"repo_root={dist_root}\n"
        "python=python\n"
        "update_url=https://raw.githubusercontent.com/"
        "Alitleis123/DaVinchi-Resolve-Smart-Upscale-Plugin/main/update/latest.json\n"
        "auto_update=true\n",
        encoding="utf-8",
    )

    readme = dist_root / "README-DragDrop.txt"
    readme.write_text(
        "Eternal2x Drag & Drop Install\n"
        "\n"
        "1) Copy the entire Eternal2x folder into Resolve's Scripts/Comp folder.\n"
        "2) Edit Eternal2x.conf and set repo_root to the full path of the Eternal2x folder.\n"
        "3) Restart Resolve.\n"
        "4) Open Workspace -> Scripts -> Eternal2x.\n"
        "\n"
        "Resolve Scripts/Comp locations:\n"
        "macOS: ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Comp/\n"
        "Windows: %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Fusion\\Scripts\\Comp\\\n"
        "\n"
        "Requirements: Python 3.8+ with numpy and opencv-python installed.\n"
        "Run: pip install numpy opencv-python\n",
        encoding="utf-8",
    )

    print(f"Built drag & drop package at: {dist_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
