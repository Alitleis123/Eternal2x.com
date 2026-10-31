"""Auto-updater: version comparison, download, checksum, and payload application.

Served over a real local HTTP server so the network path is genuinely exercised.
"""

from __future__ import annotations

import errno
import hashlib
import http.server
import json
import functools
import shutil
import socketserver
import threading
import zipfile
from pathlib import Path

import pytest

from Stages import resolve_update as ru


# --------------------------------------------------------------------------
# local http server
# --------------------------------------------------------------------------

@pytest.fixture
def http_server(tmp_path):
    """Serve `tmp_path/www` and return (base_url, www_dir)."""
    www = tmp_path / "www"
    www.mkdir()

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(www))

    class Quiet(socketserver.TCPServer):
        allow_reuse_address = True

    httpd = Quiet(("127.0.0.1", 0), handler)
    httpd.RequestHandlerClass.log_message = lambda *a, **k: None
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}", www
    finally:
        httpd.shutdown()
        httpd.server_close()


# Stand-ins for the files the updater refuses to go without.
RUNTIME = {rel: "# stub\n" for rel in ru.REQUIRED_FILES if rel != "VERSION"}


def make_payload_zip(www: Path, name: str, version: str, extra_files=None) -> str:
    """Build an update zip shaped like a real release and return its sha256."""
    zip_path = www / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        for rel, content in RUNTIME.items():
            zf.writestr(f"eternal2x/{rel}", content)
        zf.writestr("eternal2x/VERSION", version + "\n")
        zf.writestr("eternal2x/README.md", f"# Eternal2x {version}\n")
        zf.writestr("eternal2x/Pipeline/config.py", "# updated config\n")
        zf.writestr("eternal2x/Stages/motion_score.py", "# updated stage\n")
        zf.writestr("eternal2x/Installer/install_upflow2x.py", "# updated installer\n")
        for rel, content in (extra_files or {}).items():
            zf.writestr(f"eternal2x/{rel}", content)
    return hashlib.sha256(zip_path.read_bytes()).hexdigest()


def write_meta(www: Path, version: str, sha: str, zip_name: str, *, bom: bool = False):
    meta = {
        "version": version,
        "windows": {"url": f"URLBASE/{zip_name}", "sha256": sha},
        "macos": {"url": f"URLBASE/{zip_name}", "sha256": sha},
        "linux": {"url": f"URLBASE/{zip_name}", "sha256": sha},
    }
    return meta


def publish_meta(www: Path, base_url: str, meta: dict, *, bom: bool = False,
                 name: str = "latest.json"):
    text = json.dumps(meta, indent=2).replace("URLBASE", base_url)
    data = text.encode("utf-8")
    if bom:
        data = b"\xef\xbb\xbf" + data
    (www / name).write_bytes(data)
    return f"{base_url}/{name}"


@pytest.fixture
def repo(tmp_path):
    """A minimal installed copy of the plugin to update in place."""
    root = tmp_path / "installed"
    (root / "Stages").mkdir(parents=True)
    (root / "Pipeline").mkdir(parents=True)
    (root / "Installer").mkdir(parents=True)
    (root / "VERSION").write_text("0.2.0\n")
    (root / "README.md").write_text("# old\n")
    (root / "Stages" / "motion_score.py").write_text("# old stage\n")
    return root


def run(argv):
    import sys
    from unittest import mock
    with mock.patch.object(sys, "argv", ["resolve_update"] + argv):
        return ru.main()


# --------------------------------------------------------------------------
# version parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1.2.3", (1, 2, 3)),
    ("v1.2.3", (1, 2, 3)),
    ("0.2.0", (0, 2, 0)),
    ("1.2", (1, 2, 0)),
    ("1", (1, 0, 0)),
    ("", (0, 0, 0)),
    ("  1.4.9  ", (1, 4, 9)),
    ("1.2.3.4", (1, 2, 3)),
])
def test_parse_version(text, expected):
    assert ru._parse_version(text) == expected


def test_parse_version_orders_correctly():
    assert ru._parse_version("0.2.0") < ru._parse_version("0.10.0")
    assert ru._parse_version("0.9.9") < ru._parse_version("1.0.0")


def test_parse_version_survives_a_byte_order_mark():
    """VERSION files written by PowerShell start with a UTF-8 BOM.

    `_read_version` strips whitespace, but a BOM is not whitespace, so the first
    component fails to parse and silently becomes 0 -- pinning the major version
    at zero forever.
    """
    assert ru._parse_version("﻿1.2.3") == (1, 2, 3), (
        "a BOM in VERSION makes the major version parse as 0, so 1.x installs "
        "read as 0.x and re-download every release"
    )


