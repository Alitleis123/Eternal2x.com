"""Shared helpers for Resolve stage scripts."""
from __future__ import annotations


def get_resolve():
    """Connect to DaVinci Resolve."""
    import os, sys
    if sys.platform == "win32":
        lib = os.environ.get("RESOLVE_SCRIPT_LIB", "").strip('"')
        if lib:
            os.add_dll_directory(os.path.dirname(lib))
    try:
        import DaVinciResolveScript as bmd  # type: ignore
    except Exception as exc:
        raise RuntimeError("Could not import DaVinciResolveScript. Run inside Resolve.") from exc
    resolve = bmd.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError("Could not connect to Resolve.")
    return resolve


def get_clip_at_playhead(timeline):
    """Find the topmost enabled clip at the playhead position.

    Returns (item, track_index) or (None, None).
    """
    playhead = None
    try:
        tc = timeline.GetCurrentTimecode()
        if isinstance(tc, (int, float)):
            playhead = int(tc)
        elif isinstance(tc, str):
            playhead = int(tc) if tc.isdigit() else None
    except Exception:
        pass

    if playhead is None:
        return None, None

    track_count = timeline.GetTrackCount("video") or 0
    for t in range(track_count, 0, -1):
        # Skip disabled tracks
        try:
            if hasattr(timeline, "GetIsTrackEnabled"):
                if not timeline.GetIsTrackEnabled("video", t):
                    continue
        except Exception:
            pass

        track_items = timeline.GetItemListInTrack("video", t) or []
        for ti in track_items:
            try:
                s = int(ti.GetStart())
                e = int(ti.GetEnd())
            except Exception:
                continue
            if playhead >= s and playhead < e:
                # Check if clip is enabled
                try:
                    if hasattr(ti, "GetClipEnabled") and not ti.GetClipEnabled():
                        continue
                except Exception:
                    pass
                return ti, t

    return None, None
