from __future__ import annotations

import argparse


from Stages.resolve_helpers import get_resolve


def _get_video_items(timeline, track_index: int):
    if not hasattr(timeline, "GetItemListInTrack"):
        return []
    items = timeline.GetItemListInTrack("video", track_index) or []
    return list(items)


def _gap_map(clips):
    # clips: list of (start, dur, item) sorted by start
    gaps = []
    cursor = clips[0][0]
    for start, dur, _ in clips:
        gap = start - cursor
        if gap > 0:
            gaps.append((start, gap))
        cursor = start + dur
    return gaps


def _shift_frame(frame: int, gaps) -> int:
    shift = 0
    for gap_start, gap in gaps:
        if frame >= gap_start:
            shift += gap
        else:
            break
    return frame - shift


def _regroup_timeline_markers(timeline, gaps):
    # DSU markers are now on clips (not timeline), so they move automatically.
    # This only shifts any remaining non-DSU timeline markers if needed.
    if not hasattr(timeline, "GetMarkers"):
        return 0
    markers = timeline.GetMarkers() or {}
    if not markers:
        return 0
    moved = 0
    for frame_id, info in list(markers.items()):
        name = (info or {}).get("name", "")
        if not isinstance(name, str):
            continue
        # Skip DSU markers (they're on clips now)
        if name.startswith("[DSU]"):
            continue
        try:
            frame = int(frame_id)
        except Exception:
            continue
        new_frame = _shift_frame(frame, gaps)
        if new_frame == frame:
            continue
        color = (info or {}).get("color", "Blue")
        note = (info or {}).get("note", "")
        duration = int((info or {}).get("duration", 1) or 1)
        timeline.DeleteMarkerAtFrame(frame)
        timeline.AddMarker(new_frame, color, name, note, duration, "")
        moved += 1
    return moved


def _safe_set_start(item, start: int) -> bool:
    if hasattr(item, "SetStart"):
        return bool(item.SetStart(int(start)))
    if hasattr(item, "SetStartFrame"):
        return bool(item.SetStartFrame(int(start)))
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Regroup clips by removing gaps on the current timeline."
    )
    parser.add_argument("--track", type=int, default=1, help="Video track index (default: 1)")
    args = parser.parse_args()

    resolve = get_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise RuntimeError("No active project.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise RuntimeError("No active timeline.")

    items = _get_video_items(timeline, args.track)
    if not items:
        print("No clips found on video track.")
        return

    clips = []
    for it in items:
        try:
            start = int(it.GetStart())
            dur = int(it.GetDuration())
        except Exception:
            continue
        clips.append((start, dur, it))

    if not clips:
        print("No usable clips found on video track.")
        return

    clips.sort(key=lambda x: x[0])
    gaps = _gap_map(clips)
    cursor = clips[0][0]
    moved = 0
    for start, dur, it in clips:
        if start != cursor:
            ok = _safe_set_start(it, cursor)
            if not ok:
                print("Regroup failed: timeline item does not support SetStart.")
                return
            moved += 1
        cursor += dur

    marker_moved = _regroup_timeline_markers(timeline, gaps)
    print(f"Regrouped {len(clips)} clips (moved {moved}). Markers moved: {marker_moved}.")


if __name__ == "__main__":
    main()
