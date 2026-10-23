"""
A fake DaVinci Resolve scripting API, faithful to the real one.

The point of this module is to let the Stages/resolve_*.py scripts run without
Resolve open. To be useful it has to be *honest*: it only exposes methods that
the real Resolve 18/19 Python API actually exposes. If a stage calls something
Resolve does not have, the call must fail here exactly like it fails there.

That honesty is controlled by `strict`:

  strict=True  (default) -> only real API methods exist.
  strict=False           -> also adds the hypothetical methods the codebase
                            probes for (TimelineItem.SetStart/SetEnd/
                            SetClipProperty, Timeline.SplitClip/SplitClips,
                            Timeline.GetSelectedItems). Used to exercise the
                            pure logic of a stage on the assumption that the
                            API calls succeed.

Reference for the real surface (Resolve 19 Scripting README):
  Timeline:     GetItemListInTrack, GetMarkers, AddMarker, DeleteMarkerAtFrame,
                GetCurrentVideoItem, GetStartFrame, GetEndFrame, GetTrackCount
  TimelineItem: GetStart, GetEnd, GetDuration, GetLeftOffset, GetRightOffset,
                GetMediaPoolItem, GetMarkers, AddMarker, DeleteMarkerAtFrame,
                GetProperty, SetProperty, GetName
  MediaPoolItem: GetClipProperty, SetClipProperty, GetName

Notably absent from the real API: any way to split a timeline clip, and any way
to set a timeline item's start/end. Those are the methods the fake withholds.
"""

from __future__ import annotations

from typing import Dict, List, Optional


class _MarkerHost:
    """Shared marker behaviour for Timeline and TimelineItem.

    Mirrors Resolve semantics:
      - GetMarkers() returns a fresh dict (safe to mutate while deleting).
      - AddMarker() returns False if a marker already exists at that frame.
      - DeleteMarkerAtFrame() returns False if there is nothing there.
    """

    def __init__(self) -> None:
        self._markers: Dict[int, dict] = {}

    def GetMarkers(self) -> Dict[int, dict]:
        return {k: dict(v) for k, v in sorted(self._markers.items())}

    def AddMarker(self, frameId, color, name, note, duration, customData="") -> bool:
        frameId = int(frameId)
        if frameId in self._markers:
            return False
        self._markers[frameId] = {
            "color": color,
            "name": name,
            "note": note,
            "duration": int(duration) if duration else 1,
            "customData": customData,
        }
        return True

    def DeleteMarkerAtFrame(self, frameNum) -> bool:
        return self._markers.pop(int(frameNum), None) is not None

    def UpdateMarkerCustomData(self, frameNum, customData) -> bool:
        m = self._markers.get(int(frameNum))
        if m is None:
            return False
        m["customData"] = customData
        return True


class FakeMediaPoolItem:
    def __init__(self, name: str = "clip.mov", file_path: str = "/media/clip.mov",
                 properties: Optional[dict] = None) -> None:
        self._name = name
        self._props = {"File Path": file_path, "Clip Name": name}
        if properties:
            self._props.update(properties)
        # Every SetClipProperty call is recorded so tests can prove what a stage
        # actually wrote, and how many times it wrote it.
        self.set_calls: List[tuple] = []

    def GetName(self) -> str:
        return self._name

    def GetClipProperty(self, propertyName=None):
        if propertyName is None:
            return dict(self._props)
        return self._props.get(propertyName, "")

    def SetClipProperty(self, propertyName, propertyValue) -> bool:
        self.set_calls.append((propertyName, propertyValue))
        # Resolve rejects unknown property names and out-of-range values.
        if propertyName not in _MEDIA_POOL_WRITABLE:
            return False
        allowed = _MEDIA_POOL_WRITABLE[propertyName]
        if allowed is not None and str(propertyValue) not in allowed:
            return False
        self._props[propertyName] = propertyValue
        return True


# Values Resolve accepts for the clip properties this project writes.
# "Super Scale" is documented as 1 / 2 / 3 / 4 / Auto -- not "2x".
_MEDIA_POOL_WRITABLE = {
    "Super Scale": {"1", "2", "3", "4", "Auto"},
    # An image sequence's rate can be changed, as in Clip Attributes.
    "FPS": None,
    "Clip Name": None,
    "Comments": None,
    "Keywords": None,
}


