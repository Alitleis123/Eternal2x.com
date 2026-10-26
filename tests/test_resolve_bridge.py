"""The Resolve integration layer, including how it behaves when the API is thin."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from Pipeline import resolve_bridge as bridge
from tests.fake_resolve import (
    FakeBmdModule,
    FakeMediaPoolItem,
    FakeProject,
    FakeProjectManager,
    FakeResolve,
    FakeTimeline,
    FakeTimelineItem,
)


# --------------------------------------------------------------------------
# connection
# --------------------------------------------------------------------------

def test_connect_explains_running_outside_resolve(no_resolve):
    with pytest.raises(bridge.ResolveError, match="Workspace > Scripts"):
        bridge.connect()


def test_connect_reports_a_refused_scriptapp(monkeypatch):
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript", FakeBmdModule(None))
    with pytest.raises(bridge.ResolveError, match="Could not connect"):
        bridge.connect()


def test_connect_succeeds(install_fake_resolve):
    install_fake_resolve([(0, 10)])
    assert bridge.connect() is not None


def test_no_project_is_reported_clearly(monkeypatch):
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript",
                        FakeBmdModule(FakeResolve(FakeProjectManager(None))))
    with pytest.raises(bridge.ResolveError, match="No project is open"):
        bridge.current_timeline(bridge.connect())


def test_no_timeline_tells_the_user_what_to_do(monkeypatch):
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript",
                        FakeBmdModule(FakeResolve(FakeProjectManager(FakeProject(None)))))
    with pytest.raises(bridge.ResolveError, match="Open a timeline"):
        bridge.current_timeline(bridge.connect())


# --------------------------------------------------------------------------
# edition
# --------------------------------------------------------------------------

def test_studio_is_detected():
    assert bridge.is_studio(FakeResolve(studio=True)) is True


def test_free_edition_is_detected():
    assert bridge.is_studio(FakeResolve(studio=False)) is False


@pytest.mark.parametrize("product,studio", [("DaVinci Resolve Studio", True),
                                            ("DaVinci Resolve", False)])
def test_the_edition_comes_from_the_product_name(product, studio):
    """Resolve's version string is only a number, so it cannot tell."""
    class Resolve20:
        def GetVersionString(self):
            return "20.3.1.6"

        def GetProductName(self):
            return product
    assert bridge.is_studio(Resolve20()) is studio


def test_unknown_edition_returns_none():
    class Bare:
        pass
    assert bridge.is_studio(Bare()) is None


def test_edition_probe_survives_a_throwing_api():
    class Angry:
        def GetVersionString(self):
            raise RuntimeError("nope")

        def GetProductName(self):
            raise RuntimeError("nope")
    assert bridge.is_studio(Angry()) is None


# --------------------------------------------------------------------------
# finding the clip
# --------------------------------------------------------------------------

def test_selection_is_preferred(install_fake_resolve):
    _r, timeline, items, _ = install_fake_resolve([(0, 10), (10, 10)],
                                                  strict=False, selected_index=1)
    assert bridge.selected_clip(timeline) is items[1]


def test_falls_back_to_the_playhead_clip(install_fake_resolve):
    """Resolve has no GetSelectedItems, so this is the path that runs live."""
    _r, timeline, items, _ = install_fake_resolve([(0, 10)], strict=True)
    assert bridge.selected_clip(timeline) is items[0]


def test_no_clip_at_all_returns_none():
    assert bridge.selected_clip(FakeTimeline(strict=True)) is None


def _b_over_a(start=86400, rate="24", playhead="01:00:00:10"):
    """B.mov on V2 over A.mov on V1, B under the playhead as Resolve reports it."""
    timeline = FakeTimeline(strict=True)
    a = timeline.add_item(FakeTimelineItem(start, 48, FakeMediaPoolItem("A.mov", "/media/A.mov")), 1)
    b = timeline.add_item(FakeTimelineItem(start, 48, FakeMediaPoolItem("B.mov", "/media/B.mov")), 2)
    timeline._current_video_item = b
    timeline.frame_rate, timeline.playhead_tc = rate, playhead
    return timeline, a, b


