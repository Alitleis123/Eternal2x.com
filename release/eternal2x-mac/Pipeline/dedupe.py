"""Find held frames in hand-drawn animation and plan how to rebuild it.

Animation is drawn "on 2s" or "on 3s": the artist draws 12 pictures a second
and each is photographed twice or three times to fill 24fps. Playing that back
looks stepped, and interpolating it directly does nothing useful, because most
neighbouring frames are byte-identical -- there is no motion to interpolate.

So the pipeline first has to recover the real drawings:

    source     A A B B C C C D D          9 frames, drawn on 2s
    drawings   A   B   C     D            4 unique drawings
    plan       A   B   C C   D            5 intermediate frames (C was held
                                          longer, so it stays a held frame)

The plan is then rendered as a short clip and stretched back to the original
length, with the in-between frames drawn by the RIFE network. Drawings that
were held much longer than the base pattern are deliberate holds, so they keep
their proportional length and never get smoothed into motion.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import List, Sequence


@dataclass
class Hold:
    """One drawing, and how many source frames it occupies."""

    index: int   # source frame index where the drawing first appears
    length: int  # number of source frames it is held for

    @property
    def end(self) -> int:
        """Last source frame index covered by this hold (inclusive)."""
        return self.index + self.length - 1


@dataclass
class DedupePlan:
    """How to rebuild a clip from its unique drawings."""

    source_frames: int
    fps: float
    holds: List[Hold] = field(default_factory=list)
    base_hold: int = 1
    # One source frame index per intermediate frame, in order.
    output_indices: List[int] = field(default_factory=list)
    # Every frame came out new, but the scores look like grain on held
    # frames: worth turning on Grainy source and analysing again.
    grain_suspected: bool = False
    # Made from the frames the editor marked, not from detection. Every frame
    # between two marks is then an in-between, however long the gap.
    marked: bool = False

    @property
    def unique_drawings(self) -> int:
        return len(self.holds)

    @property
    def output_frames(self) -> int:
        return len(self.output_indices)

    @property
    def is_noop(self) -> bool:
        """True when there is nothing to gain.

        Either every source frame is already a new drawing, or there is only
        one drawing and so no motion to rebuild. A clip that is mostly on 1s
        with a few held frames is not a no-op, those holds still get filled.
        """
        return self.unique_drawings >= self.source_frames or self.unique_drawings <= 1

    @property
    def stretch(self) -> float:
        """How much the intermediate is stretched to refill the original length."""
        if not self.output_frames:
            return 1.0
        return self.source_frames / float(self.output_frames)

    @property
    def duplicate_frames(self) -> int:
        return max(0, self.source_frames - self.unique_drawings)

    def describe(self) -> str:
        if self.source_frames == 0:
            return "Empty clip."
        if self.marked:
            return (f"{self.source_frames} frames, {self.unique_drawings} marked. "
                    "Rebuilding from the marked frames.")
        if self.is_noop and self.unique_drawings <= 1:
            return (
                f"{self.source_frames} frames, one drawing held throughout. "
                "There is no motion to rebuild."
            )
        if self.is_noop:
            return (
                f"{self.source_frames} frames, all unique. "
                "Nothing to de-duplicate, so smoothing would not change the motion."
            )
        pct = 100.0 * self.duplicate_frames / self.source_frames
        return (
            f"{self.source_frames} frames, {self.unique_drawings} unique drawings "
            f"({self.duplicate_frames} held, {pct:.0f}%). "
            f"Animated on {self.base_hold}s. "
            f"Rebuilding from {self.output_frames} frames at {self.stretch:.2f}x stretch."
        )

    def to_dict(self) -> dict:
        return {
            "source_frames": self.source_frames,
            "fps": self.fps,
            "base_hold": self.base_hold,
            "unique_drawings": self.unique_drawings,
            "duplicate_frames": self.duplicate_frames,
            "output_frames": self.output_frames,
            "stretch": self.stretch,
            "is_noop": self.is_noop,
            "marked": self.marked,
            "holds": [{"index": h.index, "length": h.length} for h in self.holds],
            "output_indices": self.output_indices,
        }


def flag_new_drawings(scores: Sequence[float], threshold: float) -> List[bool]:
    """Mark which frames introduce a new drawing.

    `scores[i]` is how different frame i is from frame i-1. Frame 0 is always
    a new drawing because there is nothing before it.
    """
    if not scores:
        return []
    flags = [True]
    for score in scores[1:]:
        flags.append(bool(score >= threshold))
    return flags


def noise_threshold(scores: Sequence[float], minimum: float) -> float:
    """The threshold this clip's own noise calls for, never below `minimum`.

    Grain or flicker makes every held frame differ a little. With grain of 1
    code value the held frames of a clip on 2s scored 0.0045, over the fixed
    0.004, so all 24 frames looked new and nothing was rebuilt. Held frames
    under grain score almost alike, though (0.00450 to 0.00453 there), and
    well apart from the drawings (0.043), so the noise floor is the quiet side
    of the biggest jump in the sorted scores. Twice the floor leaves room for
    grain that varies more than that and still sits under the quietest drawing.

    The floor is trusted only when the clip looks like it holds frames. The
    quiet side needs a quarter of the frames, as held frames on 2s or 3s are
    half or more. The loud side needs a fifth, so a couple of cuts in steady
    motion are not taken for drawings. The jump needs to be 2.5x: frames
    creeping towards the threshold score s, 2s, 3s and so on, which are at
    most 2x apart, while grain of 4 code values still leaves a 3x jump.
    Steady motion such as scrolling credits scores the same on every frame,
    has no jump at all, and keeps `minimum`.

    Two clusters of scores are not proof of grain on their own: real motion
    that alternates small and large steps clusters the same way. So this is
    only applied when the user says the clip is grainy (`cfg.grain`).
    """
    s = sorted(scores[1:])  # frame 0 has nothing before it to differ from
    n = len(s)
    if n < 8:
        return minimum
    jump, split = max((s[k] / max(s[k - 1], 1e-6), k)
                      for k in range(n // 4, n - n // 5 + 1))
    if jump < 2.5:
        return minimum
    return max(minimum, 2.0 * s[split // 2])


def holds_from_flags(flags: Sequence[bool]) -> List[Hold]:
    """Collapse per-frame new-drawing flags into runs."""
    holds: List[Hold] = []
    for i, is_new in enumerate(flags):
        if is_new or not holds:
            holds.append(Hold(index=i, length=1))
        else:
            holds[-1].length += 1
    return holds


def detect_base_hold(holds: Sequence[Hold], *, max_pattern: int = 4) -> int:
    """Work out whether the clip is on 1s, 2s, 3s...

    The base is the most common hold length among the ordinary drawings.
    Deliberate long holds (a still, a held pose) are excluded so they cannot
    drag the estimate upwards, and ties resolve to the shorter pattern, which
    de-duplicates less and is therefore the safer guess.
    """
    lengths = [h.length for h in holds if 1 <= h.length <= max_pattern]
    if not lengths:
        return 1
    counts = Counter(lengths)
    best = max(counts.values())
    return min(length for length, count in counts.items() if count == best)


def pose_base(base_hold: int, fps: float = 24.0) -> int:
    """The pattern a hold is measured against to tell a pose from a drawing.

    It is the clip's own, but never shorter than 2s at 24 fps lasts at the
    clip's rate: 2 frames at 24, 5 at 60. On a 60 fps file made from 24 fps
    animation a drawing on 2s lasts 5 frames, so measured against 2 every
    one of them read as a pose and stayed partly still. NTSC rates count as
    their round rate: 29.97 / 12 is just under 2.5, and rounding it down
    made 5-frame holds pauses at 29.97 but not at 30.
    """
    return max(2, int(base_hold), int(fps / 12.0 + 0.51))


def hold_repeats(hold: Hold, base_hold: int, fps: float = 24.0) -> int:
    """How many intermediate frames one drawing occupies.

    A hold up to twice the pattern is an ordinary drawing and appears once,
    so everything up to the next one is drawn fresh: 3s in a clip on 2s, or
    the uneven holds a frame rate conversion leaves, like 2, 2, 2, 4 on a
    60 fps file. Kept partly still, they stuttered. A longer hold is a pose
    and keeps its length, one held for six times the pattern appearing six
    times, so it stays still instead of melting into motion. A single-frame
    drawing still gets exactly one slot, so it is never touched. Halves
    round up, not to even, so equal holds always get equal treatment.
    """
    base = pose_base(base_hold, fps)
    if hold.length <= 2 * base:
        return 1
    return max(1, int(hold.length / float(base) + 0.5))


def build_output_indices(holds: Sequence[Hold], base_hold: int,
                         fps: float = 24.0) -> List[int]:
    """Choose which source frames make up the intermediate clip."""
    indices: List[int] = []
    for hold in holds:
        indices.extend([hold.index] * hold_repeats(hold, base_hold, fps))
    return indices


def plan_from_scores(
    scores: Sequence[float],
    fps: float,
    *,
    threshold: float,
    force_base_hold: int = 0,
) -> DedupePlan:
    """Full analysis: per-frame difference scores in, rebuild plan out."""
    flags = flag_new_drawings(scores, threshold)
    holds = holds_from_flags(flags)
    base = int(force_base_hold) if force_base_hold and force_base_hold > 0 \
        else detect_base_hold(holds)
    return DedupePlan(
        source_frames=len(flags),
        fps=fps,
        holds=holds,
        base_hold=base,
        output_indices=build_output_indices(holds, base, fps),
    )


def plan_from_marks(marks: Sequence[int], source_frames: int, fps: float) -> DedupePlan:
    """A plan from the frames the editor marked, in place of detection.

    Each marked frame is kept as drawn and every frame up to the next mark is
    an in-between of the two, however much it moved in the source, so a part
    the editor leaves unmarked never counts. Frames are counted from the
    clip's first frame, which always counts: before it there is nothing to
    draw from.
    """
    keep = sorted({int(m) for m in marks if 0 <= int(m) < source_frames} | {0})
    if source_frames <= 0:
        keep = []
    holds = [Hold(index=a, length=b - a) for a, b in zip(keep, keep[1:] + [source_frames])]
    return DedupePlan(
        source_frames=max(0, source_frames),
        fps=fps,
        holds=holds,
        base_hold=1,
        output_indices=[h.index for h in holds],
        marked=True,
    )


def output_time_map(plan: DedupePlan) -> List[float]:
    """Where each final frame sits in the intermediate clip, as a float index.

    The integer part picks the pair of drawings an in-between goes between,
    the fraction is how far between them it sits.

    Each hold is mapped onto its own slots, so the frame where a drawing
    starts in the source is the frame where it starts in the output. A single
    stretch for the whole clip let rounding error pile up instead, and a cut
    could land several frames away from its place in the edit and the audio.

    The last drawing has no successor, so the final slot clamps to it and is
    held rather than interpolated. That is the one place where the source's
    original hold survives, and it is unavoidable without inventing motion
    past the end of the clip.
    """
    if plan.output_frames == 0 or plan.source_frames == 0:
        return []
    last = plan.output_frames - 1
    if not plan.holds:
        rate = plan.output_frames / float(plan.source_frames)
        return [min(last, n * rate) for n in range(plan.source_frames)]

    positions: List[float] = []
    slot = 0
    for hold in plan.holds:
        repeats = 1 if plan.marked else hold_repeats(hold, plan.base_hold, plan.fps)
        step = repeats / float(hold.length)
        positions.extend(min(last, slot + k * step) for k in range(hold.length))
        slot += repeats
    return positions
