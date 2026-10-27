"""The 2x upscale, drawn by a network trained on anime.

Resolve's Super Scale enlarges an anime clip's compression blocks and
stair-steps its edges. This is Real-ESRGAN's anime video model
(realesr-animevideov3, BSD 3-Clause, Xintao Wang,
https://github.com/xinntao/Real-ESRGAN), which clears the blocks and keeps
thin lines crisp. In a test against Super Scale and nine other upscalers on
three anime clips, it looked cleanest and was the fastest network: 0.14 s a
1080p frame on a Radeon.

It ships with the plugin in Pipeline/networks, built from Real-ESRGAN's
published weights by tools/make_upscaler_model.py, with its licence beside it.
It runs only on a GPU, through DirectML. A CPU takes seconds a frame, so
there the render stays at the clip's size and Super Scale does the upscale,
as before.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from Pipeline.rife import AIUnavailable, ProgressFn, ensure_runtime

MODEL_PATH = Path(__file__).resolve().parent / "networks" / "realesr-animevideov3-2x.onnx"


class Upscaler:
    """One loaded model, reused for every frame of a render."""

    def __init__(self, progress: Optional[ProgressFn] = None, path: Path = MODEL_PATH):
        ort = ensure_runtime(progress)
        if "DmlExecutionProvider" not in ort.get_available_providers():
            raise AIUnavailable("There is no GPU runtime here.")
        if not path.exists():
            raise AIUnavailable(f"The upscaler's model is missing: {path.name}. Re-run the "
                                f"installer.")
        self._ort, self._path = ort, path
        self.on_gpu = True
        try:
            self.session = self._session()
        except Exception as exc:  # onnxruntime raises its own exception types
            raise AIUnavailable(f"The upscaler would not start on the GPU ({exc}).") from exc
        # ONNX Runtime can catch a DirectML start-up failure itself and hand
        # back a CPU session, which would take seconds a frame.
        if "DmlExecutionProvider" not in self.session.get_providers():
            raise AIUnavailable("The upscaler would not start on the GPU.")
        self.input = self.session.get_inputs()[0].name
        self.frames = 0

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
        return ort.InferenceSession(str(self._path), options, providers=providers)

    def _infer(self, x: np.ndarray) -> np.ndarray:
        try:
            return self.session.run(None, {self.input: x})[0]
        except Exception as exc:  # onnxruntime raises its own exception types
            # The first frame decides. A GPU it does not fit on leaves the
            # upscale to Super Scale, rather than a whole render on the CPU.
            if not self.on_gpu or self.frames == 0:
                raise AIUnavailable(f"The upscaler failed on the GPU ({exc}).") from exc
            # Resolve holds much of the GPU's memory, and can take more of it
            # mid-render. The CPU is slow, but the frames so far are 2x, so
            # the rest must be too.
            self.on_gpu = False
            self.session = self._session()
            return self.session.run(None, {self.input: x})[0]

    def upscale(self, frame: np.ndarray) -> np.ndarray:
        """`frame` at twice its width and height, BGR in and out."""
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        y = self._infer(rgb.astype(np.float32).transpose(2, 0, 1)[None] / 255.0)
        expected = (1, 3, 2 * h, 2 * w)
        if getattr(y, "shape", None) != expected or not np.isfinite(y).all():
            raise AIUnavailable(f"The upscaler returned a broken frame (shape "
                                f"{getattr(y, 'shape', '?')}, expected {expected}).")
        self.frames += 1
        out = np.clip(y[0].transpose(1, 2, 0) * 255.0 + 0.5, 0, 255)
        return cv2.cvtColor(out.astype(np.uint8), cv2.COLOR_RGB2BGR)