def test_a_clip_switched_off_is_passed_over():
    """GetCurrentVideoItem returns the top clip even switched off, as measured
    in Resolve 20.3.1."""
    timeline, a, b = _b_over_a()
    b.enabled = False
    assert bridge.selected_clip(timeline) is a


def test_a_clip_on_a_track_switched_off_is_passed_over():
    timeline, a, b = _b_over_a()
    timeline.tracks_off.add(2)
    assert bridge.selected_clip(timeline) is a


def test_the_top_clip_is_used_while_it_shows():
    timeline, a, b = _b_over_a()
    assert bridge.selected_clip(timeline) is b


@pytest.mark.parametrize("rate, timecode, frame", [
    ("24", "01:00:00:10", 86410),
    ("23.976", "01:00:00:10", 86410),
    ("29.97", "01:00:00;00", 107892),
    ("29.97", "00:10:00;00", 17982),
    ("59.94", "00:01:00;04", 3600),
    ("25", "bad", None),
])
def test_the_playhead_frame_follows_the_timecode(rate, timecode, frame):
    timeline = FakeTimeline(strict=True)
    timeline.frame_rate, timeline.playhead_tc = rate, timecode
    assert bridge._playhead_frame(timeline) == frame


def test_with_every_clip_switched_off_the_top_one_is_kept():
    timeline, a, b = _b_over_a()
    a.enabled = b.enabled = False
    assert bridge.selected_clip(timeline) is b


def test_source_path_is_read_from_the_media_pool_item(install_fake_resolve):
    _r, timeline, _items, _ = install_fake_resolve([(0, 10)],
                                                   media_path="/media/scene 01.mov")
    path = bridge.clip_source_path(bridge.selected_clip(timeline))
    assert path == Path("/media/scene 01.mov")


def test_no_selection_says_so_plainly():
    with pytest.raises(bridge.ResolveError, match="No clip under the playhead"):
        bridge.clip_source_path(None)


def test_a_generator_has_no_source_media():
    class Generator:
        def GetMediaPoolItem(self):
            return None
    with pytest.raises(bridge.ResolveError, match="no source media"):
        bridge.clip_source_path(Generator())


def test_a_missing_file_path_is_reported():
    mpi = FakeMediaPoolItem(file_path="")
    item = FakeTimelineItem(0, 10, mpi)
    with pytest.raises(bridge.ResolveError, match="file path"):
        bridge.clip_source_path(item)


def trimmed_item(fps="24.000", **methods):
    item = FakeTimelineItem(0, 6, FakeMediaPoolItem(properties={"FPS": fps}), left_offset=6)
    for name, value in methods.items():
        setattr(item, name, lambda value=value: value)
    return item


def test_a_cut_is_read_in_seconds_from_its_source_frames():
    """The end frame is the last one the cut uses, so the range runs past it."""
    item = trimmed_item(GetSourceStartFrame=6, GetSourceEndFrame=11)
    assert bridge.clip_source_range(item) == (0.25, 0.5)


def test_without_an_end_frame_the_source_times_give_the_length():
    """Source times count from the media's start timecode, an hour in here."""
    item = trimmed_item(GetSourceStartFrame=6, GetSourceStartTime=3600.25,
                        GetSourceEndTime=3600.5)
    assert bridge.clip_source_range(item) == (0.25, 0.5)


def test_a_cut_with_no_known_range_is_the_whole_file():
    assert bridge.clip_source_range(trimmed_item()) is None
    assert bridge.clip_source_range(
        trimmed_item(fps="", GetSourceStartFrame=6, GetSourceEndFrame=11)) is None


# --------------------------------------------------------------------------
# importing
# --------------------------------------------------------------------------

