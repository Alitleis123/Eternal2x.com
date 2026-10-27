"""Drive the real panel (Installer/Eternal2x.lua) under a Lua runtime.

These tests load the actual UI script, click the actual buttons, and inspect
the exact shell command it hands to Python. That is the part that normally
needs Resolve open.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("lupa", reason="lupa provides the Lua runtime")
from lupa import LuaRuntime  # noqa: E402

HARNESS = Path(__file__).resolve().parent / "lua_harness.lua"


@pytest.fixture
def lua():
    return LuaRuntime(unpack_returned_tuples=True)


def _plugin_tree(dest: Path, repo_root: Path) -> Path:
    for name in ("Installer", "Stages", "Pipeline"):
        shutil.copytree(repo_root / name, dest / name)
    (dest / "VERSION").write_text("0.3.0\n")
    return dest


@pytest.fixture
def installed(tmp_path, repo_root):
    """A full install: launcher plus config in Comp, plugin tree elsewhere."""
    plugin_root = _plugin_tree(tmp_path / "Eternal2x", repo_root)
    comp = tmp_path / "Comp"
    comp.mkdir()
    shutil.copy2(plugin_root / "Installer" / "Eternal2xLauncher.lua", comp / "Eternal2x.lua")
    (comp / "Eternal2x.conf").write_text(
        f"repo_root={plugin_root}\n"
        f"python={sys.executable}\n"
        "update_url=https://example.com/latest.json\n"
        "auto_update=false\n",
        encoding="utf-8",
    )
    return comp, plugin_root


@pytest.fixture
def direct(tmp_path, repo_root):
    """The UI script with its config beside it, for exercising the panel itself."""
    root = _plugin_tree(tmp_path / "Eternal2x", repo_root)
    (root / "Installer" / "Eternal2x.conf").write_text(
        f"repo_root={root}\n"
        f"python={sys.executable}\n"
        "update_url=https://example.com/latest.json\n"
        "auto_update=false\n"
    )
    return root


def load(lua, script: Path, clip="/media/shot.mov"):
    # Forward slashes: a Windows path interpolated into Lua source turns
    # backslashes into escape sequences, and "D:\a\..." fails to parse.
    # Lua accepts forward slashes on Windows.
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.load(str(script), clip)
    return H


def ui_script(root: Path) -> Path:
    return root / "Installer" / "Eternal2x.lua"


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------

def test_panel_loads(lua, direct):
    assert load(lua, ui_script(direct)).window is not None


def test_every_control_is_present(lua, direct):
    H = load(lua, ui_script(direct))
    ids = set(H.widget_ids().split(","))
    for expected in ("SmoothBtn", "AnalyseBtn", "RefreshBtn", "RefreshStatusBtn",
                     "CancelBtn", "UpdateBtn", "HoldCombo", "FormatCombo",
                     "UpscaleCB", "InterpCB", "GrainCB", "AutoUpdateCB", "Status",
                     "Progress", "SourceLabel", "AnalysisLabel", "CompareBtn", "FolderBtn"):
        assert expected in ids, f"missing {expected}; have {sorted(ids)}"


def test_every_control_has_a_handler(lua, direct):
    H = load(lua, ui_script(direct))
    handlers = set(H.handler_ids().split(","))
    for expected in ("SmoothBtn.Clicked", "AnalyseBtn.Clicked", "RefreshBtn.Clicked",
                     "RefreshStatusBtn.Clicked", "CancelBtn.Clicked",
                     "UpdateBtn.Clicked",
                     "HoldCombo.CurrentIndexChanged", "UpscaleCB.Clicked",
                     "InterpCB.Clicked", "GrainCB.Clicked", "AutoUpdateCB.Clicked", "CompareBtn.Clicked",
                     "FolderBtn.Clicked"):
        assert expected in handlers, f"no handler for {expected}"


def test_there_is_no_quality_choice(lua, direct):
    """Pixel tracking doubled lines and smeared fast characters, and no check
    caught every case, so the network draws every in-between."""
    H = load(lua, ui_script(direct))
    assert "QualityCombo" not in H.widget_ids().split(",")
    H.click("SmoothBtn")
    assert "--quality" not in H.last_command()


def test_output_format_choices_match_the_stage(lua, direct):
    from Stages.resolve_smooth import build_parser
    H = load(lua, ui_script(direct))
    assert H.combo_items("FormatCombo").count(",") == 2

    accepted = None
    for action in build_parser()._actions:
        if "--format" in action.option_strings:
            accepted = set(action.choices)
    H.click("AnalyseBtn")
    chosen = H.last_command().split("--format ")[1].split()[0]
    assert chosen in accepted


def test_the_default_output_format_is_lossless(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    assert "--format png" in H.last_command()


def test_output_format_selection_reaches_the_command(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_combo("FormatCombo", 1)      # MP4
    H.click("AnalyseBtn")
    assert "--format mp4" in H.last_command()


def test_hold_pattern_choices_are_offered(lua, direct):
    H = load(lua, ui_script(direct))
    labels = H.combo_items("HoldCombo").split(",")
    assert labels == ["Auto detect", "On 2s", "On 3s"]


def test_the_version_is_shown(lua, direct):
    H = load(lua, ui_script(direct))
    assert "0.3.0" in H.widgets["SubTitle"].Text


def test_a_byte_order_mark_in_version_does_not_leak_into_the_title(lua, direct):
    (direct / "VERSION").write_bytes(b"\xef\xbb\xbf0.3.0\n")
    H = load(lua, ui_script(direct))
    assert "﻿" not in H.widgets["SubTitle"].Text
    assert "0.3.0" in H.widgets["SubTitle"].Text


# --------------------------------------------------------------------------
# the install chain
# --------------------------------------------------------------------------

def test_launcher_reaches_the_panel(lua, installed):
    comp, _ = installed
    assert load(lua, comp / "Eternal2x.lua").window is not None


@pytest.fixture
def no_real_install(tmp_path, monkeypatch):
    """Point the launcher's fallback away from this machine's real install."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    return home


def test_launcher_reports_a_missing_config(lua, tmp_path, repo_root, no_real_install):
    comp = tmp_path / "Comp"
    comp.mkdir()
    shutil.copy2(repo_root / "Installer" / "Eternal2xLauncher.lua", comp / "Eternal2x.lua")
    H = load(lua, comp / "Eternal2x.lua")
    assert "Missing repo_root" in " ".join(str(v) for v in dict(H.prints).values())


def test_launcher_finds_the_installed_config_when_its_own_path_is_mapped(
        lua, tmp_path, repo_root, installed, no_real_install):
    """Resolve may report the script as Scripts:/... which io.open cannot open."""
    comp, plugin_root = installed
    if sys.platform.startswith("win"):
        utility = (no_real_install / "AppData" / "Roaming" / "Blackmagic Design"
                   / "DaVinci Resolve" / "Support" / "Fusion" / "Scripts" / "Utility")
    elif sys.platform == "darwin":
        utility = (no_real_install / "Library" / "Application Support" / "Blackmagic Design"
                   / "DaVinci Resolve" / "Fusion" / "Scripts" / "Utility")
    else:
        utility = no_real_install / ".local" / "share" / "DaVinciResolve" / "Fusion" / "Scripts" / "Utility"
    utility.mkdir(parents=True)
    shutil.move(str(comp / "Eternal2x.conf"), str(utility / "Eternal2x.conf"))

    H = load(lua, comp / "Eternal2x.lua")
    assert H.window is not None, "the launcher did not fall back to the installed config"
    H.click("AnalyseBtn")
    assert f"cd '{plugin_root}'" in H.last_command() or f'"{plugin_root}"' in H.last_command()


def test_buttons_run_from_the_folder_that_holds_the_code(lua, installed):
    """The whole install chain: config in Comp, code elsewhere, cd to the code."""
    comp, plugin_root = installed
    H = load(lua, comp / "Eternal2x.lua")
    H.click("AnalyseBtn")

    cmd = H.last_command()
    assert cmd is not None, "clicking Analyse ran nothing"
    assert f"cd '{plugin_root}'" in cmd or f'"{plugin_root}"' in cmd, (
        f"ran: {cmd}\nexpected a cd into {plugin_root}")


def test_the_configured_interpreter_is_used(lua, installed):
    comp, _ = installed
    H = load(lua, comp / "Eternal2x.lua")
    H.click("AnalyseBtn")
    assert sys.executable in H.last_command()


def test_the_version_survives_the_launcher(lua, installed):
    comp, _ = installed
    H = load(lua, comp / "Eternal2x.lua")
    assert "0.3.0" in H.widgets["SubTitle"].Text


