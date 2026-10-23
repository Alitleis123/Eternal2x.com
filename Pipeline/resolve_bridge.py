"""Everything that talks to DaVinci Resolve, in one place.

The Resolve scripting API varies between versions and several things the docs
imply are possible are not, so nothing here assumes a method exists. Each
operation probes for what it needs, tries the known variants in order, and
returns a result that says exactly what happened. A step that cannot run
reports why instead of quietly doing nothing, which is the failure mode that
makes a plugin look like it is working when it is not.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ResolveError(RuntimeError):
    """Resolve is not reachable, or the project is not in a usable state."""


@dataclass
class Outcome:
    """What an operation did, and how."""

    ok: bool
    detail: str
    method: str = ""

    def __bool__(self) -> bool:
        return self.ok


# --------------------------------------------------------------------------
# connection
# --------------------------------------------------------------------------

def connect() -> Any:
    """Get the Resolve application object, or explain why we cannot."""
    try:
        import DaVinciResolveScript as bmd  # type: ignore
    except Exception as exc:
        raise ResolveError(
            "Could not import DaVinciResolveScript. Run this from "
            "Workspace > Scripts inside Resolve."
        ) from exc
    resolve = bmd.scriptapp("Resolve")
    if resolve is None:
        raise ResolveError("Could not connect to Resolve. Is a project open?")
    return resolve


def current_timeline(resolve: Any) -> Tuple[Any, Any]:
    """Return (project, timeline), raising a clear error if either is missing."""
    manager = resolve.GetProjectManager()
    if manager is None:
        raise ResolveError("Resolve returned no project manager.")
    project = manager.GetCurrentProject()
    if project is None:
        raise ResolveError("No project is open.")
    timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise ResolveError("No timeline is open. Open a timeline and try again.")
    return project, timeline


def is_studio(resolve: Any) -> Optional[bool]:
    """True on Studio, False on the free edition, None if it cannot be told.

    Super Scale and Optical Flow retiming are Studio features, so this decides
    whether the plugin can hand the upscale to Resolve or has to do it itself.
    """
    for attr in ("GetVersionString", "GetProductName"):
        getter = getattr(resolve, attr, None)
        if not callable(getter):
            continue
        try:
            value = str(getter() or "")
        except Exception:
            continue
        if value:
            return "studio" in value.lower()
    return None


# --------------------------------------------------------------------------
# finding the clip to work on
# --------------------------------------------------------------------------

def selected_clip(timeline: Any) -> Optional[Any]:
    """The clip the user means: their selection, else the one under the playhead."""
    getter = getattr(timeline, "GetSelectedItems", None)
    if callable(getter):
        try:
            items = getter()
        except Exception:
            items = None
        if items:
            if isinstance(items, dict):
                return next(iter(items.values()))
            if isinstance(items, (list, tuple)):
                return items[0]
    getter = getattr(timeline, "GetCurrentVideoItem", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


def clip_source_path(item: Any) -> Path:
    """Where the clip's media lives on disk."""
    if item is None:
        raise ResolveError(
            "No clip under the playhead. Move the playhead over a clip and try again."
        )
    mpi = item.GetMediaPoolItem() if hasattr(item, "GetMediaPoolItem") else None
    if mpi is None:
        raise ResolveError(
            "That timeline item has no source media. Generators, titles and "
            "compound clips cannot be processed."
        )
    props = mpi.GetClipProperty() if hasattr(mpi, "GetClipProperty") else {}
    path = ""
    if isinstance(props, dict):
        path = props.get("File Path") or props.get("FilePath") or ""
    if not path:
        raise ResolveError("Could not read the clip's file path from Resolve.")
    return Path(str(path))


# --------------------------------------------------------------------------
# putting the result back
# --------------------------------------------------------------------------

def _media_pool(project: Any) -> Any:
    pool = project.GetMediaPool() if hasattr(project, "GetMediaPool") else None
    if pool is None:
        raise ResolveError("Could not reach the media pool.")
    return pool


def import_media(project: Any, path: Path, *, frame_count: int = 0,
                 fps: float = 0.0) -> Tuple[Optional[Any], Outcome]:
    """Bring a rendered file or image sequence into the media pool.

    An image sequence is imported from the descriptor form only. If Resolve
    refuses it there is no fallback, since handed the first frame it imports
    a single still, which would go on the timeline in place of the render.
    """
    pool = _media_pool(project)
    is_sequence = path.is_dir()

    attempts: List[Tuple[str, Any]] = []
    if is_sequence:
        frames = sorted(path.glob("*.png"))
        if not frames:
            return None, Outcome(False, f"No frames were written to {path}.")
        # A printf pattern, as in Blackmagic's README. Given the first frame's
        # own name, Resolve has no pattern to step through.
        attempts.append(("ImportMedia(sequence descriptor)", [{
            "FilePath": str(path / "frame_%06d.png"),
            "StartIndex": 0,
            "EndIndex": max(0, len(frames) - 1),
        }]))
    else:
        attempts.append(("ImportMedia(file)", [str(path)]))

    errors = []
    for label, payload in attempts:
        importer = getattr(pool, "ImportMedia", None)
        if not callable(importer):
            return None, Outcome(False, "MediaPool.ImportMedia is not available.")
        try:
            items = importer(payload)
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue
        if items:
            item = items[0] if isinstance(items, (list, tuple)) else items
            detail = f"Imported {path.name}."
            if is_sequence and fps > 0:
                detail += " " + set_sequence_fps(item, fps).detail
            return item, Outcome(True, detail, label)
        errors.append(f"{label}: returned nothing")

    return None, Outcome(
        False,
        "Resolve would not import the rendered clip. Tried: " + "; ".join(errors),
    )