def test_import_a_rendered_file(install_fake_resolve, tmp_path):
    resolve, _tl, _items, _ = install_fake_resolve([(0, 10)])
    project, _ = bridge.current_timeline(resolve)
    rendered = tmp_path / "out.avi"
    rendered.write_bytes(b"video")

    item, outcome = bridge.import_media(project, rendered)
    assert outcome, outcome.detail
    assert item is not None
    assert "out.avi" in outcome.detail


def test_import_a_png_sequence(install_fake_resolve, tmp_path):
    resolve, _tl, _items, _ = install_fake_resolve([(0, 10)])
    project, _ = bridge.current_timeline(resolve)
    folder = tmp_path / "seq"
    folder.mkdir()
    for i in range(4):
        (folder / f"frame_{i:06d}.png").write_bytes(b"png")

    item, outcome = bridge.import_media(project, folder)
    assert outcome, outcome.detail
    assert item is not None

    # Resolve steps through a printf pattern, not the first frame's own name.
    descriptor = project.GetMediaPool().import_calls[0][0]
    assert descriptor["FilePath"] == str(folder / "frame_%06d.png")
    assert (descriptor["StartIndex"], descriptor["EndIndex"]) == (0, 3)


def test_a_refused_sequence_is_not_imported_as_a_still(install_fake_resolve, tmp_path):
    """Handed its first frame instead, Resolve imports one still, which would
    go on the timeline in place of the render. Better to say it was refused."""
    resolve, _tl, _items, _ = install_fake_resolve([(0, 10)], import_mode="file_only")
    project, _ = bridge.current_timeline(resolve)
    folder = tmp_path / "seq"
    folder.mkdir()
    for i in range(4):
        (folder / f"frame_{i:06d}.png").write_bytes(b"png")

    item, outcome = bridge.import_media(project, folder, fps=24.0)
    assert not outcome
    assert item is None
    assert "Tried:" in outcome.detail
    pool = project.GetMediaPool()
    assert pool.imported == []
    assert len(pool.import_calls) == 1, "something other than the sequence was tried"


@pytest.mark.parametrize("fps,text", [(60.0, "60"), (23.976, "23.976")])
def test_a_sequence_gets_the_rate_it_was_rendered_at(install_fake_resolve, tmp_path,
                                                     fps, text):
    """A sequence has no rate of its own, so Resolve would play a 60 fps render
    at a 24 fps project's rate, 40% speed."""
    resolve, _tl, _items, _ = install_fake_resolve([(0, 10)])
    project, _ = bridge.current_timeline(resolve)
    folder = tmp_path / "seq"
    folder.mkdir()
    (folder / "frame_000000.png").write_bytes(b"png")

    item, outcome = bridge.import_media(project, folder, frame_count=1, fps=fps)
    assert outcome, outcome.detail
    assert item.GetClipProperty("FPS") == text
    assert f"{text} fps" in outcome.detail


def test_a_frame_rate_resolve_ignores_is_reported():
    """Resolve does not always report a refusal, so the rate is read back."""
    class Ignores:
        def SetClipProperty(self, *_args):
            return True

        def GetClipProperty(self, _name=None):
            return {"FPS": "24"}
    outcome = bridge.set_sequence_fps(Ignores(), 60.0)
    assert not outcome
    assert "Clip Attributes" in outcome.detail


def test_a_frame_rate_refusal_is_reported():
    class Refuses:
        def SetClipProperty(self, *_args):
            return False
    outcome = bridge.set_sequence_fps(Refuses(), 60.0)
    assert not outcome
    assert "60" in outcome.detail


def test_an_empty_sequence_folder_is_reported(install_fake_resolve, tmp_path):
    resolve, _tl, _items, _ = install_fake_resolve([(0, 10)])
    project, _ = bridge.current_timeline(resolve)
    folder = tmp_path / "empty"
    folder.mkdir()

    item, outcome = bridge.import_media(project, folder)
    assert not outcome
    assert item is None
    assert "No frames" in outcome.detail


