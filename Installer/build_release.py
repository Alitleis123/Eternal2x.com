"""Build, publish and verify a release.

    python Installer/build_release.py            build the folders and zips
    python Installer/build_release.py --publish  build, tag, upload, verify
    python Installer/build_release.py --verify   check the live feed is sound

The tag is derived from VERSION in one place, `release_tag()`, and both the
download URLs and the git tag come from it. They used to be decided separately,
which is how 0.2.0 shipped a latest.json pointing at a v0.2.0 tag that did not
exist, leaving every download link a 404.

update/latest.json describes what is published, not what is in the working
tree, so only --publish writes it, and only after the uploaded files check out.
A plain build used to rewrite it too. After 0.3.1 a shipped file was deleted,
the rebuild put new checksums in the feed, and every update download failed
its checksum against the archives that were really on the release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLATFORMS = {"upflow2x-mac": "macos", "upflow2x-win": "windows"}

# What ships to users. Tests, release artefacts and dev tooling stay behind.
PAYLOAD = ["Pipeline", "Stages", "Installer", "VERSION", "README.md",
           "requirements.txt"]
SKIP_DIRS = {"__pycache__", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}

REPO = "sidebandstudio/Upflow2x"
RELEASE_URL = "https://github.com/" + REPO + "/releases/download/{tag}/{name}.zip"


def release_tag(version: str) -> str:
    """The one place the tag is decided. Everything else derives from this."""
    return f"v{version}"


def read_version() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8-sig").strip()


def build_metadata(version: str, checksums: dict) -> dict:
    tag = release_tag(version)
    meta = {"version": version}
    for name, key in PLATFORMS.items():
        meta[key] = {
            "url": RELEASE_URL.format(tag=tag, name=name),
            "sha256": checksums[name],
        }
    return meta


def write_metadata(meta: dict) -> Path:
    path = REPO_ROOT / "update" / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Plain UTF-8, no BOM: the updater decodes this straight off the network.
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return path


def verify_feed(meta: dict) -> bool:
    """Download what the feed advertises and check it is really there.

    This is the check that would have caught the 0.2.0 release, where the
    advertised URL 404ed because the tag was written without its v prefix.
    """
    import urllib.error
    import urllib.request

    ok = True
    for key in ("macos", "windows"):
        url = meta[key]["url"]
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                payload = response.read()
        except (urllib.error.URLError, OSError) as exc:
            print(f"  {key}: FAILED, {exc}")
            ok = False
            continue
        actual = hashlib.sha256(payload).hexdigest()
        if actual != meta[key]["sha256"]:
            print(f"  {key}: checksum mismatch against the published file")
            ok = False
        else:
            print(f"  {key}: reachable, checksum matches")
    return ok


def publish(version: str) -> bool:
    """Create the GitHub release, with the tag derived from VERSION."""
    tag = release_tag(version)
    archives = [str(REPO_ROOT / "release" / f"{name}.zip") for name in PLATFORMS]
    existing = subprocess.run(["gh", "release", "view", tag, "--repo", REPO],
                              capture_output=True, text=True)
    if existing.returncode == 0:
        # Never replace a published file. Users who already have the feed
        # verify against those exact bytes. Only upload what is missing, and
        # let the verify step report it if the build no longer matches.
        print(f"  {tag} already exists, uploading only missing assets")
        for archive in archives:
            subprocess.run(["gh", "release", "upload", tag, "--repo", REPO, archive],
                           capture_output=True, text=True)
        return True

    # Tag the commit being built, not whatever the default branch points at.
    target = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                            text=True, cwd=REPO_ROOT).stdout.strip()
    command = ["gh", "release", "create", tag, "--repo", REPO,
               "--title", f"Upflow2x {version}",
               "--notes", f"Upflow2x {version}.", *archives]
    if target:
        command[4:4] = ["--target", target]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  publishing failed: {result.stderr.strip()}")
        return False
    print(f"  published {tag}")
    return True


def _copy(src: Path, dst: Path) -> None:
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in sorted(src.iterdir()):
            if child.name in SKIP_DIRS or child.suffix in SKIP_SUFFIXES:
                continue
            _copy(child, dst / child.name)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def build_folder(name: str) -> Path:
    dest = REPO_ROOT / "release" / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for item in PAYLOAD:
        source = REPO_ROOT / item
        if source.exists():
            _copy(source, dest / item)
    return dest


# A fixed timestamp for every entry. Without it the zip embeds file mtimes,
# two builds of the same commit produce different checksums, and the sha256 in
# update/latest.json can only be trusted if it came from the exact machine that
# built the upload. With it, anyone can rebuild a tag and get the same bytes.
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def zip_folder(folder: Path) -> Path:
    """Zip a release folder reproducibly."""
    archive = folder.with_suffix(".zip")
    if archive.exists():
        archive.unlink()

    # Sort by the archive entry name, not by Path. Path comparison is
    # case-insensitive on Windows and case-sensitive elsewhere, so sorting
    # paths would order the entries differently per platform and the archives
    # would stop being reproducible across machines.
    entries = sorted(
        (f"{folder.name}/{path.relative_to(folder).as_posix()}", path)
        for path in folder.rglob("*") if path.is_file()
    )
    # Stored, not deflated: deflate output depends on the zlib build, and
    # Windows Python ships zlib-ng, so a compressed archive built there never
    # matches one built by CI. The plugin is small enough not to mind.
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED) as zf:
        for name, path in entries:
            info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            # Made on Unix, whatever builds it. zipfile says MS-DOS on Windows,
            # which changes the bytes and makes the mode below meaningless.
            info.create_system = 3
            # Regular file, 0644, so the archive does not carry local umask.
            info.external_attr = (0o100644 & 0xFFFF) << 16
            zf.writestr(info, path.read_bytes())
    return archive


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the Upflow2x release.")
    parser.add_argument("--no-zip", action="store_true",
                        help="Refresh the release folders without zipping.")
    parser.add_argument("--publish", action="store_true",
                        help="Create the GitHub release and verify the feed.")
    parser.add_argument("--verify", action="store_true",
                        help="Only check that the published feed is reachable.")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    version = read_version()
    tag = release_tag(version)

    if args.verify:
        meta = json.loads((REPO_ROOT / "update" / "latest.json")
                          .read_text(encoding="utf-8-sig"))
        print(f"Verifying the feed for {meta['version']}")
        return 0 if verify_feed(meta) else 1

    print(f"Building Upflow2x {version}, tag {tag}")

    checksums = {}
    for name in PLATFORMS:
        folder = build_folder(name)
        count = sum(1 for item in folder.rglob("*") if item.is_file())
        print(f"  {name}: {count} files")
        if args.no_zip:
            continue
        archive = zip_folder(folder)
        checksums[name] = sha256(archive)
        print(f"  {archive.name}: {checksums[name][:16]}...")

    if args.no_zip:
        return 0

    meta = build_metadata(version, checksums)

    if args.publish:
        if not publish(version):
            return 1
        print("Verifying the published downloads")
        if not verify_feed(meta):
            print("The published downloads do not match this build, so "
                  "update/latest.json was left untouched. If shipped files changed "
                  f"since {tag} went out, bump VERSION and release again.")
            return 1
        path = write_metadata(meta)
        print(f"Wrote {path.relative_to(REPO_ROOT)}")
        return 0

    print("update/latest.json is unchanged. It only moves when --publish "
          "has uploaded and verified a release.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
