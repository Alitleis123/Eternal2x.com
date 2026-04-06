from __future__ import annotations

import argparse
from typing import List, Tuple

from Pipeline.config import UpscaleConfig
from Stages.frame_detect import detect_motion_segments
from Stages.motion_score import compute_motion_scores
from Stages.resolve_helpers import get_resolve, get_clip_at_playhead


MARKER_PREFIX = "[DSU]"


def _is_dsu_marker(info) -> bool:
    custom = (info or {}).get("customData", "")
    if isinstance(custom, str) and custom == MARKER_PREFIX:
        return True
    name = (info or {}).get("name", "")
    if isinstance(name, str) and name.startswith(MARKER_PREFIX):
        return True
    return False


def _ranges_from_markers(marker_dict, base_tl_start: int) -> List[Tuple[int, int]]:
    ranges = []
    for frame_id, info in (marker_dict or {}).items():
        if not _is_dsu_marker(info):
            continue
        try:
            start = int(frame_id)
        except Exception:
            continue
        duration = int((info or {}).get("duration", 1) or 1)
        if duration < 1:
            duration = 1
        end = start + duration - 1
        ranges.append((base_tl_start + start, base_tl_start + end))
    ranges.sort()
    return ranges


def _ranges_from_video(path, cfg: UpscaleConfig, clip_start: int) -> List[Tuple[int, int]]:
    scores, _fps = compute_motion_scores(path, cfg)
    segments = detect_motion_segments(scores, cfg)
    ranges = []
    for seg in segments:
        ranges.append((clip_start + seg.start, clip_start + seg.end))
    return ranges


def _get_video_items(timeline, track_index: int):
    if not hasattr(timeline, "GetItemListInTrack"):
        return []
    items = timeline.GetItemListInTrack("video", track_index) or []
    return list(items)


def _set_clip_property(item, key: str, value: str) -> bool:
    if hasattr(item, "SetClipProperty"):
        ok = item.SetClipProperty(key, value)
        if ok:
            return True
    if hasattr(item, "GetMediaPoolItem"):
        mpi = item.GetMediaPoolItem()
        if mpi and hasattr(mpi, "SetClipProperty"):
            ok = mpi.SetClipProperty(key, value)
            if ok:
                return True
    return False


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return not (a_end < b_start or b_end < a_start)


def main():
    parser = argparse.ArgumentParser(
        description="Apply 2x upscale and gate interpolation using [DSU] markers."
    )
    parser.add_argument("--track", type=int, default=1, help="Video track index (default: 1)")
    parser.add_argument("--sensitivity", type=float, default=None, help="Override cfg.sensitivity")
    parser.add_argument("--video", default=None, help="Optional video path for recompute if no markers")
    args = parser.parse_args()

    resolve = get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise RuntimeError("No active project.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("No active timeline.")

    selected, _ = get_clip_at_playhead(timeline)
    if selected is None:
        print("No clip at playhead. Move the playhead over a clip and try again.")
        return
    clip_start = int(selected.GetStart())

    # Read markers from the clip at playhead
    ranges: List[Tuple[int, int]] = []
    if hasattr(selected, "GetMarkers"):
        ranges = _ranges_from_markers(selected.GetMarkers(), clip_start)

    if not ranges and args.video:
        cfg = UpscaleConfig()
        if args.sensitivity is not None:
            cfg.sensitivity = args.sensitivity
        ranges = _ranges_from_video(args.video, cfg, clip_start)

    if not ranges:
        print("No [DSU] markers found on clip. Run Detect first.")
        return

    items = _get_video_items(timeline, args.track)
    if not items:
        print("No clips found on video track.")
        return

    upscale_ok = 0
    interp_on = 0
    interp_off = 0

    for it in items:
        try:
            start = int(it.GetStart())
            dur = int(it.GetDuration())
        except Exception:
            continue
        end = start + dur - 1
        in_motion = any(_overlaps(start, end, rs, re) for rs, re in ranges)

        if _set_clip_property(it, "Super Scale", "2x"):
            upscale_ok += 1

        if in_motion:
            if _set_clip_property(it, "Retime Process", "Optical Flow"):
                interp_on += 1
        else:
            if _set_clip_property(it, "Retime Process", "Nearest"):
                interp_off += 1

    print(
        f"Upscale applied to {upscale_ok} clips. "
        f"Interpolation on: {interp_on}, off: {interp_off}."
    )


if __name__ == "__main__":
    main()
