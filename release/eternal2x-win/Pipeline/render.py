"""Read a clip, work out its drawing pattern, and rebuild it smoothly.

Two passes over the source:

  analyse_video()  decodes once to measure how different each frame is from the
                   one before it, which is all `Pipeline.dedupe` needs to build
                   a plan.
  render_plan()    decodes again and writes the output, holding only the two
                   drawings currently being interpolated between.

Nothing loads the whole clip into memory, so length is limited by disk rather
than by RAM.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from Pipeline.config import Eternal2xConfig
from Pipeline.dedupe import DedupePlan, plan_from_scores, output_time_map
from Pipeline.interpolate import interpolate_pair, upscale

ProgressFn = Callable[[str, float], None]


class SourceError(RuntimeError):
    """The clip could not be opened or decoded."""


@dataclass
class RenderResult:
    output: Path
    frames_written: int
    width: int
    height: int
    fps: float
    is_sequence: bool

    def to_dict(self) -> dict:
        return {
            "output": str(self.output),
            "frames_written": self.frames_written,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "is_sequence": self.is_sequence,
        }


def _open(path: Path) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SourceError(f"Could not open clip: {path}")
    return cap


def _downscale(gray: np.ndarray, max_width: int) -> np.ndarray:
    if max_width and gray.shape[1] > max_width:
        h, w = gray.shape[:2]
        scale = max_width / float(w)
        return cv2.resize(gray, (max_width, max(1, int(h * scale))),
                          interpolation=cv2.INTER_AREA)
    return gray


def _difference(prev: np.ndarray, curr: np.ndarray) -> float:
    """How different two frames are, weighted towards localised change.

    A blink or a mouth flap moves a tiny fraction of the frame, so a plain mean
    would bury it under the unchanged rest of the picture. Averaging only the
    busiest tiles keeps small real changes well clear of codec noise.
    """
    diff = cv2.absdiff(prev, curr)
    h, w = diff.shape[:2]
    gx, gy = max(1, min(8, w)), max(1, min(8, h))
    tw, th = max(1, w // gx), max(1, h // gy)
    vals = []
    for ty in range(gy):
        y1 = (ty + 1) * th if ty < gy - 1 else h
        for tx in range(gx):
            x1 = (tx + 1) * tw if tx < gx - 1 else w
            tile = diff[ty * th:y1, tx * tw:x1]
            if tile.size:
                vals.append(float(tile.mean()) / 255.0)
    if not vals:
        return 0.0
    arr = np.array(vals, dtype=np.float32)
    k = max(1, int(np.ceil(len(arr) * 0.15)))
    return float(np.partition(arr, -k)[-k:].mean())


def frame_difference_scores(
    path: Path,
    cfg: Eternal2xConfig,
    *,
    progress: Optional[ProgressFn] = None,
) -> Tuple[List[float], float, int, int]:
    """Per-frame difference from the previous frame, plus fps and dimensions."""
    cap = _open(path)
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 24.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

        ok, first = cap.read()
        if not ok:
            raise SourceError(f"Could not read any frames from: {path}")
        if not width or not height:
            height, width = first.shape[:2]

        prev = _downscale(cv2.cvtColor(first, cv2.COLOR_BGR2GRAY), cfg.analysis_width)
        scores = [0.0]

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            curr = _downscale(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cfg.analysis_width)
            scores.append(_difference(prev, curr))
            prev = curr
            if progress and total:
                progress("Analysing", min(1.0, len(scores) / float(total)))

        return scores, fps, width, height
    finally:
        cap.release()


def analyse_video(
    path: Path,
    cfg: Eternal2xConfig,
    *,
    progress: Optional[ProgressFn] = None,
) -> Tuple[DedupePlan, int, int]:
    """Decode the clip once and return a rebuild plan plus its dimensions."""
    scores, fps, width, height = frame_difference_scores(path, cfg, progress=progress)
    plan = plan_from_scores(
        scores, fps,
        threshold=cfg.duplicate_threshold,
        force_base_hold=cfg.force_base_hold,
    )
    return plan, width, height


def _iter_plan_frames(cap: cv2.VideoCapture, indices: Sequence[int]) -> Iterator[np.ndarray]:
    """Yield the source frame for each entry of `indices`, decoding in one pass.

    `indices` is non-decreasing, so the source is read straight through and a
    repeated index simply re-yields the frame already in hand.
    """
    read_upto = -1          # index of the most recently decoded frame
    current: Optional[np.ndarray] = None
    held_index: Optional[int] = None

    for want in indices:
        if held_index == want and current is not None:
            yield current
            continue
        while read_upto < want:
            ok, frame = cap.read()
            if not ok:
                if current is not None:
                    yield current
                return
            read_upto += 1
            current = frame
        held_index = want
        yield current


# Encoders to try per container, best quality first.
_ENCODERS = {
    ".avi": ("FFV1", "MJPG"),      # FFV1 is lossless
    ".mp4": ("avc1", "mp4v"),      # H.264, which Resolve always reads
    ".mov": ("mp4v", "jpeg"),
}


def _writer(path: Path, size: Tuple[int, int], fps: float) -> cv2.VideoWriter:
    w, h = size
    for fourcc in _ENCODERS.get(path.suffix.lower(), ("MJPG", "mp4v")):
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), fps, (w, h))
        if writer.isOpened():
            return writer
    raise SourceError(
        f"No encoder available for {path.suffix or 'this format'}. "
        "Try the lossless image sequence output instead."
    )


def render_plan(
    source: Path,
    plan: DedupePlan,
    output: Path,
    cfg: Eternal2xConfig,
    *,
    progress: Optional[ProgressFn] = None,
    sequence: Optional[bool] = None,
) -> RenderResult:
    """Write the rebuilt clip.

    The output has exactly as many frames as the source and runs at the same
    rate, so it drops straight back into an edit in place of the original.

    `sequence` says whether `output` is a folder of PNG frames. Left as None it
    is guessed from the suffix, which a caller that knows should not rely on,
    because a folder called `shot.v2_smooth` has the suffix `.v2_smooth`.
    """
    if plan.output_frames == 0:
        raise SourceError("Nothing to render: the plan has no frames.")

    is_sequence = (output.suffix == "") if sequence is None else bool(sequence)
    if is_sequence:
        output.mkdir(parents=True, exist_ok=True)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)

    factor = cfg.upscale_factor if cfg.upscale_enabled else 1
    positions = output_time_map(plan)

    # Which in-between times belong to each pair of consecutive drawings.
    last_pair = plan.output_frames - 2
    buckets: List[List[float]] = [[] for _ in range(max(1, plan.output_frames - 1))]
    for p in positions:
        i = min(int(p), max(0, last_pair))
        buckets[i].append(p - i)

    cap = _open(source)
    state = {"writer": None, "written": 0, "width": 0, "height": 0}

    def emit(frame: np.ndarray) -> None:
        """Upscale if asked, then write one output frame."""
        if factor > 1:
            frame = upscale(frame, factor)
        state["height"], state["width"] = frame.shape[:2]
        if is_sequence:
            target = output / f"frame_{state['written']:06d}.png"
            # imwrite reports failure, a full disk or a path OpenCV cannot
            # encode, by returning False rather than raising.
            if not cv2.imwrite(str(target), frame):
                raise SourceError(f"Could not write {target}. Check the disk has "
                                  "space and the folder is writable.")
        else:
            if state["writer"] is None:
                state["writer"] = _writer(output, (state["width"], state["height"]),
                                          plan.fps)
            state["writer"].write(frame)
        state["written"] += 1
        if progress and plan.source_frames:
            progress("Rendering",
                     min(1.0, state["written"] / float(plan.source_frames)))

    try:
        frames = _iter_plan_frames(cap, plan.output_indices)
        prev = next(frames, None)
        if prev is None:
            raise SourceError(f"Could not read frames from: {source}")

        index = 0
        for curr in frames:
            times = buckets[index] if index < len(buckets) else []
            if times:
                if cfg.interpolate_enabled:
                    produced = interpolate_pair(
                        prev, curr, times,
                        quality=cfg.quality,
                        occlusion_softness=cfg.occlusion_softness,
                        max_disagreement=cfg.max_disagreement,
                    )
                else:
                    # De-duplicate only: every output frame is a real drawing,
                    # nothing is synthesised.
                    produced = [(prev if t < 0.5 else curr).copy() for t in times]
                for frame in produced:
                    emit(frame)
            prev = curr
            index += 1

        # A single-drawing plan, or the trailing hold the buckets do not cover.
        while state["written"] < plan.source_frames:
            emit(prev)
    finally:
        cap.release()
        if state["writer"] is not None:
            state["writer"].release()

    return RenderResult(
        output=output,
        frames_written=state["written"],
        width=state["width"],
        height=state["height"],
        fps=plan.fps,
        is_sequence=is_sequence,
    )
