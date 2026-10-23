"""The network that draws the in-betweens: its runtime, its model, what it draws."""

from __future__ import annotations

import hashlib
import subprocess

import numpy as np
import pytest

from Pipeline import rife
from Pipeline.config import Eternal2xConfig
from Pipeline.interpolate import frames_are_a_cut, frames_are_unrelated
from Pipeline.render import SourceError, analyse_video, render_plan
from tests.drawings import cel


def city(w=320, h=240):
    """Another shot in the style of cel(): light sky, buildings, no character."""
    import cv2
    img = np.full((h, w, 3), (225, 215, 235), np.uint8)
    cv2.rectangle(img, (0, 200), (w, h), (150, 170, 190), -1)
    for x in range(20, w, 60):
        cv2.rectangle(img, (x, 70), (x + 30, 200), (90, 100, 120), -1)
        cv2.rectangle(img, (x, 70), (x + 30, 200), (20, 20, 20), 2)
    return img


# --------------------------------------------------------------------------
# the model download
# --------------------------------------------------------------------------

def _served(tmp_path, data: bytes):
    src = tmp_path / "served.onnx"
    src.write_bytes(data)
    return src.as_uri(), hashlib.sha256(data).hexdigest()


def test_the_model_is_downloaded_and_checked(tmp_path):
    url, sha = _served(tmp_path, b"model bytes")
    target = tmp_path / "models" / "rife.onnx"
    seen = []
    got = rife.ensure_model(lambda stage, f: seen.append((stage, f)), path=target,
                            url=url, sha256=sha)
    assert got == target and target.read_bytes() == b"model bytes"
    assert seen and seen[-1] == ("Downloading AI model", 1.0)


def test_a_model_that_fails_its_checksum_is_not_kept(tmp_path):
    url, _sha = _served(tmp_path, b"tampered")
    target = tmp_path / "models" / "rife.onnx"
    with pytest.raises(rife.AIUnavailable, match="checksum"):
        rife.ensure_model(path=target, url=url, sha256="0" * 64)
    assert not target.exists()
    assert not list(target.parent.glob("*.part")), "the partial download was left behind"


def test_a_model_already_in_place_is_not_downloaded_again(tmp_path):
    target = tmp_path / "rife.onnx"
    target.write_bytes(b"model bytes")
    sha = hashlib.sha256(b"model bytes").hexdigest()
    assert rife.ensure_model(path=target, url="http://invalid.invalid/x", sha256=sha) == target


def test_a_failed_download_says_what_to_do(tmp_path):
    target = tmp_path / "rife.onnx"
    missing = (tmp_path / "nowhere.onnx").as_uri()
    with pytest.raises(rife.AIUnavailable, match="Check the connection"):
        rife.ensure_model(path=target, url=missing, sha256="0" * 64)
    assert not target.exists()


def test_two_downloads_at_once_do_not_trip_over_each_other(tmp_path, monkeypatch):
    """Two first runs of the AI setting both download. They shared one
    temporary file, so one could rename it away while the other still wrote."""
    data = b"model" * 600_000
    sha = hashlib.sha256(data).hexdigest()
    target = tmp_path / "models" / "rife.onnx"
    state = {"nested": False}

    class Response:
        headers = {"Content-Length": str(len(data))}

        def __init__(self, first):
            self.first, self.pos = first, 0

        def read(self, n):
            if self.first and self.pos and not state["nested"]:
                state["nested"] = True
                # The other run downloads all of it while this one is halfway.
                rife.ensure_model(path=target, url="second", sha256=sha)
            chunk = data[self.pos:self.pos + n]
            self.pos += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(rife.urllib.request, "urlopen",
                        lambda url, timeout=60: Response(first=(url == "first")))
    assert rife.ensure_model(path=target, url="first", sha256=sha) == target
    assert state["nested"] and target.read_bytes() == data
    assert not list(target.parent.glob("*.part")), "a temporary file was left behind"


def test_an_unavailable_ai_setting_fails_like_any_render_problem():
    """resolve_smooth reports SourceError to the panel as a clean failure."""
    assert issubclass(rife.AIUnavailable, SourceError)


# --------------------------------------------------------------------------
# the runtime install
# --------------------------------------------------------------------------

def test_a_runtime_that_will_not_install_says_why(monkeypatch):
    def no_module(name):
        raise ImportError(name)

    monkeypatch.setattr(rife.importlib, "import_module", no_module)
    monkeypatch.setattr(rife.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, "", "ERROR: No matching distribution found for onnxruntime\n"))
    with pytest.raises(rife.AIUnavailable) as caught:
        rife.ensure_runtime()
    message = str(caught.value)
    assert "No matching distribution" in message
    assert "pip install onnxruntime" in message


