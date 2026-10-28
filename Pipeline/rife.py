"""The in-between frames, drawn by a learned network.

Warping pixels along optical flow doubles lines and leaves two faded copies of
a character that moves a long way between two drawings. RIFE is a network
trained to predict the in-between directly, and on the same shots it draws
one solid figure.

This is Practical-RIFE 4.25 by Zhewei Huang (MIT licence,
https://github.com/hzwer/Practical-RIFE), as exported to ONNX by vs-mlrt, run
with ONNX Runtime. Neither ships with the plugin. The first time in-betweens
are drawn, ONNX Runtime is installed with pip and the model is downloaded, and
the model is only used once it matches its published SHA-256.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional

import cv2
import numpy as np

from Pipeline.interpolate import frames_are_a_cut, frames_are_unrelated
from Pipeline.render import SourceError

# Pinned to one commit of the mirror, so the file behind the URL cannot change.
MODEL_URL = ("https://huggingface.co/notaneimu/onnx-image-models/resolve/"
             "f7bf1c91e94ef516900f68456528fa781e2e7174/rife_v4.25_v2.onnx")
MODEL_SHA256 = "65c57a5e4abb17ad67faf35054291ac53affab0506d509d2e66698f6ecd75584"
MODEL_BYTES = 22748049
MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "rife_v4.25.onnx"

# The network halves the frame several times, so it works on sizes that are a
# multiple of this. The frame is padded by repeating its edge, then cropped.
_PAD = 64

ProgressFn = Callable[[str, float], None]


class AIUnavailable(SourceError):
    """The network cannot run here. The message says why and what to do."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _packages() -> List[str]:
    """Which ONNX Runtime to install, best first.

    On Windows the DirectML build runs the model on any DirectX 12 GPU, AMD
    and Intel included: on a Radeon, 0.13 s an in-between at 2048x858 against
    1.4 s on the CPU. Where it will not install, the plain build uses the CPU.
    """
    if sys.platform.startswith("win"):
        return ["onnxruntime-directml", "onnxruntime"]
    return ["onnxruntime"]


# How often a pip run that is still going says so. The panel takes a run that
# has not written its status for three minutes for a dead one, and pip can
# take longer than that on a slow connection.
HEARTBEAT_SECONDS = 15.0


