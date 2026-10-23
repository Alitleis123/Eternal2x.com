"""The release builder.

The bug this guards against: 0.2.0 shipped an update feed whose download URLs
pointed at a tag `v0.2.0` while the release was actually tagged `0.2.0`, so
every link was a 404. The tag is now derived in one place and everything else
follows from it.
"""

from __future__ import annotations

import hashlib
import json
import zipfile

import pytest

from Installer import build_release as builder


@pytest.fixture
def built(tmp_path, monkeypatch, repo_root):
    """Run a real build into a temporary tree."""
    for name in ("Pipeline", "Stages", "Installer", "update"):
        target = tmp_path / name
        if (repo_root / name).exists():
            import shutil
            shutil.copytree(repo_root / name, target)
    for name in ("VERSION", "README.md", "requirements.txt"):
        (tmp_path / name).write_bytes((repo_root / name).read_bytes())

    monkeypatch.setattr(builder, "REPO_ROOT", tmp_path)
    return tmp_path


# --------------------------------------------------------------------------
# the tag
# --------------------------------------------------------------------------

def test_the_tag_carries_the_v_prefix():
    assert builder.release_tag("0.3.0") == "v0.3.0"


def test_download_urls_use_the_same_tag_the_release_is_cut_with():
    """The exact drift that made every 0.2.0 download a 404."""
    version = "1.2.3"
    meta = builder.build_metadata(version, {n: "0" * 64 for n in builder.PLATFORMS})
    tag = builder.release_tag(version)
    for key in ("macos", "windows"):
        assert f"/download/{tag}/" in meta[key]["url"], (
            f"{key} URL is {meta[key]['url']}, which does not use the tag {tag}"
        )


def test_metadata_version_matches_the_build():
    meta = builder.build_metadata("2.0.0", {n: "a" * 64 for n in builder.PLATFORMS})
    assert meta["version"] == "2.0.0"


def test_every_platform_is_listed():
    meta = builder.build_metadata("1.0.0", {n: "b" * 64 for n in builder.PLATFORMS})
    assert set(meta) == {"version", "macos", "windows"}


# --------------------------------------------------------------------------
# reproducibility
# --------------------------------------------------------------------------

def test_building_twice_gives_identical_archives(built):
    """Otherwise the checksum in the feed is only meaningful on one machine."""
    first = builder.sha256(builder.zip_folder(builder.build_folder("eternal2x-mac")))
    second = builder.sha256(builder.zip_folder(builder.build_folder("eternal2x-mac")))
    assert first == second


def test_archive_entries_have_a_fixed_timestamp(built):
    archive = builder.zip_folder(builder.build_folder("eternal2x-mac"))
    with zipfile.ZipFile(archive) as zf:
        stamps = {info.date_time for info in zf.infolist()}
    assert stamps == {builder.ZIP_TIMESTAMP}


def test_windows_and_linux_build_the_same_archive(built, monkeypatch):
    """zipfile marks entries as made on MS-DOS when run on Windows, and the
    deflate output depends on the zlib build (zlib-ng in Windows Python), so
    a Windows build could never match the checksums CI publishes."""
    shas = []
    for platform in ("win32", "linux"):
        monkeypatch.setattr("sys.platform", platform)
        shas.append(builder.sha256(builder.zip_folder(builder.build_folder("eternal2x-win"))))
    assert shas[0] == shas[1]
    with zipfile.ZipFile(built / "release" / "eternal2x-win.zip") as zf:
        assert {i.compress_type for i in zf.infolist()} == {zipfile.ZIP_STORED}


def test_archive_entries_are_sorted_the_same_way_everywhere(built):
    """Case-sensitive byte order, not the platform's path ordering.

    Path comparison is case-insensitive on Windows, so sorting paths rather
    than entry names put Eternal2x.lua on a different side of build_release.py
    depending on the machine, and the archives stopped matching across
    platforms.
    """
    archive = builder.zip_folder(builder.build_folder("eternal2x-win"))
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    assert names == sorted(names), "entries are not in case-sensitive order"
    # An explicit pin: uppercase sorts before lowercase.
    installer = [n for n in names if n.startswith("eternal2x-win/Installer/")]
    assert installer.index("eternal2x-win/Installer/Eternal2x.lua") < \
        installer.index("eternal2x-win/Installer/build_release.py")