def test_read_version_strips_a_byte_order_mark(tmp_path):
    (tmp_path / "VERSION").write_bytes(b"\xef\xbb\xbf0.2.0\n")
    assert ru._read_version(tmp_path) == "0.2.0"


def test_read_version_missing_file(tmp_path):
    assert ru._read_version(tmp_path) == "0.0.0"


# --------------------------------------------------------------------------
# metadata download
# --------------------------------------------------------------------------

def test_download_json_round_trip(http_server):
    base, www = http_server
    url = publish_meta(www, base, {"version": "9.9.9"})
    assert ru._download_json(url, timeout=5) == {"version": "9.9.9"}


def test_download_json_handles_a_byte_order_mark(http_server):
    """The published update/latest.json in this repo begins with a UTF-8 BOM.

    `_download_json` decodes as plain utf-8, leaving the BOM in the string, and
    `json.loads` rejects it. In `main()` that lands in a bare `except`, so the
    updater reports "Update check skipped" and never updates anything.
    """
    base, www = http_server
    url = publish_meta(www, base, {"version": "9.9.9"}, bom=True)
    assert ru._download_json(url, timeout=5) == {"version": "9.9.9"}, (
        "a BOM in latest.json makes every update check fail silently"
    )


def test_download_json_rejects_a_json_array(http_server):
    base, www = http_server
    (www / "arr.json").write_text("[1, 2, 3]")
    with pytest.raises(RuntimeError, match="must be a JSON object"):
        ru._download_json(f"{base}/arr.json", timeout=5)


# --------------------------------------------------------------------------
# checksums and payload shape
# --------------------------------------------------------------------------

def test_sha256_matches_hashlib(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"eternal2x" * 5000)
    assert ru._sha256(f) == hashlib.sha256(f.read_bytes()).hexdigest()


def test_find_payload_root_unwraps_a_single_top_level_folder(tmp_path):
    (tmp_path / "eternal2x-mac").mkdir()
    (tmp_path / "eternal2x-mac" / "VERSION").write_text("1.0.0")
    assert ru._find_payload_root(tmp_path).name == "eternal2x-mac"


def test_find_payload_root_uses_the_dir_itself_when_flat(tmp_path):
    (tmp_path / "VERSION").write_text("1.0.0")
    (tmp_path / "Stages").mkdir()
    assert ru._find_payload_root(tmp_path) == tmp_path


def test_apply_payload_only_touches_allowed_paths(tmp_path):
    payload = tmp_path / "payload"
    (payload / "Stages").mkdir(parents=True)
    (payload / "Stages" / "x.py").write_text("new")
    (payload / "VERSION").write_text("1.0.0")
    (payload / "secrets.env").write_text("SHOULD NOT BE COPIED")

    dest = tmp_path / "dest"
    dest.mkdir()
    ru._apply_payload(payload, dest)

    assert (dest / "Stages" / "x.py").read_text() == "new"
    assert not (dest / "secrets.env").exists(), "payload escaped the allow-list"


def _plugin(root: Path, tag: str) -> Path:
    for folder in ("Installer", "Pipeline", "Stages"):
        (root / folder).mkdir(parents=True)
        (root / folder / "code.py").write_text(tag)
    return root


def _locked(monkeypatch, path: Path):
    """Make every copy onto `path` fail, like a file another program holds."""
    real = shutil.copy2

    def copy2(src, dst, *args, **kwargs):
        if Path(dst) == path:
            raise PermissionError(errno.EACCES, "Permission denied", str(dst))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(ru.shutil, "copy2", copy2)


def test_a_failed_update_puts_every_file_back(tmp_path, monkeypatch):
    """A write refused halfway used to leave Installer new and Pipeline old."""
    live = _plugin(tmp_path / "live", "old")
    payload = _plugin(tmp_path / "payload", "new")
    (payload / "Installer" / "added.py").write_text("new")
    _locked(monkeypatch, live / "Pipeline" / "code.py")

    with pytest.raises(PermissionError):
        ru._apply_payload(payload, live)

    for folder in ("Installer", "Pipeline", "Stages"):
        assert (live / folder / "code.py").read_text() == "old", f"{folder} kept the new code"
    assert not (live / "Installer" / "added.py").exists(), "a file the update added stayed"
    assert sorted(p.name for p in live.iterdir()) == ["Installer", "Pipeline", "Stages"]


