"""Analyse the selected clip, rebuild it smoothly, and put the result back.

This is what the panel's buttons run. With --analyse it only measures the clip
and reports what it would do, which is fast and changes nothing. Without it,
the clip is rendered, imported and appended to the timeline.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Optional, Tuple

# numpy and OpenCV load through these. A Python without them must still be
# able to tell the panel so, or the panel waits on a run that never started.
try:
    from Pipeline import resolve_bridge as bridge
    from Pipeline.config import Upflow2xConfig
    from Pipeline.dedupe import plan_from_marks
    from Pipeline.render import SourceError, analyse_video, probe_video, render_plan
except ImportError as exc:
    IMPORT_ERROR: Optional[ImportError] = exc
else:
    IMPORT_ERROR = None


class Stopped(BaseException):
    """The panel's Stop. Not an Exception, so nothing on the way out catches it."""


class StatusFile:
    """Progress the panel can poll while the render runs in the background.

    Rendering takes far longer than a UI click, so the stage is launched
    detached and reports here instead of blocking Resolve's interface. Writes
    go to a temporary file and are then moved into place, so a half-written
    file is never read.
    """

    def __init__(self, path: Optional[str]) -> None:
        self.path = Path(path) if path else None
        # The panel's Stop creates this, and the run ends at its next frame.
        self.stop_path = self.path.with_suffix(".stop") if self.path else None
        # Set once the result is going into Resolve. Stopped halfway there it
        # would be imported but not placed, so from then on the run finishes.
        self.committed = False
        self.stage = ""
        self.fraction = 0.0
        self.message = ""
        # What the panel should hand to Resolve's ImportMedia. For an image
        # sequence that is the first frame, not the folder, plus the printf
        # pattern and frame count Resolve's sequence import takes.
        self.import_path = ""
        self.sequence_pattern = ""
        self.sequence_frames = 0
        # A sequence carries no frame rate, so Resolve would play it at the
        # project's. The panel sets this on the imported clip.
        self.fps = 0.0
        # After an analysis, the frames found to be drawings, counted from the
        # clip's first frame, for the panel to mark on the clip.
        self.marks = ""
        # The render is already 2x, so the panel must not add Super Scale.
        self.upscaled = False
        # How many times longer than its clip the render is, so the panel
        # places all of it, even opened again since the render started.
        self.slow_motion = 1

    def write(self, *, done: bool = False, ok: bool = False,
              message: Optional[str] = None) -> None:
        if self.path is None:
            return
        if message is not None:
            # The panel parses this file with simple pattern matching, so keep
            # the text on one line and free of quotes and backslashes.
            self.message = " ".join(str(message).split()).replace('"', "'").replace("\\", "/")
        payload = {
            "stage": self.stage,
            "fraction": round(self.fraction, 4),
            "message": self.message,
            "import_path": self.import_path,
            "sequence_pattern": self.sequence_pattern,
            "sequence_frames": self.sequence_frames,
            "fps": round(self.fps, 3),
            "marks": self.marks,
            "upscaled": self.upscaled,
            "slow_motion": self.slow_motion,
            "done": done,
            "ok": ok,
            "updated": time.time(),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            # ensure_ascii=False keeps a path like Épisode readable as UTF-8.
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            return  # progress reporting must never break the render
        # Windows refuses to replace a file another process has open, which
        # the panel does every time it polls. Losing a progress tick is fine,
        # but losing the final write leaves the panel waiting forever.
        attempts = 100 if done else 3
        for attempt in range(attempts):
            # A Stop pressed after the last frame, while the writes finished
            # or while this waits for the panel to let go of the file, still
            # counts: the run reports Stopped, not a result to import.
            if done and ok:
                self.check_stop()
            try:
                temp.replace(self.path)
                return
            except OSError:
                if attempt + 1 < attempts:
                    time.sleep(0.02)

    def check_stop(self) -> None:
        """Raise Stopped if the panel's Stop was pressed in time."""
        if not self.committed and self.stop_path is not None and self.stop_path.exists():
            raise Stopped()

    def progress(self, stage: str, fraction: float) -> None:
        self.check_stop()
        self.stage = stage
        self.fraction = fraction
        self.write()


def _progress_printer(status: "StatusFile"):
    """Print progress sparingly: Resolve's console is slow to draw."""
    state = {"last": 0.0, "stage": ""}

    def report(stage: str, fraction: float) -> None:
        now = time.time()
        status.progress(stage, fraction)
        if stage != state["stage"]:
            state["stage"] = stage
            state["last"] = 0.0
        if fraction >= 1.0 or now - state["last"] >= 1.0:
            state["last"] = now
            print(f"  {stage}: {fraction * 100:.0f}%", flush=True)

    return report


# png    lossless image sequence; always readable by Resolve, many files
# mp4    H.264; compact and universally readable, but lossy
# mkv    FFV1; lossless single file. Resolve Studio 20.3.1 imports FFV1 in
#        MKV but refuses it in AVI.
FORMATS = {"png": "", "mp4": ".mp4", "mkv": ".mkv"}

# How Pipeline/render.py names sequence frames, as Resolve's ImportMedia wants it.
SEQUENCE_PATTERN = "frame_%06d.png"


def _finished(plan, result) -> str:
    """The panel's message for a finished render."""
    if result.note:
        message = (f"Rendered {plan.unique_drawings} drawings into "
                   f"{result.frames_written} frames. {result.note}")
    else:
        message = (f"Rendered {plan.unique_drawings} drawings into "
                   f"{result.frames_written} smooth frames.")
    if result.upscale_note:
        message += (f" Real-ESRGAN did not run, so the upscale is left to Super Scale. "
                    f"{result.upscale_note}")
    return message


def _output_path(source: Path, output_dir: Optional[str], fmt: str,
                 frames: Optional[tuple] = None, slow: int = 1) -> Path:
    base = Path(output_dir) if output_dir else source.parent / "Upflow2x"
    base.mkdir(parents=True, exist_ok=True)
    suffix = FORMATS.get(fmt, "")
    # Name a trimmed render after its range, so two cuts of one file differ,
    # and a slowed one after how much slower.
    part = f"_f{frames[0]}-{frames[1]}" if frames else ""
    if slow > 1:
        part += f"_{slow}x"
    stem = f"{source.stem}{part}_smooth"

    def taken(path: Path) -> bool:
        if not path.exists():
            return False
        return any(path.iterdir()) if path.is_dir() else True

    target = base / f"{stem}{suffix}"
    n = 1
    while taken(target):
        n += 1
        target = base / f"{stem}_{n:02d}{suffix}"
    return target


def drawing_marks(plan) -> list:
    """The frames to mark on the clip for this plan's drawings.

    Each drawing is marked where it starts, so everything between two
    drawings is drawn fresh. A pose held for more than twice the clip's
    pattern is a pause, and is marked again one pattern before it ends, so it
    stays still and then moves into the next drawing at the clip's own pace.
    Marked only where it starts, it would drift the whole way there. Shorter
    holds are drawn through, like 3s in a clip mostly on 2s.
    """
    from Pipeline.dedupe import pose_base
    base = pose_base(plan.base_hold, plan.fps)
    marks = set()
    for hold in plan.holds:
        marks.add(hold.index)
        if hold.length > 2 * base:
            marks.add(hold.index + hold.length - base)
    return sorted(marks)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild the selected Resolve clip with smooth motion."
    )
    parser.add_argument("--analyse", action="store_true",
                        help="Only measure the clip and report; change nothing.")
    parser.add_argument("--video", default=None,
                        help="Process this file instead of the Resolve selection.")
    parser.add_argument("--start-time", type=float, default=None,
                        help="Seconds into the file where the clip starts. With "
                             "--end-time, only that part is analysed and rebuilt.")
    parser.add_argument("--end-time", type=float, default=None,
                        help="Seconds into the file where the clip ends.")
    parser.add_argument("--output-dir", default=None,
                        help="Where to write the result (default: an Upflow2x "
                             "folder beside the source).")
    parser.add_argument("--format", default="png", choices=sorted(FORMATS),
                        help="png: lossless image sequence (default). "
                             "mp4: compact H.264 file. "
                             "mkv: lossless FFV1 file.")
    parser.add_argument("--threshold", type=float, default=None,
                        help="Duplicate-detection threshold override. With --grain, "
                             "the clip's own noise can still raise it.")
    parser.add_argument("--grain", action="store_true",
                        help="The clip has film grain: raise the threshold above its "
                             "noise, so held frames are not each taken for a drawing. "
                             "Leave off for clean animation.")
    parser.add_argument("--base-hold", type=int, default=0,
                        help="Force the hold pattern (2 = on 2s). 0 auto-detects.")
    parser.add_argument("--marks", default=None,
                        help="Comma-separated frames, counted from the clip's first frame, "
                             "to keep as drawn. Every other frame becomes an in-between "
                             "of the marks around it, and nothing is detected.")
    # A panel still open across an update sends the quality it used to offer.
    # The in-betweens are always drawn by the network now, so it is ignored.
    parser.add_argument("--marks-file", default=None,
                        help="A file holding the --marks list, for a list too long "
                             "for the command line.")
    parser.add_argument("--quality", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--no-interpolate", action="store_true",
                        help="De-duplicate only, without drawing in-betweens. With it on, "
                             "the in-betweens are drawn by the RIFE network, on the GPU on "
                             "Windows, and the first run downloads about 50 MB.")
    parser.add_argument("--no-upscale", action="store_true",
                        help="Skip the 2x upscale.")
    parser.add_argument("--network-upscale", action="store_true",
                        help="Draw the 2x upscale in the render with Real-ESRGAN, on a GPU. "
                             "Without a GPU, or without this flag, the render stays at "
                             "source resolution and Super Scale is applied on import.")
    parser.add_argument("--slow-motion", type=int, default=1, choices=range(1, 5),
                        metavar="N",
                        help="Draw N frames for every frame of the clip, 1 to 4, at the same "
                             "rate, so the render plays N times slower, ready to time remap. "
                             "1 (default) keeps the clip's length.")
    parser.add_argument("--json", action="store_true",
                        help="Emit a machine-readable summary.")
    parser.add_argument("--status-file", default=None,
                        help="Write progress here so the panel can poll it.")
    return parser