def _install(monkeypatch, platform, refused=()):
    """Run ensure_runtime with pip faked. Returns the packages pip was asked for."""
    asked, loaded = [], {"done": False}

    def module(name):
        if not loaded["done"]:
            raise ImportError(name)
        return "onnxruntime module"

    def run(cmd, **kwargs):
        asked.append(cmd[-1])
        if cmd[-1] in refused:
            return subprocess.CompletedProcess(cmd, 1, "", f"ERROR: no {cmd[-1]} here\n")
        loaded["done"] = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rife.sys, "platform", platform)
    monkeypatch.setattr(rife.importlib, "import_module", module)
    monkeypatch.setattr(rife.subprocess, "run", run)
    assert rife.ensure_runtime() == "onnxruntime module"
    return asked


def test_windows_gets_the_gpu_runtime(monkeypatch):
    """DirectML runs the model on any DirectX 12 GPU, about 11 times faster
    than the CPU on a Radeon."""
    assert _install(monkeypatch, "win32") == ["onnxruntime-directml"]


def test_windows_falls_back_to_the_cpu_runtime(monkeypatch):
    asked = _install(monkeypatch, "win32", refused=("onnxruntime-directml",))
    assert asked == ["onnxruntime-directml", "onnxruntime"]


def test_a_gpu_runtime_that_will_not_load_makes_way_for_the_cpu_one(monkeypatch):
    """pip can install the DirectML build on a machine where its DLLs do not
    load. It is removed before the plain build goes in."""
    calls, installed = [], set()

    def module(name):
        if "onnxruntime" not in installed:
            raise ImportError("DLL load failed while importing onnxruntime_pybind11_state")
        return "onnxruntime module"

    def run(cmd, **kwargs):
        verb, package = cmd[3], cmd[-1]
        calls.append((verb, package))
        if verb == "install":
            installed.add(package)
        else:
            installed.discard(package)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rife.sys, "platform", "win32")
    monkeypatch.setattr(rife.importlib, "import_module", module)
    monkeypatch.setattr(rife.subprocess, "run", run)
    assert rife.ensure_runtime() == "onnxruntime module"
    assert calls == [("install", "onnxruntime-directml"), ("uninstall", "onnxruntime-directml"),
                     ("install", "onnxruntime")]


def test_runtimes_that_all_fail_to_load_say_why(monkeypatch):
    def module(name):
        raise ImportError("DLL load failed")

    monkeypatch.setattr(rife.sys, "platform", "win32")
    monkeypatch.setattr(rife.importlib, "import_module", module)
    monkeypatch.setattr(rife.subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", ""))
    with pytest.raises(rife.AIUnavailable, match="will not load: DLL load failed"):
        rife.ensure_runtime()


def test_macos_gets_the_plain_runtime(monkeypatch):
    assert _install(monkeypatch, "darwin") == ["onnxruntime"]


class _FakeSession:
    def __init__(self, providers, fail_on_gpu_start=False, fail_on_gpu_run=False):
        self.providers = providers
        self.fail_on_gpu_run = fail_on_gpu_run
        if fail_on_gpu_start and "DmlExecutionProvider" in providers:
            raise RuntimeError("D3D12 device could not be created")

    def get_inputs(self):
        return [type("Input", (), {"name": "input"})()]

    def run(self, _names, feed):
        if self.fail_on_gpu_run and "DmlExecutionProvider" in self.providers:
            raise RuntimeError("E_OUTOFMEMORY")
        x = feed["input"]
        return [x[:, :3]]


def _fake_ort(monkeypatch, **fail):
    made = []

    class Ort:
        ExecutionMode = type("ExecutionMode", (), {"ORT_SEQUENTIAL": 1})

        class SessionOptions:
            pass

        @staticmethod
        def get_available_providers():
            return ["DmlExecutionProvider", "CPUExecutionProvider"]

        @staticmethod
        def InferenceSession(_path, _options, providers):
            session = _FakeSession(providers, **fail)
            made.append(providers)
            return session

    monkeypatch.setattr(rife, "ensure_runtime", lambda progress=None: Ort)
    monkeypatch.setattr(rife, "ensure_model", lambda progress=None: "model.onnx")
    return made


def test_the_gpu_is_used_when_it_starts(monkeypatch):
    made = _fake_ort(monkeypatch)
    net = rife.Rife()
    assert net.on_gpu and made == [["DmlExecutionProvider", "CPUExecutionProvider"]]


def test_a_gpu_that_will_not_start_falls_back_to_the_cpu(monkeypatch):
    made = _fake_ort(monkeypatch, fail_on_gpu_start=True)
    net = rife.Rife()
    assert not net.on_gpu and made == [["CPUExecutionProvider"]]


def test_a_gpu_that_runs_out_of_memory_finishes_on_the_cpu(monkeypatch):
    """Resolve holds much of the GPU's memory while a render runs."""
    _fake_ort(monkeypatch, fail_on_gpu_run=True)
    net = rife.Rife()
    a, b = cel(120, 120), cel(150, 116)
    out = net.interpolate_pair(a, b, [0.5])[0]
    assert out.shape == a.shape and not net.on_gpu
    net.interpolate_pair(a, b, [0.5])
    assert not net.on_gpu


def test_the_runtime_is_installed_into_this_python(monkeypatch):
    calls, loaded = [], {"done": False}

    def module(name):
        if not loaded["done"]:
            raise ImportError(name)
        return "onnxruntime module"

    def run(cmd, **kwargs):
        calls.append(cmd)
        loaded["done"] = True
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rife.importlib, "import_module", module)
    monkeypatch.setattr(rife.subprocess, "run", run)
    assert rife.ensure_runtime() == "onnxruntime module"
    assert calls and calls[0][:4] == [rife.sys.executable, "-m", "pip", "install"]


