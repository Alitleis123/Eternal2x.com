from __future__ import annotations

import argparse
from typing import List, Tuple


def _get_resolve():
    try:
        import os, sys
        if sys.platform == "win32":
            lib = os.environ.get("RESOLVE_SCRIPT_LIB", "")
            if lib:
                os.add_dll_directory(os.path.dirname(lib))
        import DaVinciResolveScript as bmd  # type: ignore
    except Exception as exc:
        raise RuntimeError("Could not import DaVinciResolveScript. Run inside Resolve.") from exc
    resolve = bmd.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError("Could not connect to Resolve.")
    return resolve


def _pick_target(timeline):
    if hasattr(timeline, "GetSelectedItems"):
        items = timeline.GetSelectedItems()
        if items:
            if isinstance(items, dict):
                return next(iter(items.values())), "clip"
            if isinstance(items, list):
                return items[0], "clip"
    if hasattr(timeline, "GetCurrentVideoItem"):
        item = timeline.GetCurrentVideoItem()
        if item:
            return item, "clip"
    return timeline, "timeline"


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

    resolve = _get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise RuntimeError("No active project.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("No active timeline.")

    target, target_type = _pick_target(timeline)

    marker_dict = target.GetMarkers() if hasattr(target, "GetMarkers") else {}
    if not marker_dict:
        print("No markers found on selected clip/timeline.")
        return

    frames = _markers_to_frames(marker_dict)
    if not frames:
        print("Markers present but no usable frames.")
        return

    if target_type != "clip":
        print("No selected clip; cannot map markers to source frames.")
        return

    mpi = target.GetMediaPoolItem()
    if mpi is None:
        print("Selected clip has no media pool item.")
        return

    clip_start = int(target.GetStart())
    clip_duration = int(target.GetDuration())
    clip_end = clip_start + clip_duration - 1

    # Marker frameIds on a clip are relative to clip start.
    cut_frames = []
    for f in frames:
        abs_frame = clip_start + f
        if abs_frame <= clip_start or abs_frame >= clip_end:
            continue
        cut_frames.append(abs_frame)
    cut_frames = sorted(set(cut_frames))

    if not cut_frames:
        print("No valid cut frames inside the selected clip.")
        return

    # Perform splits at each marker position.
    split_ok = 0
    for frame in cut_frames:
        if _split_at_frame(timeline, target, frame):
            split_ok += 1

    # After splitting, shrink each resulting clip segment to 1 frame.
    track_items = timeline.GetItemListInTrack("video", 1) or []
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