def config_from_args(args) -> Upflow2xConfig:
    cfg = Upflow2xConfig()
    if args.threshold is not None:
        cfg.duplicate_threshold = args.threshold
    if args.base_hold:
        cfg.force_base_hold = args.base_hold
    cfg.grain = bool(getattr(args, "grain", False))
    cfg.interpolate_enabled = not args.no_interpolate
    # The network draws the 2x upscale on a GPU. Without one, the renderer
    # works at source resolution and Super Scale is applied to the imported
    # clip. Lanczos, the renderer's own upscale, is never used here.
    # The panel has to ask for the network, since it must then leave Super
    # Scale off. A panel from before the upscaler, still open while an update
    # replaces this file, does not ask, and would scale a 2x render to 4x.
    cfg.upscale_enabled = False
    cfg.upscale_factor = 1 if args.no_upscale else 2
    cfg.network_upscale = args.network_upscale and not args.no_upscale
    cfg.slow_motion = args.slow_motion
    return cfg


def source_frames(source: Path, start_time: Optional[float],
                  end_time: Optional[float]) -> Tuple[Optional[tuple], bool]:
    """The [start, end) source frames a clip covers, or None for all, and
    whether that is the whole file.

    The panel passes seconds rather than frames because seconds mean the same
    thing whatever the timeline's frame rate, and only the file knows its own.
    Some containers only carry an estimated frame count, a few frames short or
    even negative, so the count never moves a cut's bounds and reads stop at
    the real end of the file. It only decides whether a cut from frame 0 is
    the whole file, which keeps the range out of the render's name.
    """
    if start_time is None and end_time is None:
        return None, True
    fps, total = probe_video(source)
    total = max(0, total)
    start = max(0, int(round((start_time or 0.0) * fps)))
    end = int(round(end_time * fps)) if end_time is not None else total
    if start >= end:
        raise SourceError(
            f"The clip's in and out points ({start_time}s to {end_time}s) are "
            f"outside its media.")
    return (start, end), start == 0 and end == total


