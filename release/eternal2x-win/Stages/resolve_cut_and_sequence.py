from __future__ import annotations

import argparse
from typing import List, Tuple

from Stages.resolve_helpers import get_resolve, get_clip_at_playhead


def _markers_to_frames(marker_dict) -> List[int]:
    frames = []
    for frame_id, _info in (marker_dict or {}).items():
        try:
            frames.append(int(frame_id))
        except Exception:
            continue
    frames = sorted(set(frames))
    return frames


def _split_at_frame(timeline, item, frame: int) -> bool:
    # Try a few known API variants.
    if hasattr(timeline, "SplitClip"):
        try:
            return bool(timeline.SplitClip(item, frame))
        except Exception:
            pass
    if hasattr(timeline, "SplitClips"):
        try:
            return bool(timeline.SplitClips(frame))
        except Exception:
            pass
    return False


def _set_duration_one_frame(item) -> bool:
    start = int(item.GetStart())
    end = start + 1
    if hasattr(item, "SetEnd"):
        try:
            return bool(item.SetEnd(end))
        except Exception:
            pass
    if hasattr(item, "SetEndFrame"):
        try:
            return bool(item.SetEndFrame(end))
        except Exception:
            pass
    if hasattr(item, "SetClipProperty"):
        try:
            return bool(item.SetClipProperty("Duration", "1"))
        except Exception:
            pass
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Cut selected clip at markers and set each resulting clip to 1 frame."
    )
    args = parser.parse_args()

    resolve = get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise RuntimeError("No active project.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("No active timeline.")

    # Read [DSU] markers from the timeline
    marker_dict = timeline.GetMarkers() or {}
    dsu_frames = []
    for frame_id, info in (marker_dict or {}).items():
        name = (info or {}).get("name", "")
        if isinstance(name, str) and name.startswith("[DSU]"):
            try:
                dsu_frames.append(int(frame_id))
            except Exception:
                continue
    dsu_frames = sorted(set(dsu_frames))

    if not dsu_frames:
        print("No [DSU] markers found. Run Detect first.")
        return

    # Find the clip at the playhead
    target, target_track = get_clip_at_playhead(timeline)
    if target is None:
        print("No clip at playhead. Move the playhead over a clip and run Sequence.")
        return

    mpi = target.GetMediaPoolItem()
    if mpi is None:
        print("Clip at playhead has no media pool item.")
        return

    clip_start = int(target.GetStart())
    clip_end = int(target.GetEnd())

    # Timeline markers are absolute frame positions — filter to those within the clip
    cut_frames = [f for f in dsu_frames if f > clip_start and f < clip_end]
    if not cut_frames:
        print("No [DSU] markers fall within the clip at playhead.")
        return

    # Perform splits at each marker position
    split_ok = 0
    for frame in cut_frames:
        if _split_at_frame(timeline, target, frame):
            split_ok += 1

    # After splitting, shrink each resulting clip segment to 1 frame
    track_items = timeline.GetItemListInTrack("video", target_track) or []
    one_frame_ok = 0
    for it in track_items:
        try:
            start = int(it.GetStart())
            dur = int(it.GetDuration())
        except Exception:
            continue
        if start < clip_start or start > clip_end:
            continue
        if dur <= 1:
            continue
        if it.GetMediaPoolItem() != mpi:
            continue
        if _set_duration_one_frame(it):
            one_frame_ok += 1

    print(
        f"Cut at {len(cut_frames)} markers (split ok: {split_ok}). "
        f"Set {one_frame_ok} clips to 1 frame."
    )


if __name__ == "__main__":
    main()