def test_a_disk_that_fills_while_staging_changes_nothing(tmp_path, monkeypatch):
    live = _plugin(tmp_path / "live", "old")
    payload = _plugin(tmp_path / "payload", "new")
    real = shutil.copy2

    def copy2(src, dst, *args, **kwargs):
        dst = Path(dst)
        if dst.parts[-3].startswith(".eternal2x_update_") and dst.parent.name == "Stages":
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(ru.shutil, "copy2", copy2)
    with pytest.raises(OSError):
        ru._apply_payload(payload, live)

    for folder in ("Installer", "Pipeline", "Stages"):
        assert (live / folder / "code.py").read_text() == "old"
    assert sorted(p.name for p in live.iterdir()) == ["Installer", "Pipeline", "Stages"]


def test_an_update_that_cannot_be_undone_keeps_the_old_copies(tmp_path, monkeypatch):
    """If putting a file back fails too, say which, and keep its old copy."""
    live = _plugin(tmp_path / "live", "old")
    payload = _plugin(tmp_path / "payload", "new")
    real = shutil.copy2
    failed = []

    def copy2(src, dst, *args, **kwargs):
        dst = Path(dst)
        if dst == live / "Pipeline" / "code.py":
            failed.append(dst)
            raise PermissionError(errno.EACCES, "Permission denied", str(dst))
        if failed and dst == live / "Installer" / "code.py":
            raise PermissionError(errno.EACCES, "Permission denied", str(dst))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(ru.shutil, "copy2", copy2)
    with pytest.raises(ru.RollbackError) as info:
        ru._apply_payload(payload, live)

    message = str(info.value)
    assert "Installer/code.py" in message
    kept = [p for p in live.glob(".*/**/Installer/code.py") if p.read_text() == "old"]
    assert len(kept) == 1, "the old copy of the file that could not be put back is gone"
    assert str(kept[0].parents[1]) in message


# --------------------------------------------------------------------------
# end to end
# --------------------------------------------------------------------------

def test_no_update_when_already_current(http_server, repo, capsys):
    base, www = http_server
    sha = make_payload_zip(www, "pkg.zip", "0.2.0")
    url = publish_meta(www, base, write_meta(www, "0.2.0", sha, "pkg.zip"))

    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 0
    assert "up to date" in capsys.readouterr().out
    assert repo.joinpath("VERSION").read_text().strip() == "0.2.0"


def test_no_update_when_remote_is_older(http_server, repo, capsys):
    base, www = http_server
    sha = make_payload_zip(www, "pkg.zip", "0.1.0")
    url = publish_meta(www, base, write_meta(www, "0.1.0", sha, "pkg.zip"))

    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 0
    assert repo.joinpath("VERSION").read_text().strip() == "0.2.0"


def test_applies_a_newer_update(http_server, repo, capsys):
    base, www = http_server
    sha = make_payload_zip(www, "pkg.zip", "0.3.0")
    url = publish_meta(www, base, write_meta(www, "0.3.0", sha, "pkg.zip"))

    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 0

    out = capsys.readouterr().out
    assert "0.2.0 -> 0.3.0" in out
    assert repo.joinpath("VERSION").read_text().strip() == "0.3.0"
    assert repo.joinpath("Stages/motion_score.py").read_text() == "# updated stage\n"
    assert "Restart Resolve" in out


def test_checksum_mismatch_aborts_without_touching_the_install(http_server, repo, capsys):
    base, www = http_server
    make_payload_zip(www, "pkg.zip", "0.3.0")
    bad_sha = "0" * 64
    url = publish_meta(www, base, write_meta(www, "0.3.0", bad_sha, "pkg.zip"))

    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 1
    assert "checksum mismatch" in capsys.readouterr().out
    assert repo.joinpath("VERSION").read_text().strip() == "0.2.0", "install was modified"
    assert repo.joinpath("Stages/motion_score.py").read_text() == "# old stage\n"


def test_an_update_that_fails_partway_says_nothing_changed(http_server, repo, monkeypatch, capsys):
    """The panel shows the last line, so it has to say the old version is intact."""
    base, www = http_server
    sha = make_payload_zip(www, "pkg.zip", "0.3.0")
    url = publish_meta(www, base, write_meta(www, "0.3.0", sha, "pkg.zip"))
    _locked(monkeypatch, repo.resolve() / "Pipeline" / "config.py")

    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 1

    assert "nothing was changed" in capsys.readouterr().out.strip().splitlines()[-1]
    assert repo.joinpath("VERSION").read_text().strip() == "0.2.0"
    assert repo.joinpath("README.md").read_text() == "# old\n"
    assert not repo.joinpath("Installer/install_upflow2x.py").exists()


