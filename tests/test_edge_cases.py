"""Awkward clips. None of these should crash, hang, or corrupt the output."""

from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest

from Pipeline.config import Upflow2xConfig
from Pipeline.dedupe import plan_from_scores
from Pipeline.render import SourceError, analyse_video, render_plan


def cfg(**kw):
    c = Upflow2xConfig(upscale_enabled=False)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def roundtrip(src, tmp_path, read_frames, name="out.avi", **kw):
    c = cfg(**kw)
    plan, _w, _h = analyse_video(src, c)
    result = render_plan(src, plan, tmp_path / name, c)
    return plan, result, read_frames(result.output)


# --------------------------------------------------------------------------
# tiny clips
# --------------------------------------------------------------------------

def test_single_frame_clip(make_anime, tmp_path, read_frames):
    plan, _r, frames = roundtrip(make_anime(holds=(1,)), tmp_path, read_frames)
    assert plan.source_frames == 1
    assert len(frames) == 1


def test_two_identical_frames(make_anime, tmp_path, read_frames):
    plan, _r, frames = roundtrip(make_anime(holds=(2,)), tmp_path, read_frames)
    assert plan.source_frames == 2
    assert len(frames) == 2


def test_two_different_frames(make_anime, tmp_path, read_frames):
    plan, _r, frames = roundtrip(make_anime(holds=(1, 1)), tmp_path, read_frames)
    assert len(frames) == 2


def test_three_frames_on_twos(make_anime, tmp_path, read_frames):
    _plan, _r, frames = roundtrip(make_anime(holds=(2, 1)), tmp_path, read_frames)
    assert len(frames) == 3


# --------------------------------------------------------------------------
# degenerate content
# --------------------------------------------------------------------------

def test_a_completely_static_shot_is_a_noop(make_anime, tmp_path):
    """One drawing held for the whole clip has no motion to recover."""
    plan, _w, _h = analyse_video(make_anime(holds=(30,)), cfg())
    assert plan.unique_drawings == 1
    assert plan.is_noop, "a static shot should not be rebuilt"


def test_a_black_clip_does_not_crash(tmp_path, read_frames):
    import cv2
    src = tmp_path / "black.avi"
    writer = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"FFV1"), 24.0, (160, 120))
    for _ in range(20):
        writer.write(np.zeros((120, 160, 3), np.uint8))
    writer.release()

    plan, _w, _h = analyse_video(src, cfg())
    assert plan.is_noop


def test_pure_noise_is_treated_as_all_unique(tmp_path):
    """Every frame differs, so there is nothing to de-duplicate."""
    import cv2
    rng = np.random.default_rng(11)
    src = tmp_path / "noise.avi"
    writer = cv2.VideoWriter(str(src), cv2.VideoWriter_fourcc(*"FFV1"), 24.0, (160, 120))
    for _ in range(20):
        writer.write(rng.integers(0, 255, (120, 160, 3), dtype=np.uint8))
    writer.release()

    plan, _w, _h = analyse_video(src, cfg())
    assert plan.base_hold == 1
    assert plan.is_noop


# --------------------------------------------------------------------------
# awkward dimensions and rates
# --------------------------------------------------------------------------

@pytest.mark.parametrize("size", [(64, 48), (161, 121), (320, 180)])
def test_unusual_dimensions_are_preserved(make_anime, tmp_path, read_frames, size):
    """Output must match whatever the source actually decoded to.

    Video codecs quietly round odd dimensions to even, so the source may not be
    the size that was requested. What matters is that the rebuild does not
    change it again.
    """
    import cv2
    src = make_anime(holds=(2,) * 6, size=size, step=3)
    cap = cv2.VideoCapture(str(src))
    source_size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                   int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()

    _plan, result, frames = roundtrip(src, tmp_path, read_frames, name=f"o{size[0]}.avi")
    assert len(frames) == 12
    assert (result.width, result.height) == source_size


class _Frames:
    """Stands in for the decoder, so a test controls exactly what it hands over.

    OpenCV's video writers all round 161x121 down to 160x120, so a clip written
    to test odd sizes is no longer odd by the time it is read back.
    """

    def __init__(self, frames, fail_after=None):
        self.frames, self.fail_after, self.reads = list(frames), fail_after, 0

    def read(self):
        if not self.frames or (self.fail_after is not None and self.reads >= self.fail_after):
            return False, None
        self.reads += 1
        return True, self.frames.pop(0)

    def release(self):
        pass


