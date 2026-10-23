"""The full rebuild: video in, smoothly interpolated video out."""

from __future__ import annotations

import numpy as np
import pytest

from Pipeline.config import Eternal2xConfig
from Pipeline.render import SourceError, analyse_video, render_plan


def cfg(**kw) -> Eternal2xConfig:
    c = Eternal2xConfig(upscale_enabled=False)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def duplicate_pairs(frames):
    return sum(1 for i in range(1, len(frames))
               if np.array_equal(frames[i], frames[i - 1]))


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------

def test_analyse_detects_a_clip_on_twos(make_anime):
    plan, w, h = analyse_video(make_anime(holds=(2,) * 12), cfg())
    assert plan.source_frames == 24
    assert plan.unique_drawings == 12
    assert plan.base_hold == 2
    assert (w, h) == (200, 120)


def test_analyse_detects_a_clip_on_threes(make_anime):
    plan, _, _ = analyse_video(make_anime(holds=(3,) * 8), cfg())
    assert plan.unique_drawings == 8
    assert plan.base_hold == 3


def test_analyse_reads_the_frame_rate(make_anime):
    plan, _, _ = analyse_video(make_anime(holds=(2,) * 6, fps=30.0), cfg())
    assert plan.fps == pytest.approx(30.0, abs=0.5)


def test_analyse_treats_every_frame_unique_footage_as_a_noop(make_anime):
    plan, _, _ = analyse_video(make_anime(holds=(1,) * 24), cfg())
    assert plan.base_hold == 1
    assert plan.is_noop


def test_analyse_handles_mixed_holds(make_anime):
    plan, _, _ = analyse_video(make_anime(holds=(2, 2, 3, 2, 2, 6, 2, 2)), cfg())
    assert plan.base_hold == 2
    assert plan.unique_drawings == 8


def test_analyse_rejects_a_missing_file(tmp_path):
    with pytest.raises(SourceError, match="Could not open"):
        analyse_video(tmp_path / "nope.avi", cfg())


def test_analysis_is_deterministic(make_anime):
    src = make_anime(holds=(2,) * 10)
    a, _, _ = analyse_video(src, cfg())
    b, _, _ = analyse_video(src, cfg())
    assert a.to_dict() == b.to_dict()


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def test_output_has_the_same_length_as_the_source(make_anime, tmp_path, read_frames):
    """The result has to drop straight back into an edit."""
    src = make_anime(holds=(2,) * 12)
    plan, _, _ = analyse_video(src, cfg())
    result = render_plan(src, plan, tmp_path / "out.avi", cfg())

    assert result.frames_written == 24
    assert len(read_frames(result.output)) == 24


def test_output_keeps_the_source_frame_rate(make_anime, tmp_path):
    src = make_anime(holds=(2,) * 12, fps=24.0)
    plan, _, _ = analyse_video(src, cfg())
    result = render_plan(src, plan, tmp_path / "out.avi", cfg())
    assert result.fps == pytest.approx(24.0, abs=0.5)


def test_duplicate_frames_are_gone(make_anime, tmp_path, read_frames):
    """The whole point: no more stepped motion."""
    src = make_anime(holds=(2,) * 12)
    plan, _, _ = analyse_video(src, cfg())
    result = render_plan(src, plan, tmp_path / "out.avi", cfg())

    frames = read_frames(result.output)
    # The final drawing has no successor to interpolate towards, so the last
    # slot is held. Everything before it must be genuinely new.
    body = frames[:-plan.base_hold]
    assert duplicate_pairs(body) == 0, (
        f"{duplicate_pairs(body)} held frames survived the rebuild"
    )


def test_motion_becomes_evenly_spaced(make_anime, tmp_path, read_frames):
    """Stepped 2s motion should come out as steady per-frame movement."""
    src = make_anime(holds=(2,) * 12, step=6)
    plan, _, _ = analyse_video(src, cfg())
    result = render_plan(src, plan, tmp_path / "out.avi", cfg())

    frames = read_frames(result.output)[:-plan.base_hold]   # drop the held tail
    deltas = [float(np.abs(frames[i].astype(float) - frames[i - 1].astype(float)).mean())
              for i in range(1, len(frames))]
    # Every step should be a similar size; a stepped clip alternates big/zero.
    assert min(deltas) > 0.5, f"some frames barely moved: min delta {min(deltas):.2f}"
    assert max(deltas) / min(deltas) < 4.0, (
        f"motion is still uneven: deltas ranged {min(deltas):.2f}..{max(deltas):.2f}"
    )


def test_original_drawings_survive_untouched(make_anime, tmp_path, read_frames):
    """Real drawings must pass through, not be resampled into softness."""
    src = make_anime(holds=(2,) * 12)
    plan, _, _ = analyse_video(src, cfg())
    result = render_plan(src, plan, tmp_path / "out.avi", cfg())

    source_frames = read_frames(src)
    out_frames = read_frames(result.output)
    # On a clean 2s clip, even output frames land exactly on real drawings.
    exact = sum(1 for i in range(0, len(out_frames), 2)
                if np.array_equal(out_frames[i], source_frames[i]))
    assert exact >= 10, f"only {exact} of 12 original drawings came through untouched"


