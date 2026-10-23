"""The optical-flow in-betweener, including how it fails."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from Pipeline.interpolate import (
    CUT_THRESHOLD,
    frames_are_a_cut,
    interpolate_pair,
    upscale,
)


@pytest.fixture
def texture():
    rng = np.random.default_rng(7)
    base = cv2.GaussianBlur(rng.integers(0, 255, (120, 200), dtype=np.uint8), (0, 0), 2.0)
    return np.dstack([base] * 3)


def shifted(img, dx, dy=0):
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (img.shape[1], img.shape[0]),
                          borderMode=cv2.BORDER_REPLICATE)


def mean_err(a, b):
    return float(np.abs(a.astype(float) - b.astype(float)).mean())


# --------------------------------------------------------------------------
# endpoints
# --------------------------------------------------------------------------

def test_t_zero_returns_the_first_drawing_untouched(texture):
    b = shifted(texture, 8)
    assert np.array_equal(interpolate_pair(texture, b, [0.0])[0], texture)


def test_t_one_returns_the_second_drawing_untouched(texture):
    b = shifted(texture, 8)
    assert np.array_equal(interpolate_pair(texture, b, [1.0])[0], b)


def test_times_outside_the_range_are_clamped(texture):
    b = shifted(texture, 8)
    out = interpolate_pair(texture, b, [-0.5, 1.5])
    assert np.array_equal(out[0], texture)
    assert np.array_equal(out[1], b)


def test_no_times_gives_no_frames(texture):
    assert interpolate_pair(texture, texture, []) == []


def test_many_times_in_one_call(texture):
    b = shifted(texture, 12)
    out = interpolate_pair(texture, b, [0.25, 0.5, 0.75])
    assert len(out) == 3
    assert all(f.shape == texture.shape for f in out)
    assert all(f.dtype == np.uint8 for f in out)


# --------------------------------------------------------------------------
# it actually tracks motion
# --------------------------------------------------------------------------

@pytest.mark.parametrize("dx", [4, 8, 16])
def test_interpolation_beats_a_cross_dissolve(texture, dx):
    """The reason to use optical flow at all."""
    b = shifted(texture, dx)
    truth = shifted(texture, dx / 2.0)
    mid = interpolate_pair(texture, b, [0.5])[0]
    dissolve = (texture.astype(np.float32) * 0.5 + b.astype(np.float32) * 0.5).astype(np.uint8)

    assert mean_err(mid, truth) < mean_err(dissolve, truth) / 3.0, (
        f"flow error {mean_err(mid, truth):.2f} vs dissolve {mean_err(dissolve, truth):.2f}"
    )


def test_the_in_between_lands_between_the_drawings(texture):
    b = shifted(texture, 16)
    mid = interpolate_pair(texture, b, [0.5])[0]
    assert mean_err(mid, shifted(texture, 8)) < mean_err(mid, texture)
    assert mean_err(mid, shifted(texture, 8)) < mean_err(mid, b)


def test_quarter_and_three_quarter_times_are_ordered(texture):
    b = shifted(texture, 16)
    quarter, three_quarter = interpolate_pair(texture, b, [0.25, 0.75])
    assert mean_err(quarter, texture) < mean_err(three_quarter, texture)
    assert mean_err(three_quarter, b) < mean_err(quarter, b)


def test_vertical_motion_works_too(texture):
    b = shifted(texture, 0, 10)
    mid = interpolate_pair(texture, b, [0.5])[0]
    assert mean_err(mid, shifted(texture, 0, 5)) < mean_err(mid, texture)


# --------------------------------------------------------------------------
# stills and cuts: the failure modes that matter
# --------------------------------------------------------------------------

def test_identical_drawings_produce_identical_in_betweens(texture):
    """A held pose must stay perfectly still, with no invented drift."""
    out = interpolate_pair(texture, texture.copy(), [0.25, 0.5, 0.75])
    assert all(np.array_equal(f, texture) for f in out)


def test_a_cut_is_detected():
    dark = np.full((120, 200, 3), 10, np.uint8)
    bright = np.full((120, 200, 3), 245, np.uint8)
    assert frames_are_a_cut(dark, bright)


def test_similar_drawings_are_not_a_cut(texture):
    assert not frames_are_a_cut(texture, shifted(texture, 8))


def test_mismatched_sizes_count_as_a_cut(texture):
    assert frames_are_a_cut(texture, np.zeros((60, 100, 3), np.uint8))


def test_a_cut_snaps_rather_than_dissolving():
    """Blending across a scene change would show both shots at once."""
    dark = np.full((120, 200, 3), 10, np.uint8)
    bright = np.full((120, 200, 3), 245, np.uint8)
    out = interpolate_pair(dark, bright, [0.25, 0.5, 0.75])
    for frame in out:
        assert np.array_equal(frame, dark) or np.array_equal(frame, bright), (
            "a frame across the cut is a blend of both shots"
        )


def test_a_cut_holds_the_first_shot_until_the_second_starts():
    """The second shot starts on its own frame, or the cut moves off the edit."""
    dark = np.full((120, 200, 3), 10, np.uint8)
    bright = np.full((120, 200, 3), 245, np.uint8)
    for frame in interpolate_pair(dark, bright, [0.25, 0.5, 0.75]):
        assert np.array_equal(frame, dark), "the second shot appeared early"


def test_cut_threshold_is_above_ordinary_motion(texture):
    """The cut detector must not fire on fast but continuous movement."""
    fast = shifted(texture, 40)
    diff = float(cv2.absdiff(cv2.cvtColor(texture, cv2.COLOR_BGR2GRAY),
                             cv2.cvtColor(fast, cv2.COLOR_BGR2GRAY)).mean()) / 255.0
    assert diff < CUT_THRESHOLD, f"fast motion scored {diff:.3f}, at or above the cut threshold"


def test_flat_frames_do_not_explode(texture):
    flat = np.full((120, 200, 3), 128, np.uint8)
    out = interpolate_pair(flat, flat.copy(), [0.5])
    assert np.array_equal(out[0], flat)


def test_output_stays_in_range(texture):
    b = shifted(texture, 20)
    for frame in interpolate_pair(texture, b, [0.3, 0.6]):
        assert frame.min() >= 0 and frame.max() <= 255


# --------------------------------------------------------------------------
# upscale fallback
# --------------------------------------------------------------------------

def test_upscale_doubles_dimensions(texture):
    assert upscale(texture, 2).shape[:2] == (240, 400)


def test_upscale_by_one_is_a_passthrough(texture):
    assert np.array_equal(upscale(texture, 1), texture)


def test_upscale_preserves_dtype(texture):
    assert upscale(texture, 2).dtype == np.uint8