def _odd_clip(drawings=6, hold=2, size=(161, 121)):
    import cv2
    w, h = size
    rng = np.random.default_rng(3)
    tex = cv2.GaussianBlur(rng.integers(0, 255, (h, w), dtype=np.uint8), (0, 0), 2.0)
    frames = []
    for drawing in range(drawings):
        M = np.float32([[1, 0, drawing * 3], [0, 1, 0]])
        shifted = cv2.warpAffine(np.dstack([tex] * 3), M, (w, h),
                                 borderMode=cv2.BORDER_REPLICATE)
        frames.extend([shifted] * hold)
    scores = [0.0] + [0.5 if i % hold == 0 else 0.0 for i in range(1, len(frames))]
    return frames, plan_from_scores(scores, 24.0, threshold=0.004)


def test_the_lossless_sequence_keeps_odd_dimensions_exactly(monkeypatch, tmp_path, read_frames):
    """PNG has no even-dimension requirement, which video codecs do."""
    from Pipeline import render
    frames, plan = _odd_clip()
    monkeypatch.setattr(render, "_open", lambda *a, **k: _Frames(frames))
    result = render_plan(tmp_path / "odd.mov", plan, tmp_path / "seq_out", cfg(),
                         sequence=True)
    out = read_frames(result.output)
    assert len(out) == 12
    assert (result.width, result.height) == (161, 121)
    assert out[0].shape[:2] == (121, 161)
    assert np.array_equal(out[0], frames[0]), "a real drawing was resampled"


@pytest.mark.parametrize("suffix", [".mp4", ".mkv"])
def test_a_video_file_refuses_odd_dimensions(monkeypatch, tmp_path, suffix):
    """Every writer here crops 161x121 to 160x120 without a word."""
    from Pipeline import render
    frames, plan = _odd_clip()
    monkeypatch.setattr(render, "_open", lambda *a, **k: _Frames(frames))
    with pytest.raises(SourceError, match="image sequence"):
        render_plan(tmp_path / "odd.mov", plan, tmp_path / f"out{suffix}", cfg())
    assert not (tmp_path / f"out{suffix}").exists()


def test_a_frame_that_cannot_be_read_stops_the_render(monkeypatch, tmp_path):
    """It used to repeat the last frame it had, and report a finished render."""
    from Pipeline import render
    frames, plan = _odd_clip(size=(160, 120))
    monkeypatch.setattr(render, "_open", lambda *a, **k: _Frames(frames, fail_after=5))
    with pytest.raises(SourceError, match="frame 5"):
        render_plan(tmp_path / "src.mov", plan, tmp_path / "seq_out", cfg(), sequence=True)


@pytest.mark.parametrize("fps", [12.0, 23.976, 25.0, 29.97, 60.0])
def test_various_frame_rates_are_preserved(make_anime, tmp_path, read_frames, fps):
    src = make_anime(holds=(2,) * 6, fps=fps)
    plan, result, _frames = roundtrip(src, tmp_path, read_frames, name=f"o{int(fps)}.avi")
    assert result.fps == pytest.approx(fps, rel=0.02)


def _ffmpeg(*args):
    """Make a fixture OpenCV cannot write, one with its own timestamps."""
    exe = shutil.which("ffmpeg")
    if not exe:
        pytest.skip("ffmpeg makes the timestamped fixtures")
    subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", *map(str, args)],
                   check=True)


def test_a_variable_frame_rate_clip_is_refused(make_anime, tmp_path):
    """The render runs at one rate, so a clip whose frames are unevenly spaced
    would change length: 0.72 s of input once came out as 0.24 s."""
    src = make_anime(holds=(2,) * 3, fps=25.0)
    vfr = tmp_path / "vfr.mkv"
    # Frames 0-2 are 40 ms apart, then 120 and 200 ms.
    _ffmpeg("-i", src, "-vf", "setpts='if(lt(N,3),N,5*N-10)/(25*TB)'",
            "-fps_mode", "vfr", "-c:v", "ffv1", vfr)
    with pytest.raises(SourceError, match="variable frame rate"):
        analyse_video(vfr, cfg())
    # A range after the uneven part starts at the wrong time too.
    with pytest.raises(SourceError, match="variable frame rate"):
        analyse_video(vfr, cfg(), frames=(4, 6))