def test_the_update_url_survives_the_launcher(lua, installed):
    comp, _ = installed
    H = load(lua, comp / "Eternal2x.lua")
    H.click("UpdateBtn")
    assert "example.com/latest.json" in (H.last_command() or "")


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def test_analyse_runs_the_stage_in_analyse_mode(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    cmd = H.last_command()
    assert "-m Stages.resolve_smooth" in cmd
    assert "--analyse" in cmd


def test_analyse_also_runs_in_the_background(lua, direct):
    """Analysis is quick on a short clip but slow on a long one, and Resolve
    must not freeze for it either."""
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    cmd = H.last_command()
    assert cmd.rstrip().endswith("&") or "start " in cmd
    assert "--status-file" in cmd


def test_analyse_result_arrives_through_the_status_file(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True,
                 message="48 frames, 24 unique drawings. Animated on 2s.")
    H.click("RefreshStatusBtn")
    assert "24 unique drawings" in H.widgets["AnalysisLabel"].Text
    assert "Analysis complete" in H.status()


def test_a_failed_analyse_says_so_in_the_panel(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    write_status(direct, done=True, ok=False, message="The clip's media is missing.")
    H.click("RefreshStatusBtn")
    assert "media is missing" in H.widgets["AnalysisLabel"].Text
    assert H.enabled("AnalyseBtn")


def test_analyse_never_imports_anything(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="On 2s.",
                 import_path="/tmp/should-not-be-imported.mp4")
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 0, "Analyse imported a clip"


def test_smooth_runs_in_the_background(lua, direct):
    """A render takes minutes; blocking here would freeze Resolve."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    cmd = H.last_command()
    assert "-m Stages.resolve_smooth" in cmd
    assert "--analyse" not in cmd
    assert cmd.rstrip().endswith("&") or "start " in cmd


def test_smooth_passes_a_status_file(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert "--status-file" in H.last_command()


def test_interpolate_off_reaches_the_command(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("InterpCB", False)
    H.click("SmoothBtn")
    assert "--no-interpolate" in H.last_command()


def test_hold_pattern_selection_reaches_the_command(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_combo("HoldCombo", 1)         # On 2s
    H.click("AnalyseBtn")
    assert "--base-hold 2" in H.last_command()


def test_auto_hold_pattern_sends_no_override(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_combo("HoldCombo", 0)         # Auto detect
    H.click("AnalyseBtn")
    assert "--base-hold" not in H.last_command()


def test_unchecking_upscale_reaches_the_command(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)
    H.click("AnalyseBtn")
    assert "--no-upscale" in H.last_command()


def test_unchecking_interpolate_reaches_the_command(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("InterpCB", False)
    H.click("AnalyseBtn")
    assert "--no-interpolate" in H.last_command()


def test_grainy_source_is_off_unless_ticked(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    assert "--grain" not in H.last_command()
    H.set_checkbox("GrainCB", True)
    H.click("AnalyseBtn")
    assert "--grain" in H.last_command()


def test_grainy_source_is_remembered(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("GrainCB", True)
    conf = (direct / "Installer" / "Eternal2x.conf").read_text()
    assert "grain=true" in conf
    H = load(lua, ui_script(direct))
    assert H.widgets["GrainCB"].Checked is True


def test_defaults_send_no_opt_out_flags(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    cmd = H.last_command()
    assert "--no-upscale" not in cmd and "--no-interpolate" not in cmd


def test_a_clip_path_with_spaces_is_quoted(lua, direct):
    H = load(lua, ui_script(direct), clip="/media/My Footage/shot 01.mov")
    assert "My Footage" in H.widgets["Meta"].Text


def test_every_module_the_panel_calls_exists(lua, direct, repo_root):
    for button in ("AnalyseBtn", "SmoothBtn", "UpdateBtn"):
        H = load(lua, ui_script(direct))
        H.click(button)
        module = H.last_command().split("-m ")[1].split()[0]
        assert (repo_root / Path(module.replace(".", "/") + ".py")).exists(), \
            f"{button} calls missing module {module}"


def test_the_command_the_panel_builds_actually_runs(lua, direct):
    """Run the panel's own command string and confirm it resolves the package."""
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    cmd = H.last_command()

    subprocess.run(cmd, shell=True, capture_output=True, text=True)
    log = (direct / ".eternal2x_last_run.log")
    assert log.exists(), "the panel's command produced no log"
    text = log.read_text()
    for missing in ("No module named 'Stages'", "No module named 'Pipeline'"):
        assert missing not in text, f"the panel cannot reach the plugin modules:\n{text}"


# --------------------------------------------------------------------------
# source clip
# --------------------------------------------------------------------------

def test_the_selected_clip_is_shown_on_open(lua, direct):
    H = load(lua, ui_script(direct), clip="/media/scene 04.mov")
    assert "scene 04.mov" in H.widgets["SourceLabel"].Text


def test_refresh_picks_up_the_selection(lua, direct):
    H = load(lua, ui_script(direct), clip="/media/shot.mov")
    H.click("RefreshBtn")
    assert "shot.mov" in H.widgets["SourceLabel"].Text
    assert "Ready" in H.status()


# --------------------------------------------------------------------------
# progress reporting
# --------------------------------------------------------------------------

def write_status(root: Path, **fields):
    payload = {"stage": "Rendering", "fraction": 0.5, "message": "",
               "import_path": "", "done": False, "ok": False, "updated": 0}
    payload.update(fields)
    (root / ".eternal2x_status.json").write_text(json.dumps(payload))


def test_progress_is_shown_while_running(lua, direct):
    H = load(lua, ui_script(direct))
    write_status(direct, stage="Rendering", fraction=0.5)
    H.click("RefreshStatusBtn")
    assert "50%" in H.widgets["Progress"].Text


def test_a_stage_at_zero_percent_is_still_named(lua, direct):
    H = load(lua, ui_script(direct))
    write_status(direct, stage="Installing AI runtime", fraction=0.0)
    H.click("RefreshStatusBtn")
    assert "Installing AI runtime" in H.widgets["Progress"].Text


def test_progress_bar_fills_as_it_goes(lua, direct):
    H = load(lua, ui_script(direct))
    filled = []
    for fraction in (0.1, 0.9):
        write_status(direct, fraction=fraction)
        H.click("RefreshStatusBtn")
        filled.append(H.widgets["Progress"].Text.count("█"))
    assert filled[1] > filled[0], "the progress bar did not grow"


def test_completion_message_is_surfaced(lua, direct):
    H = load(lua, ui_script(direct))
    write_status(direct, fraction=1.0, done=True, ok=True,
                 message="Done. 12 drawings rebuilt into 24 smooth frames.")
    H.click("RefreshStatusBtn")
    assert "12 drawings" in H.status()


def test_a_finished_render_is_imported_once_even_after_reopening(lua, direct):
    """The status file outlives the panel. Pressing Refresh Progress in a new
    panel must not import the last render again and append a second copy."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Done.",
                 import_path=str(direct / "shot_smooth.mp4"))
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    H.click("RefreshStatusBtn")

    H = load(lua, ui_script(direct))
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 0, "the reopened panel imported the render again"
    assert "No run in progress" in H.status()


def test_a_failure_is_surfaced(lua, direct):
    H = load(lua, ui_script(direct))
    write_status(direct, done=True, ok=False, message="The clip's media is missing.")
    H.click("RefreshStatusBtn")
    assert "media is missing" in H.status()


def test_buttons_are_disabled_while_a_render_runs(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert not H.enabled("SmoothBtn"), "Smooth stayed clickable during a render"
    assert not H.enabled("AnalyseBtn")


def test_buttons_come_back_when_the_render_finishes(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Done.")
    H.click("RefreshStatusBtn")
    assert H.enabled("SmoothBtn")
    assert H.enabled("AnalyseBtn")


def test_reset_clears_a_stuck_run(lua, direct):
    """The run ended without saying it was done, and the panel never polled."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    (direct / ".eternal2x_exit").write_text("1\n")
    H.click("CancelBtn")
    assert H.enabled("SmoothBtn")
    assert H.widgets["Progress"].Text == ""
    assert not (direct / ".eternal2x_status.json").exists()


def test_refreshing_with_no_run_says_so(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("RefreshStatusBtn")
    assert "No run in progress" in H.status()


# --------------------------------------------------------------------------
# settings persistence
# --------------------------------------------------------------------------

def test_settings_are_saved(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_combo("HoldCombo", 2)        # On 3s
    H.set_checkbox("UpscaleCB", False)

    conf = (direct / "Installer" / "Eternal2x.conf").read_text()
    assert "base_hold=3" in conf
    assert "upscale=false" in conf


def test_a_saved_avi_output_falls_back_to_the_image_sequence(lua, direct):
    """Resolve Studio 20.3.1 refuses FFV1 in AVI, so the lossless file is now
    MKV, and a setting saved by an older version picks the safe default."""
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(conf.read_text() + "format=avi\n")
    H = load(lua, ui_script(direct))
    assert "MKV" in H.combo_items("FormatCombo") and "AVI" not in H.combo_items("FormatCombo")
    H.click("SmoothBtn")
    assert "--format png" in H.last_command()


def test_saving_settings_keeps_the_rest_of_the_config(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)
    conf = (direct / "Installer" / "Eternal2x.conf").read_text()
    assert f"repo_root={direct}" in conf
    assert "update_url=https://example.com/latest.json" in conf


def test_saved_settings_are_restored_next_time(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_combo("HoldCombo", 1)        # On 2s
    H.set_checkbox("GrainCB", True)

    H2 = load(lua, ui_script(direct))
    H2.click("AnalyseBtn")
    cmd = H2.last_command()
    assert "--grain" in cmd
    assert "--base-hold 2" in cmd


def test_auto_update_toggle_persists(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("AutoUpdateCB", True)
    assert "auto_update=true" in (direct / "Installer" / "Eternal2x.conf").read_text()


# --------------------------------------------------------------------------
# background launching
# --------------------------------------------------------------------------

def test_the_background_command_redirects_into_the_log(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    cmd = H.last_command()
    assert ".eternal2x_last_run.log" in cmd
    assert "2>&1" in cmd


def test_the_windows_background_form_keeps_the_redirect_inside_cmd(repo_root):
    """`start ... > log` binds the redirect to start, leaving the log empty."""
    lua_src = (repo_root / "Installer" / "Eternal2x.lua").read_text(encoding="utf-8")
    block = lua_src[lua_src.index("local function build_command"):
                    lua_src.index("local function run_blocking")]
    assert 'cmd /c' in block, (
        "the Windows background launch must wrap the command in cmd /c so the "
        "redirection applies to the child process"
    )


def test_a_background_run_does_not_block(lua, direct):
    """The panel must not wait on the render, or Resolve freezes."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert not H.enabled("SmoothBtn"), "the panel should show it is busy"
    assert "Resolve stays usable" in H.status()


# --------------------------------------------------------------------------
# the panel imports the finished render itself
# --------------------------------------------------------------------------

def test_python_is_given_the_clip_path_directly(lua, direct):
    """The stage must not have to reconnect to Resolve.

    A Python process spawned from Lua can only import DaVinciResolveScript if
    the user has external scripting configured. The panel already holds a
    connection, so it passes the path down and does the import itself.
    """
    H = load(lua, ui_script(direct), clip="/media/shot.mov")
    H.click("AnalyseBtn")
    assert "--video" in H.last_command()
    assert "/media/shot.mov" in H.last_command()


def test_a_finished_render_is_imported_and_appended(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out/frame_000000.png")
    H.click("RefreshStatusBtn")

    assert H.imported_count() == 1
    assert H.imported_at(1) == "/tmp/out/frame_000000.png"
    assert H.appended == 1
    assert "above the original" in H.status()


def finish_render(H, root, **fields):
    H.click("SmoothBtn")
    status = {"fraction": 1.0, "done": True, "ok": True, "message": "Rendered.",
              "import_path": "/tmp/out.mp4"}
    status.update(fields)
    write_status(root, **status)
    H.click("RefreshStatusBtn")


def test_the_render_lines_up_on_a_new_track_above_the_original(lua, direct):
    H = load(lua, ui_script(direct))
    finish_render(H, direct)

    assert len(H.placements) == 1
    placed = H.placements[1]
    assert placed.trackIndex == 2
    assert placed.recordFrame == 86400, "it does not start where the original does"
    # Resolve 20.3 reads endFrame as one past the last frame, so 0 to 48 is
    # all 48 frames. 0 to 47 left the original showing on the last one.
    assert (placed.startFrame, placed.endFrame) == (0, 48)
    assert placed.mediaType == 1
    assert H.track_names[2] == "Eternal2x"
    assert "V2 above the original" in H.status()


def test_a_free_track_above_is_used_as_it_is(lua, direct):
    H = load(lua, ui_script(direct))
    lua.execute("local H = ...; H.tracks[2] = {}; H.tracks[3] = {}", H)
    finish_render(H, direct)
    assert H.placements[1].trackIndex == 2
    assert len(H.tracks) == 3, "a track was added though one was free"
    assert H.track_names[2] is None, "the user's track was renamed"


def test_a_track_with_a_clip_in_the_way_is_skipped(lua, direct):
    H = load(lua, ui_script(direct))
    # Track 2 has a clip overlapping the span, track 3 one well after it.
    lua.execute("local H = ...; H.tracks[2] = { { 86440, 86500 } }; "
                "H.tracks[3] = { { 90000, 90100 } }", H)
    finish_render(H, direct)
    assert H.placements[1].trackIndex == 3


def test_a_sequence_is_placed_with_its_own_length(lua, direct):
    H = load(lua, ui_script(direct))
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=118, fps=60.0)
    assert H.placements[1].endFrame == 118


def test_without_the_placement_calls_it_goes_at_the_end(lua, direct):
    H = load(lua, ui_script(direct))
    H.no_place_api = True
    H.click("RefreshBtn")
    finish_render(H, direct)
    assert len(H.placements) == 0
    assert H.appended == 1
    assert "end of the timeline" in H.status()


def test_a_different_timeline_gets_the_render_at_its_end(lua, direct):
    """Lined up against another timeline, the frame numbers mean nothing."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    H.timeline_id = "another-timeline"
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert len(H.placements) == 0
    assert "end of the timeline" in H.status()


def test_a_refused_placement_removes_its_track_and_appends(lua, direct):
    H = load(lua, ui_script(direct))
    H.place_works = False
    finish_render(H, direct)
    assert len(H.tracks) == 1, "the empty track it added was left behind"
    assert H.appended == 1
    assert "end of the timeline" in H.status()


def test_a_source_moved_during_the_render_is_followed(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    lua.execute("local H = ...; H.src_span[1] = 86500; H.src_span[2] = 86548", H)
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert H.placements[1].recordFrame == 86500


def test_a_source_retrimmed_during_the_render_gets_the_render_at_the_end(lua, direct):
    """Lined up with a different cut of the clip, it would not match it."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    lua.execute("local H = ...; H.src_span[2] = 86430", H)
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert len(H.placements) == 0
    assert "end of the timeline" in H.status()


def test_a_source_deleted_during_the_render_gets_the_render_at_the_end(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    lua.execute("local H = ...; table.remove(H.tracks[1], 1)", H)
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert len(H.placements) == 0
    assert H.appended == 1


def test_a_render_longer_than_the_source_is_cut_to_it(lua, direct):
    """Lined up over a 48 frame clip, the rest of a 120 frame render would
    cover the next shot."""
    H = load(lua, ui_script(direct))
    H.media_frames = "120"
    lua.execute("local H = ...; H.tracks[2] = { { 86460, 86480 } }", H)
    finish_render(H, direct)
    assert H.placements[1].endFrame == 48
    assert H.placements[1].trackIndex == 2


def test_a_render_a_frame_longer_than_the_source_ends_with_it(lua, direct):
    """Resolve can report a clip's last frame one past where it ends, so the
    render has a frame more. Seen in Resolve 20.3.1."""
    H = load(lua, ui_script(direct))
    H.media_frames = "49"
    finish_render(H, direct)
    assert H.placements[1].endFrame == 48
    assert H.tracks[2][1][2] == 86448, "it runs past the original"


def _with_source(lua, direct, **fields):
    H = load(lua, ui_script(direct))
    sets = "; ".join(f"H.{k} = {str(v).lower() if isinstance(v, bool) else repr(v)}"
                     for k, v in fields.items())
    lua.execute(f"local H = ...; H.tracks = nil; H.src_span = nil; {sets}", H)
    H.click("RefreshBtn")
    return H


def test_a_slower_render_on_a_faster_timeline_ends_inside_the_original(lua, direct):
    """20 frames at 24 fps last 50 frames of a 60 fps timeline, two past a
    48 frame original, so 19 are placed."""
    H = _with_source(lua, direct, timeline_fps="60")
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=20, fps=24.0)
    assert H.placements[1].endFrame == 19
    assert H.tracks[2][1][2] <= 86448, "it runs into the next shot"
    assert "past it" not in H.status()


def test_a_long_ntsc_render_fills_the_original(lua, direct):
    """23.976 is 24000/1001. Counted with 23.976, 999 frames came out one
    short of a 999 frame original on a 24 fps timeline."""
    H = _with_source(lua, direct, item_duration=999)
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=999, fps=23.976)
    assert H.placements[1].endFrame == 999
    assert H.tracks[2][1][2] == 86400 + 999


def test_a_rate_near_ntsc_is_taken_as_it_is(lua, direct):
    """23.98 is not 24000/1001. Taken for it, 1000 frames were cut to 999
    over a 1000 frame original, a frame short."""
    H = _with_source(lua, direct, item_duration=1000)
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=1000, fps=23.98)
    assert H.placements[1].endFrame == 1000
    assert H.tracks[2][1][2] == 86400 + 1000


def test_a_placement_that_runs_past_the_original_says_so(lua, direct):
    """A Resolve that rounds lengths to nearest, not down, places 47.6
    timeline frames as 48 over a 47 frame original."""
    H = _with_source(lua, direct, item_duration=47, place_rounds=True)
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=120, fps=60.0)
    assert H.placements[1].endFrame == 119
    assert "runs 1 frame past it" in H.status()


def test_a_render_a_frame_short_is_placed_whole(lua, direct):
    H = load(lua, ui_script(direct))
    H.media_frames = "47"
    finish_render(H, direct)
    assert H.placements[1].endFrame == 47


def test_a_sequence_at_an_unconfirmed_rate_stays_in_the_media_pool(lua, direct):
    """At the wrong rate it plays at the wrong speed and covers more timeline."""
    H = load(lua, ui_script(direct))
    H.fps_sticks = False
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=120, fps=60.0)
    assert H.appended == 0
    assert "Clip Attributes" in H.status() and "media pool" in H.status()


def test_a_failed_track_rename_does_not_add_a_second_copy(lua, direct):
    H = load(lua, ui_script(direct))
    H.rename_throws = True
    finish_render(H, direct)
    assert H.appended == 1
    assert "above the original" in H.status()


def test_a_placement_that_raises_removes_its_track_and_appends_once(lua, direct):
    H = load(lua, ui_script(direct))
    H.place_throws = True
    finish_render(H, direct)
    assert len(H.tracks) == 1, "the empty track it added was left behind"
    assert H.appended == 1
    assert "end of the timeline" in H.status()


def _stacked(lua, direct, top, *under):
    """`top` under the playhead, with `under` lined up below it on tracks 1
    and up, as an original and earlier renders are."""
    H = load(lua, ui_script(direct), clip=top)
    paths = ", ".join("'" + p.replace("\\", "\\\\") + "'" for p in under)
    lua.execute(f"local H = ...; H.tracks = nil; H.src_span = nil; "
                f"H.item_track = {len(under) + 1}; H.under_paths = {{ {paths} }}", H)
    H.click("RefreshBtn")
    return H


def test_a_render_over_its_original_is_not_smoothed_again(lua, direct):
    """The render placed above is the top clip under the playhead from then
    on, so smoothing again with other settings smoothed the render. Seen in
    Resolve 20.3.1."""
    H = _stacked(lua, direct, "/r/shot_f7-73_smooth_02/frame_[000000-000047].png",
                 "/media/shot.mov")
    assert "shot.mov" in H.text_of("SourceLabel")
    H.click("AnalyseBtn")
    assert "/media/shot.mov" in H.last_command()
    assert "shot_f7-73" not in H.last_command()


def test_a_single_file_render_over_its_original_is_skipped_too(lua, direct):
    H = _stacked(lua, direct, "/r/shot_smooth.mkv", "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "/media/shot.mov" in H.last_command()


def _under_a_clip(lua, direct, setup=""):
    """B.mov on V2 over A.mov on V1, the playhead ten frames in."""
    H = load(lua, ui_script(direct), clip="/media/B.mov")
    lua.execute("local H = ...; H.tracks = nil; H.src_span = nil; H.item_track = 2; "
                "H.under_paths = { '/media/A.mov' }; H.playhead_tc = '01:00:00:10'; " + setup, H)
    H.click("RefreshBtn")
    return H


def test_a_clip_switched_off_is_passed_over(lua, direct):
    """GetCurrentVideoItem returns the top clip even switched off, as measured
    in Resolve 20.3.1, so the panel took a clip the viewer did not show."""
    H = _under_a_clip(lua, direct, "H.item_off = true")
    assert "A.mov" in H.text_of("SourceLabel")
    H.click("AnalyseBtn")
    assert "/media/A.mov" in H.last_command()


def test_a_clip_on_a_track_switched_off_is_passed_over(lua, direct):
    H = _under_a_clip(lua, direct, "H.tracks_off = { [2] = true }")
    assert "A.mov" in H.text_of("SourceLabel")


def test_the_top_clip_is_used_while_it_shows(lua, direct):
    H = _under_a_clip(lua, direct)
    assert "B.mov" in H.text_of("SourceLabel")


def test_the_playhead_counts_whole_frames_on_ntsc_timelines(lua, direct):
    """Timecode on a 23.976 timeline numbers 24 frames a second. Taken as
    23.976, an hour in lands 86 frames early, outside the clip."""
    H = _under_a_clip(lua, direct, "H.item_off = true; H.timeline_fps = '23.976'")
    assert "A.mov" in H.text_of("SourceLabel")


def test_the_playhead_is_found_in_drop_frame_timecode(lua, direct):
    """01:00:00;00 is frame 107892 at 29.97 drop-frame, not 108000."""
    H = _under_a_clip(lua, direct, "H.item_off = true; H.timeline_fps = '29.97'; "
                                   "H.item_start = 107892; H.playhead_tc = '01:00:00;10'")
    assert "A.mov" in H.text_of("SourceLabel")


def test_the_third_smooth_skips_both_earlier_renders(lua, direct):
    """Two runs leave renders on V2 and V3. The third took the V2 render."""
    H = _stacked(lua, direct, "C:\\r\\shot_f0-47_smooth_02.mkv",
                 "C:\\media\\shot.mov", "C:\\r\\shot_f0-47_smooth.mkv")
    H.click("SmoothBtn")
    assert "shot.mov" in H.last_command()
    assert "_f0-47_smooth" not in H.last_command()


def test_a_clip_that_is_not_a_render_is_used_as_it_is(lua, direct):
    H = _stacked(lua, direct, "/media/smooth_jazz.mov", "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "/media/smooth_jazz.mov" in H.last_command()


def test_a_file_named_like_a_render_of_another_clip_is_used(lua, direct):
    """Only a render named after the clip under it stands for that clip."""
    for top in ("/media/camera_smooth123.mov", "/media/camera_smooth.mp4",
                "/media/camera_smooth_02/frame_000000.png"):
        H = _stacked(lua, direct, top, "/media/background.mov")
        H.click("AnalyseBtn")
        assert top.split("/")[2] in H.last_command(), top
        assert "background.mov" not in H.last_command(), top


def test_a_render_with_nothing_under_it_is_used(lua, direct):
    """Dragged somewhere else, or on its own timeline: it is all there is."""
    H = load(lua, ui_script(direct), clip="/r/shot_smooth/frame_[000000-000047].png")
    H.click("AnalyseBtn")
    assert "shot_smooth" in H.last_command()


def test_compare_switches_the_smoothed_clip_off_and_on(lua, direct):
    H = load(lua, ui_script(direct))
    assert not H.enabled("CompareBtn"), "Compare works before there is anything to compare"
    finish_render(H, direct)
    assert H.enabled("CompareBtn")

    H.click("CompareBtn")
    assert H.last_placed.enabled is False
    assert H.widgets["CompareBtn"].Text == "Show Eternal2x"
    assert "original" in H.status()
    H.click("CompareBtn")
    assert H.last_placed.enabled is True
    assert H.widgets["CompareBtn"].Text == "Show Original"


def test_compare_turns_off_when_a_new_run_starts(lua, direct):
    H = load(lua, ui_script(direct))
    finish_render(H, direct)
    H.click("SmoothBtn")
    assert not H.enabled("CompareBtn")


def test_show_in_folder_points_at_the_render(lua, direct):
    H = load(lua, ui_script(direct))
    assert not H.enabled("FolderBtn")
    finish_render(H, direct, import_path="/tmp/renders/out.mp4")
    H.click("FolderBtn")
    cmd = H.last_command()
    assert "/tmp/renders/out.mp4" in cmd.replace("\\", "/")
    assert "explorer /select," in cmd or "open -R" in cmd


def test_show_in_folder_opens_a_sequence_by_its_folder(lua, direct):
    H = load(lua, ui_script(direct))
    finish_render(H, direct, import_path="/tmp/renders/shot_smooth/frame_000000.png",
                  sequence_pattern="/tmp/renders/shot_smooth/frame_%06d.png",
                  sequence_frames=24, fps=24.0)
    H.click("FolderBtn")
    cmd = H.last_command().replace("\\", "/")
    assert "/tmp/renders/shot_smooth" in cmd and "frame_000000" not in cmd


def test_show_in_folder_works_after_a_refused_import(lua, direct):
    H = load(lua, ui_script(direct))
    H.import_works = False
    finish_render(H, direct, import_path="/tmp/renders/out.mp4")
    assert H.enabled("FolderBtn")


def test_a_timeline_too_small_for_the_upscale_says_so(lua, direct):
    H = load(lua, ui_script(direct))
    finish_render(H, direct)
    assert "3840x2160" in H.widgets["Meta"].Text


def test_a_big_enough_timeline_gets_no_upscale_tip(lua, direct):
    H = load(lua, ui_script(direct))
    H.timeline_width = "3840"
    finish_render(H, direct)
    assert "3840x2160" not in H.widgets["Meta"].Text


def test_no_upscale_tip_when_upscale_is_off(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)
    finish_render(H, direct)
    assert "3840x2160" not in H.widgets["Meta"].Text


def test_the_time_left_is_estimated_from_the_pace(lua, direct):
    H = load(lua, ui_script(direct))
    lua.execute("CLOCK = 1000; os.time = function() return CLOCK end")
    write_status(direct, stage="Rendering", fraction=0.1)
    H.click("RefreshStatusBtn")
    assert "left" not in H.widgets["Progress"].Text, "an estimate from one reading"
    lua.execute("CLOCK = 1010")
    write_status(direct, stage="Rendering", fraction=0.3)
    H.click("RefreshStatusBtn")
    # 0.2 of the work in 10 s leaves 0.7 for 35 s.
    assert "about 35 s left" in H.widgets["Progress"].Text


def test_a_new_stage_restarts_the_estimate(lua, direct):
    H = load(lua, ui_script(direct))
    lua.execute("CLOCK = 1000; os.time = function() return CLOCK end")
    write_status(direct, stage="Seeking", fraction=0.1)
    H.click("RefreshStatusBtn")
    lua.execute("CLOCK = 1010")
    write_status(direct, stage="Rendering", fraction=0.3)
    H.click("RefreshStatusBtn")
    assert "left" not in H.widgets["Progress"].Text


def test_super_scale_is_applied_with_a_value_resolve_accepts(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert H.property_of("Super Scale") == "2"


def test_upscale_unchecked_leaves_super_scale_alone(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert H.property_of("Super Scale") is None


def test_a_refused_import_still_tells_the_user_where_the_render_is(lua, direct):
    H = load(lua, ui_script(direct))
    H.import_works = False
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/renders/out.mp4")
    H.click("RefreshStatusBtn")

    status = H.status()
    # The status box fits two lines, so the path goes on the line below it.
    assert "would not import" in status
    assert H.widgets["Meta"].Text == "/tmp/renders/out.mp4"
    assert "drag it in" in status.lower()
    assert H.enabled("SmoothBtn"), "the panel stayed busy after a failed import"


def test_a_refused_sequence_is_pointed_at_by_its_folder(lua, direct):
    """The first frame's name alone, frame_000000.png, says nothing."""
    H = load(lua, ui_script(direct))
    H.import_works = False
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/renders/shot_smooth/frame_000000.png",
                 sequence_pattern="/tmp/renders/shot_smooth/frame_%06d.png",
                 sequence_frames=24, fps=24.0)
    H.click("RefreshStatusBtn")
    assert H.widgets["Meta"].Text == "/tmp/renders/shot_smooth"


def test_the_import_happens_once_not_on_every_poll(lua, direct):
    """Polling repeatedly must not append the clip again and again."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    for _ in range(4):
        H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    assert H.appended == 1


def test_a_failed_run_imports_nothing(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, done=True, ok=False, message="The clip's media is missing.")
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 0


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX shell quoting")
@pytest.mark.parametrize("name", ["Épisode $HOME 1.mov", "it's `date`.mov", "a\\b \"q\".mov"])
def test_odd_clip_names_reach_python_unchanged(lua, direct, tmp_path, name):
    """$ and backticks expanded inside the old double quotes, and a quote
    ended the path early."""
    import subprocess
    clip = f"/media/{name}"
    H = load(lua, ui_script(direct), clip=clip)
    H.click("AnalyseBtn")
    cmd = H.last_command()
    quoted = cmd.split("--video ")[1].split(" --format")[0]
    seen = subprocess.run(["sh", "-c", f"printf %s {quoted}"],
                          capture_output=True, text=True).stdout
    assert seen == clip


@pytest.mark.parametrize("path", ["/tmp/Épisode 1/frame_000000.png",
                                  'C:\\clips\\a "b"\\out.mp4'])
def test_the_import_path_is_decoded_before_resolve_sees_it(lua, direct, path):
    """The JSON escapes used to reach ImportMedia, which then found nothing."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path=path)
    H.click("RefreshStatusBtn")
    assert H.imported_at(1) == path


def test_a_refused_super_scale_is_not_reported_as_done(lua, direct):
    """Resolve says no by returning false, not by raising, on the free edition."""
    H = load(lua, ui_script(direct))
    H.super_scale_works = False
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")
    assert "upscaled 2x" not in H.status()
    assert "upscale not applied" in H.status()


# LuaJIT's ffi as the panel uses it, recording what CreateProcessW is given.
# The tests' Lua has no ffi of its own.
FAKE_FFI = r'''
CREATED = {}
package.preload.ffi = function()
    local ffi = {}
    function ffi.typeof(name) error("no such type: " .. name) end
    function ffi.cdef(text) end
    function ffi.sizeof(x) return 104 end
    function ffi.new(kind) return { [0] = 0 } end
    function ffi.load(name)
        return {
            MultiByteToWideChar = function(page, flags, s, n, out)
                if out then out.text = s end
                return n
            end,
            CreateProcessW = function(app, line, pa, ta, inherit, flags, env, cwd, si, pi)
                CREATED[#CREATED + 1] = { line = line.text, flags = flags }
                return REFUSE and 0 or 1
            end,
            WaitForSingleObject = function()
                if WAIT_FAILS then error("wait failed") end
                return 0
            end,
            GetExitCodeProcess = function(process, code) code[0] = 0 return 1 end,
            CloseHandle = function() CLOSED = (CLOSED or 0) + 1 return 1 end,
        }
    end
    return ffi
end
'''


def test_windows_commands_start_without_a_console_window(lua, direct):
    """os.execute flashed a console window up on every update check. The same
    command line now goes to CreateProcessW with CREATE_NO_WINDOW."""
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    lua.execute(FAKE_FFI)
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    created = lua.eval("CREATED")
    last = created[len(created)]
    assert last.flags == 0x08000000
    assert ' /c start "Eternal2x" /D ' in last.line
    assert last.line.endswith(" /MIN cmd /c .\\.eternal2x_run.cmd")
    assert not any("start" in c for c in H.commands.values()), "os.execute ran it as well"


def test_a_command_that_started_never_runs_twice(lua, direct):
    """A failure while waiting for the started command fell through to
    os.execute, which ran the command again."""
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    lua.execute(FAKE_FFI + "\nWAIT_FAILS = true")
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert not any("start" in c for c in H.commands.values()), "os.execute ran it again"
    assert lua.eval("CLOSED") == 2 * len(lua.eval("CREATED")), "a process handle was left open"


def test_without_create_process_the_command_still_runs(lua, direct):
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    lua.execute(FAKE_FFI + "\nREFUSE = true")
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert H.last_command().startswith('start "Eternal2x" /D ')


def test_the_windows_background_run_goes_through_a_script(lua, direct):
    """An & in a path split the old nested `cmd /c "..."` command.

    Pretends to be Windows by changing the directory separator Lua reports,
    then checks the script the panel writes instead of a nested command.
    """
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    H = load(lua, ui_script(direct), clip="C:/clips/Tom & Jerry 100%.mov")
    H.click("SmoothBtn")
    cmd = H.last_command()
    outer = cmd.split("\n")[0]
    assert outer.startswith('start "Eternal2x" /D "'), outer
    assert outer.endswith(" /MIN cmd /c .\\.eternal2x_run.cmd"), outer
    assert "/" not in outer.split("/D ")[1].split(" /MIN")[0], outer
    assert "Tom & Jerry" not in outer, "the clip path must not be on the outer command line"

    script = (direct / ".eternal2x_run.cmd").read_bytes()
    assert b"\r\r\n" not in script
    text = script.decode("utf-8")
    assert text.startswith("@echo off\r\nchcp 65001 >nul\r\n")
    assert '"C:/clips/Tom & Jerry 100%%.mov"' in text, "the path must be quoted and % doubled"
    assert "2>&1" in text


def test_a_saved_on_1s_setting_falls_back_to_auto_detect(lua, direct):
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(conf.read_text() + "base_hold=1\n")
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    assert "--base-hold" not in H.last_command()


# --------------------------------------------------------------------------
# reaching Resolve the way a real Scripts-menu run does
# --------------------------------------------------------------------------

def test_the_panel_reaches_resolve_without_a_lua_module(lua, direct):
    """Resolve gives Lua a Resolve() global. There is no DaVinciResolveScript.lua."""
    H = load(lua, ui_script(direct), clip="/media/shot.mov")
    assert lua.eval('package.preload["DaVinciResolveScript"]') is None
    assert "shot.mov" in H.widgets["SourceLabel"].Text


def test_the_panel_uses_a_resolve_global_when_there_is_one(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    lua.execute('resolve = nil')
    H.load(str(ui_script(direct)), "/media/first.mov")
    lua.execute('resolve = Resolve(); Resolve = nil')
    H.click("RefreshBtn")
    assert "first.mov" in H.widgets["SourceLabel"].Text


def test_no_clip_under_the_playhead_is_explained(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.under_playhead = False
    H.load(str(ui_script(direct)), "/media/shot.mov")
    assert "playhead" in H.widgets["SourceLabel"].Text


def test_a_throwing_api_does_not_stop_the_panel_opening(lua, direct):
    """The clip is read while the panel opens, where an error would kill it."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.make_resolve = lua.eval(
        'function() return { GetProjectManager = function() error("no access") end } end')
    H.load(str(ui_script(direct)), "/media/shot.mov")
    assert H.window.__shown
    assert "no access" in H.widgets["SourceLabel"].Text


# --------------------------------------------------------------------------
# a run that dies is reported, not waited on forever
# --------------------------------------------------------------------------

def test_a_run_that_died_without_a_status_is_reported(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    (direct / ".eternal2x_last_run.log").write_text(
        "Traceback (most recent call last):\nModuleNotFoundError: No module named 'cv2'\n")
    (direct / ".eternal2x_exit").write_text("1\n")
    H.click("RefreshStatusBtn")
    assert "No module named 'cv2'" in H.status()
    assert H.enabled("SmoothBtn"), "the panel stayed busy after the run died"


def test_a_run_still_going_is_not_reported_as_dead(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.4)
    H.click("RefreshStatusBtn")
    assert not H.enabled("SmoothBtn")
    assert "40%" in H.widgets["Progress"].Text


def test_a_finished_run_that_exited_is_not_taken_for_a_crash(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4")
    (direct / ".eternal2x_exit").write_text("0\n")
    H.click("RefreshStatusBtn")
    assert "Stopped without finishing" not in H.status()
    assert "above the original" in H.status()


def test_starting_a_run_clears_the_last_exit_code(lua, direct):
    (direct / ".eternal2x_exit").write_text("1\n")
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert not (direct / ".eternal2x_exit").exists()


def test_the_background_command_records_how_python_exited(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    cmd = H.last_command()
    if sys.platform.startswith("win"):
        return  # the POSIX form; the Windows script is checked below
    assert cmd.startswith("(") and cmd.endswith(") &"), cmd
    assert "echo $? > " in cmd and ".eternal2x_exit" in cmd


def test_the_windows_script_records_how_python_exited(lua, direct):
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    text = (direct / ".eternal2x_run.cmd").read_bytes().decode("utf-8")
    last = text.rstrip("\r\n").split("\r\n")[-1]
    # Redirect first: `echo 2> file` would redirect stderr instead of writing 2.
    assert last.startswith(">") and last.endswith(" echo %ERRORLEVEL%"), last
    assert ".eternal2x_exit" in last


# --------------------------------------------------------------------------
# image sequences are imported as sequences
# --------------------------------------------------------------------------

def test_a_sequence_is_imported_with_the_documented_clipinfo(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out/frame_000000.png",
                 sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=24)
    H.click("RefreshStatusBtn")
    assert H.sequence_import_count() == 1
    assert H.last_sequence_field("FilePath") == "/tmp/out/frame_%06d.png"
    assert H.last_sequence_field("StartIndex") == 0
    assert H.last_sequence_field("EndIndex") == 23
    assert H.imported_count() == 1, "the first frame was imported as well"
    assert "above the original" in H.status()


def test_a_refused_sequence_import_is_not_swapped_for_a_still(lua, direct):
    """Handed its first frame instead, Resolve imports one still image, which
    the panel would append and call done."""
    H = load(lua, ui_script(direct))
    H.sequence_import_works = False
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out/frame_000000.png",
                 sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=24)
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 0 and H.appended == 0
    assert "would not import" in H.status() and H.widgets["Meta"].Text == "/tmp/out"


# --------------------------------------------------------------------------
# the clip is read when the button is pressed
# --------------------------------------------------------------------------

def test_smooth_uses_the_clip_under_the_playhead_now(lua, direct):
    H = load(lua, ui_script(direct), clip="/media/first.mov")
    lua.execute('Resolve = (function(make) return function() return make("/media/second.mov") end end)(...)',
                lua.eval('function(H) return H.make_resolve end')(H))
    H.click("SmoothBtn")
    assert "/media/second.mov" in H.last_command()


def test_smooth_keeps_the_last_clip_when_the_playhead_is_in_a_gap(lua, direct):
    H = load(lua, ui_script(direct), clip="/media/first.mov")
    H.under_playhead = False
    H.click("SmoothBtn")
    assert "/media/first.mov" in H.last_command()


# --------------------------------------------------------------------------
# update checks report their verdict
# --------------------------------------------------------------------------

def updater_writes(direct, text):
    """Play the updater: write its log when the panel runs it."""
    def run(cmd):
        if "Stages.resolve_update" in cmd:
            (direct / ".eternal2x_update.log").write_text(text)
    return run


def test_a_failed_update_is_not_reported_as_updating(lua, direct):
    H = load(lua, ui_script(direct))
    H.exit_code = 1
    H.on_execute = updater_writes(
        direct, "Updating Eternal2x: 0.3.0 -> 0.3.2\nUpdate package checksum mismatch.\n")
    H.click("UpdateBtn")
    assert H.status() == "Update package checksum mismatch."


def test_opening_the_panel_keeps_the_render_log(lua, direct):
    """The update check runs on open. The render log is the one users send in."""
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(conf.read_text().replace("auto_update=false", "auto_update=true"))
    H = load(lua, ui_script(direct))
    cmd = H.last_command()
    assert "Stages.resolve_update" in cmd
    assert ".eternal2x_update.log" in cmd and ".eternal2x_last_run.log" not in cmd


def test_an_install_set_up_with_the_old_feed_checks_the_new_one(lua, direct):
    """Installs keep the feed they were set up with through every update."""
    import re
    conf = direct / "Installer" / "Eternal2x.conf"
    old = "https://raw.githubusercontent.com/Alitleis123/Eternal2x.com/main/update/latest.json"
    conf.write_text(re.sub(r"update_url=.*", "update_url=" + old, conf.read_text()))
    H = load(lua, ui_script(direct))
    H.click("UpdateBtn")
    assert "sidebandstudio/Eternal2x.com/main/update/latest.json" in H.last_command()
    assert "Alitleis123" not in H.last_command()


def test_an_update_check_that_never_ran_does_not_repeat_the_last_verdict(lua, direct):
    (direct / ".eternal2x_update.log").write_text(
        "Update applied. Restart Resolve to use the new version.\n")
    H = load(lua, ui_script(direct))
    H.exit_code = 1
    H.click("UpdateBtn")
    assert "Update applied" not in H.status()
    assert "failed" in H.status()


def test_a_reopened_panel_follows_the_run_it_left_behind(lua, direct):
    """Closed mid-render and reopened, the panel must not start a second run
    over the first one's files, and should still import the first."""
    import time
    write_status(direct, fraction=0.4, updated=time.time())
    H = load(lua, ui_script(direct))
    assert not H.enabled("SmoothBtn") and not H.enabled("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Done.",
                 import_path=str(direct / "shot_smooth.mp4"), updated=time.time())
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1
    assert H.enabled("SmoothBtn")


def test_reset_does_not_drop_a_run_that_is_still_reporting(lua, direct):
    """Reset is for a run that stopped. A live one would carry on underneath
    the next run, sharing its files."""
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.4, updated=time.time())
    H.click("CancelBtn")
    assert (direct / ".eternal2x_status.json").exists()
    assert not H.enabled("SmoothBtn")
    assert "still running" in H.status()


def test_reset_does_not_drop_a_run_that_has_not_reported_yet(lua, direct):
    """Python takes a moment to start. Reset in that gap, then Smooth, used to
    launch a second run over the first."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    H.click("CancelBtn")
    assert not H.enabled("SmoothBtn")
    assert "still running" in H.status()


def test_a_heartbeat_from_before_the_clock_moved_back_is_not_a_live_run(lua, direct):
    """Stamped an hour ahead of now, it would block Reset for that hour."""
    import time
    write_status(direct, fraction=0.4, updated=time.time() + 3600)
    H = load(lua, ui_script(direct))
    assert H.enabled("SmoothBtn")
    H.click("CancelBtn")
    assert H.status() == "Ready."


def test_the_panel_keeps_its_own_word_while_a_run_reports_progress(lua, direct):
    """Python's status says Starting until the run is done. Shown on every
    poll, it hid why Reset did nothing and sat under a bar at 52%."""
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.52, message="Starting", updated=time.time())
    H.click("CancelBtn")
    H.click("RefreshStatusBtn")
    assert "still running" in H.status()
    assert "52%" in H.widgets["Progress"].Text


def test_the_stall_warning_goes_once_progress_resumes(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.3, updated=time.time() - 10 * 60)
    H.click("RefreshStatusBtn")
    assert "No progress" in H.status()
    write_status(direct, fraction=0.4, updated=time.time())
    H.click("RefreshStatusBtn")
    assert "No progress" not in H.status()


def test_reset_clears_a_run_that_went_quiet(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.4, updated=time.time() - 10 * 60)
    H.click("CancelBtn")
    assert H.enabled("SmoothBtn")
    assert H.status() == "Ready."


def test_no_update_while_a_render_runs(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert not H.enabled("UpdateBtn")


def test_no_update_while_a_render_from_an_earlier_panel_runs(lua, direct):
    """Reopened mid-render, the panel must not replace the files it is using."""
    import time
    write_status(direct, fraction=0.4, updated=time.time())
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(conf.read_text().replace("auto_update=false", "auto_update=true"))
    H = load(lua, ui_script(direct))
    assert H.command_count() == 0, "the auto update ran during a render"
    H.click("UpdateBtn")
    assert H.command_count() == 0
    assert "still running" in H.status()


def test_a_sequence_gets_the_source_frame_rate(lua, direct):
    """Left alone, a 60 fps sequence plays at the project's rate, too slow."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out/frame_000000.png",
                 sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=24, fps=60.0)
    H.click("RefreshStatusBtn")
    assert H.property_of("FPS") == "60"
    assert "at 60 fps" in H.status()


def test_a_fractional_frame_rate_is_kept(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out/frame_000000.png",
                 sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=24, fps=23.976)
    H.click("RefreshStatusBtn")
    assert H.property_of("FPS") == "23.976"


def test_a_frame_rate_resolve_ignored_is_reported(lua, direct):
    H = load(lua, ui_script(direct))
    H.fps_sticks = False
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out/frame_000000.png",
                 sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=24, fps=60.0)
    H.click("RefreshStatusBtn")
    assert "Clip Attributes" in H.status()
    assert "at 60 fps" not in H.status()


def test_a_single_file_keeps_its_own_frame_rate(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out.mp4", fps=60.0)
    H.click("RefreshStatusBtn")
    assert H.property_of("FPS") is None



# --------------------------------------------------------------------------
# only the part of the file the timeline clip uses is processed
# --------------------------------------------------------------------------

def test_a_trimmed_clip_sends_only_its_range(lua, direct):
    """Two seconds of an 84 second file should cost two seconds of work."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_range = lua.table(24.5, 26.5)
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "--start-time 24.500000 --end-time 26.500000" in H.last_command()
    assert "2.00 s long" in H.widgets["SourceLabel"].Text


def test_smooth_sends_the_range_too(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_range = lua.table(1.0, 3.0)
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("SmoothBtn")
    assert "--start-time 1.000000 --end-time 3.000000" in H.last_command()


def test_a_build_without_trim_info_uses_the_whole_file(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    assert "--start-time" not in H.last_command()
    assert "Whole file" in H.widgets["SourceLabel"].Text


def test_trim_frames_are_converted_at_the_media_rate(lua, direct):
    """Older builds give frames, which count at the clip's own rate. The end
    frame is the last one the cut uses."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_frames = lua.table(1470, 1589)
    H.clip_fps = "60"
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "--start-time 24.500000 --end-time 26.500000" in H.last_command()


def test_media_with_a_start_timecode_still_sends_file_seconds(lua, direct):
    """Resolve's source times count from the media's start timecode, so on
    footage that starts at 01:00:00:00 they are an hour past the file."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_range = lua.table(24.5, 26.5)
    H.start_tc = 3600.0
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("SmoothBtn")
    assert "--start-time 24.500000 --end-time 26.500000" in H.last_command()


def test_the_end_frame_wins_over_the_end_time(lua, direct):
    """Measured on Resolve Studio 20.3.1: a 47 frame cut of 60 fps media on a
    24 fps timeline covers 117.5 source frames from 1200 and reported end
    frame 1317, the last one used, but an end time at frame 1316. Rendered
    from the times it came out a timeline frame short of the cut."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.clip_fps = "60"
    H.start_tc = 3600.0
    H.source_range = lua.table(20.0, 1316 / 60)
    H.source_frames = lua.table(1200, 1317)
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("SmoothBtn")
    assert "--start-time 20.000000 --end-time 21.966667" in H.last_command()


def test_a_cut_at_the_timeline_rate_ends_where_the_clip_does(lua, direct):
    """Measured on Resolve Studio 20.3.1: at 23.976 fps a 40 frame cut from
    frame 20 reports end frame 60, one past its last, so the render read a
    frame past the cut, which can be the next shot."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_frames = lua.table(20, 60)
    H.clip_fps, H.timeline_fps, H.item_duration = "23.976", "23.976", 40
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "--start-time 0.834168 --end-time 2.502503" in H.last_command()


def test_a_cut_whose_end_frame_is_its_last_keeps_it(lua, direct):
    """At 60 fps the same build reports the last frame used: 119 frames
    from 1200 end at 1318."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_frames = lua.table(1200, 1318)
    H.clip_fps, H.timeline_fps, H.item_duration = "60", "60", 119
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "--start-time 20.000000 --end-time 21.983333" in H.last_command()


def test_moving_to_another_cut_of_the_same_file_picks_up_its_range(lua, direct):
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_range = lua.table(1.0, 2.0)
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.source_range = lua.table(10.0, 12.0)
    H.click("SmoothBtn")
    assert "--start-time 10.000000 --end-time 12.000000" in H.last_command()



# --------------------------------------------------------------------------
# a run killed from outside is noticed
# --------------------------------------------------------------------------

def test_a_run_that_went_quiet_is_flagged(lua, direct):
    """Closing the run's window kills it before it can write anything."""
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.05, updated=time.time() - 30 * 60)
    H.click("RefreshStatusBtn")
    assert "No progress for 30 minutes" in H.status()
    assert "Reset" in H.status()


def test_a_run_that_is_still_reporting_is_not_flagged(lua, direct):
    import time
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    write_status(direct, fraction=0.05, updated=time.time())
    H.click("RefreshStatusBtn")
    assert "No progress" not in H.status()


def advance_clock(lua, seconds):
    lua.execute(f"local real = os.time; os.time = function(t) "
                f"if t then return real(t) end return real() + {seconds} end")


def test_a_run_that_never_started_is_flagged(lua, direct):
    """Its window closed before Python wrote a status, so there is no file to
    go quiet. The panel said Working, and Refresh said no run, for good."""
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    advance_clock(lua, 600)
    H.click("RefreshStatusBtn")
    assert "Python has not started after 10 minutes" in H.status()
    H.click("CancelBtn")
    assert H.enabled("SmoothBtn")


def test_a_launch_that_fails_is_reported_at_once(lua, direct):
    H = load(lua, ui_script(direct))
    H.exit_code = 1
    H.click("SmoothBtn")
    assert "Could not start the run" in H.status()
    assert H.enabled("SmoothBtn") and H.enabled("AnalyseBtn")


def test_an_unwritable_run_script_does_not_rerun_the_last_one(lua, direct):
    """The script left from the last run would start in its place."""
    H = load(lua, ui_script(direct))
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    (direct / ".eternal2x_run.cmd").mkdir()
    before = H.command_count()
    H.click("AnalyseBtn")
    assert H.command_count() == before, "a run started without its script"
    assert "Could not start the run" in H.status()
    assert "Could not start the run" in H.text_of("AnalysisLabel")
    assert H.enabled("SmoothBtn")


def test_a_render_that_finished_while_the_panel_was_closed_is_offered(lua, direct):
    write_status(direct, fraction=1.0, done=True, ok=True, message="Done.",
                 import_path=str(direct / "shot_smooth.mp4"))
    H = load(lua, ui_script(direct))
    assert "finished while the panel was closed" in H.status()
    assert H.enabled("FolderBtn")
    assert H.imported_count() == 0
    H.click("RefreshStatusBtn")
    assert H.imported_count() == 1


def test_the_windows_run_gets_its_own_minimised_window(lua, direct):
    """Sharing os.execute's console left it open, and closing it killed the run."""
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    outer = H.last_command().split("\n")[0]
    assert " /MIN " in outer and " /B " not in outer, outer


def test_a_percent_in_the_plugin_path_is_refused_on_windows(lua, tmp_path, repo_root):
    """cmd expands %NAME% in a command line even inside quotes."""
    root = _plugin_tree(tmp_path / "VFX %TEMP% tools" / "Eternal2x", repo_root)
    (root / "Installer" / "Eternal2x.conf").write_text(
        f"repo_root={root}\npython={sys.executable}\n"
        "update_url=https://example.com/latest.json\nauto_update=false\n")
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    H.load(str(ui_script(root)), "/media/shot.mov")
    for button in ("AnalyseBtn", "SmoothBtn", "UpdateBtn"):
        H.click(button)
        assert H.command_count() == 0, f"{button} ran a command from the % folder"
        assert "contains %" in H.status()
    assert H.enabled("SmoothBtn"), "the panel was left busy"


def test_show_in_folder_keeps_a_percent_in_the_path_literal(lua, direct):
    """cmd would expand %TEMP% in a command line and open somewhere else."""
    H = load(lua, ui_script(direct))
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    finish_render(H, direct, import_path="C:/renders/%TEMP%/out.mp4")
    H.click("FolderBtn")
    command, _, script = H.last_command().partition("\n")
    assert "%TEMP%" not in command, "the path went through cmd's own command line"
    assert "%%TEMP%%" in script and "explorer /select," in script


def test_a_frame_rate_that_cannot_be_read_back_is_not_trusted(lua, direct):
    """Resolve said yes to 60 fps, but the readback failed, so 60 is unproven."""
    H = load(lua, ui_script(direct))
    H.fps_sticks = False
    H.fps_read_throws = True
    finish_render(H, direct, import_path="/tmp/out/frame_000000.png",
                  sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=120, fps=60.0)
    assert H.appended == 0
    assert "at 60 fps" not in H.status()


def test_a_saved_quality_from_an_older_version_is_dropped(lua, direct):
    conf = direct / "Installer" / "Eternal2x.conf"
    conf.write_text(conf.read_text() + "quality=better\n")
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)
    assert "quality=" not in conf.read_text()
    H.click("SmoothBtn")
    assert "--quality" not in H.last_command()


# --------------------------------------------------------------------------
# frames marked on the clip
# --------------------------------------------------------------------------

def marks_sent(direct):
    """The frames Render handed the stage, from the file it wrote them to."""
    return (direct / ".eternal2x_marks.txt").read_text()


def test_the_run_button_says_render(lua, direct):
    """It smooths and upscales, so Smooth Clip undersold it."""
    H = load(lua, ui_script(direct))
    assert "Render" in H.widgets["SmoothBtn"].Text


def test_render_hands_over_the_frames_marked_on_the_clip(lua, direct):
    H = load(lua, ui_script(direct))
    for frame in (12, 0, 6):
        H.add_marker(frame)
    H.click("SmoothBtn")
    assert "--marks-file" in H.last_command()
    assert marks_sent(direct) == "0,6,12"
    assert "3 frames marked" in H.status()


def test_render_without_markers_finds_the_drawings_itself(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    assert "--marks" not in H.last_command()
    assert "No markers" in H.status()


def test_markers_past_the_end_of_the_clip_do_not_count(lua, direct):
    """The clip may be trimmed shorter than the media its markers sit on."""
    H = load(lua, ui_script(direct))
    H.add_marker(2)
    H.add_marker(400)
    H.click("SmoothBtn")
    assert marks_sent(direct) == "2"


def test_analyse_marks_the_drawings_it_found_on_the_clip(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,2,4,8",
                 message="12 frames, 4 unique drawings. Animated on 2s.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == "0,2,4,8"
    assert H.tagged_markers() == 4
    assert "Marked 4 frames" in H.widgets["AnalysisLabel"].Text
    assert "Render" in H.status()


def test_analysing_again_replaces_only_its_own_markers(lua, direct):
    """The editor's markers stay. Eternal2x's from last time go."""
    H = load(lua, ui_script(direct))
    H.add_marker(5)
    H.add_marker(9, "eternal2x")
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,2", message="On 2s.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == "0,2,5"


def test_the_threshold_slider_sets_how_big_a_change_counts(lua, direct):
    H = load(lua, ui_script(direct))
    H.click("AnalyseBtn")
    assert "--threshold" not in H.last_command(), "the default should send nothing"
    H.set_slider("ThresholdSlider", 7)
    assert H.widgets["ThresholdValue"].Text == "7"
    H.click("AnalyseBtn")
    assert "--threshold 0.008000" in H.last_command()
    conf = (direct / "Installer" / "Eternal2x.conf").read_text()
    assert "threshold=7" in conf


def test_a_trimmed_clip_counts_its_markers_from_its_left_edge(lua, direct):
    """Resolve keys the markers from the media's start: M pressed 10 frames
    into a clip trimmed 20 frames in reads back as frame 30."""
    H = load(lua, ui_script(direct))
    H.left_offset = 20
    for key in (30, 20, 5, 75):
        H.add_marker(key)
    H.click("SmoothBtn")
    assert marks_sent(direct) == "0,10", "the trimmed-off frames count for nothing"


def test_analyse_marks_a_trimmed_clip_where_its_frames_are(lua, direct):
    H = load(lua, ui_script(direct))
    H.left_offset = 20
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,2,4", message="On 2s.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == "20,22,24"


def test_markers_on_a_clip_at_another_rate_become_its_own_frames(lua, direct):
    """A 30 fps clip on a 23.976 timeline: markers sit on timeline frames."""
    H = load(lua, ui_script(direct))
    H.clip_fps, H.timeline_fps = "30", "23.976"
    H.left_offset = 47
    H.add_marker(47)
    H.add_marker(57)
    H.click("SmoothBtn")
    assert marks_sent(direct) == "0,13"
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,13,25", message="On 2s.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == "47,57,67"


def test_markers_that_cannot_be_read_stop_the_render(lua, direct):
    """Rendering as if the clip were unmarked would undo the editor's choice."""
    H = load(lua, ui_script(direct))
    H.markers_fail = True
    before = H.last_command()
    H.click("SmoothBtn")
    assert H.last_command() == before, "it should not have started"
    assert "Could not read the markers" in H.status()


def test_an_analysis_with_nothing_to_mark_clears_its_old_markers(lua, direct):
    H = load(lua, ui_script(direct))
    H.add_marker(5)
    H.add_marker(9, "eternal2x")
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="",
                 message="Every frame is a new drawing, so there is nothing to rebuild.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == "5"


def test_markers_on_a_render_above_its_original_count_too(lua, direct):
    """The editor sees the render on top and may mark that instead."""
    H = _stacked(lua, direct, "/r/shot_smooth/frame_[000000-000047].png", "/media/shot.mov")
    lua.execute("local H = ...; H.under_markers[1][0] = {}; H.under_markers[1][12] = {}", H)
    H.click("RefreshBtn")
    H.add_marker(6)
    H.click("SmoothBtn")
    assert "shot.mov" in H.last_command()
    assert marks_sent(direct) == "0,6,12"
    H.click("AnalyseBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, marks="0,3", message="On 3s.")
    H.click("RefreshStatusBtn")
    assert H.marker_frames() == "6", "Analyse marks the original, not the render"


def test_a_long_clip_name_leaves_room_for_its_range(lua, direct):
    """Scene pack names run long, and wrapped they hid the range line."""
    name = ("『葬送のフリーレン』第2期ノンクレジットオープニング映像／OPテーマ：「lulu.」"
            "Mrs. GREEN APPLE／フリーレンOP - TOHO animation チャンネル (1080p, h264).mp4")
    H = load(lua, ui_script(direct), clip="/media/" + name)
    first, second = H.text_of("SourceLabel").split("\n")
    assert first.startswith("『葬送のフリーレン』") and first.endswith("(1080p, h264).mp4")
    assert "..." in first and len(first) < len(name)
    assert "\ufffd" not in first
    assert second.startswith("Whole file") or "of the file" in second


def test_a_short_clip_name_is_left_whole(lua, direct):
    H = load(lua, ui_script(direct), clip="/media/Gojo s laughter.mp4")
    assert H.text_of("SourceLabel").startswith("Gojo s laughter.mp4\n")
