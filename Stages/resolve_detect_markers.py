from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

from Pipeline.config import UpscaleConfig
from Stages.frame_detect import detect_motion_segments, segments_to_dict
from Stages.motion_score import compute_motion_scores
from Stages.resolve_helpers import get_resolve, get_clip_at_playhead, _timecode_to_frames, _get_timeline_fps


MARKER_PREFIX = "[DSU]"
DEFAULT_COLOR = "Blue"


def _load_segments(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _compute_segments_from_video(video_path: Path, cfg: UpscaleConfig,
                                  start_frame: int = 0, end_frame: int = -1) -> Dict:
    scores, fps = compute_motion_scores(
        video_path, cfg, start_frame=start_frame, end_frame=end_frame
    )
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


def _is_dsu_marker(info) -> bool:
    """Check if a marker belongs to Eternal2x (by customData or legacy name prefix)."""
    custom = (info or {}).get("customData", "")
    if isinstance(custom, str) and custom == MARKER_PREFIX:
        return True
    name = (info or {}).get("name", "")
    if isinstance(name, str) and name.startswith(MARKER_PREFIX):
        return True
    return False


def _clear_dsu_markers(target) -> int:
    markers = target.GetMarkers() or {}
    removed = 0
    for frame_id, info in list(markers.items()):
        if _is_dsu_marker(info):
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

    # Connect to Resolve and find clip FIRST, so we know the source range to scan
    resolve = get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise RuntimeError("No active project.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("No active timeline.")

    clip, _track = get_clip_at_playhead(timeline)
    if clip is None:
        raise RuntimeError("No clip at playhead. Move the playhead over a clip and try again.")

    # Get the clip's source in/out points
    fps = _get_timeline_fps(timeline)
    raw_start = clip.GetStart()
    raw_end = clip.GetEnd()
    if isinstance(raw_start, str) and not raw_start.isdigit():
        clip_start = _timecode_to_frames(raw_start, fps) or 0
    else:
        clip_start = int(raw_start)
    if isinstance(raw_end, str) and not raw_end.isdigit():
        clip_end = _timecode_to_frames(raw_end, fps) or 0
    else:
        clip_end = int(raw_end)
    clip_duration = clip_end - clip_start

    # LeftOffset = how many frames into the source media the clip begins
    source_start = 0
    if hasattr(clip, "GetLeftOffset"):
        try:
            source_start = int(clip.GetLeftOffset())
        except Exception:
            pass
    source_end = source_start + clip_duration - 1

    print(f"[DEBUG] Clip duration: {clip_duration} frames, source range: {source_start}-{source_end}")

    # Compute or load segments — only scan the clip's source range
    cfg = UpscaleConfig()
    if args.sensitivity is not None:
        cfg.sensitivity = args.sensitivity

    # For clip-based scanning, use min_segment_frames=1 so even short bursts are caught
    cfg.min_segment_frames = 1

    print(f"[DEBUG] Sensitivity: {cfg.sensitivity}, clip: {clip_duration}f")

    # Get raw motion scores for the clip's source range
    from Stages.motion_score import compute_motion_scores as _cms
    if video_path:
        raw_scores, _ = _cms(
            Path(video_path), cfg,
            start_frame=source_start, end_frame=source_end,
        )
    else:
        print("No video path provided.")
        return

    import numpy as _np

    scores_arr = _np.array(raw_scores, dtype=_np.float64)

    if len(scores_arr) == 0 or scores_arr.max() == 0:
        print("No motion detected in clip.")
        return

    # Use SSIM-like approach: compare consecutive frames structurally
    # Sort non-zero scores to find the natural noise floor
    nonzero = scores_arr[scores_arr > 0]
    if len(nonzero) == 0:
        print("All frames are identical.")
        return

    # Find the gap between noise and real changes using Otsu-like thresholding
    sorted_scores = _np.sort(nonzero)
    best_threshold = 0.0
    best_variance = 0.0

    for t_idx in range(1, len(sorted_scores)):
        t = sorted_scores[t_idx]
        below = sorted_scores[:t_idx]
        above = sorted_scores[t_idx:]
        if len(below) == 0 or len(above) == 0:
            continue
        w0 = len(below) / len(sorted_scores)
        w1 = len(above) / len(sorted_scores)
        var_between = w0 * w1 * (below.mean() - above.mean()) ** 2
        if var_between > best_variance:
            best_variance = var_between
            best_threshold = t

    # Sensitivity slider adjusts around the auto-detected threshold
    # Higher sensitivity = lower threshold = more markers
    # sensitivity 0.5 = use auto threshold
    # sensitivity 1.0 = mark everything with any change
    # sensitivity 0.0 = only mark the biggest changes
    if cfg.sensitivity >= 0.99:
        final_threshold = 0.0  # mark everything
    elif cfg.sensitivity <= 0.01:
        final_threshold = scores_arr.max() * 0.9  # only top changes
    else:
        # Scale threshold: at 0.5 use auto, above 0.5 go lower, below 0.5 go higher
        scale = 1.0 - cfg.sensitivity  # 0.5 sensitivity -> scale 0.5
        final_threshold = best_threshold * (scale * 2)  # at 0.5: use 1x auto threshold

    print(f"[DEBUG] Auto threshold (Otsu): {best_threshold:.6f}")
    print(f"[DEBUG] Final threshold: {final_threshold:.6f} (sensitivity: {cfg.sensitivity})")
    print(f"[DEBUG] Scores: {[round(float(s), 5) for s in scores_arr]}")

    # Place markers ON THE CLIP
    removed = _clear_dsu_markers(clip)
    _clear_dsu_markers(timeline)

    added = 0
    for i in range(1, len(scores_arr)):  # skip frame 0
        if scores_arr[i] > final_threshold:
            source_frame = source_start + i
            marker_num = added + 1
            label = f"Marker {marker_num}"
            ok = clip.AddMarker(source_frame, args.color, label, "", 1, MARKER_PREFIX)
            if ok:
                added += 1

    if added == 0:
        print("No motion detected at this sensitivity. Try increasing the sensitivity slider.")
    else:
        print(f"Removed {removed} old markers. Added {added} markers on clip.")


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
