from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

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
    for frame_id, info in list(markers.items()):
        name = (info or {}).get("name", "")
        if isinstance(name, str) and name.startswith(MARKER_PREFIX):
            target.DeleteMarkerAtFrame(frame_id)
            removed += 1
    return removed


def _add_segment_markers(target, segments, color: str, source_start: int, source_end: int) -> int:
    """Add markers to a clip. Frame positions are relative to clip source start (frame 0 = first frame of clip)."""
    added = 0
    for idx, seg in enumerate(segments):
        raw_start = int(seg["start"])
        raw_end = int(seg["end"])

        # Skip segments outside the clip's source range
        if raw_end < source_start or raw_start > source_end:
            continue

        # Clamp to clip boundaries
        clamped_start = max(raw_start, source_start)
        clamped_end = min(raw_end, source_end)

        # Convert to clip-relative frame (0 = first frame of clip on timeline)
        marker_frame = clamped_start - source_start
        length = clamped_end - clamped_start + 1

        label = f"{MARKER_PREFIX} seg {idx:03d}"
        note = f"motion frames {clamped_start}-{clamped_end} ({length}f)"
        ok = target.AddMarker(marker_frame, color, label, note, length, "")
        if ok:
            added += 1
        else:
            print(f"[DEBUG] AddMarker failed: frame={marker_frame}, len={length}")
    return added


def main():
    parser = argparse.ArgumentParser(
        description="Place [DSU] motion markers on the clip at the playhead."
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
        print("No motion detected. Try lowering the sensitivity.")
        return

    resolve = get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise RuntimeError("No active project.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("No active timeline.")

    # Find clip at playhead
    clip, _track = get_clip_at_playhead(timeline)
    if clip is None:
        raise RuntimeError("No clip at playhead. Move the playhead over a clip and try again.")

    # Get the clip's source in/out points (which part of the video file is used)
    clip_start = int(clip.GetStart())
    clip_end = int(clip.GetEnd())
    clip_duration = clip_end - clip_start

    # LeftOffset = how many frames into the source media the clip begins
    source_start = 0
    if hasattr(clip, "GetLeftOffset"):
        try:
            source_start = int(clip.GetLeftOffset())
        except Exception:
            pass
    source_end = source_start + clip_duration - 1

    print(f"[DEBUG] Clip on timeline: {clip_start}-{clip_end} ({clip_duration} frames)")
    print(f"[DEBUG] Source range: {source_start}-{source_end}")
    print(f"[DEBUG] Total segments from detection: {len(segments)}")

    # Filter segments to those within the source range
    relevant = [s for s in segments
                 if int(s["end"]) >= source_start and int(s["start"]) <= source_end]
    print(f"[DEBUG] Segments within clip source range: {len(relevant)}")

    # Clear old DSU markers from this clip, then add new ones
    removed = _clear_dsu_markers(clip)
    added = _add_segment_markers(clip, relevant, args.color, source_start, source_end)

    print(f"Removed {removed} old markers. Added {added} new markers on clip.")


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