# --------------------------------------------------------------------------
# telling a cut from fast motion
# --------------------------------------------------------------------------

def test_a_cut_the_pixel_difference_misses_is_unrelated():
    a, b = cel(140, 120), city()
    assert not frames_are_a_cut(a, b), "the pixel difference was expected to miss this cut"
    assert frames_are_unrelated(a, b)


@pytest.mark.parametrize("dx", [6, 40, 160])
def test_motion_however_fast_is_not_a_cut(dx):
    assert not frames_are_unrelated(cel(80, 120), cel(80 + dx, 120))


def test_frames_of_different_sizes_are_unrelated():
    assert frames_are_unrelated(cel(140, 120), cel(70, 60, w=160, h=120))


# --------------------------------------------------------------------------
# the renderer picks it only when asked
# --------------------------------------------------------------------------

class _FakeRife:
    made = 0

    def __init__(self, progress=None):
        _FakeRife.made += 1

    def interpolate_pair(self, a, b, times, **_):
        return [np.full_like(a, 77) for _ in times]


def test_the_network_draws_the_in_betweens(monkeypatch, make_anime, tmp_path, read_frames):
    monkeypatch.setattr(rife, "Rife", _FakeRife)
    src = make_anime(holds=(2,) * 6)
    plan, _, _ = analyse_video(src, Eternal2xConfig())

    _FakeRife.made = 0
    out = read_frames(render_plan(src, plan, tmp_path / "ai.avi", Eternal2xConfig()).output)
    assert _FakeRife.made == 1, "the model should load once per render"
    assert any(np.all(f == 77) for f in out), "the network's in-betweens were not used"

    render_plan(src, plan, tmp_path / "off.avi", Eternal2xConfig(interpolate_enabled=False))
    assert _FakeRife.made == 1, "Interpolate off still loaded the network"


def test_without_the_network_the_drawings_are_kept_as_drawn(monkeypatch, make_anime, tmp_path,
                                                             read_frames):
    """Offline on the first run it must still render, and invent nothing: no
    other way of making an in-between avoids ghosting."""
    def broken(progress=None):
        raise rife.AIUnavailable("Could not download the AI model (offline).")

    monkeypatch.setattr(rife, "Rife", broken)
    src = make_anime(holds=(2,) * 6)
    plan, _, _ = analyse_video(src, Eternal2xConfig())
    smooth = render_plan(src, plan, tmp_path / "out", Eternal2xConfig(), sequence=True)
    plain = render_plan(src, plan, tmp_path / "plain", Eternal2xConfig(interpolate_enabled=False),
                        sequence=True)
    assert "kept as drawn" in smooth.note and "offline" in smooth.note
    assert all(np.array_equal(x, y) for x, y in zip(read_frames(smooth.output),
                                                   read_frames(plain.output)))


# --------------------------------------------------------------------------
# the real network, when it is installed
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def model():
    pytest.importorskip("onnxruntime")
    if not rife.MODEL_PATH.exists():
        pytest.skip("the AI model has not been downloaded")
    return rife.Rife()


def test_real_drawings_pass_through_untouched(model):
    a, b = cel(120, 120), cel(150, 116)
    first, last = model.interpolate_pair(a, b, [0.0, 1.0])
    assert np.array_equal(first, a) and np.array_equal(last, b)


def test_the_network_draws_one_figure_where_flow_draws_two(model):
    """A move too fast for the flow guard: the network still lands it halfway."""
    a, b = cel(80, 120), cel(200, 120)
    truth = cel(140, 120)
    mid = model.interpolate_pair(a, b, [0.5])[0]
    err = float(np.abs(mid.astype(float) - truth.astype(float)).mean())
    held = float(np.abs(a.astype(float) - truth.astype(float)).mean())
    assert err < held / 2.0, f"error {err:.2f} against {held:.2f} for holding a drawing"


def test_the_network_holds_across_a_cut(model):
    a, b = cel(140, 120), city()
    out = model.interpolate_pair(a, b, [0.25, 0.5, 0.75])
    assert all(np.array_equal(f, a) for f in out)
