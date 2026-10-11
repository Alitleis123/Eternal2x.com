"""Auto-updater: version comparison, download, checksum, and payload application.

Served over a real local HTTP server so the network path is genuinely exercised.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import functools
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


def make_payload_zip(www: Path, name: str, version: str, extra_files=None) -> str:
    """Build an update zip shaped like a real release and return its sha256."""
    zip_path = www / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("eternal2x/VERSION", version + "\n")
        zf.writestr("eternal2x/README.md", f"# Eternal2x {version}\n")
        zf.writestr("eternal2x/Pipeline/config.py", "# updated config\n")
        zf.writestr("eternal2x/Stages/motion_score.py", "# updated stage\n")
        zf.writestr("eternal2x/Installer/install_eternal2x.py", "# updated installer\n")
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
    }))
    assert run(["--meta-url", url, "--repo-root", str(repo)]) == 0
    assert repo.joinpath("Stages/new.py").read_text() == "# new\n"
    assert repo.joinpath("VERSION").read_text().strip() == "9.9.9"
    assert not any(p.name.startswith(".eternal2x_update_") for p in repo.iterdir())