def test_missing_platform_package_is_reported(http_server, repo, capsys):
    base, www = http_server
    url = publish_meta(www, base, {"version": "0.3.0"})
    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 1
    assert "No update package listed" in capsys.readouterr().out


def test_metadata_without_a_version_is_rejected(http_server, repo, capsys):
    base, www = http_server
    url = publish_meta(www, base, {"macos": {"url": "x", "sha256": ""}})
    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 1
    assert "missing version" in capsys.readouterr().out


def test_unreachable_server_does_not_crash_resolve(repo, capsys):
    """A failed update check must never take the plugin down."""
    assert run(["--meta-url", "http://127.0.0.1:1/latest.json",
                "--repo-root", str(repo)]) == 0
    assert "skipped" in capsys.readouterr().out


def test_auto_mode_stays_quiet(repo, capsys):
    assert run(["--meta-url", "http://127.0.0.1:1/latest.json",
                "--repo-root", str(repo), "--auto"]) == 0
    assert "Auto update check skipped." in capsys.readouterr().out


def test_update_preserves_unrelated_user_files(http_server, repo):
    base, www = http_server
    (repo / "my_notes.txt").write_text("keep me")
    sha = make_payload_zip(www, "pkg.zip", "0.3.0")
    url = publish_meta(www, base, write_meta(www, "0.3.0", sha, "pkg.zip"))

    run(["--meta-url", url, "--repo-root", str(repo)])
    assert (repo / "my_notes.txt").read_text() == "keep me"


def test_repeated_update_is_idempotent(http_server, repo, capsys):
    """Running the check twice must not re-download or churn the install."""
    base, www = http_server
    sha = make_payload_zip(www, "pkg.zip", "0.3.0")
    url = publish_meta(www, base, write_meta(www, "0.3.0", sha, "pkg.zip"))

    run(["--meta-url", url, "--repo-root", str(repo)])
    capsys.readouterr()
    run(["--meta-url", url, "--repo-root", str(repo)])
    assert "up to date" in capsys.readouterr().out


def test_platform_key_is_one_of_the_published_keys():
    assert ru._detect_platform_key() in {"windows", "macos", "linux"}


def test_a_download_that_404s_does_not_traceback(http_server, repo, capsys):
    """latest.json can advertise a release before the files are uploaded."""
    base, www = http_server
    url = publish_meta(www, base, {
        "version": "9.9.9",
        "windows": {"url": f"{base}/missing.zip", "sha256": "0" * 64},
        "macos": {"url": f"{base}/missing.zip", "sha256": "0" * 64},
        "linux": {"url": f"{base}/missing.zip", "sha256": "0" * 64},
    })

    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 1
    assert "Could not download the update" in capsys.readouterr().out
    assert repo.joinpath("VERSION").read_text().strip() == "0.2.0"


def test_a_corrupt_package_is_reported(http_server, repo, capsys):
    base, www = http_server
    (www / "broken.zip").write_bytes(b"this is not a zip file")
    sha = hashlib.sha256(b"this is not a zip file").hexdigest()
    url = publish_meta(www, base, {
        "version": "9.9.9",
        "windows": {"url": f"{base}/broken.zip", "sha256": sha},
        "macos": {"url": f"{base}/broken.zip", "sha256": sha},
        "linux": {"url": f"{base}/broken.zip", "sha256": sha},
    })

    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 1
    assert "could not be opened" in capsys.readouterr().out
    assert repo.joinpath("Stages/motion_score.py").read_text() == "# old stage\n"


def _feed_for(www, base, zip_bytes, sha=None):
    (www / "pkg.zip").write_bytes(zip_bytes)
    digest = hashlib.sha256(zip_bytes).hexdigest() if sha is None else sha
    blob = {"url": f"{base}/pkg.zip", "sha256": digest}
    return publish_meta(www, base, {"version": "9.9.9", "windows": blob,
                                    "macos": blob, "linux": blob})


def _zip_of(entries):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_an_update_without_a_checksum_is_refused(http_server, repo, capsys):
    """The README promises every update is verified, so no checksum, no update."""
    base, www = http_server
    url = _feed_for(www, base, _zip_of({"pkg/Stages/new.py": "# new\n"}), sha="")
    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 1
    assert "no checksum" in capsys.readouterr().out
    assert not repo.joinpath("Stages/new.py").exists()
    assert repo.joinpath("VERSION").read_text().strip() == "0.2.0"