def test_a_long_hold_stays_still(make_anime, tmp_path, read_frames):
    """A deliberate held pose must not be smoothed into drifting motion."""
    src = make_anime(holds=(2, 2, 12, 2, 2))
    plan, _, _ = analyse_video(src, cfg())
    result = render_plan(src, plan, tmp_path / "out.avi", cfg())

    frames = read_frames(result.output)
    # The still occupies source frames 4..15; sample well inside it.
    inner = frames[7:14]
    deltas = [float(np.abs(inner[i].astype(float) - inner[i - 1].astype(float)).mean())
              for i in range(1, len(inner))]
    assert max(deltas) < 1.0, (
        f"the held pose drifted during the rebuild, max frame delta {max(deltas):.2f}"
    )


def test_a_hard_cut_does_not_ghost(make_anime, tmp_path, read_frames):
    """Blending across a scene change would produce a double exposure."""
    src = make_anime(holds=(2,) * 12, cut_at=6)
    plan, _, _ = analyse_video(src, cfg())
    result = render_plan(src, plan, tmp_path / "out.avi", cfg())

    frames = read_frames(result.output)
    deltas = [float(np.abs(frames[i].astype(float) - frames[i - 1].astype(float)).mean())
              for i in range(1, len(frames))]
    biggest = max(deltas)
    others = sorted(deltas)[:-1]
    # The cut should stay one hard jump, not smear across several blended frames.
    assert biggest > 4 * (sum(others) / len(others)), (
        f"the cut was blended away: largest jump {biggest:.1f} against a "
        f"mean of {sum(others) / len(others):.1f}"
    )


def test_a_hard_cut_stays_on_its_frame(make_anime, tmp_path, read_frames):
    """A cut that lands a frame early is out of sync with the edit and the audio."""
    src = make_anime(holds=(2,) * 12, cut_at=6)
    plan, _, _ = analyse_video(src, cfg())
    out = read_frames(render_plan(src, plan, tmp_path / "out.avi", cfg()).output)
    source = read_frames(src)

    def jump(frames):
        deltas = [float(np.abs(frames[i].astype(float) - frames[i - 1].astype(float)).mean())
                  for i in range(1, len(frames))]
        return 1 + int(np.argmax(deltas))

    assert jump(out) == jump(source), (
        f"the cut is at frame {jump(out)}, not frame {jump(source)} as in the source"
    )


def test_render_reports_output_dimensions(make_anime, tmp_path):
    src = make_anime(holds=(2,) * 6, size=(200, 120))
    plan, _, _ = analyse_video(src, cfg())
    result = render_plan(src, plan, tmp_path / "out.avi", cfg())
    assert (result.width, result.height) == (200, 120)


def test_upscale_doubles_the_output(make_anime, tmp_path, read_frames):
    src = make_anime(holds=(2,) * 6, size=(200, 120))
    c = cfg(upscale_enabled=True, upscale_factor=2)
    plan, _, _ = analyse_video(src, c)
    result = render_plan(src, plan, tmp_path / "out.avi", c)

    assert (result.width, result.height) == (400, 240)
    assert read_frames(result.output)[0].shape[:2] == (240, 400)


def test_png_sequence_output(make_anime, tmp_path, read_frames):
    """A directory target writes a lossless image sequence."""
    src = make_anime(holds=(2,) * 8)
    plan, _, _ = analyse_video(src, cfg())
    out = tmp_path / "sequence"
    result = render_plan(src, plan, out, cfg())

    assert result.is_sequence
    assert result.frames_written == 16
    assert len(sorted(out.glob("*.png"))) == 16
    assert len(read_frames(out)) == 16


def test_png_sequence_is_zero_padded_and_ordered(make_anime, tmp_path):
    src = make_anime(holds=(2,) * 6)
    plan, _, _ = analyse_video(src, cfg())
    out = tmp_path / "sequence"
    render_plan(src, plan, out, cfg())
    names = [p.name for p in sorted(out.glob("*.png"))]
    assert names[0] == "frame_000000.png"
    assert names == sorted(names), "frame numbering does not sort in playback order"


def test_progress_is_reported(make_anime, tmp_path):
    src = make_anime(holds=(2,) * 8)
    seen = []
    c = cfg()
    plan, _, _ = analyse_video(src, c, progress=lambda s, f: seen.append((s, f)))
    render_plan(src, plan, tmp_path / "out.avi", c,
                progress=lambda s, f: seen.append((s, f)))

    stages = {s for s, _ in seen}
    assert "Analysing" in stages and "Rendering" in stages
    assert all(0.0 <= f <= 1.0 for _, f in seen)
    assert max(f for s, f in seen if s == "Rendering") == pytest.approx(1.0, abs=0.01)


