"""Duplicate-frame detection and rebuild planning."""

from __future__ import annotations

import pytest

from Pipeline.dedupe import (
    DedupePlan,
    Hold,
    build_output_indices,
    detect_base_hold,
    flag_new_drawings,
    holds_from_flags,
    noise_threshold,
    output_time_map,
    plan_from_scores,
)

T = 0.004  # duplicate threshold


def scores_for(holds):
    """Difference scores for a clip whose drawings are held for `holds` frames."""
    out = [0.0]
    for i, hold in enumerate(holds):
        if i:
            out.append(0.5)          # a new drawing
        out.extend([0.0] * (hold - 1))  # held frames are identical
    return out


# --------------------------------------------------------------------------
# flagging
# --------------------------------------------------------------------------

def test_first_frame_is_always_a_new_drawing():
    assert flag_new_drawings([0.0, 0.0], T)[0] is True


def test_empty_scores():
    assert flag_new_drawings([], T) == []


def test_identical_frames_are_not_new_drawings():
    assert flag_new_drawings([0.0, 0.0, 0.0], T) == [True, False, False]


def test_threshold_is_inclusive():
    assert flag_new_drawings([0.0, T], T) == [True, True]


def test_noise_below_threshold_is_still_a_duplicate():
    """Codec noise must not be mistaken for a new drawing."""
    assert flag_new_drawings([0.0, 0.0009, 0.0012], T) == [True, False, False]


# --------------------------------------------------------------------------
# noise floor
# --------------------------------------------------------------------------

def test_clean_holds_keep_the_configured_threshold():
    assert noise_threshold(scores_for([2] * 12), T) == T


def test_grain_lifts_the_threshold_between_held_frames_and_drawings():
    """Scores measured on 12 drawings on 2s with grain of 1 code value."""
    t = noise_threshold([0.0] + [0.0045, 0.043] * 11 + [0.0045], T)
    assert 0.0045 < t < 0.043


def test_steady_motion_is_not_mistaken_for_grain():
    """Scrolling credits score the same on every frame, as grain on a still
    would, but every frame is new."""
    assert noise_threshold([0.0] + [0.0177] * 23, T) == T


def test_a_couple_of_cuts_do_not_make_steady_motion_look_like_grain():
    assert noise_threshold([0.0] + [0.0177] * 21 + [0.5, 0.6], T) == T


def test_live_action_keeps_the_configured_threshold():
    """Real motion spreads its scores out, with no gap between still and moving."""
    assert noise_threshold([0.0] + [0.005 * 1.1 ** i for i in range(30)], T) == T


def test_a_slow_fade_is_not_mistaken_for_grain():
    """Creeping one level a frame scores 1 and 2 levels in turn, a 2x jump."""
    assert noise_threshold([0.0] + [1 / 255, 2 / 255] * 12, T) == T


def test_too_few_frames_to_judge_the_noise():
    assert noise_threshold([0.0, 0.0045, 0.043, 0.0045, 0.043], T) == T


# --------------------------------------------------------------------------
# holds
# --------------------------------------------------------------------------

def test_holds_from_all_new_frames():
    holds = holds_from_flags([True] * 4)
    assert [(h.index, h.length) for h in holds] == [(0, 1), (1, 1), (2, 1), (3, 1)]


def test_holds_on_twos():
    holds = holds_from_flags([True, False, True, False, True, False])
    assert [(h.index, h.length) for h in holds] == [(0, 2), (2, 2), (4, 2)]


def test_hold_end_is_inclusive():
    assert Hold(index=4, length=3).end == 6


def test_holds_from_empty():
    assert holds_from_flags([]) == []


# --------------------------------------------------------------------------
# base hold detection
# --------------------------------------------------------------------------

@pytest.mark.parametrize("holds,expected", [
    ([1] * 10, 1),
    ([2] * 10, 2),
    ([3] * 10, 3),
    ([2, 2, 2, 3, 2, 2], 2),
    ([2, 2, 3, 3, 2, 3, 2], 2),
])
def test_detect_base_hold(holds, expected):
    assert detect_base_hold([Hold(0, h) for h in holds]) == expected


def test_long_stills_do_not_skew_the_base():
    """A held pose is not the animation pattern."""
    holds = [Hold(0, 2)] * 8 + [Hold(0, 40), Hold(0, 36)]
    assert detect_base_hold(holds) == 2


def test_ties_resolve_to_the_shorter_pattern():
    """Guessing short de-duplicates less, which is the safer error."""
    assert detect_base_hold([Hold(0, 2), Hold(0, 3)]) == 2


def test_base_hold_of_nothing_is_one():
    assert detect_base_hold([]) == 1


# --------------------------------------------------------------------------
# output planning
# --------------------------------------------------------------------------

def test_on_twos_halves_the_frame_count():
    holds = [Hold(i * 2, 2) for i in range(6)]
    assert build_output_indices(holds, 2) == [0, 2, 4, 6, 8, 10]


def test_long_holds_keep_their_proportional_length():
    """A drawing held six frames on a 2s clip stays a still, not motion."""
    holds = [Hold(0, 2), Hold(2, 6), Hold(8, 2)]
    assert build_output_indices(holds, 2) == [0, 2, 2, 2, 8]


def test_every_drawing_appears_at_least_once():
    """A 1-frame drawing in a 3s clip must not be dropped entirely."""
    holds = [Hold(0, 3), Hold(3, 1), Hold(4, 3)]
    assert build_output_indices(holds, 3) == [0, 3, 4]


def test_base_hold_of_one_changes_nothing():
    holds = [Hold(i, 1) for i in range(5)]
    assert build_output_indices(holds, 1) == [0, 1, 2, 3, 4]


# --------------------------------------------------------------------------
# whole plan
# --------------------------------------------------------------------------

