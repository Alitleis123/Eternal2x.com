"""The 2x upscale drawn by Real-ESRGAN, in Pipeline/upscaler.py.

The network only runs on a DirectML GPU. Where it cannot run, the render stays
at the clip's size and the panel leaves the upscale to Super Scale.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from Pipeline import upscaler
from Pipeline.config import Eternal2xConfig
from Pipeline.render import SourceError, analyse_video, render_plan
from Pipeline.rife import AIUnavailable
from tests.drawings import cel


# --------------------------------------------------------------------------
# starting the network, with ONNX Runtime faked
# --------------------------------------------------------------------------

class _FakeSession:
    def __init__(self, providers, fail_on_gpu_start=False, fail_on_gpu_run=False,
                 gpu_quietly_on_cpu=False):
        self.providers = providers
        self.fail_on_gpu_run = fail_on_gpu_run
        if fail_on_gpu_start and "DmlExecutionProvider" in providers:
            raise RuntimeError("D3D12 device could not be created")
        if gpu_quietly_on_cpu:
            self.providers = [p for p in providers if p != "DmlExecutionProvider"]

    def get_providers(self):
        return self.providers

    def get_inputs(self):
        return [type("Input", (), {"name": "input"})()]

    def run(self, _names, feed):
        if self.fail_on_gpu_run and "DmlExecutionProvider" in self.providers:
            raise RuntimeError("E_OUTOFMEMORY")
        return [feed["input"].repeat(2, axis=2).repeat(2, axis=3)]


def _fake_ort(monkeypatch, providers=("DmlExecutionProvider", "CPUExecutionProvider"), **fail):
    made = []

    class Ort:
        ExecutionMode = type("ExecutionMode", (), {"ORT_SEQUENTIAL": 1})

        class SessionOptions:
            pass

        @staticmethod
        def get_available_providers():
            return list(providers)

        @staticmethod
        def InferenceSession(_path, _options, providers):
            session = _FakeSession(providers, **fail)
            made.append(providers)
            return session

    monkeypatch.setattr(upscaler, "ensure_runtime", lambda progress=None: Ort)
    return made


def test_the_gpu_draws_each_frame_at_twice_its_size(monkeypatch):
    made = _fake_ort(monkeypatch)
    frame = cel(120, 120, w=75, h=51)
    out = upscaler.Upscaler().upscale(frame)
    assert out.shape == (102, 150, 3) and made == [["DmlExecutionProvider", "CPUExecutionProvider"]]
    assert np.array_equal(out[::2, ::2], frame)


@pytest.mark.parametrize("setup", [
    dict(providers=("CPUExecutionProvider",)),        # a Mac, or no GPU runtime
    dict(fail_on_gpu_start=True),                     # a GPU without DirectX 12
    dict(gpu_quietly_on_cpu=True),                    # ONNX Runtime swapped in the CPU
])
def test_without_a_working_gpu_it_steps_aside(monkeypatch, setup):
    """A CPU takes seconds a frame. Super Scale does the upscale instead."""
    _fake_ort(monkeypatch, **setup)
    with pytest.raises(AIUnavailable):
        upscaler.Upscaler()


def test_a_missing_model_says_to_reinstall(monkeypatch, tmp_path):
    _fake_ort(monkeypatch)
    with pytest.raises(AIUnavailable, match="Re-run the installer"):
        upscaler.Upscaler(path=tmp_path / "gone.onnx")


def test_a_first_frame_the_gpu_cannot_fit_leaves_it_to_super_scale(monkeypatch):
    _fake_ort(monkeypatch, fail_on_gpu_run=True)
    with pytest.raises(AIUnavailable, match="failed on the GPU"):
        upscaler.Upscaler().upscale(cel(120, 120))


def test_a_gpu_that_runs_out_of_memory_mid_render_finishes_on_the_cpu(monkeypatch):
    """The frames so far are 2x, so the rest must be too."""
    made = _fake_ort(monkeypatch)
    net = upscaler.Upscaler()
    net.upscale(cel(120, 120))
    net.session.fail_on_gpu_run = True
    out = net.upscale(cel(130, 120))
    assert out.shape == (480, 640, 3) and not net.on_gpu and made[-1] == ["CPUExecutionProvider"]


@pytest.mark.parametrize("broken", [
    lambda x: x,                                      # not upscaled
    lambda x: np.full((1, 3, 480, 640), np.nan, np.float32),
])
def test_a_broken_frame_from_the_network_is_never_used(monkeypatch, broken):
    _fake_ort(monkeypatch)
    net = upscaler.Upscaler()
    monkeypatch.setattr(net, "_infer", broken)
    with pytest.raises(AIUnavailable, match="broken frame"):
        net.upscale(cel(120, 120))


# --------------------------------------------------------------------------
# the renderer
# --------------------------------------------------------------------------

class _TwiceAsBig:
    """Stands in for the network: nearest-neighbour 2x, optionally failing."""
    fail_at = None
    made = 0

    def __init__(self, progress=None):
        _TwiceAsBig.made += 1
        self.frames = 0

    def upscale(self, frame):
        if self.frames == _TwiceAsBig.fail_at:
            raise AIUnavailable("E_OUTOFMEMORY")
        self.frames += 1
        return cv2.resize(frame, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)


@pytest.fixture
def twice_as_big(monkeypatch):
    _TwiceAsBig.fail_at, _TwiceAsBig.made = None, 0
    monkeypatch.setattr(upscaler, "Upscaler", _TwiceAsBig)
    return _TwiceAsBig


def _render(make_anime, tmp_path, **cfg):
    src = make_anime(holds=(2,) * 6)
    plan, _, _ = analyse_video(src, Eternal2xConfig())
    return render_plan(src, plan, tmp_path / "out", Eternal2xConfig(**cfg), sequence=True)


def test_the_render_comes_out_at_2x(twice_as_big, make_anime, tmp_path, read_frames):
    result = _render(make_anime, tmp_path, network_upscale=True, upscale_enabled=False)
    frames = read_frames(result.output)
    assert result.upscaled and (result.width, result.height) == (400, 240)
    assert len(frames) == 12 and all(f.shape == (240, 400, 3) for f in frames)


def test_the_network_and_lanczos_never_both_scale(twice_as_big, monkeypatch, make_anime,
                                                tmp_path):
    """Lanczos is on by default, and on top of the network it made 4x. Where
    the network cannot run, the render stays 1x for Super Scale."""
    assert Eternal2xConfig().upscale_enabled
    assert _render(make_anime, tmp_path / "a", network_upscale=True).width == 400

    def no_gpu(progress=None):
        raise AIUnavailable("There is no GPU runtime here.")

    monkeypatch.setattr(upscaler, "Upscaler", no_gpu)
    assert _render(make_anime, tmp_path / "b", network_upscale=True).width == 200


def test_the_network_is_only_loaded_when_asked(twice_as_big, make_anime, tmp_path):
    result = _render(make_anime, tmp_path, upscale_enabled=False)
    assert twice_as_big.made == 0 and not result.upscaled and result.width == 200


def test_without_a_gpu_the_render_stays_at_the_clips_size(monkeypatch, make_anime, tmp_path):
    def no_gpu(progress=None):
        raise AIUnavailable("There is no GPU runtime here.")

    monkeypatch.setattr(upscaler, "Upscaler", no_gpu)
    result = _render(make_anime, tmp_path, network_upscale=True, upscale_enabled=False)
    assert not result.upscaled and result.width == 200
    assert "no GPU" in result.upscale_note


def test_a_first_frame_that_fails_leaves_the_whole_render_at_1x(twice_as_big, make_anime,
                                                                 tmp_path, read_frames):
    twice_as_big.fail_at = 0
    result = _render(make_anime, tmp_path, network_upscale=True, upscale_enabled=False)
    assert not result.upscaled and "E_OUTOFMEMORY" in result.upscale_note
    assert all(f.shape == (120, 200, 3) for f in read_frames(result.output))


def test_a_later_frame_that_fails_stops_the_render(twice_as_big, make_anime, tmp_path):
    """Half the frames at 2x and half at 1x is no render at all."""
    twice_as_big.fail_at = 3
    with pytest.raises(SourceError, match="E_OUTOFMEMORY"):
        _render(make_anime, tmp_path, network_upscale=True, upscale_enabled=False)


# --------------------------------------------------------------------------
# the network that ships, run on the CPU
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def on_cpu():
    ort = pytest.importorskip("onnxruntime")
    net = upscaler.Upscaler.__new__(upscaler.Upscaler)
    net._ort, net._path, net.on_gpu, net.frames = ort, upscaler.MODEL_PATH, False, 0
    net.session = net._session()
    net.input = net.session.get_inputs()[0].name
    return net


def test_the_shipped_network_doubles_any_size(on_cpu):
    assert on_cpu.upscale(cel(40, 30, w=75, h=51)).shape == (102, 150, 3)


def test_the_shipped_network_draws_the_same_picture_bigger(on_cpu):
    """Close to a plain resize overall, with no channels swapped. The network
    moves flat colours by a few levels, 180 to 184 here, so it is not exact."""
    frame = cel(160, 110)
    frame[:40, :60] = (255, 0, 0)  # pure blue in BGR
    out = on_cpu.upscale(frame)
    plain = cv2.resize(frame, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    assert float(np.abs(out.astype(float) - plain.astype(float)).mean()) < 8.0
    corner = out[10:60, 10:100].reshape(-1, 3).mean(axis=0)
    assert corner[0] > 220 and max(corner[1], corner[2]) < 30, f"blue came out as {corner}"
