"""Read a clip, work out its drawing pattern, and rebuild it smoothly.

Two passes over the source:

  analyse_video()  decodes once to measure how different each frame is from the
                   last new drawing, which is all `Pipeline.dedupe` needs to
                   build a plan. A grainy clip is measured a second time.
  render_plan()    decodes again and writes the output, holding only the two
                   drawings currently being interpolated between.

Nothing loads the whole clip into memory, so length is limited by disk rather
than by RAM.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from Pipeline.config import Eternal2xConfig
from Pipeline.dedupe import DedupePlan, noise_threshold, plan_from_scores, output_time_map
from Pipeline.interpolate import upscale

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
    # Why no in-betweens were drawn, if Interpolate was on and none were.
    note: str = ""
    # Whether the network drew every frame at 2x, and if it was asked to and
    # did not, why.
    upscaled: bool = False
    upscale_note: str = ""

    def to_dict(self) -> dict:
        return {
            "output": str(self.output),
            "frames_written": self.frames_written,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "is_sequence": self.is_sequence,
            "note": self.note,
            "upscaled": self.upscaled,
            "upscale_note": self.upscale_note,
        }


# Source frames [start, end) to work on. None means the whole file.
FrameRange = Optional[Tuple[int, Optional[int]]]


class _ClipReader:
    """A capture that only shows frames [start, end) of its source.

    A clip trimmed to two seconds of a long file should cost two seconds of
    work, so everything downstream sees just that range, numbered from 0, and
    cannot tell it from a short file.
    """

    def __init__(self, cap: cv2.VideoCapture, start: int = 0,
                 end: Optional[int] = None,
                 progress: Optional[ProgressFn] = None) -> None:
        self._cap = cap
        # Reads stop where the file does, not at its frame count. Some
        # containers only carry an estimate, a few frames short or, in an
        # MKV that was never finished, negative.
        self._start = max(0, start)
        self._end = end
        self._read = 0
        # When the file's first frame is shown, in ms. Some files start late.
        self._origin: Optional[float] = None
        # grab() decodes without converting, and unlike seeking it lands on
        # exactly the frame asked for, whatever the codec. Deep into a long
        # file that takes a while, so it reports like any other stage.
        for i in range(self._start):
            if progress and i % 100 == 0:
                progress("Seeking", i / float(self._start))
            if not cap.grab():
                break
            if i == 0:
                self._origin = cap.get(cv2.CAP_PROP_POS_MSEC)

    def read(self):
        if self._end is not None and self._start + self._read >= self._end:
            return False, None
        ok, frame = self._cap.read()
        if ok:
            self._read += 1
            if self._origin is None:
                self._origin = self._cap.get(cv2.CAP_PROP_POS_MSEC)
        return ok, frame

    def frames_off_schedule(self, fps: float) -> float:
        """How far, in frames, the last frame read is from where `fps` puts it.

        Counted from the start of the file, so unevenly spaced frames before
        the range show up too. A file without timestamps reports 0 for every
        frame and counts as on time.
        """
        ms = self._cap.get(cv2.CAP_PROP_POS_MSEC)
        if ms <= 0 or self._origin is None:
            return 0.0
        return abs((ms - self._origin) * fps / 1000.0 - (self._start + self._read - 1))

    def get(self, prop: int) -> float:
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            # Only a guide to progress, which is all it is good for.
            total = self._cap.get(prop) if self._end is None else self._end - self._start
            return float(max(0, total))
        return self._cap.get(prop)

    def release(self) -> None:
        self._cap.release()


def _open(path: Path, frames: FrameRange = None,
          progress: Optional[ProgressFn] = None) -> _ClipReader:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SourceError(f"Could not open clip: {path}")
    start, end = frames if frames else (0, None)
    return _ClipReader(cap, start, end, progress)


def probe_video(path: Path) -> Tuple[float, int]:
    """The clip's frame rate and frame count, without decoding it."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SourceError(f"Could not open clip: {path}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 24.0
        return fps, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        cap.release()


def _downscale(gray: np.ndarray, max_width: int) -> np.ndarray:
    if max_width and gray.shape[1] > max_width:
        h, w = gray.shape[:2]
        scale = max_width / float(w)
        return cv2.resize(gray, (max_width, max(1, int(h * scale))),
                          interpolation=cv2.INTER_AREA)
    return gray


