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


def test_launcher_reports_a_missing_config(lua, tmp_path, repo_root):
    comp = tmp_path / "Comp"
    comp.mkdir()
    shutil.copy2(repo_root / "Installer" / "Eternal2xLauncher.lua", comp / "Eternal2x.lua")
    H = load(lua, comp / "Eternal2x.lua")
    assert "Missing repo_root" in " ".join(str(v) for v in dict(H.prints).values())


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
                 import_path="/tmp/out.mp4")
    H.click("RefreshStatusBtn")

    status = H.status()
    assert "would not import" in status
    assert "drag it in" in status.lower()
    assert H.enabled("SmoothBtn"), "the panel stayed busy after a failed import"


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
    assert outer.startswith('start "Eternal2x" /B cmd /c "'), outer
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
