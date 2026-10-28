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


def test_a_colour_change_at_equal_brightness_is_a_new_drawing(tmp_path):
    """Pure red and this green have the same luma, so comparing brightness
    alone saw no change and the green drawing vanished from the result."""
    import cv2
    src = tmp_path / "colour.avi"
    writer = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"FFV1"), 24.0, (160, 120))
    for bgr in [(0, 0, 255)] * 2 + [(0, 130, 0)] * 2 + [(220, 220, 220)] * 2:
        writer.write(np.full((120, 160, 3), bgr, np.uint8))
    writer.release()
    plan, _w, _h = analyse_video(src, cfg())
    assert [(h.index, h.length) for h in plan.holds] == [(0, 2), (2, 2), (4, 2)]


def test_a_slow_fade_keeps_moving(tmp_path, read_frames):
    """One level a frame is under the threshold, so comparing each frame only
    with the one before found no change and froze 22 frames of a fade."""
    import cv2
    src = tmp_path / "fade.avi"
    writer = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"FFV1"), 24.0, (160, 120))
    for level in range(40, 64):
        writer.write(np.full((120, 160, 3), level, np.uint8))
    writer.release()
    c = cfg(interpolate_enabled=False)
    plan, _w, _h = analyse_video(src, c)
    assert max(h.length for h in plan.holds) <= 2
    out = read_frames(render_plan(src, plan, tmp_path / "out", c, sequence=True).output)
    lag = max(40 + i - int(f[0, 0, 0]) for i, f in enumerate(out))
    assert lag <= 1, f"the fade fell {lag} levels behind the source"


def test_a_slow_pan_is_not_one_long_hold(make_anime):
    """A tenth of a pixel a frame never crossed the threshold between
    neighbours, so a moving shot came out as one still drawing. Holds up to
    4 frames still count as timing, not as a deliberate still."""
    plan, _w, _h = analyse_video(make_anime(holds=(1,) * 24, step=0.1), cfg())
    assert max(h.length for h in plan.holds) <= 4


def _drawings_on_twos(tmp_path, change=None):
    """12 flat drawings, each held two frames, with `change(frame, i)` applied."""
    import cv2
    src = tmp_path / "twos.avi"
    writer = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"FFV1"), 24.0, (320, 180))
    for i in range(24):
        d = i // 2
        frame = np.full((180, 320, 3), 180, np.uint8)
        cv2.rectangle(frame, (40 + d * 4, 40), (80 + d * 4, 110), (30, 30, 30), 3)
        cv2.circle(frame, (60 + d * 4, 70), 12, (75, 110, 180), -1)
        writer.write(change(frame, i) if change else frame)
    writer.release()
    return src


@pytest.mark.parametrize("sigma", [1, 2, 4])
def test_grain_does_not_hide_the_drawings(tmp_path, sigma):
    """Grain of 1 code value put every held frame over the threshold, so 12
    drawings on 2s came out as 24 unique frames and nothing was rebuilt."""
    rng = np.random.default_rng(17)

    def grain(frame, _i):
        noise = rng.normal(0, sigma, frame.shape[:2] + (1,))
        return np.clip(frame + noise, 0, 255).astype(np.uint8)

    plan, _w, _h = analyse_video(_drawings_on_twos(tmp_path, grain), cfg(grain=True))
    assert plan.unique_drawings == 12
    assert plan.base_hold == 2


def test_correlated_grain_does_not_hide_the_drawings(tmp_path):
    """Grain blurred across neighbouring pixels, as compression leaves it."""
    import cv2
    rng = np.random.default_rng(5)

    def grain(frame, _i):
        noise = cv2.GaussianBlur(rng.normal(0, 1, frame.shape[:2]), (0, 0), 1.0)
        noise *= 1.0 / noise.std()
        return np.clip(frame + noise[..., None], 0, 255).astype(np.uint8)

    plan, _w, _h = analyse_video(_drawings_on_twos(tmp_path, grain), cfg(grain=True))
    assert plan.unique_drawings == 12


def test_grain_is_only_seen_through_when_asked(tmp_path):
    """Unasked, a grainy clip is all new frames, and says it looks grainy."""
    rng = np.random.default_rng(17)

    def grain(frame, _i):
        noise = rng.normal(0, 1, frame.shape[:2] + (1,))
        return np.clip(frame + noise, 0, 255).astype(np.uint8)

    plan, _w, _h = analyse_video(_drawings_on_twos(tmp_path, grain), cfg())
    assert plan.unique_drawings == 24
    assert plan.grain_suspected


def test_a_clean_clip_is_not_suspected_of_grain(make_anime):
    plan, _w, _h = analyse_video(make_anime(holds=(2,) * 12), cfg())
    assert not plan.grain_suspected


