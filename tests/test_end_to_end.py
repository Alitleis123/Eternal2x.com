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

from Installer.install_eternal2x import write_config  # noqa: E402

HARNESS = Path(__file__).resolve().parent / "lua_harness.lua"


@pytest.fixture
def installation(tmp_path, repo_root, make_anime):
    """A real install, a real clip, and a panel loaded through the launcher."""
    plugin = tmp_path / "Eternal2x"
    for name in ("Installer", "Stages", "Pipeline"):
        shutil.copytree(repo_root / name, plugin / name)
    shutil.copy2(repo_root / "VERSION", plugin / "VERSION")

    comp = tmp_path / "Comp"
    comp.mkdir()
    shutil.copy2(plugin / "Installer" / "Eternal2xLauncher.lua", comp / "Eternal2x.lua")
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
        H.load(str(comp / "Eternal2x.lua"), str(src))
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
    log = root / ".eternal2x_last_run.log"
    detail = log.read_text(errors="replace")[-2000:] if log.exists() else "(no log written)"
    raise AssertionError(
        f"the render did not finish within {timeout}s.\n"
        f"last status: {last}\n"
        f"command: {H.last_command()}\n"
        f"log tail:\n{detail}"
    )


def outputs(src: Path):
    folder = src.parent / "Eternal2x"
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

    summary = H.widgets["AnalysisLabel"].Text
    assert "12 unique drawings" in summary, f"panel showed: {summary!r}"
    assert "on 2s" in summary
    assert "Analysis complete" in H.status()


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
    assert "48 smooth frames" in status or "24 smooth frames" in status
    assert "added to the timeline" in status

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
    assert len(frames) == 24, "the rebuild changed the clip length"

    # The stepped motion should be gone from everything but the held tail.
    duplicates = sum(1 for i in range(1, len(frames) - 2)
                     if np.array_equal(frames[i], frames[i - 1]))
    assert duplicates == 0, f"{duplicates} held frames survived"


def test_the_panel_hands_resolve_a_path_it_can_import(installation):
    H, src = installation()
    H.click("SmoothBtn")
    finish(H)

    imported = Path(H.imported_at(1))
    assert imported.is_file(), f"Resolve was given {imported}, which is not a file"
    assert cv2.imread(str(imported)) is not None, "the imported frame is not readable"


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


def test_forcing_a_hold_pattern_reaches_the_render(installation):
    H, _src = installation(holds=(2,) * 12)
    H.set_combo("HoldCombo", 2)          # On 3s
    H.click("AnalyseBtn")
    finish(H)
    assert "on 3s" in H.widgets["AnalysisLabel"].Text


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
    assert "added to the timeline" in H.status()
    assert len(read_frames(rendered_output(src))) == 24
