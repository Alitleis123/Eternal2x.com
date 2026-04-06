from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Optional

from Pipeline.config import UpscaleConfig
from Stages.frame_detect import detect_motion_segments, segments_to_dict
from Stages.motion_score import compute_motion_scores
from Stages.resolve_helpers import get_resolve, get_clip_at_playhead


MARKER_PREFIX = "[DSU]"
DEFAULT_COLOR = "Blue"


def _load_segments(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _compute_segments_from_video(video_path: Path, cfg: UpscaleConfig) -> Dict:
    scores, fps = compute_motion_scores(video_path, cfg)
    segments = detect_motion_segments(scores, cfg)
    return {
        "settings": {
            "sensitivity": cfg.sensitivity,
            "min_segment_frames": cfg.min_segment_frames,
            "merge_gap_frames": cfg.merge_gap_frames,
        },
        "fps": fps,
        "frame_count": len(scores),
        "segments": segments_to_dict(segments),
    }


def _clear_dsu_markers(target) -> int:
    markers = target.GetMarkers() or {}
    removed = 0
    for frame_id, info in markers.items():
        name = (info or {}).get("name", "")
        if isinstance(name, str) and name.startswith(MARKER_PREFIX):
            target.DeleteMarkerAtFrame(frame_id)
            removed += 1
    return removed


def _add_segment_markers(target, segments, color: str) -> int:
    added = 0
    for idx, seg in enumerate(segments):
        start = int(seg["start"])
        end = int(seg["end"])
        length = int(seg.get("length", end - start + 1))
        label = f"{MARKER_PREFIX} seg {idx:03d}: {start}-{end}"
        note = f"len {length} frames"
        ok = target.AddMarker(start, color, label, note, length, "")
        if ok:
            added += 1
    return added


def main():
    parser = argparse.ArgumentParser(
        description="Place [DSU] motion markers in Resolve from segments.json or a video."
    )
    parser.add_argument(
        "--segments",
        default="segments.json",
        help="Path to segments.json (default: segments.json)",
    )
    parser.add_argument(
        "--video",
        default=None,
        help="Optional video path. If provided, compute segments directly.",
    )
    parser.add_argument(
        "--video-file",
        default=None,
        help="Path to a text file containing the video path (avoids cmd.exe Unicode issues).",
    )
    parser.add_argument(
        "--color",
        default=DEFAULT_COLOR,
        help="Resolve marker color (default: Blue)",
    )
    parser.add_argument(
        "--sensitivity",
        type=float,
        default=None,
        help="Override cfg.sensitivity when computing from --video",
    )
    args = parser.parse_args()

    # Resolve the video path: --video-file takes priority (Unicode-safe)
    video_path = args.video
    if args.video_file:
        vf = Path(args.video_file)
        if vf.exists():
            video_path = vf.read_text(encoding="utf-8").strip()

    cfg = UpscaleConfig()
    if args.sensitivity is not None:
        cfg.sensitivity = args.sensitivity
    if video_path:
        payload = _compute_segments_from_video(Path(video_path), cfg)
    else:
        payload = _load_segments(Path(args.segments))

    segments = payload.get("segments", [])
    if not segments:
        print("No segments found. Nothing to mark.")
        return

    resolve = get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise RuntimeError("No active project.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("No active timeline.")

    # Place markers on the timeline (visible regardless of track)
    removed = _clear_dsu_markers(timeline)
    added = _add_segment_markers(timeline, segments, args.color)

    print(f"Removed {removed} old markers. Added {added} new markers.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback, os
        log = os.path.join(os.environ.get("TEMP", "."), "eternal2x_error.log")
        with open(log, "w") as f:
            traceback.print_exc(file=f)
        traceback.print_exc()
        raise
