"""Shared fixtures: repo on sys.path, a fake Resolve module, synthetic videos."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.fake_resolve import FakeBmdModule, build_session  # noqa: E402


@pytest.fixture(autouse=True)
def _stand_in_for_the_network(request, monkeypatch):
    """Renders draw in-betweens with a blend instead of the RIFE network, whose
    model would otherwise be downloaded. tests/test_rife.py tests the real one."""
    if request.module.__name__.endswith("test_rife"):
        return
    from Pipeline import rife
    from tests.fake_rife import StandInRife

    monkeypatch.setattr(rife, "Rife", StandInRife)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def install_fake_resolve(monkeypatch):
    """Install a fake `DaVinciResolveScript` module and return a session builder.

    Usage:
        resolve, timeline, items, mpi = install_fake_resolve([(0, 100)])
    """

    def _install(clips, *, strict: bool = True, selected_index: int = 0, **kwargs):
        resolve, timeline, items, mpi = build_session(
            clips, strict=strict, selected_index=selected_index, **kwargs
        )
        monkeypatch.setitem(sys.modules, "DaVinciResolveScript", FakeBmdModule(resolve))
        return resolve, timeline, items, mpi

    return _install


@pytest.fixture
def no_resolve(monkeypatch):
    """Simulate running outside Resolve: the import fails."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "DaVinciResolveScript":
            raise ImportError("No module named 'DaVinciResolveScript'")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "DaVinciResolveScript", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)


# --------------------------------------------------------------------------
# Synthetic video generation
# --------------------------------------------------------------------------

def _write_video(path: Path, frames, fps: float = 30.0) -> Path:
    import cv2

    h, w = frames[0].shape[:2]
    # FFV1 in .avi is lossless, so motion scores are not polluted by codec noise.
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"FFV1"), fps, (w, h))
    if not writer.isOpened():
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (w, h))
    assert writer.isOpened(), "OpenCV could not open a video writer"
    for f in frames:
        writer.write(f)
    writer.release()
    return path


@pytest.fixture
def make_video(tmp_path):
    """Build a video whose motion is known exactly, frame by frame.

    `motion_frames` is the set of frame indices that differ from their
    predecessor; every other frame is a byte-for-byte repeat.
    """

    def _make(name: str = "clip.avi", n_frames: int = 60, motion_frames=(),
              fps: float = 30.0, size=(160, 120), amplitude: int = 200) -> Path:
        w, h = size
        motion = set(motion_frames)
        frames = []
        base = np.zeros((h, w, 3), dtype=np.uint8)
        base[:, :] = (30, 30, 30)
        current = base.copy()
        for i in range(n_frames):
            if i in motion:
                current = current.copy()
                # A bright block in a corner: localized, so the tile-based
                # "detail" scorer sees it strongly.
                y0 = (i * 7) % max(1, h - 40)
                x0 = (i * 11) % max(1, w - 40)
                current[y0:y0 + 40, x0:x0 + 40] = (amplitude, amplitude, amplitude)
            frames.append(current.copy())
        return _write_video(tmp_path / name, frames, fps=fps)

    return _make


@pytest.fixture
def make_anime(tmp_path):
    """Build a clip that behaves like hand-drawn animation.

    `holds` gives the number of source frames each successive drawing is held
    for, so `[2] * 12` is twelve drawings on 2s. Each drawing is the same
    texture translated a little further. The cut detector tracks that
    texture to tell motion from a change of shot; a flat shape on a flat
    background gives it nothing to lock onto.
    """
    import cv2

    def _make(name="anime.avi", holds=(2,) * 12, fps=24.0, size=(200, 120),
              step=6, texture_seed=1, cut_at=None):
        w, h = size
        rng = np.random.default_rng(texture_seed)
        tex = cv2.GaussianBlur(rng.integers(0, 255, (h, w), dtype=np.uint8), (0, 0), 2.0)
        # A genuinely different shot, not just different noise, so the cut
        # detector sees it the way it would see a real scene change.
        alt = cv2.GaussianBlur(rng.integers(200, 255, (h, w), dtype=np.uint8), (0, 0), 2.0)

        frames = []
        for drawing_index, hold in enumerate(holds):
            base = alt if (cut_at is not None and drawing_index >= cut_at) else tex
            offset = drawing_index * step
            if cut_at is not None and drawing_index >= cut_at:
                offset = (drawing_index - cut_at) * step
            M = np.float32([[1, 0, offset], [0, 1, 0]])
            drawing = cv2.warpAffine(np.dstack([base] * 3), M, (w, h),
                                     borderMode=cv2.BORDER_REPLICATE)
            frames.extend([drawing] * hold)

        return _write_video(tmp_path / name, frames, fps=fps)

    return _make


@pytest.fixture
def read_frames():
    """Decode a rendered clip (or PNG sequence) back into a list of arrays."""
    import cv2

    def _read(path):
        path = Path(path)
        if path.is_dir():
            return [cv2.imread(str(p)) for p in sorted(path.glob("*.png"))]
        cap = cv2.VideoCapture(str(path))
        out = []
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            out.append(frame)
        cap.release()
        return out

    return _read