def test_plan_for_a_clip_on_twos():
    plan = plan_from_scores(scores_for([2] * 12), 24.0, threshold=T)
    assert plan.source_frames == 24
    assert plan.unique_drawings == 12
    assert plan.duplicate_frames == 12
    assert plan.base_hold == 2
    assert plan.output_frames == 12
    assert plan.stretch == pytest.approx(2.0)
    assert not plan.is_noop


def test_plan_for_a_clip_on_threes():
    plan = plan_from_scores(scores_for([3] * 8), 24.0, threshold=T)
    assert plan.base_hold == 3
    assert plan.unique_drawings == 8
    assert plan.stretch == pytest.approx(3.0)


def test_live_action_is_a_noop():
    """Every frame unique: there is nothing to recover, so do nothing."""
    plan = plan_from_scores([0.0] + [0.4] * 29, 24.0, threshold=T)
    assert plan.base_hold == 1
    assert plan.is_noop
    assert plan.output_frames == plan.source_frames
    assert "all unique" in plan.describe()


def test_plan_on_an_empty_clip():
    plan = plan_from_scores([], 24.0, threshold=T)
    assert plan.source_frames == 0
    assert plan.output_frames == 0
    assert plan.describe() == "Empty clip."


def test_forced_base_hold_overrides_detection():
    plan = plan_from_scores(scores_for([2] * 12), 24.0, threshold=T, force_base_hold=3)
    assert plan.base_hold == 3


def test_plan_survives_mixed_timing():
    plan = plan_from_scores(scores_for([2, 2, 3, 2, 8, 2, 2]), 24.0, threshold=T)
    assert plan.base_hold == 2
    assert plan.unique_drawings == 7
    # 8-frame hold -> 4 frames, 3-frame hold -> 2, the five 2-frame holds -> 1 each
    assert plan.output_frames == 11


def test_plan_serialises():
    plan = plan_from_scores(scores_for([2] * 4), 24.0, threshold=T)
    d = plan.to_dict()
    assert d["base_hold"] == 2
    assert d["unique_drawings"] == 4
    assert len(d["output_indices"]) == d["output_frames"]


def test_describe_reports_the_pattern():
    plan = plan_from_scores(scores_for([2] * 12), 24.0, threshold=T)
    text = plan.describe()
    assert "12 unique drawings" in text
    assert "on 2s" in text


# --------------------------------------------------------------------------
# time mapping
# --------------------------------------------------------------------------

def test_time_map_has_one_entry_per_output_frame():
    plan = plan_from_scores(scores_for([2] * 12), 24.0, threshold=T)
    assert len(output_time_map(plan)) == plan.source_frames


def test_time_map_spans_the_whole_intermediate():
    plan = plan_from_scores(scores_for([2] * 12), 24.0, threshold=T)
    positions = output_time_map(plan)
    assert positions[0] == 0.0
    assert positions[-1] == pytest.approx(plan.output_frames - 1)


def test_time_map_is_monotonic():
    plan = plan_from_scores(scores_for([2, 3, 2, 6, 2]), 24.0, threshold=T)
    positions = output_time_map(plan)
    assert positions == sorted(positions)


def test_time_map_lands_on_real_drawings_for_a_clean_pattern():
    """On a perfect 2s clip every other output frame is an original drawing."""
    plan = plan_from_scores(scores_for([2] * 12), 24.0, threshold=T)
    positions = output_time_map(plan)
    originals = [p for p in positions if abs(p - round(p)) < 1e-6]
    assert len(originals) >= plan.output_frames - 1


def test_time_map_of_a_single_frame():
    plan = DedupePlan(source_frames=1, fps=24.0, output_indices=[0])
    assert output_time_map(plan) == [0.0]


def test_time_map_of_an_empty_plan():
    assert output_time_map(DedupePlan(source_frames=0, fps=24.0)) == []


def _starts(holds):
    starts, at = [], 0
    for length in holds:
        starts.append(at)
        at += length
    return starts


@pytest.mark.parametrize("holds", [
    [3] * 8 + [2] * 12,             # 3s then a cut to 2s
    [2, 3, 2, 5, 2, 7, 2, 2, 3],    # odd holds that round both ways
    [1, 1, 2, 1, 3, 1, 1, 2, 12, 2],
])
def test_every_drawing_starts_where_it_did_in_the_source(holds):
    """A cut that moves goes out of sync with the edit and the audio.

    With one stretch for the whole clip, 8 drawings on 3s followed by 12 on
    2s put the cut at output frame 27 instead of 24.
    """
    plan = plan_from_scores(scores_for(holds), 24.0, threshold=T)
    positions = output_time_map(plan)
    slots = {}
    slot = 0
    for i in plan.output_indices:
        slots.setdefault(i, slot)
        slot += 1
    for start in _starts(holds):
        assert positions[start] == pytest.approx(slots[start]), (
            f"the drawing at source frame {start} starts at {positions[start]}, "
            f"not on its own slot {slots[start]}"
        )


def test_a_mostly_ones_clip_with_held_frames_is_not_a_noop():
    """It used to report 'all unique' and stop, leaving the holds stepped."""
    holds = [1, 1, 1, 1, 1, 1, 2, 2]
    plan = plan_from_scores(scores_for(holds), 24.0, threshold=T)
    assert not plan.is_noop
    assert "all unique" not in plan.describe()
    positions = output_time_map(plan)
    # The held frames get in-betweens, the single frames stay exact.
    assert any(p != int(p) for p in positions)
    for start in range(6):
        assert positions[start] == start


def test_a_single_drawing_is_still_a_noop():
    plan = plan_from_scores(scores_for([24]), 24.0, threshold=T)
    assert plan.is_noop
