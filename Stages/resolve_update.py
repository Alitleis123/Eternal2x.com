from __future__ import annotations

import argparse
import ast
import filecmp
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

# Files no release can do without, because the launcher, the panel and
# Python's imports look them up by these names. Pipeline modules are not
# listed, because _required_files reads them off the release's own imports.
# A list here would live on in every installed updater, and refuse any later
# release that drops or renames a module.
REQUIRED_FILES = ("VERSION", "Installer/Eternal2x.lua", "Installer/Eternal2xLauncher.lua",
                  "Stages/resolve_smooth.py", "Stages/resolve_update.py",
                  "Pipeline/__init__.py")


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


def _required_files(payload_root: Path) -> list[str]:
    """REQUIRED_FILES plus every Pipeline module the release's code imports."""
    needed = set(REQUIRED_FILES)
    for path in sorted([*payload_root.glob("Stages/**/*.py"),
                        *payload_root.glob("Pipeline/**/*.py")]):
        for node in ast.walk(ast.parse(path.read_bytes(), filename=str(path))):
            if not isinstance(node, ast.ImportFrom) or node.level:
                continue
            package, _, module = (node.module or "").partition(".")
            if package != "Pipeline":
                continue
            if module:
                needed.add(f"Pipeline/{module.split('.')[0]}.py")
            else:
                needed.update(f"Pipeline/{alias.name}.py" for alias in node.names)
    return sorted(needed)


def _check_payload(payload_root: Path, version: str) -> str:
    """Why the payload must not be installed, or "" if it is a whole release.

    A checksum only proves the archive is the one the feed named, not that
    it holds a whole release. One with only a README in it used to install
    that README, move VERSION on, and hide the real update for good.
    """
    try:
        missing = [rel for rel in _required_files(payload_root)
                   if not (payload_root / rel).is_file()]
    except (SyntaxError, ValueError) as exc:
        # Code written for a newer Python than the one running this lands here.
        return f"The update package has code this Python cannot read, so it was not applied: {exc}"
    if missing:
        return f"The update package is missing {', '.join(missing)}, so it was not applied."
    found = _read_version(payload_root)
    if _parse_version(found) != _parse_version(version):
        return (f"The update package holds version {found}, not the {version} "
                "the feed announced, so it was not applied.")
    return ""


def _copy_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        target = dst / child.name
        if child.is_dir():
            _copy_tree(child, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)


class RollbackError(OSError):
    """An update failed partway and some old files could not be put back."""


def _roll_back(written: list[Path], backup: Path, repo_root: Path) -> list[str]:
    """Put back the files an update replaced and remove the ones it added.

    Returns the files that could not be put back.
    """
    stuck = []
    for live in reversed(written):
        saved = backup / live.relative_to(repo_root)
        try:
            if not saved.exists():
                live.unlink(missing_ok=True)
            # The write that failed usually never changed its file, and trying
            # it again would only be refused again.
            elif not (live.exists() and filecmp.cmp(saved, live, shallow=False)):
                shutil.copy2(saved, live)
        except OSError:
            stuck.append(live.relative_to(repo_root).as_posix())
    return stuck


def _apply_payload(payload_root: Path, repo_root: Path) -> int:
    """Copy the runtime files into the install. Returns how many were copied.

    Everything is staged beside the install first, so a disk that fills up
    halfway fails before anything live has been touched. Every file about to
    be replaced is backed up then too, so a write refused partway through can
    be undone instead of leaving half the plugin on each version.
    """
    present = [name for name in PAYLOAD_NAMES if (payload_root / name).exists()]
    if not present:
        return 0
    staging = Path(tempfile.mkdtemp(prefix=".eternal2x_update_", dir=repo_root))
    keep_staging = False
    try:
        for name in present:
            src = payload_root / name
            if src.is_dir():
                _copy_tree(src, staging / name)
            else:
                shutil.copy2(src, staging / name)
        files = [(f, repo_root / f.relative_to(staging))
                 for f in sorted(staging.rglob("*")) if f.is_file()]
        backup = staging / "backup"
        for _, live in files:
            if live.exists():
                saved = backup / live.relative_to(repo_root)
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(live, saved)
        written: list[Path] = []
        try:
            for staged, live in files:
                written.append(live)
                live.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(staged, live)
        except OSError as exc:
            stuck = _roll_back(written, backup, repo_root)
            if stuck:
                # Deleting the backup now would lose the only good copies.
                keep_staging = True
                raise RollbackError(
                    f"{exc}. Could not put back {', '.join(stuck)}. The old "
                    f"copies are in {backup}, or reinstall Eternal2x."
                ) from exc
            raise
        return len(files)
    finally:
        if not keep_staging:
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
        problem = _check_payload(payload_root, latest_version)
        if problem:
            print(problem)
            return 1
        # VERSION comes from the package with everything else, so a failed
        # update puts it back too.
        try:
            _apply_payload(payload_root, repo_root)
        except RollbackError as exc:
            print(f"The update failed partway and could not be fully undone: {exc}")
            return 1
        except OSError as exc:
            print(f"The update could not be applied, so nothing was changed: {exc}")
            return 1

    print("Update applied. Restart Resolve to use the new version.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