def _pip(cmd: List[str], progress: Optional[ProgressFn]) -> subprocess.CompletedProcess:
    """Run pip, writing the status again every HEARTBEAT_SECONDS until it ends."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while True:
        try:
            out, err = proc.communicate(timeout=HEARTBEAT_SECONDS)
            return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
        except subprocess.TimeoutExpired:
            if progress:
                progress("Installing AI runtime", 0.0)


def ensure_runtime(progress: Optional[ProgressFn] = None):
    """Import ONNX Runtime, installing it into this Python the first time."""
    try:
        return importlib.import_module("onnxruntime")
    except ImportError:
        pass
    if progress:
        progress("Installing AI runtime", 0.0)
    why = ""
    for package in _packages():
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", package]
        done = _pip(cmd, progress)
        if done.returncode != 0:
            lines = [x for x in (done.stderr or done.stdout or "").splitlines() if x.strip()]
            why = lines[-1].strip() if lines else f"pip exited with {done.returncode}"
            continue
        importlib.invalidate_caches()
        try:
            return importlib.import_module("onnxruntime")
        except ImportError as exc:
            # The DirectML build can install and still not load, missing a
            # DLL. It is removed, so the next build does not share its files.
            why = f"{package} installed but will not load: {exc}"
            for name in [m for m in sys.modules if m.split(".")[0] == "onnxruntime"]:
                del sys.modules[name]
            _pip([sys.executable, "-m", "pip", "uninstall", "-y", package], progress)
    raise AIUnavailable(
        f"Could not install ONNX Runtime for the in-betweens ({why}). This is Python "
        f"{sys.version_info[0]}.{sys.version_info[1]}, and it has builds for 3.10 to 3.14, "
        f"or up to 3.13 on an Intel Mac. To install it by hand, run: "
        f"\"{sys.executable}\" -m pip install onnxruntime")


def ensure_model(progress: Optional[ProgressFn] = None, path: Path = MODEL_PATH,
                 url: str = MODEL_URL, sha256: str = MODEL_SHA256) -> Path:
    """The model file, downloaded and checked the first time it is needed.

    Two runs can both find it missing and download at once, so each writes its
    own temporary file. Whichever finishes second finds a good model in place,
    and Windows will not replace a file the other run has open, so it keeps
    that one.
    """
    if path.exists() and _sha256(path) == sha256:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".",
                                    suffix=".part")
    part = Path(name)
    try:
        with os.fdopen(handle, "wb") as f:
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    total = int(response.headers.get("Content-Length") or MODEL_BYTES)
                    got = 0
                    for chunk in iter(lambda: response.read(1 << 20), b""):
                        f.write(chunk)
                        got += len(chunk)
                        if progress:
                            progress("Downloading AI model", min(1.0, got / float(total)))
            except OSError as exc:
                raise AIUnavailable(f"Could not download the AI model ({exc}). Check the "
                                    f"connection and try again.") from exc
        if _sha256(part) != sha256:
            raise AIUnavailable("The downloaded AI model did not match its checksum, so it "
                                "was not used. Try again.")
        try:
            os.replace(part, path)
        except OSError:
            if not (path.exists() and _sha256(path) == sha256):
                raise
        return path
    finally:
        part.unlink(missing_ok=True)


class Rife:
    """One loaded model, reused for every pair of drawings in a render."""

    def __init__(self, progress: Optional[ProgressFn] = None):
        ort = ensure_runtime(progress)
        model = ensure_model(progress)
        self._ort, self._model = ort, model
        # DirectML runs it on any Windows GPU, AMD included, when the
        # onnxruntime-directml package is the one installed.
        self.on_gpu = "DmlExecutionProvider" in ort.get_available_providers()
        try:
            self.session = self._session()
            # ONNX Runtime can catch a DirectML start-up failure itself and
            # hand back a CPU session, one without the core left for Resolve.
            if self.on_gpu and "DmlExecutionProvider" not in self.session.get_providers():
                raise RuntimeError("DirectML did not start")
        except Exception as exc:  # onnxruntime raises its own exception types
            if not self.on_gpu:
                raise AIUnavailable(f"The AI model would not load ({exc}).") from exc
            # A GPU without DirectX 12 lists DirectML but cannot start it.
            self.on_gpu = False
            try:
                self.session = self._session()
            except Exception as exc2:
                raise AIUnavailable(f"The AI model would not load ({exc2}).") from exc2
        self.input = self.session.get_inputs()[0].name

    def _session(self):
        ort = self._ort
        options = ort.SessionOptions()
        if self.on_gpu:
            options.enable_mem_pattern = False
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            providers = ["DmlExecutionProvider", "CPUExecutionProvider"]
        else:
            # One core left over, so Resolve stays responsive while it works.
            options.intra_op_num_threads = max(1, (os.cpu_count() or 2) - 1)
            providers = ["CPUExecutionProvider"]
        return ort.InferenceSession(str(self._model), options, providers=providers)

    def _infer(self, x: np.ndarray) -> np.ndarray:
        try:
            return self.session.run(None, {self.input: x})[0]
        except Exception:  # onnxruntime raises its own exception types
            if not self.on_gpu:
                raise
            # Resolve holds much of the GPU's memory, and a big frame can need
            # more than is left. The CPU is slower but has room.
            self.on_gpu = False
            self.session = self._session()
            return self.session.run(None, {self.input: x})[0]

    def _run(self, a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
        h, w = a.shape[:2]
        ph, pw = -h % _PAD, -w % _PAD
        planes = []
        for frame in (a, b):
            padded = cv2.copyMakeBorder(frame, 0, ph, 0, pw, cv2.BORDER_REPLICATE)
            rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
            planes.append(rgb.astype(np.float32).transpose(2, 0, 1) / 255.0)
        planes.append(np.full((1, h + ph, w + pw), t, np.float32))
        y = self._infer(np.concatenate(planes)[None])
        # A frame of another size would be written as it came, and a video
        # writer drops it without a word, so the render would come out short.
        expected = (1, 3, h + ph, w + pw)
        if getattr(y, "shape", None) != expected or not np.isfinite(y).all():
            raise AIUnavailable(f"The AI model returned a broken frame (shape "
                                f"{getattr(y, 'shape', '?')}, expected {expected}), so the "
                                f"render was stopped.")
        rgb = np.clip(y[0, :, :h, :w].transpose(1, 2, 0) * 255.0 + 0.5, 0, 255)
        return cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2BGR)

    def interpolate_pair(self, a: np.ndarray, b: np.ndarray, times: List[float],
                         unrelated: Optional[bool] = None) -> List[np.ndarray]:
        """In-betweens for `times` (each in 0..1) between drawings a and b.

        t == 0 returns `a` untouched and t == 1 returns `b` untouched, so real
        drawings are never resampled. Across a cut the first drawing is held
        until the next one starts, since the network would otherwise morph one
        shot into the other. `unrelated` is the renderer's verdict on whether
        the pair is two shots, made with the pairs around it in view, or False
        where the editor's markers already said. Without it the pair is judged
        on its own.
        """
        if not times:
            return []
        if np.array_equal(a, b):
            return [a.copy() for _ in times]
        if unrelated is None:
            unrelated = frames_are_a_cut(a, b) or frames_are_unrelated(a, b)
        if unrelated:
            return [(a.copy() if t < 1.0 else b.copy()) for t in times]
        out: List[np.ndarray] = []
        for t in times:
            t = float(min(1.0, max(0.0, t)))
            if t <= 0.0:
                out.append(a.copy())
            elif t >= 1.0:
                out.append(b.copy())
            else:
                out.append(self._run(a, b, t))
        return out
