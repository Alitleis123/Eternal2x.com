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
                     "CancelBtn", "UpdateBtn", "QualityCombo", "HoldCombo", "FormatCombo",
                     "UpscaleCB", "InterpCB", "AutoUpdateCB", "Status",
                     "Progress", "SourceLabel", "AnalysisLabel"):
        assert expected in ids, f"missing {expected}; have {sorted(ids)}"


def test_every_control_has_a_handler(lua, direct):
    H = load(lua, ui_script(direct))
    handlers = set(H.handler_ids().split(","))
    for expected in ("SmoothBtn.Clicked", "AnalyseBtn.Clicked", "RefreshBtn.Clicked",
                     "RefreshStatusBtn.Clicked", "CancelBtn.Clicked",
                     "UpdateBtn.Clicked", "QualityCombo.CurrentIndexChanged",
                     "HoldCombo.CurrentIndexChanged", "UpscaleCB.Clicked",
                     "InterpCB.Clicked", "AutoUpdateCB.Clicked"):
        assert expected in handlers, f"no handler for {expected}"


def test_quality_choices_match_what_the_stage_accepts(lua, direct):
    from Stages.resolve_smooth import build_parser
    H = load(lua, ui_script(direct))
    labels = [x.lower() for x in H.combo_items("QualityCombo").split(",")]

    accepted = None
    for action in build_parser()._actions:
        if "--quality" in action.option_strings:
            accepted = set(action.choices)
    assert accepted is not None
    assert set(labels) == accepted, f"panel offers {labels}, stage accepts {accepted}"


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


def test_quality_selection_reaches_the_command(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_combo("QualityCombo", 2)      # Best
    H.click("AnalyseBtn")
    assert "--quality best" in H.last_command()


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
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
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
    H.set_combo("QualityCombo", 2)
    H.set_combo("HoldCombo", 2)        # On 3s
    H.set_checkbox("UpscaleCB", False)

    conf = (direct / "Installer" / "Eternal2x.conf").read_text()
    assert "quality=best" in conf
    assert "base_hold=3" in conf
    assert "upscale=false" in conf


def test_saving_settings_keeps_the_rest_of_the_config(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_checkbox("UpscaleCB", False)
    conf = (direct / "Installer" / "Eternal2x.conf").read_text()
    assert f"repo_root={direct}" in conf
    assert "update_url=https://example.com/latest.json" in conf


def test_saved_settings_are_restored_next_time(lua, direct):
    H = load(lua, ui_script(direct))
    H.set_combo("QualityCombo", 0)     # Fast
    H.set_combo("HoldCombo", 1)        # On 2s

    H2 = load(lua, ui_script(direct))
    H2.click("AnalyseBtn")
    cmd = H2.last_command()
    assert "--quality fast" in cmd
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
    assert "added to the timeline" in H.status()


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
    assert "would not import /tmp/renders/out.mp4" in status
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
    assert "would not import /tmp/renders/shot_smooth." in H.status()


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
    quoted = cmd.split("--video ")[1].split(" --quality")[0]
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
    assert outer.startswith('start "Eternal2x" /MIN cmd /c "'), outer
    assert outer.endswith('.eternal2x_run.cmd"') and "/" not in outer.split('cmd /c')[1], outer
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
    assert "added to the timeline" in H.status()


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
    assert "added to the timeline" in H.status()


def test_a_refused_sequence_import_falls_back_to_the_first_frame(lua, direct):
    H = load(lua, ui_script(direct))
    H.sequence_import_works = False
    H.click("SmoothBtn")
    write_status(direct, fraction=1.0, done=True, ok=True, message="Rendered.",
                 import_path="/tmp/out/frame_000000.png",
                 sequence_pattern="/tmp/out/frame_%06d.png", sequence_frames=24)
    H.click("RefreshStatusBtn")
    assert H.imported_at(1) == "/tmp/out/frame_000000.png"


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


def test_an_update_check_that_never_ran_does_not_repeat_the_last_verdict(lua, direct):
    (direct / ".eternal2x_update.log").write_text(
        "Update applied. Restart Resolve to use the new version.\n")
    H = load(lua, ui_script(direct))
    H.exit_code = 1
    H.click("UpdateBtn")
    assert "Update applied" not in H.status()
    assert "failed" in H.status()


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
    """Older builds give frames, which count at the clip's own rate."""
    H = lua.eval(f'dofile("{HARNESS.as_posix()}")')
    H.source_frames = lua.table(1470, 1590)
    H.clip_fps = "60"
    H.load(str(ui_script(direct)), "/media/shot.mov")
    H.click("AnalyseBtn")
    assert "--start-time 24.500000 --end-time 26.500000" in H.last_command()


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


def test_the_windows_run_gets_its_own_minimised_window(lua, direct):
    """Sharing os.execute's console left it open, and closing it killed the run."""
    lua.execute('package.config = "\\\\" .. package.config:sub(2)')
    H = load(lua, ui_script(direct))
    H.click("SmoothBtn")
    outer = H.last_command().split("\n")[0]
    assert " /MIN " in outer and " /B " not in outer, outer
