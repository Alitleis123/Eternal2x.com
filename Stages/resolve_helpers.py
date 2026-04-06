"""Shared helpers for Resolve stage scripts."""
from __future__ import annotations

import re


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


def _timecode_to_frames(tc_str, fps):
    """Convert 'HH:MM:SS:FF' or 'HH:MM:SS;FF' timecode to a frame number."""
    m = re.match(r"(\d+)[;:](\d+)[;:](\d+)[;:](\d+)", tc_str)
    if not m:
        return None
    h, mi, s, f = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return int((h * 3600 + mi * 60 + s) * fps + f)


def _get_timeline_fps(timeline):
    """Get the timeline frame rate as a float."""
    fps = 24.0
    try:
        setting = timeline.GetSetting("timelineFrameRate")
        if setting:
            fps = float(setting)
    except Exception:
        pass
    return fps


def get_clip_at_playhead(timeline):
    """Find the topmost enabled clip at the playhead position.

    Returns (item, track_index) or (None, None).
    """
    fps = _get_timeline_fps(timeline)

    playhead = None
    try:
        tc = timeline.GetCurrentTimecode()
        if isinstance(tc, (int, float)):
            playhead = int(tc)
        elif isinstance(tc, str):
            if tc.isdigit():
                playhead = int(tc)
            else:
                playhead = _timecode_to_frames(tc, fps)
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
                s = ti.GetStart()
                e = ti.GetEnd()
                # Convert to frames if timecode strings
                if isinstance(s, str) and not s.isdigit():
                    s = _timecode_to_frames(s, fps)
                else:
                    s = int(s)
                if isinstance(e, str) and not e.isdigit():
                    e = _timecode_to_frames(e, fps)
                else:
                    e = int(e)
            except Exception:
                continue
            if s is None or e is None:
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