AnalysisFrame = Tuple[np.ndarray, np.ndarray, np.ndarray]


def _analysis_frame(frame: np.ndarray, max_width: int) -> AnalysisFrame:
    """Brightness and the two colour planes, small enough to compare quickly.

    Brightness is made exactly as it always was, so clips whose drawings
    differ in brightness keep the same answer to the last bit. Colour is
    sampled at half that size with a plain linear resize, about 0.1 ms a frame
    where an area average costs 1 to 2 ms. Codecs keep colour at half the
    width and height anyway: on held frames of real clips this scored colour
    about 14% higher than a full-size area average, under 0.003 at half weight.
    """
    luma = _downscale(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), max_width)
    size = (max(1, luma.shape[1] // 2), max(1, luma.shape[0] // 2))
    ycc = cv2.cvtColor(cv2.resize(frame, size, interpolation=cv2.INTER_LINEAR),
                       cv2.COLOR_BGR2YCrCb)
    return luma, cv2.extractChannel(ycc, 1), cv2.extractChannel(ycc, 2)


def _difference(prev: AnalysisFrame, curr: AnalysisFrame) -> float:
    """How different two frames are, in brightness or in colour.

    Brightness alone cannot tell pure red from a mid green, which have the
    same luma, so a drawing that only changed colour vanished. Colour counts
    at half weight because codecs store it at half the width and height and
    its noise runs higher. On held frames of a VP9 film it scored up to 3.1
    times the brightness, and 0.0054 at a keyframe, over the 0.004 threshold.
    Real drawing changes score 0.2 to 0.6 times their brightness.
    """
    colour = cv2.max(cv2.absdiff(prev[1], curr[1]), cv2.absdiff(prev[2], curr[2]))
    return max(_busiest_tiles(cv2.absdiff(prev[0], curr[0])), 0.5 * _busiest_tiles(colour))


def _busiest_tiles(diff: np.ndarray) -> float:
    """How much a difference image changed, weighted towards localised change.

    A blink or a mouth flap moves a tiny fraction of the frame, so a plain mean
    would bury it under the unchanged rest of the picture. Averaging only the
    busiest tiles keeps small real changes well clear of codec noise.

    cv2.mean returns exactly what numpy's mean does on these tiles and takes
    a fraction of the time, which pays for scoring colour as well.
    """
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
                vals.append(cv2.mean(tile)[0] / 255.0)
    if not vals:
        return 0.0
    arr = np.array(vals, dtype=np.float32)
    k = max(1, int(np.ceil(len(arr) * 0.15)))
    return float(np.partition(arr, -k)[-k:].mean())


def _require_constant_rate(cap: _ClipReader, fps: float, path: Path) -> None:
    """Refuse a variable frame rate rather than quietly retime the clip.

    The render writes every frame at one rate, so a clip whose frames are
    unevenly spaced would come out a different length and drift against its
    audio. Past half a frame a frame lands in another frame's slot. Real
    constant-rate files sit far inside that: Matroska and WebM round times to
    whole milliseconds, which is at most 0.03 of a frame at 59.94 fps.
    """
    if cap.frames_off_schedule(fps) > 0.5:
        raise SourceError(
            f"{path.name} has a variable frame rate, and Upflow2x needs every frame "
            "to last the same time or the result changes length. Convert it to a "
            "constant frame rate first, for example by rendering it from Resolve, or "
            f"with ffmpeg -i \"{path.name}\" -r 24 constant.mp4 using your timeline's "
            "frame rate.")


def frame_difference_scores(
    path: Path,
    cfg: Eternal2xConfig,
    *,
    progress: Optional[ProgressFn] = None,
    frames: FrameRange = None,
    threshold: Optional[float] = None,
) -> Tuple[List[float], float, int, int]:
    """Per-frame difference from the last new drawing, plus fps and dimensions.

    Comparing each frame only with the one before missed change that builds
    up slowly: a fade of one level a frame never crossed the threshold, and
    22 frames froze on the first. Against the last drawing the change adds up
    until it counts as a new one, so fades and slow pans keep moving.

    `threshold` decides where each drawing starts, so the plan has to be made
    with the same one. It defaults to `cfg.duplicate_threshold`.
    """
    if threshold is None:
        threshold = cfg.duplicate_threshold
    cap = _open(path, frames, progress)
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 24.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

        ok, first = cap.read()
        if not ok and frames and frames[0] > 0:
            raise SourceError(f"The clip's in and out points are outside its media, "
                              f"which ends before frame {frames[0]}.")
        if not ok:
            raise SourceError(f"Could not read any frames from: {path}")
        _require_constant_rate(cap, fps, path)
        if not width or not height:
            height, width = first.shape[:2]

        drawing = _analysis_frame(first, cfg.analysis_width)
        scores = [0.0]

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            _require_constant_rate(cap, fps, path)
            curr = _analysis_frame(frame, cfg.analysis_width)
            scores.append(_difference(drawing, curr))
            if scores[-1] >= threshold:
                drawing = curr
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
    frames: FrameRange = None,
) -> Tuple[DedupePlan, int, int]:
    """Decode the clip and return a rebuild plan plus its dimensions.

    With `frames`, only that range of the source is read, and the plan's frame
    numbers count from its first frame.

    Grain makes every held frame differ a little, so they all look new. With
    `cfg.grain` the threshold rises above the clip's own noise floor, and a
    grainy clip is decoded a second time with it. Without, the floor is only
    measured, so a clip that came out as all new frames can say it looks
    grainy. Raising it unasked merged real drawings: no test on the scores or
    the pixels told grain apart from fine real change on every clip.
    """
    scores, fps, width, height = frame_difference_scores(path, cfg, progress=progress,
                                                         frames=frames)
    floor = noise_threshold(scores, cfg.duplicate_threshold)
    threshold = floor if cfg.grain else cfg.duplicate_threshold
    if threshold > cfg.duplicate_threshold:
        scores, fps, width, height = frame_difference_scores(
            path, cfg, progress=progress, frames=frames, threshold=threshold)
    plan = plan_from_scores(
        scores, fps,
        threshold=threshold,
        force_base_hold=cfg.force_base_hold,
    )
    plan.grain_suspected = (not cfg.grain and floor > cfg.duplicate_threshold
                            and plan.is_noop)
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
                # Analysis read this frame, so it exists. Repeating the last
                # one instead would freeze the rest of the shot and still
                # report a finished render.
                raise SourceError(f"Could not read source frame {read_upto + 1} the second "
                                  "time through. The file may be damaged, or still being "
                                  "written.")
            read_upto += 1
            current = frame
        held_index = want
        yield current


# Encoders to try per container, best quality first.
_ENCODERS = {
    ".mkv": ("FFV1",),             # lossless; Resolve refuses FFV1 in AVI
    ".avi": ("FFV1", "MJPG"),
    ".mp4": ("avc1", "mp4v"),      # H.264, which Resolve always reads
    ".mov": ("mp4v", "jpeg"),
}


def _writer(path: Path, size: Tuple[int, int], fps: float) -> cv2.VideoWriter:
    w, h = size
    # OpenCV's FFmpeg writer takes the path as given. Where FFmpeg has no
    # H.264 encoder, Windows falls back to Media Foundation, which garbles a
    # name outside ASCII, so the render landed where the panel could not find
    # it. Such a path gets FFmpeg alone.
    apis = [cv2.CAP_FFMPEG]
    if not sys.platform.startswith("win") or str(path).isascii():
        apis.append(cv2.CAP_ANY)
    for fourcc in _ENCODERS.get(path.suffix.lower(), ("MJPG", "mp4v")):
        for api in apis:
            writer = cv2.VideoWriter(str(path), api, cv2.VideoWriter_fourcc(*fourcc), fps, (w, h))
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
    frames: FrameRange = None,
) -> RenderResult:
    """Write the rebuilt clip.

    The output has exactly as many frames as the source and runs at the same
    rate, so it drops straight back into an edit in place of the original.

    `sequence` says whether `output` is a folder of PNG frames. Left as None it
    is guessed from the suffix, which a caller that knows should not rely on,
    because a folder called `shot.v2_smooth` has the suffix `.v2_smooth`.

    `frames` must be the range of the source the plan was made from.
    """
    if plan.output_frames == 0:
        raise SourceError("Nothing to render: the plan has no frames.")

    is_sequence = (output.suffix == "") if sequence is None else bool(sequence)
    if is_sequence:
        output.mkdir(parents=True, exist_ok=True)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)

    # The network's 2x and Lanczos never both apply, or the render would be 4x.
    # Where the network cannot run, the upscale is left to Super Scale.
    factor = cfg.upscale_factor if cfg.upscale_enabled and not cfg.network_upscale else 1
    positions = output_time_map(plan)

    # Set up before the source is opened, since the first render installs the
    # network's runtime and downloads its model, and a failure there should
    # come before any frame is written. Where it cannot run, the drawings are
    # kept as drawn: no other way of making an in-between avoids ghosting.
    pair = None
    note = ""
    if cfg.interpolate_enabled:
        from Pipeline.rife import AIUnavailable, Rife  # needs onnxruntime
        try:
            pair = Rife(progress).interpolate_pair
        except AIUnavailable as exc:
            note = f"No in-betweens were drawn, so every drawing is kept as drawn. {exc}"
    upscaler = None
    upscale_note = ""
    if cfg.network_upscale:
        from Pipeline.rife import AIUnavailable
        from Pipeline.upscaler import Upscaler  # needs onnxruntime
        try:
            upscaler = Upscaler(progress)
        except AIUnavailable as exc:
            upscale_note = str(exc)

    # Which in-between times belong to each pair of consecutive drawings.
    last_pair = plan.output_frames - 2
    buckets: List[List[float]] = [[] for _ in range(max(1, plan.output_frames - 1))]
    for p in positions:
        i = min(int(p), max(0, last_pair))
        buckets[i].append(p - i)

    cap = _open(source, frames, progress)
    state = {"writer": None, "written": 0, "width": 0, "height": 0, "upscaler": upscaler,
             "upscale_note": upscale_note}

    def emit(frame: np.ndarray) -> None:
        """Upscale if asked, then write one output frame."""
        if state["upscaler"] is not None:
            try:
                frame = state["upscaler"].upscale(frame)
            except AIUnavailable as exc:
                # Past the first frame, the frames so far are 2x and the rest
                # cannot be left at 1x, so the render stops and says why.
                if state["written"]:
                    raise
                state["upscaler"], state["upscale_note"] = None, str(exc)
        if factor > 1:
            frame = upscale(frame, factor)
        if state["written"] and frame.shape[:2] != (state["height"], state["width"]):
            raise SourceError(f"Frame {state['written']} came out "
                              f"{frame.shape[1]}x{frame.shape[0]}, not "
                              f"{state['width']}x{state['height']}.")
        state["height"], state["width"] = frame.shape[:2]
        if is_sequence:
            target = output / f"frame_{state['written']:06d}.png"
            # Encode in memory and let Python write the file. cv2.imwrite
            # cannot open a non-ASCII path on Windows, so a clip in a folder
            # called Épisode 01 would fail on its first frame.
            encoded, data = cv2.imencode(".png", frame)
            if not encoded:
                raise SourceError(f"Could not encode frame {state['written']}.")
            try:
                target.write_bytes(data.tobytes())
            except OSError as exc:
                raise SourceError(f"Could not write {target}: {exc}. Check the disk "
                                  "has space and the folder is writable.") from exc
        else:
            if state["writer"] is None:
                # Every writer here rounds an odd size down to even without a
                # word, so the file would be a pixel short of what it reports.
                if state["width"] % 2 or state["height"] % 2:
                    raise SourceError(
                        f"{output.suffix.lstrip('.').upper() or 'Video'} output needs an even "
                        f"width and height, and this clip is {state['width']}x"
                        f"{state['height']}. Use the image sequence output, which keeps "
                        f"any size.")
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
                if pair is not None:
                    produced = pair(prev, curr, times)
                else:
                    # De-duplicate only: every output frame is a real drawing,
                    # nothing is synthesised, and each starts where it did.
                    produced = [(prev if t < 1.0 else curr).copy() for t in times]
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
        note=note,
        upscaled=state["upscaler"] is not None,
        upscale_note=state["upscale_note"],
    )
