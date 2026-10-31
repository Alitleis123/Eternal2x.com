"""The whole journey: install, open the panel, click the buttons, get a result.

Unlike the other panel tests, this one really runs the commands the panel
builds. The Lua constructs a shell command, the shell runs Python, Python
writes a status file and a rendered clip, and the panel reads it all back.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

pytest.importorskip("lupa", reason="lupa provides the Lua runtime")
from lupa import LuaRuntime  # noqa: E402

from Installer.install_upflow2x import write_config  # noqa: E402
from tests import fake_rife  # noqa: E402

HARNESS = Path(__file__).resolve().parent / "lua_harness.lua"


@pytest.fixture
def installation(tmp_path, repo_root, make_anime):
    """A real install, a real clip, and a panel loaded through the launcher."""
    return install(tmp_path, repo_root, make_anime, "Eternal2x")


def install(tmp_path, repo_root, make_anime, folder):
    plugin = tmp_path / folder
    for name in ("Installer", "Stages", "Pipeline"):
        shutil.copytree(repo_root / name, plugin / name)
    shutil.copy2(repo_root / "VERSION", plugin / "VERSION")
    # The stage runs in its own Python here, where the network would download
    # its model. A blend stands in for it, as in the other tests, and the
    # upscaler finds no GPU.
    (plugin / "Pipeline" / "rife.py").rename(plugin / "Pipeline" / "_rife_real.py")
    (plugin / "Pipeline" / "rife.py").write_text(fake_rife.MODULE, encoding="utf-8")
    (plugin / "Pipeline" / "upscaler.py").write_text(fake_rife.UPSCALER_MODULE, encoding="utf-8")

    comp = tmp_path / "Comp"
    comp.mkdir()
    shutil.copy2(plugin / "Installer" / "Eternal2xLauncher.lua", comp / "Upflow2x.lua")
    write_config(comp, plugin, sys.executable)

    def _open(holds=(2,) * 12, clip_name="scene 01.avi"):
        # Keep the media well away from the plugin folder: the default output
        # directory is an Eternal2x folder beside the source.
        media = tmp_path / "media"
        media.mkdir(exist_ok=True)
        src = media / clip_name
        shutil.move(str(make_anime(name=clip_name, holds=holds)), src)

        lua = LuaRuntime(unpack_returned_tuples=True)
        # Forward slashes: a Windows path interpolated into Lua source turns
        # backslashes into escape sequences, and "D:\a\..." fails to parse.
        # Lua accepts forward slashes on Windows.
        H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
        H.real_execute = True
        H.load(str(comp / "Upflow2x.lua"), str(src))
        H.plugin_root = str(plugin)
        return H, src

    return _open


# Generous, because a shared CI runner can be very slow and the cost of
# waiting is nothing next to the cost of a flaky failure.
FINISH_TIMEOUT = 300.0


def finish(H, timeout: float = FINISH_TIMEOUT):
    """Wait for the background render, then let the panel pick up the result."""
    root = Path(H.plugin_root)
    status = root / ".eternal2x_status.json"
    deadline = time.time() + timeout
    last = None

    while time.time() < deadline:
        try:
            last = json.loads(status.read_text())
        except (OSError, ValueError):
            last = last
        else:
            if last.get("done"):
                H.click("RefreshStatusBtn")
                return
        time.sleep(0.05)

    # Say what actually happened, rather than just that time ran out.
    log = root / ".upflow2x_last_run.log"
    detail = log.read_text(errors="replace")[-2000:] if log.exists() else "(no log written)"
    raise AssertionError(
        f"the render did not finish within {timeout}s.\n"
        f"last status: {last}\n"
        f"command: {H.last_command()}\n"
        f"log tail:\n{detail}"
    )


def outputs(src: Path):
    folder = src.parent / "Upflow2x"
    return sorted(folder.iterdir()) if folder.exists() else []


def rendered_output(src: Path) -> Path:
    return outputs(src)[0]


# --------------------------------------------------------------------------

def test_the_panel_opens_with_the_selected_clip(installation):
    H, src = installation()
    assert src.name in H.widgets["SourceLabel"].Text
    assert "Ready" in H.status()


def test_analyse_reports_the_real_pattern(installation):
    """Click Analyse, run the real command, read the real result back."""
    H, _src = installation(holds=(2,) * 12)
    H.click("AnalyseBtn")
    finish(H)

    assert H.widgets["AnalysisLabel"].Text == "Placed 12 markers. Animated on 2s."
    # The whole report is in the line's tip.
    summary = H.widgets["AnalysisLabel"].ToolTip
    assert "12 unique drawings" in summary, f"panel showed: {summary!r}"
    assert "on 2s" in summary
    # The drawings it found are marked on the clip, ready to edit.
    assert "Marked 12 frames on the clip" in summary
    assert H.marker_frames() == ",".join(str(f) for f in range(0, 24, 2))
    assert "press Render" in H.status()


def test_analyse_detects_threes(installation):
    H, _src = installation(holds=(3,) * 8)
    H.click("AnalyseBtn")
    finish(H)
    assert "on 3s" in H.widgets["AnalysisLabel"].Text


def test_analyse_changes_nothing_on_disk(installation):
    H, src = installation()
    H.click("AnalyseBtn")
    finish(H)
    assert outputs(src) == []
    assert H.imported_count() == 0


def test_footage_with_nothing_to_do_is_reported_in_the_panel(installation):
    H, _src = installation(holds=(1,) * 20)
    H.click("AnalyseBtn")
    finish(H)
    assert "already on 1s" in H.widgets["AnalysisLabel"].Text


def test_smooth_renders_imports_and_reports(installation):
    H, src = installation(holds=(2,) * 12)
    H.click("SmoothBtn")
    finish(H)

    status = H.status()
    assert "12 drawings" in status, f"panel showed: {status!r}"
    assert "72 smooth frames" in status, "the render is not three times the clip"
    assert "above the original" in status

    assert H.imported_count() == 1
    assert H.appended == 1
    assert H.property_of("Super Scale") == "2"
    assert H.enabled("SmoothBtn"), "the panel never came back from busy"


def test_the_rendered_clip_is_real_and_correct(installation, read_frames):
    H, src = installation(holds=(2,) * 12)
    H.click("SmoothBtn")
    finish(H)

    out = rendered_output(src)
    frames = read_frames(out)
    assert len(frames) == 72, "the render is not three times the clip"

    # The stepped motion should be gone from everything but the held tail,
    # the last drawing's 2 frames made 6.
    duplicates = sum(1 for i in range(1, len(frames) - 5)
                     if np.array_equal(frames[i], frames[i - 1]))
    assert duplicates == 0, f"{duplicates} held frames survived"


def test_the_panel_hands_resolve_a_path_it_can_import(installation):
    H, src = installation()
    H.click("SmoothBtn")
    finish(H)

    # A sequence goes in as Resolve's clipInfo: a printf pattern and a range.
    assert H.sequence_import_count() == 1
    pattern = H.last_sequence_field("FilePath")
    first, last = H.last_sequence_field("StartIndex"), H.last_sequence_field("EndIndex")
    assert last - first + 1 == 72, "the range does not cover the whole render"
    for index in (first, last):
        frame = Path(pattern % index)
        assert frame.is_file(), f"Resolve was given {pattern}, but {frame.name} is missing"
        assert cv2.imread(str(frame)) is not None, f"{frame.name} is not readable"
    assert not Path(pattern % (last + 1)).exists(), "the range stops short of the last frame"


def test_progress_reaches_the_end(installation):
    H, _src = installation()
    H.click("SmoothBtn")
    finish(H)
    assert "100%" in H.widgets["Progress"].Text


def test_settings_chosen_in_the_panel_reach_the_render(installation, read_frames):
    """Pick MP4 and no upscale, and check that is what comes out."""
    H, src = installation()
    H.set_combo("FormatCombo", 1)        # MP4
    H.set_checkbox("UpscaleCB", False)
    H.click("SmoothBtn")
    finish(H)

    out = rendered_output(src)
    assert out.suffix == ".mp4", f"got {out.name}"
    assert H.property_of("Super Scale") is None, "Super Scale was applied anyway"


def test_a_second_run_does_not_overwrite_the_first(installation):
    # Two full renders, so keep the clip short.
    H, src = installation(holds=(2,) * 4)
    H.click("SmoothBtn")
    finish(H)
    H.click("SmoothBtn")
    finish(H)
    assert len(outputs(src)) == 2


def test_a_clip_path_with_spaces_survives_the_shell(installation, read_frames):
    H, src = installation(clip_name="my shot 01.avi")
    H.click("SmoothBtn")
    finish(H)
    assert "above the original" in H.status()
    assert len(read_frames(rendered_output(src))) == 72


def test_a_python_that_cannot_start_is_reported_not_waited_on(installation, tmp_path):
    """No status file is ever written, so only the exit code says it is over."""
    H, _src = installation()
    conf = Path(H.plugin_root).parent / "Comp" / "Eternal2x.conf"
    missing = tmp_path / "no such python" / ("python.exe" if sys.platform.startswith("win")
                                              else "python3")
    conf.write_text(conf.read_text(encoding="utf-8").replace(
        f"python={sys.executable}", f"python={missing}"), encoding="utf-8")
    H, _src = installation(clip_name="second.avi")
    H.click("SmoothBtn")

    exit_file = Path(H.plugin_root) / ".eternal2x_exit"
    deadline = time.time() + 60
    while not exit_file.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert exit_file.exists(), "the shell never recorded how the run ended"
    time.sleep(0.2)  # let the shell finish writing it
    H.click("RefreshStatusBtn")
    assert "Stopped without finishing" in H.status(), H.status()
    assert H.enabled("SmoothBtn"), "the panel stayed busy"


def test_a_trimmed_timeline_clip_is_rebuilt_from_just_its_range(installation, read_frames):
    """The panel reads the clip's in and out points and Python renders only those."""
    H, src = installation(holds=(2,) * 12)
    H.set_source_range(0.25, 0.75)       # frames 6 to 17 of a 24 fps file
    H.click("SmoothBtn")
    finish(H)
    assert len(read_frames(rendered_output(src))) == 36
    assert H.last_sequence_field("EndIndex") == 35


def test_a_plugin_folder_with_shell_characters_still_runs(tmp_path, repo_root, make_anime,
                                                       read_frames):
    """cmd strips the quotes around a script path holding &, and splits there.
    `call` would fix that but doubles a ^ in the path."""
    H, src = install(tmp_path, repo_root, make_anime, "VFX & Tools ^1/Eternal2x")(
        holds=(2,) * 6)
    H.click("SmoothBtn")
    finish(H)
    assert len(read_frames(rendered_output(src))) == 36
