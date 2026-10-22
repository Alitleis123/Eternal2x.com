"""The stage the panel's buttons run, end to end against a fake Resolve."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from Stages import resolve_smooth as smooth


def run(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["resolve_smooth"] + list(argv))
    return smooth.main()


@pytest.fixture
def anime_in_resolve(make_anime, install_fake_resolve, tmp_path):
    """A Resolve session whose selected clip is a real file on disk."""
    def _build(holds=(2,) * 12, **kwargs):
        src = make_anime(holds=holds)
        resolve, timeline, items, mpi = install_fake_resolve(
            [(0, len(holds) * 2)], media_path=str(src), **kwargs
        )
        return src, resolve, timeline, items, mpi
    return _build


# --------------------------------------------------------------------------
# analysis only
# --------------------------------------------------------------------------

def test_analyse_reports_the_pattern_without_changing_anything(anime_in_resolve,
                                                               monkeypatch, capsys,
                                                               tmp_path):
    src, _r, timeline, _items, _ = anime_in_resolve()
    before = len(timeline.GetItemListInTrack("video", 1))

    assert run(monkeypatch, ["--analyse", "--output-dir", str(tmp_path / "out")]) == 0

    out = capsys.readouterr().out
    assert "12 unique drawings" in out
    assert "on 2s" in out
    assert "Nothing was changed" in out
    assert len(timeline.GetItemListInTrack("video", 1)) == before


def test_analyse_writes_no_files(anime_in_resolve, monkeypatch, tmp_path):
    anime_in_resolve()
    out_dir = tmp_path / "out"
    run(monkeypatch, ["--analyse", "--output-dir", str(out_dir)])
    assert not out_dir.exists() or not list(out_dir.iterdir())


def test_footage_with_nothing_to_do_says_so(anime_in_resolve, monkeypatch, capsys,
                                            tmp_path):
    anime_in_resolve(holds=(1,) * 24)
    assert run(monkeypatch, ["--output-dir", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "no duplicated frames" in out
    assert "already animated on 1s" in out


# --------------------------------------------------------------------------
# the full run
# --------------------------------------------------------------------------

def test_full_run_renders_imports_and_appends(anime_in_resolve, monkeypatch, capsys,
                                              tmp_path):
    src, _r, timeline, _items, _ = anime_in_resolve()
    before = len(timeline.GetItemListInTrack("video", 1))

    assert run(monkeypatch, ["--output-dir", str(tmp_path / "out")]) == 0

    out = capsys.readouterr().out
    assert "Wrote 24 frames" in out
    assert "Imported:" in out
    assert "Timeline:" in out
    assert "Done." in out
    assert len(timeline.GetItemListInTrack("video", 1)) == before + 1


def test_the_rendered_file_lands_where_it_says(anime_in_resolve, monkeypatch, capsys,
                                               tmp_path):
    anime_in_resolve()
    out_dir = tmp_path / "out"
    run(monkeypatch, ["--format", "mp4", "--output-dir", str(out_dir)])

    rendered = list(out_dir.glob("*.mp4"))
    assert len(rendered) == 1
    assert rendered[0].stat().st_size > 0
    assert rendered[0].name in capsys.readouterr().out


def test_the_default_output_is_a_lossless_sequence(anime_in_resolve, monkeypatch,
                                                   tmp_path):
    """Quality is the point of the tool, so the default must not be lossy."""
    anime_in_resolve()
    out_dir = tmp_path / "out"
    run(monkeypatch, ["--output-dir", str(out_dir)])

    folders = [p for p in out_dir.iterdir() if p.is_dir()]
    assert len(folders) == 1
    assert len(list(folders[0].glob("*.png"))) == 24


@pytest.mark.parametrize("fmt,pattern", [("mp4", "*.mp4"), ("avi", "*.avi")])
def test_single_file_formats_render(anime_in_resolve, monkeypatch, tmp_path, fmt, pattern):
    """Decode the result back. A writer that opens but silently encodes
    nothing, which happens when a platform is missing a codec, would
    otherwise pass a file-size check."""
    import cv2

    anime_in_resolve()
    out_dir = tmp_path / fmt
    assert run(monkeypatch, ["--format", fmt, "--output-dir", str(out_dir)]) == 0

    written = list(out_dir.glob(pattern))
    assert len(written) == 1 and written[0].stat().st_size > 0

    cap = cv2.VideoCapture(str(written[0]))
    decoded = 0
    while True:
        ok, _frame = cap.read()
        if not ok:
            break
        decoded += 1
    cap.release()
    assert decoded == 24, f"{fmt} wrote a file that decodes to {decoded} frames, not 24"


def test_super_scale_is_applied_to_the_imported_clip(anime_in_resolve, monkeypatch,
                                                     tmp_path):
    _src, resolve, _tl, _items, _ = anime_in_resolve()
    run(monkeypatch, ["--output-dir", str(tmp_path / "out")])

    pool = resolve.GetProjectManager().GetCurrentProject().GetMediaPool()
    assert pool.imported, "nothing was imported"
    assert pool.imported[0].GetClipProperty("Super Scale") == "2"


def test_no_upscale_flag_skips_super_scale(anime_in_resolve, monkeypatch, tmp_path):
    _src, resolve, _tl, _items, _ = anime_in_resolve()
    run(monkeypatch, ["--no-upscale", "--output-dir", str(tmp_path / "out")])

    pool = resolve.GetProjectManager().GetCurrentProject().GetMediaPool()
    assert pool.imported[0].GetClipProperty("Super Scale") == ""


def test_free_edition_skips_the_upscale_and_says_why(anime_in_resolve, monkeypatch,
                                                     capsys, tmp_path):
    anime_in_resolve(studio=False)
    run(monkeypatch, ["--output-dir", str(tmp_path / "out")])
    assert "needs DaVinci Resolve Studio" in capsys.readouterr().out


def test_sequence_frames_are_readable_images(anime_in_resolve, monkeypatch, tmp_path):
    import cv2
    anime_in_resolve()
    out_dir = tmp_path / "out"
    run(monkeypatch, ["--format", "png", "--output-dir", str(out_dir)])

    folder = [p for p in out_dir.iterdir() if p.is_dir()][0]
    first = cv2.imread(str(sorted(folder.glob("*.png"))[0]))
    assert first is not None and first.shape[:2] == (120, 200)


def test_a_failed_import_still_points_at_the_render(anime_in_resolve, monkeypatch,
                                                    capsys, tmp_path):
    """A render that Resolve will not take is still a finished render."""
    anime_in_resolve(import_mode="none")
    assert run(monkeypatch, ["--output-dir", str(tmp_path / "out")]) == 0

    out = capsys.readouterr().out
    assert "Import failed" in out
    assert "waiting at" in out
    assert "Drag it into your media pool" in out


# --------------------------------------------------------------------------
# guards
# --------------------------------------------------------------------------

def test_running_outside_resolve_is_explained(no_resolve, monkeypatch, capsys):
    assert run(monkeypatch, []) == 2
    assert "Workspace > Scripts" in capsys.readouterr().out


def test_a_missing_media_file_is_reported(install_fake_resolve, monkeypatch, capsys):
    install_fake_resolve([(0, 24)], media_path="/media/gone.mov")
    assert run(monkeypatch, []) == 2
    assert "media is missing" in capsys.readouterr().out


def test_no_selected_clip_is_reported(monkeypatch, capsys):
    from tests.fake_resolve import (FakeBmdModule, FakeProject, FakeProjectManager,
                                    FakeResolve, FakeTimeline)
    monkeypatch.setitem(
        sys.modules, "DaVinciResolveScript",
        FakeBmdModule(FakeResolve(FakeProjectManager(FakeProject(FakeTimeline())))))
    assert run(monkeypatch, []) == 2
    assert "No clip under the playhead" in capsys.readouterr().out


# --------------------------------------------------------------------------
# options
# --------------------------------------------------------------------------

def test_video_flag_bypasses_resolve_entirely(make_anime, monkeypatch, capsys, tmp_path):
    """This is the path the panel uses: it passes the clip and imports the
    result itself, so the stage never needs its own Resolve connection."""
    src = make_anime(holds=(2,) * 8)
    assert run(monkeypatch, ["--video", str(src), "--output-dir", str(tmp_path / "o")]) == 0
    out = capsys.readouterr().out
    assert "Rendered to:" in out
    assert "Could not import DaVinciResolveScript" not in out


def test_forced_base_hold_is_honoured(make_anime, monkeypatch, capsys, tmp_path):
    src = make_anime(holds=(2,) * 12)
    run(monkeypatch, ["--video", str(src), "--base-hold", "3",
                      "--output-dir", str(tmp_path / "o")])
    assert "on 3s" in capsys.readouterr().out


def test_json_summary_is_machine_readable(make_anime, monkeypatch, capsys, tmp_path):
    src = make_anime(holds=(2,) * 8)
    run(monkeypatch, ["--video", str(src), "--json", "--output-dir", str(tmp_path / "o")])

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["plan"]["base_hold"] == 2
    assert payload["render"]["frames_written"] == 16


def test_quality_presets_are_accepted(make_anime, monkeypatch, tmp_path):
    src = make_anime(holds=(2,) * 6)
    for quality in ("fast", "better", "best"):
        assert run(monkeypatch, ["--video", str(src), "--quality", quality,
                                 "--output-dir", str(tmp_path / quality)]) == 0


def test_repeat_runs_do_not_overwrite_the_previous_render(make_anime, monkeypatch,
                                                          tmp_path):
    src = make_anime(holds=(2,) * 6)
    out_dir = tmp_path / "o"
    for _ in range(2):
        run(monkeypatch, ["--video", str(src), "--output-dir", str(out_dir)])
    folders = [p for p in out_dir.iterdir() if p.is_dir()]
    assert len(folders) == 2, "the second run overwrote the first"

    run(monkeypatch, ["--video", str(src), "--format", "mp4", "--output-dir", str(out_dir)])
    run(monkeypatch, ["--video", str(src), "--format", "mp4", "--output-dir", str(out_dir)])
    assert len(list(out_dir.glob("*.mp4"))) == 2, "a repeat mp4 run overwrote the first"


# --------------------------------------------------------------------------
# status file
# --------------------------------------------------------------------------

def test_status_file_tracks_progress_and_completion(make_anime, monkeypatch, tmp_path):
    src = make_anime(holds=(2,) * 8)
    status = tmp_path / "status.json"
    run(monkeypatch, ["--video", str(src), "--status-file", str(status),
                      "--output-dir", str(tmp_path / "o")])

    payload = json.loads(status.read_text())
    assert payload["done"] is True
    assert payload["ok"] is True
    assert payload["fraction"] == pytest.approx(1.0)
    assert "shot" in payload["message"] or "smooth" in payload["message"]


def test_status_file_records_failures(install_fake_resolve, monkeypatch, tmp_path):
    install_fake_resolve([(0, 24)], media_path="/media/gone.mov")
    status = tmp_path / "status.json"
    run(monkeypatch, ["--status-file", str(status)])

    payload = json.loads(status.read_text())
    assert payload["done"] is True
    assert payload["ok"] is False
    assert "missing" in payload["message"]


def test_status_file_never_contains_a_partial_write(make_anime, monkeypatch, tmp_path):
    """The panel polls this file, so it must always parse."""
    src = make_anime(holds=(2,) * 8)
    status = tmp_path / "status.json"
    run(monkeypatch, ["--video", str(src), "--status-file", str(status),
                      "--output-dir", str(tmp_path / "o")])
    json.loads(status.read_text())
    assert not status.with_suffix(".tmp").exists()


def test_an_unwritable_status_path_does_not_break_the_render(make_anime, monkeypatch,
                                                             tmp_path, capsys):
    src = make_anime(holds=(2,) * 6)
    assert run(monkeypatch, ["--video", str(src),
                             "--status-file", "/nonexistent-root/status.json",
                             "--output-dir", str(tmp_path / "o")]) == 0
    assert "Wrote 12 frames" in capsys.readouterr().out


def test_the_no_interpolate_flag_changes_the_output(make_anime, monkeypatch, tmp_path):
    """A switch the panel exposes has to actually do something."""
    import cv2

    def frames_of(folder):
        path = list(folder.glob("*.avi"))[0]
        cap, out = cv2.VideoCapture(str(path)), []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            out.append(frame)
        cap.release()
        return out

    src = make_anime(holds=(2,) * 8)
    run(monkeypatch, ["--video", str(src), "--format", "avi",
                      "--output-dir", str(tmp_path / "on")])
    run(monkeypatch, ["--video", str(src), "--format", "avi", "--no-interpolate",
                      "--output-dir", str(tmp_path / "off")])

    on, off = frames_of(tmp_path / "on"), frames_of(tmp_path / "off")
    assert len(on) == len(off)
    assert any(not (a == b).all() for a, b in zip(on, off)), (
        "--no-interpolate produced identical output, so the setting does nothing"
    )


def test_a_dotted_source_name_still_renders_an_image_sequence(make_anime, monkeypatch, tmp_path):
    """`shot.v2_smooth` has the suffix `.v2_smooth`, which was taken for a
    video container and failed with 'No encoder available'."""
    src = make_anime(holds=(2,) * 6)
    dotted = src.with_name("shot.v2.avi")
    src.rename(dotted)
    out = tmp_path / "out"
    assert run(monkeypatch, ["--video", str(dotted), "--output-dir", str(out)]) == 0
    folder = out / "shot.v2_smooth"
    assert folder.is_dir()
    assert len(list(folder.glob("frame_*.png"))) == 12


def test_a_frame_that_cannot_be_written_fails_the_run(make_anime, monkeypatch, tmp_path, capsys):
    """A failed frame write used to be ignored, leaving a short sequence."""
    src = make_anime(holds=(2,) * 6)

    def refuse(self, data):
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "write_bytes", refuse)
    assert run(monkeypatch, ["--video", str(src), "--output-dir", str(tmp_path / "o")]) == 2
    assert "Could not write" in capsys.readouterr().out


def test_a_frame_that_cannot_be_encoded_fails_the_run(make_anime, monkeypatch, tmp_path, capsys):
    import cv2
    src = make_anime(holds=(2,) * 6)
    monkeypatch.setattr(cv2, "imencode", lambda *a, **k: (False, None))
    assert run(monkeypatch, ["--video", str(src), "--output-dir", str(tmp_path / "o")]) == 2
    assert "Could not encode" in capsys.readouterr().out


def test_a_non_ascii_output_folder_gets_every_frame(make_anime, monkeypatch, tmp_path):
    """cv2.imwrite cannot open a path like this on Windows."""
    src = make_anime(holds=(2,) * 6)
    out = tmp_path / "Épisode 01"
    assert run(monkeypatch, ["--video", str(src), "--output-dir", str(out)]) == 0
    assert len(list((out / f"{src.stem}_smooth").glob("frame_*.png"))) == 12


def test_a_crash_is_reported_to_the_panel(make_anime, monkeypatch, tmp_path):
    """Without this the panel says Working... until someone presses Reset."""
    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(smooth, "render_plan", boom)
    status = tmp_path / "status.json"
    src = make_anime(holds=(2,) * 6)
    assert run(monkeypatch, ["--video", str(src), "--status-file", str(status)]) == 1
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["done"] is True and payload["ok"] is False
    assert "disk on fire" in payload["message"]


def test_missing_packages_are_reported_to_the_panel(monkeypatch, tmp_path):
    monkeypatch.setattr(smooth, "IMPORT_ERROR",
                        ImportError("No module named 'cv2'"))
    status = tmp_path / "status.json"
    assert run(monkeypatch, ["--video", "x.mov", "--status-file", str(status)]) == 3
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["done"] is True and payload["ok"] is False
    assert "cv2" in payload["message"]


def test_the_final_status_survives_a_reader_holding_the_file(tmp_path, monkeypatch):
    """Windows refuses the replace while the panel has the file open."""
    status = smooth.StatusFile(str(tmp_path / "status.json"))
    real_replace = Path.replace
    refusals = {"left": 5}

    def busy(self, target):
        if refusals["left"]:
            refusals["left"] -= 1
            raise PermissionError("in use")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", busy)
    status.write(done=True, ok=True, message="Finished")
    payload = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert payload["done"] is True


def test_a_sequence_tells_the_panel_how_to_import_it(make_anime, monkeypatch, tmp_path):
    """Handed one frame, Resolve imports a still. It needs a pattern and a range."""
    status = tmp_path / "status.json"
    src = make_anime(holds=(2,) * 6)
    out = tmp_path / "out"
    assert run(monkeypatch, ["--video", str(src), "--output-dir", str(out),
                             "--status-file", str(status)]) == 0
    payload = json.loads(status.read_text(encoding="utf-8"))
    folder = out / f"{src.stem}_smooth"
    assert payload["sequence_pattern"] == str(folder / "frame_%06d.png")
    assert payload["sequence_frames"] == 12
    assert payload["fps"] == 24.0, "the panel needs the rate to set on the sequence"
    assert (folder / ("frame_%06d.png" % (payload["sequence_frames"] - 1))).exists()


def test_json_output_is_the_whole_of_stdout(make_anime, monkeypatch, capsys, tmp_path):
    """`| jq` has to work, so progress lines must not share stdout."""
    src = make_anime(holds=(2,) * 6)
    assert run(monkeypatch, ["--video", str(src), "--json", "--analyse"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["plan"]["unique_drawings"] == 6


def test_json_output_reports_errors_too(monkeypatch, capsys, tmp_path):
    assert run(monkeypatch, ["--video", str(tmp_path / "missing.mov"), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "missing" in payload["error"]
    assert payload["exit_code"] == 2


def test_a_trimmed_clip_renders_only_its_range(make_anime, monkeypatch, tmp_path):
    """Seconds in, so the timeline's frame rate cannot skew it. 24 fps here."""
    src = make_anime(holds=(2,) * 12, fps=24.0)
    out = tmp_path / "out"
    status = tmp_path / "status.json"
    assert run(monkeypatch, ["--video", str(src), "--output-dir", str(out),
                             "--start-time", "0.25", "--end-time", "0.75",
                             "--status-file", str(status)]) == 0
    folder = out / f"{src.stem}_f6-18_smooth"
    assert len(list(folder.glob("frame_*.png"))) == 12
    assert json.loads(status.read_text(encoding="utf-8"))["sequence_frames"] == 12


