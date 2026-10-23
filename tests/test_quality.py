"""Quality guards on content that looks like what this plugin is for.

Flat fills with hard outlines behave very differently under optical flow from
the textured noise most synthetic tests use, and they are what anime actually
looks like. These tests measure the rebuilt frame against the drawing that
should have been there.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from Pipeline.interpolate import interpolate_pair

BACKGROUND = (235, 225, 215)


def cel(cx: int, cy: int, w: int = 320, h: int = 240) -> np.ndarray:
    """One frame of flat-shaded animation: fills, outlines, no texture."""
    img = np.full((h, w, 3), BACKGROUND, np.uint8)
    cv2.rectangle(img, (0, h - 60), (w, h), (180, 200, 170), -1)
    cv2.circle(img, (cx, cy), 34, (120, 160, 240), -1)
    cv2.circle(img, (cx, cy), 34, (20, 20, 20), 3)
    cv2.ellipse(img, (cx - 13, cy - 6), (6, 9), 0, 0, 360, (30, 30, 30), -1)
    cv2.ellipse(img, (cx + 13, cy - 6), (6, 9), 0, 0, 360, (30, 30, 30), -1)
    cv2.line(img, (cx - 10, cy + 16), (cx + 10, cy + 16), (40, 40, 40), 2)
    return img


def error_against(frame: np.ndarray, truth: np.ndarray) -> float:
    return float(np.abs(frame.astype(float) - truth.astype(float)).mean())


def dissolve(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a.astype(np.float32) * 0.5 + b.astype(np.float32) * 0.5).astype(np.uint8)


def subject_span(img: np.ndarray):
    """Horizontal extent of the character, ignoring the ground band."""
    mask = np.abs(img.astype(int) - np.array(BACKGROUND)).sum(axis=2) > 60
    mask[-60:, :] = False
    cols = np.where(mask.any(axis=0))[0]
    return (int(cols.min()), int(cols.max())) if len(cols) else (0, 0)


# --------------------------------------------------------------------------

def test_interpolation_beats_a_dissolve_on_flat_artwork():
    a, b, truth = cel(120, 120), cel(140, 112), cel(130, 116)
    mid = interpolate_pair(a, b, [0.5])[0]
    assert error_against(mid, truth) < error_against(dissolve(a, b), truth) / 5.0


def test_the_character_does_not_ghost():
    """A dissolve shows the character in both positions at once."""
    a, b, truth = cel(120, 120), cel(140, 112), cel(130, 116)
    mid = interpolate_pair(a, b, [0.5])[0]

    lo, hi = subject_span(mid)
    t_lo, t_hi = subject_span(truth)
    a_lo, _ = subject_span(a)
    _, b_hi = subject_span(b)

    assert abs(lo - t_lo) <= 4 and abs(hi - t_hi) <= 4, (
        f"character landed at {(lo, hi)}, expected about {(t_lo, t_hi)}"
    )
    assert (hi - lo) < (b_hi - a_lo) * 0.85, "the character is smeared across both poses"


def test_the_in_between_moves_the_right_way():
    # Slow enough to interpolate. A faster move is held, so both frames are a.
    a, b = cel(100, 120), cel(140, 120)
    quarter, three_quarter = interpolate_pair(a, b, [0.25, 0.75])
    assert subject_span(quarter)[0] < subject_span(three_quarter)[0]


@pytest.mark.parametrize("dx,dy", [(6, 0), (20, 8), (40, 0), (0, 24), (90, 0)])
def test_an_in_between_is_never_worse_than_the_source(dx, dy):
    """The contract: a real improvement, or an untouched drawing. Never a smear.

    Past a certain speed the areas a drawing uncovers exist in only one of the
    two frames and no in-between can be reconstructed. Rather than emit a
    melted frame, the first drawing is held, which just reads as the original
    stepped timing.
    """
    a = cel(140, 120)
    b = cel(140 + dx, 120 + dy)
    truth = cel(140 + dx // 2, 120 + dy // 2)
    mid = interpolate_pair(a, b, [0.5])[0]

    held = np.array_equal(mid, a) or np.array_equal(mid, b)
    improved = error_against(mid, truth) < error_against(dissolve(a, b), truth) / 2.0
    assert held or improved, (
        f"offset ({dx},{dy}) produced a blended frame that is no better than a "
        "dissolve, which means it smeared"
    )


def test_motion_that_is_too_fast_holds_instead_of_smearing():
    a, b = cel(80, 120), cel(240, 120)
    mid = interpolate_pair(a, b, [0.5])[0]
    assert np.array_equal(mid, a) or np.array_equal(mid, b), (
        "a very fast move should hold a real drawing, not invent a smeared one"
    )


def test_motion_that_is_too_fast_keeps_the_source_timing():
    """Held means held: the next drawing still starts on its own frame."""
    a, b = cel(80, 120), cel(240, 120)
    for frame in interpolate_pair(a, b, [0.25, 0.5, 0.75]):
        assert np.array_equal(frame, a), "the next drawing appeared early"


def test_ordinary_motion_is_still_interpolated():
    """The guard must not be so eager that normal animation stops smoothing."""
    a, b, truth = cel(120, 120), cel(142, 112), cel(131, 116)
    mid = interpolate_pair(a, b, [0.5])[0]
    assert not np.array_equal(mid, a) and not np.array_equal(mid, b), (
        "a normal on-2s move was held instead of interpolated"
    )
    assert error_against(mid, truth) < error_against(dissolve(a, b), truth) / 5.0


def test_the_guard_is_adjustable():
    a, b = cel(100, 120), cel(180, 120)
    held = interpolate_pair(a, b, [0.5])[0]
    forced = interpolate_pair(a, b, [0.5], max_disagreement=99.0)[0]
    assert np.array_equal(held, a) or np.array_equal(held, b)
    assert not np.array_equal(forced, a) and not np.array_equal(forced, b)


def test_outlines_stay_dark():
    """Blending an outline against a light fill would wash it out."""
    a, b = cel(120, 120), cel(136, 114)
    mid = interpolate_pair(a, b, [0.5])[0]
    assert mid.min() < 60, "the line art lost its density"


def test_a_held_drawing_is_bit_identical():
    a = cel(130, 118)
    assert np.array_equal(interpolate_pair(a, a.copy(), [0.5])[0], a)


def test_flat_background_stays_flat():
    """Invented motion in an empty region is the classic warping artifact."""
    a, b = cel(120, 120), cel(140, 112)
    mid = interpolate_pair(a, b, [0.5])[0]
    corner = mid[0:40, 0:40].reshape(-1, 3)
    assert corner.std(axis=0).max() < 3.0, "the untouched background developed texture"


@pytest.mark.parametrize("quality", ["fast", "better", "best"])
def test_every_quality_preset_beats_a_dissolve(quality):
    a, b, truth = cel(120, 120), cel(142, 110), cel(131, 115)
    mid = interpolate_pair(a, b, [0.5], quality=quality)[0]
    assert error_against(mid, truth) < error_against(dissolve(a, b), truth), (
        f"{quality} was no better than a plain dissolve"
    )