def test_fine_moving_detail_keeps_every_drawing(tmp_path):
    """Diagonal shading moving 1 px a frame inside a shape that moves 8 px every
    fourth frame. Every frame is distinct and over the threshold, and taking
    the small steps for grain merged half of them."""
    import cv2
    src = tmp_path / "detail.avi"
    writer = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"FFV1"), 24.0, (640, 360))
    yy, xx = np.mgrid[0:48, 0:48]
    for i in range(24):
        frame = np.full((360, 640, 3), 200, np.uint8)
        x = 200 + 8 * (i // 4)
        cv2.rectangle(frame, (x, 120), (x + 120, 240), (90, 90, 90), -1)
        shade = np.where(((xx + yy + i) // 2) % 2 == 0, 96, 160).astype(np.uint8)
        frame[150:198, x + 30:x + 78] = shade[..., None]
        writer.write(frame)
    writer.release()
    plan, _w, _h = analyse_video(src, cfg())
    assert plan.unique_drawings == 24


def test_flicker_is_not_taken_for_grain(tmp_path):
    """Flicker shifts every pixel the same way, as a fade does, and a fade's
    steps are real. Taking it for grain would merge real frames elsewhere, so
    a flickering clip reads as all new frames and nothing is merged."""
    def flicker(frame, i):
        return np.clip(frame.astype(int) + 2 * (i % 2), 0, 255).astype(np.uint8)

    plan, _w, _h = analyse_video(_drawings_on_twos(tmp_path, flicker), cfg())
    assert plan.unique_drawings == 24


def _cel_on_ones(tmp_path, steps):
    """24 distinct cel drawings on 1s, moving the character `steps` pixels in turn."""
    import cv2
    from tests.drawings import cel
    src = tmp_path / "ones.avi"
    writer = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"FFV1"), 24.0, (320, 240))
    x = 100
    for i in range(24):
        writer.write(cel(x, 120))
        x += steps[i % len(steps)]
    writer.release()
    return src


@pytest.mark.parametrize("steps", [(1, 1, 1, 4), (2, 10)])
def test_small_real_moves_are_not_taken_for_grain(tmp_path, steps):
    """A clip moving 1 px three times and 4 px the fourth has two clusters of
    scores, as grain on held frames does. Read as grain, half its drawings were
    merged away. A real move survives a slight blur, and grain does not."""
    plan, _w, _h = analyse_video(_cel_on_ones(tmp_path, steps), cfg())
    assert plan.unique_drawings == 24


def test_a_fade_with_uneven_steps_keeps_every_frame(tmp_path):
    """A fade of 1, 1, 1 then 4 levels clusters like grain too."""
    import cv2
    src = tmp_path / "fade.avi"
    writer = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"FFV1"), 24.0, (160, 120))
    level = 40
    for i in range(24):
        writer.write(np.full((120, 160, 3), level, np.uint8))
        level += (1, 1, 1, 4)[i % 4]
    writer.release()
    plan, _w, _h = analyse_video(src, cfg())
    assert max(h.length for h in plan.holds) <= 2, "the fade froze"


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


def test_a_marked_clip_is_drawn_through_a_cut(make_anime, tmp_path, read_frames):
    """The editor's markers say which frames go together, so Render draws
    every gap between two of them and second-guesses none."""
    from Pipeline.dedupe import plan_from_marks
    src = make_anime(holds=(2,) * 12, cut_at=6)
    plan = plan_from_marks(range(0, 24, 2), 24, 24.0)
    out = read_frames(render_plan(src, plan, tmp_path / "out.avi", cfg()).output)
    held = [i for i in range(1, 23, 2)
            if float(np.abs(out[i].astype(float) - out[i - 1].astype(float)).mean()) < 0.5]
    assert not held, f"in-betweens {held} were held between two markers"


def _held_in_betweens(monkeypatch, make_anime, tmp_path, read_frames, scores):
    """Render eight drawings on 2s with each pair's shot change set by hand."""
    from Pipeline import interpolate
    given = iter(scores)
    monkeypatch.setattr(interpolate, "_disagreement", lambda a, b: next(given))
    src = make_anime(holds=(2,) * 8)
    plan, _, _ = analyse_video(src, cfg())
    out = read_frames(render_plan(src, plan, tmp_path / "out.avi", cfg()).output)
    return [i for i in range(1, 15, 2)
            if float(np.abs(out[i].astype(float) - out[i - 1].astype(float)).mean()) < 0.5]