def _run(args, summary: dict) -> int:
    cfg = config_from_args(args)
    status = StatusFile(args.status_file)
    status.write(message="Starting")

    resolve = None
    project = timeline = None
    start_time, end_time = args.start_time, args.end_time
    if args.video:
        source = Path(args.video)
    else:
        try:
            resolve = bridge.connect()
            project, timeline = bridge.current_timeline(resolve)
            item = bridge.selected_clip(timeline)
            source = bridge.clip_source_path(item)
        except bridge.ResolveError as exc:
            print(f"Error: {exc}")
            summary["error"] = str(exc)
            status.write(done=True, ok=False, message=str(exc))
            return 2
        # The panel passes the cut's range. Run directly, the stage reads it
        # from Resolve the same way, or a trimmed cut would be rebuilt whole.
        if start_time is None and end_time is None:
            start_time, end_time = bridge.clip_source_range(item) or (None, None)

    if not source.exists():
        message = f"The clip's media is missing: {source.name}"
        print(f"Error: {message}")
        summary["error"] = message
        status.write(done=True, ok=False, message=message)
        return 2

    print(f"Source: {source.name}")
    progress = _progress_printer(status)

    try:
        frames, whole = source_frames(source, start_time, end_time)
        if not whole:
            print(f"Trimmed to source frames {frames[0]} to {frames[1] - 1}.")
            summary["frames"] = list(frames)
        plan, width, height = analyse_video(source, cfg, progress=progress,
                                            frames=frames)
    except SourceError as exc:
        print(f"Error: {exc}")
        summary["error"] = str(exc)
        status.write(done=True, ok=False, message=str(exc))
        return 2

    if args.marks_file is not None:
        try:
            args.marks = Path(args.marks_file).read_text(encoding="utf-8")
        except OSError as exc:
            args.marks = f"(unreadable: {exc})"
    if args.marks is not None:
        try:
            marks = [int(m) for m in args.marks.split(",") if m.strip()]
        except ValueError:
            message = f"The marked frames could not be read: {args.marks}"
            print(f"Error: {message}")
            summary["error"] = message
            status.write(done=True, ok=False, message=message)
            return 2
        plan = plan_from_marks(marks, plan.source_frames, plan.fps)
    print(plan.describe())
    summary.update({"source": str(source), "width": width, "height": height,
                    "plan": plan.to_dict()})

    # Slowed down, a clip on 1s or with every frame marked still has frames
    # to draw between them. Analyse has nothing more to mark either way.
    same_length = cfg.slow_motion == 1 or args.analyse
    if plan.is_noop and plan.marked and (same_length or plan.unique_drawings <= 1):
        if plan.unique_drawings <= 1:
            why = "Only the first frame is marked, so there is nothing to draw between."
            short = "Mark at least one more frame on the clip, or delete the markers to let Upflow2x find the drawings."
        else:
            why = "Every frame is marked, so there is nothing to draw between."
            short = "Every frame is marked. Delete the markers on frames that should be drawn fresh."
        print(f"Nothing to do. {why}")
        summary["skipped"] = "marks"
        summary["ok"] = True
        status.write(done=True, ok=True, message=short)
        return 0

    if plan.is_noop and (same_length or plan.unique_drawings <= 1):
        if plan.unique_drawings <= 1:
            why = "This clip is a single held image, so there is no motion to rebuild."
            short = "One held image. There is no motion to rebuild."
            summary["skipped"] = "single drawing"
        else:
            why = "This clip has no duplicated frames. It is live action or already animated on 1s."
            short = "No duplicate frames. This clip is already on 1s."
            summary["skipped"] = "no duplicate frames"
            if plan.grain_suspected:
                why = ("Every frame differs, but by about the same small amount, as grain "
                       "makes held frames differ. If the clip is grainy, mark the drawings "
                       "with M, or run with --grain and try again.")
                short = ("Every frame differs, but the differences look like grain. Mark the "
                         "drawings with M, then press Render.")
        print(f"Nothing to do. {why}")
        summary["ok"] = True
        status.write(done=True, ok=True, message=short)
        return 0

    if args.analyse:
        print("Analysis only. Nothing was changed.")
        summary["ok"] = True
        status.marks = ",".join(str(f) for f in drawing_marks(plan))
        status.write(done=True, ok=True, message=plan.describe())
        return 0

    output = _output_path(source, args.output_dir, args.format, None if whole else frames,
                          cfg.slow_motion)
    print(f"Rendering to: {output}")
    try:
        result = render_plan(source, plan, output, cfg, progress=progress,
                             sequence=(args.format == "png"), frames=frames)
    except SourceError as exc:
        print(f"Error: {exc}")
        summary["error"] = str(exc)
        status.write(done=True, ok=False, message=str(exc))
        return 2
    # Stopped while the last frames were written: nothing goes into Resolve.
    status.check_stop()

    print(f"Wrote {result.frames_written} frames at {result.fps:.3f} fps.")
    if result.note:
        print(result.note)
    if result.upscaled:
        print(f"Upscaled 2x to {result.width}x{result.height} with Real-ESRGAN.")
    elif cfg.network_upscale:
        print(f"Real-ESRGAN did not run, so Super Scale does the upscale. {result.upscale_note}")
    status.upscaled = result.upscaled
    status.slow_motion = cfg.slow_motion
    summary["render"] = result.to_dict()

    # Resolve imports an image sequence from its first frame, not its folder.
    if result.is_sequence:
        frames = sorted(result.output.glob("*.png"))
        status.import_path = str(frames[0]) if frames else str(result.output)
        if frames:
            status.sequence_pattern = str(result.output / SEQUENCE_PATTERN)
            status.sequence_frames = len(frames)
            status.fps = result.fps
    else:
        status.import_path = str(result.output)

    if resolve is None:
        # The panel has its own Resolve connection and finishes the import from
        # there, which avoids depending on external scripting being enabled.
        print(f"Rendered to: {result.output}")
        summary["ok"] = True
        summary["import_path"] = status.import_path
        status.write(done=True, ok=True, message=_finished(plan, result))
        return 0

    # Too late to stop from here, or the clip would be left half placed.
    status.committed = True
    try:
        media_item, imported = bridge.import_media(project, result.output,
                                                   frame_count=result.frames_written,
                                                   fps=result.fps)
        print(("Imported: " if imported else "Import failed: ") + imported.detail)
        summary["import"] = {"ok": imported.ok, "detail": imported.detail}

        if not imported:
            print(f"The render is fine and is waiting at: {result.output}")
            print("Drag it into your media pool to finish.")
            summary["ok"] = True
            status.write(done=True, ok=True,
                         message=f"Rendered, but Resolve would not import it. "
                                 f"Drag in {result.output.name} to finish.")
            return 0

        if result.upscaled:
            summary["upscale"] = {"ok": True, "detail": "Drawn 2x by Real-ESRGAN."}
        elif cfg.upscale_factor > 1:
            studio = bridge.is_studio(resolve)
            if studio is False:
                print("Skipping upscale: Super Scale needs DaVinci Resolve Studio.")
                summary["upscale"] = {"ok": False, "detail": "not Studio"}
            else:
                scaled = bridge.set_super_scale(media_item, cfg.upscale_factor)
                print(("Upscale: " if scaled else "Upscale not applied: ") + scaled.detail)
                summary["upscale"] = {"ok": scaled.ok, "detail": scaled.detail}

        appended = bridge.append_to_timeline(project, media_item)
        print(("Timeline: " if appended else "Timeline not updated: ") + appended.detail)
        summary["append"] = {"ok": appended.ok, "detail": appended.detail}

        summary["ok"] = True
        print("Done.")
        status.write(done=True, ok=True, message=_finished(plan, result))
        return 0
    finally:
        # However this ends, a Stop that came now was not used, and would end
        # the next run.
        if status.stop_path is not None:
            with contextlib.suppress(OSError):
                status.stop_path.unlink()