def test_a_refused_import_explains_what_was_tried(install_fake_resolve, tmp_path):
    resolve, _tl, _items, _ = install_fake_resolve([(0, 10)], import_mode="none")
    project, _ = bridge.current_timeline(resolve)
    rendered = tmp_path / "out.avi"
    rendered.write_bytes(b"video")

    item, outcome = bridge.import_media(project, rendered)
    assert not outcome
    assert item is None
    assert "Tried:" in outcome.detail


# --------------------------------------------------------------------------
# appending
# --------------------------------------------------------------------------

def test_append_puts_the_clip_on_the_timeline(install_fake_resolve, tmp_path):
    resolve, timeline, _items, _ = install_fake_resolve([(0, 10)])
    project, _ = bridge.current_timeline(resolve)
    before = len(timeline.GetItemListInTrack("video", 1))

    rendered = tmp_path / "out.avi"
    rendered.write_bytes(b"video")
    media_item, _ = bridge.import_media(project, rendered)
    outcome = bridge.append_to_timeline(project, media_item)

    assert outcome, outcome.detail
    assert len(timeline.GetItemListInTrack("video", 1)) == before + 1


def test_append_reports_a_missing_api(install_fake_resolve):
    resolve, timeline, _items, _ = install_fake_resolve([(0, 10)])
    project, _ = bridge.current_timeline(resolve)

    class PoolWithoutAppend:
        pass
    project._media_pool = PoolWithoutAppend()

    outcome = bridge.append_to_timeline(project, object())
    assert not outcome
    assert "AppendToTimeline is not available" in outcome.detail


# --------------------------------------------------------------------------
# super scale
# --------------------------------------------------------------------------

def test_super_scale_uses_a_value_resolve_accepts():
    """Resolve takes 1/2/3/4 or Auto. "2x" is rejected."""
    mpi = FakeMediaPoolItem()
    outcome = bridge.set_super_scale(mpi, 2)
    assert outcome, outcome.detail
    assert mpi.GetClipProperty("Super Scale") == "2"


def test_super_scale_rejects_an_unsupported_factor():
    outcome = bridge.set_super_scale(FakeMediaPoolItem(), 5)
    assert not outcome
    assert "5x" in outcome.detail


def test_super_scale_reports_a_missing_api():
    class Bare:
        pass
    outcome = bridge.set_super_scale(Bare(), 2)
    assert not outcome
    assert "cannot set Super Scale" in outcome.detail


def test_super_scale_reports_a_refusal():
    class Refuses:
        def SetClipProperty(self, *_args):
            return False
    outcome = bridge.set_super_scale(Refuses(), 2)
    assert not outcome
    assert "rejected" in outcome.detail


def test_super_scale_survives_a_throwing_api():
    class Throws:
        def SetClipProperty(self, *_args):
            raise RuntimeError("boom")
    outcome = bridge.set_super_scale(Throws(), 2)
    assert not outcome
    assert "boom" in outcome.detail


# --------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------

def test_probe_reports_present_and_absent_methods(install_fake_resolve):
    resolve, _tl, _items, _ = install_fake_resolve([(0, 10)], strict=True)
    report = bridge.probe(resolve)

    assert report["MediaPool"]["ImportMedia"] is True
    assert report["TimelineItem"]["GetStart"] is True
    # Not part of the real API, and the fake is honest about that.
    assert report["TimelineItem"]["SetStart"] is False
    assert report["Timeline"]["GetSelectedItems"] is False


def test_probe_covers_every_object_even_without_a_project(monkeypatch):
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript",
                        FakeBmdModule(FakeResolve(FakeProjectManager(None))))
    report = bridge.probe(bridge.connect())
    assert set(report) == set(bridge.PROBE_TARGETS)
    assert report["Resolve"]["GetProjectManager"] is True


def test_outcome_is_truthy_when_ok():
    assert bool(bridge.Outcome(True, "fine")) is True
    assert bool(bridge.Outcome(False, "nope")) is False