def test_fast_action_through_a_whole_shot_is_interpolated(monkeypatch, make_anime, tmp_path,
                                                          read_frames):
    """Fast action disagrees as much as a cut. Holding every such pair left a
    Jujutsu Kaisen fight still on 37 of its 53 in-betweens."""
    held = _held_in_betweens(monkeypatch, make_anime, tmp_path, read_frames,
                             [0.15, 0.2, 0.12, 0.18, 0.15, 0.2, 0.14])
    assert not held, f"in-betweens {held} were held as if the shot cut there"


def test_a_cut_between_calm_pairs_still_holds(monkeypatch, make_anime, tmp_path, read_frames):
    held = _held_in_betweens(monkeypatch, make_anime, tmp_path, read_frames,
                             [0.01, 0.02, 0.01, 0.2, 0.02, 0.01, 0.02])
    assert held == [7], f"held {held}, expected only the in-between across the cut"


def test_a_move_between_two_poses_is_judged_against_the_moves_around_it(
        monkeypatch, make_anime, tmp_path, read_frames):
    """A pose fills several slots with one drawing. Those repeats are not
    changes, so they must not make the move between two poses stand out."""
    from Pipeline import interpolate
    given = iter([0.08, 0.13, 0.08])
    monkeypatch.setattr(interpolate, "_disagreement", lambda a, b: next(given))
    src = make_anime(holds=(2, 12, 12, 2))
    plan, _, _ = analyse_video(src, cfg())
    assert plan.output_indices == [0] + [2] * 6 + [14] * 6 + [26]
    out = read_frames(render_plan(src, plan, tmp_path / "out.avi", cfg()).output)
    # Frame 13 is the in-between from the first pose to the second.
    assert float(np.abs(out[13].astype(float) - out[12].astype(float)).mean()) > 0.5, (
        "the move between the two poses was held as if the shot cut there")


def test_slow_motion_draws_three_times_the_frames_at_the_same_rate(make_anime, tmp_path,
                                                                  read_frames):
    """Three times the frames, for the editor to time remap. Each drawing
    lands three times further in and every frame between two is drawn."""
    src = make_anime(holds=(2,) * 8)
    plan, _, _ = analyse_video(src, cfg())
    result = render_plan(src, plan, tmp_path / "out", cfg(slow_motion=3), sequence=True)
    out, source = read_frames(result.output), read_frames(src)
    assert result.frames_written == len(out) == 48
    assert result.fps == plan.fps
    assert all(np.array_equal(out[3 * i], source[i]) for i in range(0, 16, 2))
    held = [i for i in range(1, 42)
            if float(np.abs(out[i].astype(float) - out[i - 1].astype(float)).mean()) < 0.5]
    assert not held, f"frames {held} repeat the one before"


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


def miscounted(monkeypatch, count):
    """Have OpenCV report `count` frames, as it does for files that only carry
    an estimate: an MKV that was never finished says -221139789095419232, and
    an MPEG-2 program stream 238 where 240 decode."""
    import cv2
    from Pipeline import render
    real = cv2.VideoCapture

    class Capture:
        def __init__(self, path):
            self._cap = real(path)

        def get(self, prop):
            return count if prop == cv2.CAP_PROP_FRAME_COUNT else self._cap.get(prop)

        def __getattr__(self, name):
            return getattr(self._cap, name)

    monkeypatch.setattr(render.cv2, "VideoCapture", Capture)


@pytest.mark.parametrize("count", [-221139789095419232, 20])
def test_every_frame_is_read_whatever_the_file_says_its_count_is(make_anime, monkeypatch,
                                                                  tmp_path, count):
    src = make_anime(holds=(2,) * 12)
    miscounted(monkeypatch, count)
    plan, _w, _h = analyse_video(src, cfg())
    assert plan.source_frames == 24
    result = render_plan(src, plan, tmp_path / "out", cfg(), sequence=True)
    assert result.frames_written == 24


def test_a_long_gap_between_markers_is_drawn_a_few_frames_at_a_time(monkeypatch, make_anime,
                                                                    tmp_path):
    """A minute's pause between two markers was drawn as one list, held in
    memory before the first frame was written: over 30 GB at 4K."""
    from Pipeline import rife
    from Pipeline.dedupe import plan_from_marks
    from Pipeline.render import BATCH

    asked = []
    real = rife.Rife.interpolate_pair

    def spy(self, a, b, times, **kw):
        asked.append(len(times))
        return real(self, a, b, times, **kw)

    monkeypatch.setattr(rife.Rife, "interpolate_pair", spy)
    src = make_anime(holds=(30, 2))
    plan, _, _ = analyse_video(src, Eternal2xConfig())
    marked = plan_from_marks([0, 30], plan.source_frames, plan.fps)
    result = render_plan(src, marked, tmp_path / "out", Eternal2xConfig(), sequence=True)
    assert result.frames_written == 32
    assert max(asked) <= BATCH and sum(asked) >= 29