def test_quality_presets_all_render(make_anime, tmp_path, read_frames):
    src = make_anime(holds=(2,) * 6)
    for quality in ("fast", "better", "best"):
        c = cfg(quality=quality)
        plan, _, _ = analyse_video(src, c)
        result = render_plan(src, plan, tmp_path / f"out_{quality}.avi", c)
        assert len(read_frames(result.output)) == 12, f"{quality} produced the wrong length"


def test_render_rejects_an_empty_plan(make_anime, tmp_path):
    from Pipeline.dedupe import DedupePlan
    src = make_anime(holds=(2,) * 4)
    with pytest.raises(SourceError, match="no frames"):
        render_plan(src, DedupePlan(source_frames=0, fps=24.0), tmp_path / "o.avi", cfg())


def test_noop_footage_still_renders_the_right_length(make_anime, tmp_path, read_frames):
    """Nothing to dedupe, but the render must not corrupt the clip."""
    src = make_anime(holds=(1,) * 20)
    plan, _, _ = analyse_video(src, cfg())
    result = render_plan(src, plan, tmp_path / "out.avi", cfg())
    assert len(read_frames(result.output)) == 20


def test_interpolation_can_be_turned_off(make_anime, tmp_path, read_frames):
    """With interpolation off, every output frame must be a real drawing."""
    src = make_anime(holds=(2,) * 10)
    source_frames = read_frames(src)

    c = cfg(interpolate_enabled=False)
    plan, _w, _h = analyse_video(src, c)
    result = render_plan(src, plan, tmp_path / "off.avi", c)

    out = read_frames(result.output)
    assert len(out) == plan.source_frames
    originals = {i: f.tobytes() for i, f in enumerate(source_frames)}
    for frame in out:
        assert frame.tobytes() in originals.values(), (
            "an output frame was synthesised even though interpolation was off"
        )


def test_interpolation_off_keeps_every_drawing_on_its_frame(make_anime, tmp_path,
                                                            read_frames):
    """With nothing generated, a drawing shown a frame early is just out of sync."""
    src = make_anime(holds=(2, 3, 2, 4, 2, 2, 3))
    source = read_frames(src)

    c = cfg(interpolate_enabled=False)
    plan, _w, _h = analyse_video(src, c)
    out = read_frames(render_plan(src, plan, tmp_path / "off.avi", c).output)

    assert len(out) == len(source)
    moved = [i for i, (a, b) in enumerate(zip(out, source)) if not np.array_equal(a, b)]
    assert not moved, f"frames {moved} show a different drawing than the source"


def test_turning_interpolation_off_changes_the_result(make_anime, tmp_path, read_frames):
    src = make_anime(holds=(2,) * 10)
    plan, _w, _h = analyse_video(src, cfg())

    on = read_frames(render_plan(src, plan, tmp_path / "on.avi", cfg()).output)
    off = read_frames(render_plan(src, plan, tmp_path / "off.avi",
                                  cfg(interpolate_enabled=False)).output)

    assert len(on) == len(off)
    assert any(not np.array_equal(a, b) for a, b in zip(on, off)), (
        "the interpolate setting made no difference to the output"
    )


# --------------------------------------------------------------------------
# a trimmed range of the source
# --------------------------------------------------------------------------

def test_analysing_a_range_reads_only_that_range(make_anime):
    """A two second cut of a long file must not cost the whole file."""
    plan, _w, _h = analyse_video(make_anime(holds=(2,) * 12), cfg(), frames=(6, 18))
    assert plan.source_frames == 12
    assert plan.unique_drawings == 6


def test_a_range_starts_on_exactly_the_frame_asked_for(make_anime, tmp_path, read_frames):
    """Off by one here would shift every drawing against the edit."""
    src = make_anime(holds=(2,) * 12)
    source = read_frames(src)
    plan, _w, _h = analyse_video(src, cfg(), frames=(6, 18))
    out = tmp_path / "range"
    result = render_plan(src, plan, out, cfg(), sequence=True, frames=(6, 18))
    frames = read_frames(out)
    assert result.frames_written == len(frames) == 12
    assert np.array_equal(frames[0], source[6]), "the render did not start at frame 6"


def test_reaching_the_start_of_a_range_reports_progress(make_anime, tmp_path):
    """Every frame before the range is decoded to reach it, which can take
    minutes deep into an episode. The panel takes minutes of silence for a
    dead run, so both passes must report while they get there."""
    src = make_anime(holds=(2,) * 12)
    stages = []
    plan, _w, _h = analyse_video(src, cfg(), frames=(6, 18),
                                 progress=lambda s, f: stages.append(s))
    assert stages[0] == "Seeking"
    stages.clear()
    render_plan(src, plan, tmp_path / "range", cfg(), sequence=True, frames=(6, 18),
                progress=lambda s, f: stages.append(s))
    assert stages[0] == "Seeking"


def test_a_range_past_the_end_of_the_file_stops_at_the_end(make_anime):
    plan, _w, _h = analyse_video(make_anime(holds=(2,) * 12), cfg(), frames=(20, 400))
    assert plan.source_frames == 4