@pytest.mark.parametrize("fps", [23.976, 29.97, 59.94])
def test_rounded_timestamps_still_count_as_a_constant_rate(make_anime, tmp_path, fps):
    """Matroska and WebM store whole milliseconds, so frames at these rates sit
    up to half a millisecond off their exact time."""
    src = make_anime(holds=(2,) * 60, fps=fps)
    mkv = tmp_path / "cfr.mkv"
    _ffmpeg("-i", src, "-c:v", "ffv1", mkv)
    plan, _w, _h = analyse_video(mkv, cfg())
    assert plan.source_frames == 120
    plan, _w, _h = analyse_video(mkv, cfg(), frames=(100, 120))
    assert plan.source_frames == 20


def test_upscale_doubles_whatever_the_source_is(make_anime, tmp_path, read_frames):
    import cv2
    src = make_anime(holds=(2,) * 4, size=(162, 122), step=3)
    cap = cv2.VideoCapture(str(src))
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    _plan, result, frames = roundtrip(src, tmp_path, read_frames,
                                      upscale_enabled=True, upscale_factor=2)
    assert (result.width, result.height) == (w * 2, h * 2)
    assert frames[0].shape[:2] == (h * 2, w * 2)


# --------------------------------------------------------------------------
# unusual timing
# --------------------------------------------------------------------------

def test_very_long_hold_among_short_ones(make_anime, tmp_path, read_frames):
    src = make_anime(holds=(2, 2, 40, 2, 2))
    plan, _r, frames = roundtrip(src, tmp_path, read_frames)
    assert plan.base_hold == 2
    assert len(frames) == plan.source_frames


def test_wildly_mixed_timing(make_anime, tmp_path, read_frames):
    src = make_anime(holds=(1, 5, 2, 3, 1, 8, 2, 2, 4))
    plan, _r, frames = roundtrip(src, tmp_path, read_frames)
    assert len(frames) == plan.source_frames


def test_a_clip_that_is_one_long_hold_then_motion(make_anime, tmp_path, read_frames):
    src = make_anime(holds=(20, 2, 2, 2, 2))
    plan, _r, frames = roundtrip(src, tmp_path, read_frames)
    assert len(frames) == plan.source_frames


def test_on_threes_with_a_stray_two(make_anime, tmp_path, read_frames):
    src = make_anime(holds=(3, 3, 2, 3, 3, 3))
    plan, _r, _frames = roundtrip(src, tmp_path, read_frames)
    assert plan.base_hold == 3


# --------------------------------------------------------------------------
# thresholds
# --------------------------------------------------------------------------

def test_an_absurdly_high_threshold_finds_one_drawing(make_anime, tmp_path):
    plan, _w, _h = analyse_video(make_anime(holds=(2,) * 10),
                                 cfg(duplicate_threshold=0.99))
    assert plan.unique_drawings == 1


def test_a_zero_threshold_treats_everything_as_unique(make_anime, tmp_path):
    plan, _w, _h = analyse_video(make_anime(holds=(2,) * 10),
                                 cfg(duplicate_threshold=0.0))
    assert plan.is_noop


def test_forcing_a_hold_larger_than_the_clip(make_anime, tmp_path, read_frames):
    src = make_anime(holds=(2, 2))
    c = cfg(force_base_hold=99)
    plan, _w, _h = analyse_video(src, c)
    result = render_plan(src, plan, tmp_path / "o.avi", c)
    assert result.frames_written == plan.source_frames


# --------------------------------------------------------------------------
# broken input
# --------------------------------------------------------------------------

def test_a_missing_file(tmp_path):
    with pytest.raises(SourceError):
        analyse_video(tmp_path / "nope.avi", cfg())


def test_a_file_that_is_not_a_video(tmp_path):
    junk = tmp_path / "notavideo.avi"
    junk.write_bytes(b"this is not a video" * 100)
    with pytest.raises(SourceError):
        analyse_video(junk, cfg())


def test_an_empty_file(tmp_path):
    empty = tmp_path / "empty.avi"
    empty.write_bytes(b"")
    with pytest.raises(SourceError):
        analyse_video(empty, cfg())


def test_planning_with_no_scores():
    plan = plan_from_scores([], 24.0, threshold=0.004)
    assert plan.source_frames == 0
    assert plan.output_frames == 0
    assert plan.stretch == 1.0