def set_sequence_fps(media_item: Any, fps: float) -> Outcome:
    """Give an imported image sequence the rate it was rendered at.

    A sequence has no frame rate of its own, so Resolve gives it the
    project's, and a 60 fps render in a 24 fps project plays at 40% speed.
    Resolve does not always report a refusal, so the value is read back. Call
    this before the clip is appended, since its length there depends on it.
    """
    text = str(round(fps)) if abs(fps - round(fps)) < 0.001 else f"{fps:.3f}"
    failed = Outcome(False, f"Set its frame rate to {text} in Clip Attributes, "
                            f"or it plays at the project's.")
    setter = getattr(media_item, "SetClipProperty", None)
    if not callable(setter):
        return failed
    try:
        if not setter("FPS", text):
            return failed
    except Exception:
        return failed
    getter = getattr(media_item, "GetClipProperty", None)
    if callable(getter):
        try:
            got = getter("FPS")
        except Exception:
            got = text  # cannot check, so trust the set
        if isinstance(got, dict):
            got = got.get("FPS")
        try:
            if abs(float(got) - fps) > 0.01:
                return failed
        except (TypeError, ValueError):
            return failed
    return Outcome(True, f"Frame rate set to {text} fps.", "SetClipProperty")


def append_to_timeline(project: Any, media_item: Any) -> Outcome:
    """Put the imported clip at the end of the current timeline."""
    pool = _media_pool(project)
    appender = getattr(pool, "AppendToTimeline", None)
    if not callable(appender):
        return Outcome(False, "MediaPool.AppendToTimeline is not available.")
    for label, payload in (("list", [media_item]), ("single", media_item)):
        try:
            result = appender(payload)
        except Exception as exc:
            last = f"{label}: {exc}"
            continue
        if result:
            return Outcome(True, "Added the result to the timeline.", label)
        last = f"{label}: returned nothing"
    return Outcome(False, f"Could not append to the timeline ({last}).")


# Resolve accepts 1, 2, 3, 4 or Auto for Super Scale. "2x" is rejected.
SUPER_SCALE_VALUES = {2: "2", 3: "3", 4: "4"}


def set_super_scale(media_item: Any, factor: int) -> Outcome:
    """Ask Resolve to do the upscale, which it does better than we can."""
    value = SUPER_SCALE_VALUES.get(int(factor))
    if value is None:
        return Outcome(False, f"Super Scale does not offer {factor}x.")
    setter = getattr(media_item, "SetClipProperty", None)
    if not callable(setter):
        return Outcome(False, "This Resolve version cannot set Super Scale from a script.")
    try:
        ok = setter("Super Scale", value)
    except Exception as exc:
        return Outcome(False, f"Setting Super Scale failed: {exc}")
    if ok:
        return Outcome(True, f"Super Scale set to {factor}x.", "SetClipProperty")
    return Outcome(False, "Resolve rejected the Super Scale setting.")


# --------------------------------------------------------------------------
# diagnostics
# --------------------------------------------------------------------------

PROBE_TARGETS = {
    "Resolve": ["GetProjectManager", "GetVersionString", "GetProductName",
                "GetCurrentPage", "OpenPage"],
    "Project": ["GetCurrentTimeline", "GetMediaPool", "GetName", "SetSetting",
                "GetSetting"],
    "MediaPool": ["ImportMedia", "AppendToTimeline", "CreateTimelineFromClips",
                  "GetCurrentFolder", "AddSubFolder"],
    "Timeline": ["GetItemListInTrack", "GetMarkers", "AddMarker",
                 "DeleteMarkerAtFrame", "GetCurrentVideoItem", "GetSelectedItems",
                 "GetStartFrame", "GetEndFrame", "GetSetting", "SetSetting"],
    "TimelineItem": ["GetStart", "GetEnd", "GetDuration", "GetLeftOffset",
                     "GetMediaPoolItem", "GetProperty", "SetProperty",
                     "GetMarkers", "AddMarker", "SetStart", "SetEnd",
                     "SetClipProperty"],
    "MediaPoolItem": ["GetClipProperty", "SetClipProperty", "GetName"],
}


def probe(resolve: Any) -> Dict[str, Dict[str, bool]]:
    """Report which API methods this Resolve build actually exposes.

    Used by Stages/resolve_probe.py so an unexpected Resolve version can be
    diagnosed from its output rather than by guesswork.
    """
    found: Dict[str, Dict[str, bool]] = {}

    objects: Dict[str, Any] = {"Resolve": resolve}
    try:
        project, timeline = current_timeline(resolve)
        objects["Project"] = project
        objects["Timeline"] = timeline
        pool = project.GetMediaPool() if hasattr(project, "GetMediaPool") else None
        if pool is not None:
            objects["MediaPool"] = pool
        item = selected_clip(timeline)
        if item is not None:
            objects["TimelineItem"] = item
            mpi = item.GetMediaPoolItem() if hasattr(item, "GetMediaPoolItem") else None
            if mpi is not None:
                objects["MediaPoolItem"] = mpi
    except ResolveError:
        pass

    for name, methods in PROBE_TARGETS.items():
        obj = objects.get(name)
        found[name] = {
            m: bool(obj is not None and callable(getattr(obj, m, None)))
            for m in methods
        }
    return found