# --------------------------------------------------------------------------
# payload
# --------------------------------------------------------------------------

def test_the_payload_carries_what_the_plugin_needs(built):
    folder = builder.build_folder("eternal2x-mac")
    for needed in ("Stages/resolve_smooth.py", "Stages/resolve_probe.py",
                   "Pipeline/dedupe.py", "Pipeline/interpolate.py",
                   "Pipeline/render.py", "Pipeline/resolve_bridge.py",
                   "Installer/Eternal2x.lua", "Installer/Eternal2xLauncher.lua",
                   "Installer/install_eternal2x.py", "VERSION", "requirements.txt"):
        assert (folder / needed).exists(), f"the release is missing {needed}"


def test_the_payload_does_not_ship_tests_or_build_junk(built):
    folder = builder.build_folder("eternal2x-mac")
    shipped = [p.as_posix() for p in
               (item.relative_to(folder) for item in folder.rglob("*") if item.is_file())]
    for path in shipped:
        assert "test" not in path.lower(), f"the release ships {path}"
        assert not path.endswith((".pyc", ".pyo")), f"the release ships {path}"
        assert "__pycache__" not in path


def test_a_rebuild_clears_files_that_left_the_repo(built):
    """A stale module must not linger in the release folder forever."""
    folder = builder.build_folder("eternal2x-mac")
    stale = folder / "Stages" / "gone_module.py"
    stale.write_text("# removed upstream\n")
    assert not (builder.build_folder("eternal2x-mac") / "Stages" / "gone_module.py").exists()


# --------------------------------------------------------------------------
# the feed
# --------------------------------------------------------------------------

def test_the_feed_is_written_without_a_byte_order_mark(built):
    meta = builder.build_metadata("1.0.0", {n: "c" * 64 for n in builder.PLATFORMS})
    path = builder.write_metadata(meta)
    assert path.read_bytes()[:3] != b"\xef\xbb\xbf"


def test_the_written_feed_parses_the_way_the_updater_reads_it(built):
    """The updater decodes straight off the network, so this has to be plain."""
    from Stages.resolve_update import _parse_version

    meta = builder.build_metadata("1.4.0", {n: "d" * 64 for n in builder.PLATFORMS})
    raw = builder.write_metadata(meta).read_bytes().decode("utf-8")
    loaded = json.loads(raw)
    assert _parse_version(loaded["version"]) == (1, 4, 0)


def test_checksums_in_the_feed_describe_the_archives(built):
    checksums = {}
    for name in builder.PLATFORMS:
        checksums[name] = builder.sha256(builder.zip_folder(builder.build_folder(name)))
    meta = builder.build_metadata(builder.read_version(), checksums)

    for name, key in builder.PLATFORMS.items():
        archive = built / "release" / f"{name}.zip"
        assert meta[key]["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_a_plain_build_leaves_the_feed_alone(built, monkeypatch):
    """The feed describes the published release, not the working tree.

    After 0.3.1 a shipped file was deleted and a rebuild rewrote the feed, so
    it advertised checksums for archives nobody had uploaded and every update
    download failed verification.
    """
    feed = built / "update" / "latest.json"
    before = feed.read_bytes()
    (built / "Installer" / "extra_tool.py").write_text("# changes the archives\n")
    monkeypatch.setattr("sys.argv", ["build_release.py"])
    assert builder.main() == 0
    assert feed.read_bytes() == before


def test_publish_never_replaces_files_on_an_existing_release(built, monkeypatch):
    """Users already verify against those bytes, so --clobber is off limits."""
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(builder.subprocess, "run", fake_run)
    assert builder.publish("0.3.1") is True
    assert calls[0][:3] == ["gh", "release", "view"]
    uploads = calls[1:]
    assert uploads and all(c[:3] == ["gh", "release", "upload"] for c in uploads)
    assert not any("--clobber" in c for c in uploads)


def test_verify_reports_an_unreachable_download(built, capsys):
    meta = {
        "version": "9.9.9",
        "macos": {"url": "http://127.0.0.1:1/nope.zip", "sha256": "e" * 64},
        "windows": {"url": "http://127.0.0.1:1/nope.zip", "sha256": "e" * 64},
    }
    assert builder.verify_feed(meta) is False
    assert "FAILED" in capsys.readouterr().out