def test_a_package_with_nothing_to_install_leaves_the_version_alone(http_server, repo, capsys):
    """Bumping VERSION after copying nothing would hide the update forever."""
    base, www = http_server
    url = _feed_for(www, base, _zip_of({"a/readme.txt": "x", "b/other.txt": "y"}))
    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 1
    assert repo.joinpath("VERSION").read_text().strip() == "0.2.0"


def test_a_finder_zip_with_a_macosx_folder_still_installs(http_server, repo):
    base, www = http_server
    url = _feed_for(www, base, _zip_of({
        "__MACOSX/eternal2x-mac/._README.md": "junk",
        "eternal2x-mac/Stages/new.py": "# new\n",
        "eternal2x-mac/VERSION": "9.9.9\n",
        **{f"eternal2x-mac/{rel}": text for rel, text in RUNTIME.items()},
    }))
    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 0
    assert repo.joinpath("Stages/new.py").read_text() == "# new\n"
    assert repo.joinpath("VERSION").read_text().strip() == "9.9.9"
    assert not any(p.name.startswith(".eternal2x_update_") for p in repo.iterdir())


def test_a_package_without_the_runtime_files_is_refused(http_server, repo, capsys):
    """A checksum proves the archive is the one the feed named, not that it is whole.

    A README-only archive used to report success and move VERSION on while
    every line of code stayed old, so the real update was never offered again.
    """
    base, www = http_server
    url = _feed_for(www, base, _zip_of({"plugin/README.md": "readme only"}))

    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 1

    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert "missing" in last and "VERSION" in last
    assert repo.joinpath("VERSION").read_text().strip() == "0.2.0"
    assert repo.joinpath("README.md").read_text() == "# old\n"


def test_a_package_missing_a_module_its_code_imports_is_refused(http_server, repo, capsys):
    base, www = http_server
    url = _feed_for(www, base, _zip_of({
        **{f"pkg/{rel}": text for rel, text in RUNTIME.items()},
        "pkg/VERSION": "9.9.9\n",
        "pkg/Stages/resolve_smooth.py": "from Pipeline import render\n",
    }))

    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 1

    assert "Pipeline/render.py" in capsys.readouterr().out.strip().splitlines()[-1]
    assert repo.joinpath("VERSION").read_text().strip() == "0.2.0"
    assert not repo.joinpath("Stages/resolve_smooth.py").exists()


def test_a_package_this_python_cannot_read_is_refused(http_server, repo, capsys):
    """What code written for a newer Python looks like to an older one."""
    base, www = http_server
    url = _feed_for(www, base, _zip_of({
        **{f"pkg/{rel}": text for rel, text in RUNTIME.items()},
        "pkg/VERSION": "9.9.9\n",
        "pkg/Pipeline/render.py": "def broken(:\n",
    }))

    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 1

    assert "cannot read" in capsys.readouterr().out.strip().splitlines()[-1]
    assert repo.joinpath("VERSION").read_text().strip() == "0.2.0"


def test_a_package_of_another_version_is_refused(http_server, repo, capsys):
    """Installing it would leave VERSION saying one thing and the code another."""
    base, www = http_server
    url = _feed_for(www, base, _zip_of({
        **{f"pkg/{rel}": text for rel, text in RUNTIME.items()},
        "pkg/VERSION": "9.9.8\n",
    }))

    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 1

    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert "9.9.8" in last and "9.9.9" in last
    assert repo.joinpath("VERSION").read_text().strip() == "0.2.0"
    assert not repo.joinpath("Stages/resolve_smooth.py").exists()


def test_a_release_built_from_this_tree_passes_the_check(repo_root, tmp_path):
    """Every updater from this one on judges a release by this same check, so
    whatever this tree builds has to pass it, or users stop getting updates.

    If this fails, change the release rather than the check. Updaters already
    installed keep running the old one.
    """
    from Installer import build_release as builder

    for item in builder.PAYLOAD:
        builder._copy(repo_root / item, tmp_path / item)

    assert ru._check_payload(tmp_path, ru._read_version(repo_root)) == ""
    shipped = {f"Pipeline/{p.name}" for p in (tmp_path / "Pipeline").glob("*.py")}
    assert shipped <= set(ru._required_files(tmp_path)), (
        "a Pipeline module ships that nothing imports, so an update could arrive without it"
    )