class FakeTimelineItem(_MarkerHost):
    """A clip on the timeline.

    Frame model matches Resolve:
      GetStart()      -> first timeline frame occupied (inclusive)
      GetEnd()        -> one past the last timeline frame (exclusive)
      GetDuration()   -> GetEnd() - GetStart()
      GetLeftOffset() -> offset into the source media of the first used frame

    Marker frame ids on a TimelineItem are *source-relative*, so the first
    visible frame of a trimmed clip carries frame id == GetLeftOffset().
    """

    def __init__(self, start: int, duration: int, media_pool_item: FakeMediaPoolItem,
                 left_offset: int = 0, name: str = "clip.mov", strict: bool = True) -> None:
        super().__init__()
        self._start = int(start)
        self._duration = int(duration)
        self._mpi = media_pool_item
        self._left_offset = int(left_offset)
        self._name = name
        self._properties: Dict[str, object] = {}
        self._strict = strict
        self.set_calls: List[tuple] = []

        if not strict:
            # Methods the codebase probes for that Resolve does not provide.
            self.SetStart = self._set_start          # type: ignore[attr-defined]
            self.SetEnd = self._set_end              # type: ignore[attr-defined]
            self.SetClipProperty = self._set_clip_property  # type: ignore[attr-defined]

    # --- real API -----------------------------------------------------------
    def GetName(self) -> str:
        return self._name

    def GetStart(self) -> int:
        return self._start

    def GetEnd(self) -> int:
        return self._start + self._duration

    def GetDuration(self) -> int:
        return self._duration

    def GetLeftOffset(self) -> int:
        return self._left_offset

    def GetRightOffset(self) -> int:
        return self._left_offset + self._duration

    def GetMediaPoolItem(self) -> FakeMediaPoolItem:
        return self._mpi

    def GetProperty(self, propertyName=None):
        if propertyName is None:
            return dict(self._properties)
        return self._properties.get(propertyName, "")

    def SetProperty(self, propertyName, propertyValue) -> bool:
        self._properties[propertyName] = propertyValue
        return True

    # --- non-strict extras --------------------------------------------------
    def _set_start(self, frame) -> bool:
        self._start = int(frame)
        return True

    def _set_end(self, frame) -> bool:
        new_duration = int(frame) - self._start
        if new_duration < 1:
            return False
        self._duration = new_duration
        return True

    def _set_clip_property(self, propertyName, propertyValue) -> bool:
        self.set_calls.append((propertyName, propertyValue))
        self._properties[propertyName] = propertyValue
        return True

    def __repr__(self) -> str:
        return f"<Item {self._name} start={self._start} dur={self._duration}>"


class FakeTimeline(_MarkerHost):
    def __init__(self, name: str = "Timeline 1", strict: bool = True) -> None:
        super().__init__()
        self._name = name
        self._tracks: Dict[int, List[FakeTimelineItem]] = {}
        self._current_video_item: Optional[FakeTimelineItem] = None
        self._selected: List[FakeTimelineItem] = []
        self._strict = strict
        self.split_calls: List[int] = []

        if not strict:
            self.GetSelectedItems = self._get_selected_items  # type: ignore[attr-defined]
            self.SplitClip = self._split_clip                 # type: ignore[attr-defined]

    # --- real API -----------------------------------------------------------
    def GetName(self) -> str:
        return self._name

    def GetTrackCount(self, trackType: str) -> int:
        return len(self._tracks) if trackType == "video" else 0

    def GetItemListInTrack(self, trackType: str, index: int):
        if trackType != "video":
            return []
        return list(self._tracks.get(int(index), []))

    def GetCurrentVideoItem(self) -> Optional[FakeTimelineItem]:
        return self._current_video_item

    def GetStartFrame(self) -> int:
        items = [i for t in self._tracks.values() for i in t]
        return min((i.GetStart() for i in items), default=0)

    def GetEndFrame(self) -> int:
        items = [i for t in self._tracks.values() for i in t]
        return max((i.GetEnd() for i in items), default=0)

    # --- non-strict extras --------------------------------------------------
    def _get_selected_items(self):
        return {i + 1: item for i, item in enumerate(self._selected)} if self._selected else {}

    def _split_clip(self, item, frame) -> bool:
        """Split `item` at absolute timeline `frame`, keeping the track ordered."""
        frame = int(frame)
        self.split_calls.append(frame)
        for index, items in self._tracks.items():
            if item not in items:
                continue
            if not (item.GetStart() < frame < item.GetEnd()):
                return False
            right = FakeTimelineItem(
                start=frame,
                duration=item.GetEnd() - frame,
                media_pool_item=item.GetMediaPoolItem(),
                left_offset=item.GetLeftOffset() + (frame - item.GetStart()),
                name=item.GetName(),
                strict=self._strict,
            )
            item._duration = frame - item.GetStart()
            items.insert(items.index(item) + 1, right)
            return True
        return False

    # --- test helpers -------------------------------------------------------
    def add_item(self, item: FakeTimelineItem, track: int = 1) -> FakeTimelineItem:
        self._tracks.setdefault(int(track), []).append(item)
        self._tracks[int(track)].sort(key=lambda i: i.GetStart())
        if self._current_video_item is None:
            self._current_video_item = item
        return item

    def select(self, *items: FakeTimelineItem) -> None:
        self._selected = list(items)
        if items:
            self._current_video_item = items[0]