def test_analyse_describes_only_the_trimmed_part(make_anime, monkeypatch, capsys):
    src = make_anime(holds=(2,) * 12, fps=24.0)
    assert run(monkeypatch, ["--video", str(src), "--analyse",
                             "--start-time", "0.25", "--end-time", "0.75"]) == 0
    out = capsys.readouterr().out
    assert "12 frames, 6 unique drawings" in out
    assert "source frames 6 to 17" in out


def test_in_and_out_points_outside_the_media_are_reported(make_anime, monkeypatch, capsys):
    src = make_anime(holds=(2,) * 12, fps=24.0)
    assert run(monkeypatch, ["--video", str(src), "--analyse",
                             "--start-time", "50", "--end-time", "52"]) == 2
    assert "outside its media" in capsys.readouterr().out


def test_a_range_covering_the_whole_file_is_the_whole_file(make_anime):
    src = make_anime(holds=(2,) * 12, fps=24.0)
    assert smooth.source_frames(src, 0.0, 1.0) is None
    assert smooth.source_frames(src, None, None) is None
    assert smooth.source_frames(src, 0.5, None) == (12, 24)


def test_a_clip_named_outside_the_system_code_page_runs(make_anime, repo_root, tmp_path):
    """The panel sends output to a log file, which Python on Windows encodes
    in the ANSI code page, so printing a Japanese file name used to crash."""
    import os
    import subprocess

    src = make_anime(holds=(2,) * 6)
    clip = src.with_name("ヴァイオレット" + src.suffix)
    src.rename(clip)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONUTF8"}
    env["PYTHONIOENCODING"] = "cp1252"
    log = tmp_path / "run.log"
    with open(log, "wb") as out:
        code = subprocess.call([sys.executable, "-m", "Stages.resolve_smooth",
                                "--video", str(clip), "--analyse"],
                               cwd=str(repo_root), env=env, stdout=out, stderr=out)
    text = log.read_bytes().decode("utf-8")
    assert code == 0, text
    assert "ヴァイオレット" in text
