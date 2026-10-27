"""Telling a cut from motion, and upscaling.

The in-between frames are drawn by the RIFE network in Pipeline/rife.py.
Optical flow is used here only to tell whether two frames belong to one shot.
An in-between made by warping pixels along it doubles lines and smears a
character that moves far, on hand-drawn animation, and no check of the flow
catches every case, so none is made from it.
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

# Above this mean absolute difference, two drawings are treated as unrelated
# (a hard cut).
CUT_THRESHOLD = 0.38


def _flow_engine():
    return cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST)


def _to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def _warp(image: np.ndarray, flow: np.ndarray) -> np.ndarray:
    """Resample `image` along `flow`."""
    h, w = flow.shape[:2]
    grid_x, grid_y = np.meshgrid(np.arange(w, dtype=np.float32),
                                 np.arange(h, dtype=np.float32))
    map_x = grid_x + flow[..., 0]
    map_y = grid_y + flow[..., 1]
    return cv2.remap(image, map_x, map_y, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)


def frames_are_a_cut(a: np.ndarray, b: np.ndarray) -> bool:
    """True when two drawings are too different to interpolate between."""
    ga, gb = _to_gray(a), _to_gray(b)
    if ga.shape != gb.shape:
        return True
    return float(cv2.absdiff(ga, gb).mean()) / 255.0 > CUT_THRESHOLD


# Above this, the midpoint warps of two frames disagree too much for them to be
# one shot, measured at UNRELATED_WIDTH. Most cuts change pixels by less than
# CUT_THRESHOLD on average: across the 30 cuts in the open movie Hero it was
# 0.22 to 0.34, and a learned interpolator would morph one shot into the other.
# On Hero every cut scored 0.098 or more here, and the fastest motion 0.045.
UNRELATED_DISAGREEMENT = 0.06
UNRELATED_WIDTH = 480


def shot_change(a: np.ndarray, b: np.ndarray) -> float:
    """How far the midpoint warps of two frames disagree, as a fraction of full scale.

    Identical frames score 0. Frames of different sizes cannot be one shot and
    score infinity.
    """
    if a.shape != b.shape:
        return float("inf")
    if np.array_equal(a, b):
        return 0.0
    h, w = a.shape[:2]
    if w > UNRELATED_WIDTH:
        size = (UNRELATED_WIDTH, max(1, int(round(h * UNRELATED_WIDTH / float(w)))))
        a = cv2.resize(a, size, interpolation=cv2.INTER_AREA)
        b = cv2.resize(b, size, interpolation=cv2.INTER_AREA)
    engine = _flow_engine()
    ga, gb = _to_gray(a), _to_gray(b)
    flow_ab, flow_ba = engine.calc(ga, gb, None), engine.calc(gb, ga, None)
    probe_a = _warp(a.astype(np.float32), flow_ba * 0.5)
    probe_b = _warp(b.astype(np.float32), flow_ab * 0.5)
    return _disagreement(probe_a, probe_b)


def frames_are_unrelated(a: np.ndarray, b: np.ndarray) -> bool:
    """True when two frames look like different shots, even at similar brightness."""
    return shot_change(a, b) > UNRELATED_DISAGREEMENT


# Fast action disagrees as much as a cut. In a Jujutsu Kaisen fight that is one
# shot, 37 of 53 pairs of marked drawings scored 0.06 to 0.21 and were held as
# if each were a cut. A cut usually stands out from the pairs on either side,
# which belong to one shot each, while fast action keeps a run of high scores.
# In that fight no pair scored more than 2.3 times both its neighbours.
#
# Two cuts in a row, as in an opening's montage, do not stand out from each
# other, so a pair over ALWAYS_A_CUT, or one whose colours change more than
# PALETTE_CUT, is a cut whatever surrounds it. That fight's fastest pair scored
# 0.21 and its colours changed by at most 0.74, at a lightning flash.
#
# Measured over 13,645 pairs of drawings in 98 anime clips, plus 69 cuts made
# by joining one clip's last drawings to the next clip's first: the threshold
# alone held 933 pairs and caught all 69 cuts. These rules hold 468, mostly
# cuts, flashes and glitch effects, and catch 66.
CUT_SPIKE = 3.0
ALWAYS_A_CUT = 0.3
PALETTE_CUT = 0.8


def palette_change(a: np.ndarray, b: np.ndarray) -> float:
    """How far apart two frames' hue and saturation spreads are, 0 to 1."""
    hists = []
    for frame in (a, b):
        h, w = frame.shape[:2]
        if w > UNRELATED_WIDTH:
            size = (UNRELATED_WIDTH, max(1, int(round(h * UNRELATED_WIDTH / float(w)))))
            frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [30, 32], [0, 180, 0, 256])
        hists.append(cv2.normalize(hist, hist).flatten())
    return float(cv2.compareHist(hists[0], hists[1], cv2.HISTCMP_BHATTACHARYYA))


def is_a_cut(a: np.ndarray, b: np.ndarray, here: float,
             before: Optional[float] = None, after: Optional[float] = None) -> bool:
    """Whether drawings a and b, whose shot_change is `here`, span a cut.

    `before` and `after` are the shot_change of the pairs on either side, or
    None where there is none. A pair with no neighbours has nothing to stand
    out from, so the plain threshold decides.
    """
    if here <= UNRELATED_DISAGREEMENT:
        return False
    around = [s for s in (before, after) if s is not None]
    if here > ALWAYS_A_CUT or not around or here > CUT_SPIKE * max(around):
        return True
    return palette_change(a, b) > PALETTE_CUT


def _disagreement(warped_a: np.ndarray, warped_b: np.ndarray) -> float:
    """How far the two warps disagree, as a fraction of full scale."""
    return float(np.abs(warped_a - warped_b).mean()) / 255.0


def upscale(frame: np.ndarray, factor: int) -> np.ndarray:
    """Resize by an integer factor.

    Lanczos keeps hand-drawn line art crisp. This is the fallback used when
    Resolve's Super Scale is not available to do the job better.
    """
    if factor <= 1:
        return frame
    h, w = frame.shape[:2]
    return cv2.resize(frame, (w * factor, h * factor), interpolation=cv2.INTER_LANCZOS4)