def _run_reporting(args, summary: dict) -> int:
    """Run, and make sure the panel hears about any failure."""
    if IMPORT_ERROR is not None:
        message = (f"This Python cannot load the packages Upflow2x needs "
                   f"({IMPORT_ERROR}). Re-run the installer.")
        print(f"Error: {message}")
        print(f"Python: {sys.executable}")
        summary["error"] = message
        StatusFile(args.status_file).write(done=True, ok=False, message=message)
        return 3
    try:
        return _run(args, summary)
    except Stopped:
        print("Stopped.")
        # It may have stopped after the summary already said it worked.
        summary["ok"] = False
        summary.pop("import_path", None)
        summary["error"] = "Stopped."
        status = StatusFile(args.status_file)
        with contextlib.suppress(OSError):
            status.stop_path.unlink()
        status.write(done=True, ok=False, message="Stopped.")
        return 1
    except Exception as exc:
        traceback.print_exc()
        message = f"Upflow2x hit an unexpected error: {exc}"
        summary["error"] = message
        StatusFile(args.status_file).write(done=True, ok=False, message=message)
        return 1


def main() -> int:
    # The panel sends this output to a log file, which Python on Windows would
    # encode in the ANSI code page, and a clip named in Japanese would then
    # fail on its first print. The panel reads the log as UTF-8.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args()
    summary: dict = {"ok": False}
    if not args.json:
        return _run_reporting(args, summary)
    # With --json, stdout carries exactly one JSON object, so the progress and
    # human-readable lines go to stderr where they cannot break a parser.
    with contextlib.redirect_stdout(sys.stderr):
        code = _run_reporting(args, summary)
    summary["exit_code"] = code
    print(json.dumps(summary, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