class FakeMediaPool:
    """Import and timeline-append, the two operations the plugin relies on.

    `import_mode` controls which call shape Resolve accepts, so tests can pin
    down behaviour on a build that only supports one of them:
      "any"        -- every documented form works
      "file_only"  -- plain file paths only, sequence descriptors rejected
      "none"       -- imports always fail
    """

    def __init__(self, timeline: Optional["FakeTimeline"] = None,
                 import_mode: str = "any") -> None:
        self._timeline = timeline
        self.import_mode = import_mode
        self.imported: List[FakeMediaPoolItem] = []
        self.import_calls: List[object] = []
        self.append_calls: List[object] = []

    def ImportMedia(self, payload):
        self.import_calls.append(payload)
        if self.import_mode == "none":
            return []
        entries = payload if isinstance(payload, (list, tuple)) else [payload]
        if not entries:
            return []
        first = entries[0]
        if isinstance(first, dict):
            if self.import_mode == "file_only":
                return []
            path = str(first.get("FilePath", ""))
        else:
            path = str(first)
        if not path:
            return []
        item = FakeMediaPoolItem(name=path.rsplit("/", 1)[-1], file_path=path)
        self.imported.append(item)
        return [item]

    def AppendToTimeline(self, payload):
        self.append_calls.append(payload)
        items = payload if isinstance(payload, (list, tuple)) else [payload]
        if self._timeline is None:
            return []
        appended = []
        for mpi in items:
            if not isinstance(mpi, FakeMediaPoolItem):
                return []
            start = self._timeline.GetEndFrame()
            item = FakeTimelineItem(start, 24, mpi, name=mpi.GetName(),
                                    strict=self._timeline._strict)
            self._timeline.add_item(item, track=1)
            appended.append(item)
        return appended

    def GetCurrentFolder(self):
        return None


class FakeProject:
    def __init__(self, timeline: Optional[FakeTimeline] = None, name: str = "Project",
                 media_pool: Optional[FakeMediaPool] = None) -> None:
        self._timeline = timeline
        self._name = name
        self._media_pool = media_pool if media_pool is not None else FakeMediaPool(timeline)

    def GetName(self) -> str:
        return self._name

    def GetCurrentTimeline(self) -> Optional[FakeTimeline]:
        return self._timeline

    def GetMediaPool(self) -> FakeMediaPool:
        return self._media_pool

    def GetTimelineCount(self) -> int:
        return 1 if self._timeline else 0


class FakeProjectManager:
    def __init__(self, project: Optional[FakeProject] = None) -> None:
        self._project = project

    def GetCurrentProject(self) -> Optional[FakeProject]:
        return self._project


class FakeResolve:
    def __init__(self, project_manager: Optional[FakeProjectManager] = None,
                 studio: bool = True, version: str = "19.0.3") -> None:
        self._pm = project_manager or FakeProjectManager()
        self._studio = studio
        self._version = version

    def GetProjectManager(self) -> FakeProjectManager:
        return self._pm

    def GetVersionString(self) -> str:
        # Only the number, as on Resolve Studio 20.3.1, which reports
        # "20.3.1.6". The edition is in the product name.
        return self._version

    def GetProductName(self) -> str:
        return "DaVinci Resolve Studio" if self._studio else "DaVinci Resolve"

    def GetCurrentPage(self) -> str:
        return "edit"

    def OpenPage(self, name: str) -> bool:
        return True


class FakeBmdModule:
    """Stands in for the `DaVinciResolveScript` module."""

    def __init__(self, resolve: Optional[FakeResolve]) -> None:
        self._resolve = resolve

    def scriptapp(self, name: str):
        return self._resolve if name == "Resolve" else None


def build_session(clips, *, strict: bool = True, selected_index: Optional[int] = 0,
                  media_path: str = "/media/clip.mov", track: int = 1,
                  studio: bool = True, import_mode: str = "any"):
    """Build a Resolve session in one call.

    `clips` is a list of (start, duration) or (start, duration, left_offset).
    All clips share one media pool item, which is what this project's workflow
    actually produces -- one source clip sliced into many timeline segments.

    Returns (resolve, timeline, items, media_pool_item).
    """
    mpi = FakeMediaPoolItem(file_path=media_path)
    timeline = FakeTimeline(strict=strict)
    items = []
    for spec in clips:
        start, duration = spec[0], spec[1]
        left_offset = spec[2] if len(spec) > 2 else 0
        item = FakeTimelineItem(start, duration, mpi, left_offset=left_offset, strict=strict)
        timeline.add_item(item, track=track)
        items.append(item)
    if selected_index is not None and items:
        timeline.select(items[selected_index])
    pool = FakeMediaPool(timeline, import_mode=import_mode)
    project = FakeProject(timeline, media_pool=pool)
    resolve = FakeResolve(FakeProjectManager(project), studio=studio)
    return resolve, timeline, items, mpi
