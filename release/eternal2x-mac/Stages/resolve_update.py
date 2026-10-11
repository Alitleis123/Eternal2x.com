from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import urllib.request
import zipfile


def _detect_platform_key() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _read_version(repo_root: Path) -> str:
    version_path = repo_root / "VERSION"
    if not version_path.exists():
        return "0.0.0"
    # utf-8-sig: PowerShell writes VERSION with a BOM, which is not whitespace
    # and would otherwise survive .strip() and break _parse_version.
    return version_path.read_text(encoding="utf-8-sig").strip() or "0.0.0"


def _parse_version(value: str) -> tuple[int, int, int]:
    clean = (value or "").lstrip("\ufeff").strip().lstrip("v")
    parts = clean.split(".")
    nums: list[int] = []
    for p in parts[:3]:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def _download_json(url: str, timeout: int) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8-sig")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("Update metadata must be a JSON object.")
    return data


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# Names the updater replaces. Anything else in the archive is left alone.
PAYLOAD_NAMES = ("Installer", "Pipeline", "Stages", "VERSION", "README.md")


def _find_payload_root(extract_dir: Path) -> Path:
    """The folder inside the archive that holds the plugin files.

    Archives re-zipped in Finder carry a __MACOSX folder beside the real one,
    so look for the folder that actually has the payload in it.
    """
    if any((extract_dir / name).exists() for name in PAYLOAD_NAMES):
        return extract_dir
    for child in sorted(extract_dir.iterdir()):
        if child.is_dir() and any((child / name).exists() for name in PAYLOAD_NAMES):
            return child
    return extract_dir


def _copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            _copy_tree(child, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)


def _apply_payload(payload_root: Path, repo_root: Path) -> int:
    """Copy the runtime files into the install. Returns how many were copied.

    Everything is staged beside the install first, so a disk that fills up
    halfway fails before anything live has been touched.
    """
    present = [name for name in PAYLOAD_NAMES if (payload_root / name).exists()]
    if not present:
        return 0
    staging = Path(tempfile.mkdtemp(prefix=".eternal2x_update_", dir=repo_root))
    try:
        for name in present:
            src = payload_root / name
            if src.is_dir():
                _copy_tree(src, staging / name)
            else:
                shutil.copy2(src, staging / name)
        copied = 0
        for name in present:
            staged = staging / name
            if staged.is_dir():
                _copy_tree(staged, repo_root / name)
                copied += sum(1 for f in staged.rglob("*") if f.is_file())
            else:
                shutil.copy2(staged, repo_root / name)
                copied += 1
        return copied
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Eternal2x cloud update checker.")
    parser.add_argument("--meta-url", required=True, help="URL to latest.json metadata.")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repo root where Eternal2x files are installed (default: current directory).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=8,
        help="Network timeout in seconds (default: 8).",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Silent-ish mode for startup checks.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    current_version = _read_version(repo_root)
    platform_key = _detect_platform_key()

    try:
        meta = _download_json(args.meta_url, timeout=args.timeout)
    except Exception as exc:
        msg = f"Update check skipped: {exc}"
        print(msg if not args.auto else "Auto update check skipped.")
        return 0

    latest_version = str(meta.get("version", "")).strip()
    if not latest_version:
        print("Update metadata missing version.")
        return 1

    if _parse_version(latest_version) <= _parse_version(current_version):
        if not args.auto:
            print(f"No update. Current version {current_version} is up to date.")
        return 0

    platform_blob = meta.get(platform_key)
    if not isinstance(platform_blob, dict):
        print(f"No update package listed for platform: {platform_key}")
        return 1

    download_url = str(platform_blob.get("url", "")).strip()
    expected_sha = str(platform_blob.get("sha256", "")).strip().lower()
    if not download_url:
        print("Update package URL missing.")
        return 1
    if not expected_sha:
        # Never apply code that cannot be checked against the feed.
        print("Update package has no checksum, so it was not applied.")
        return 1

    print(f"Updating Eternal2x: {current_version} -> {latest_version}")
    with tempfile.TemporaryDirectory(prefix="eternal2x_update_") as td:
        temp_dir = Path(td)
        zip_path = temp_dir / "update.zip"
        extract_dir = temp_dir / "extract"
        try:
            with urllib.request.urlopen(download_url, timeout=max(args.timeout, 20)) as resp:
                zip_path.write_bytes(resp.read())
        except Exception as exc:
            # A published latest.json can point at a release that is not up
            # yet. That is a bad download, not a reason to show a traceback.
            print(f"Could not download the update: {exc}")
            return 1

        actual_sha = _sha256(zip_path).lower()
        if actual_sha != expected_sha:
            print("Update package checksum mismatch.")
            return 1

        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
        except (zipfile.BadZipFile, OSError) as exc:
            print(f"The update package could not be opened: {exc}")
            return 1

        payload_root = _find_payload_root(extract_dir)
        try:
            copied = _apply_payload(payload_root, repo_root)
        except OSError as exc:
            print(f"The update could not be applied: {exc}")
            return 1
        if not copied:
            # Leave VERSION alone, or every later check would say up to date.
            print("The update package held none of the plugin files, so nothing changed.")
            return 1

    (repo_root / "VERSION").write_text(latest_version + "\n", encoding="utf-8")
    print("Update applied. Restart Resolve to use the new version.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
