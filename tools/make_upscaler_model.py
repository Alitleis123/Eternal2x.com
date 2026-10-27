"""Build the upscaler's network file from Real-ESRGAN's published weights.

    python tools/make_upscaler_model.py

Needs numpy, onnx and onnxconverter-common, none of which ship with the
plugin. It downloads realesr-animevideov3.pth from Real-ESRGAN's own release
(BSD 3-Clause, Xintao Wang, https://github.com/xinntao/Real-ESRGAN), checks
it against its SHA-256, and writes Pipeline/networks/realesr-animevideov3-2x.onnx.

The network is SRVGGNetCompact, which draws the frame at 4x. Averaging each
2x2 block of that brings it to 2x, still on the GPU, so only a 2x frame comes
back. It runs in half precision, with 32-bit floats in and out. On a Radeon,
with whole frames, that took a 1080p frame from 0.66 s to 0.14 s.

The weights are read without PyTorch, by an unpickler that only accepts the
three names a plain state dict uses, so the file cannot run code.
"""

from __future__ import annotations

import collections
import hashlib
import io
import pickle
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper
from onnxconverter_common import float16

URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-animevideov3.pth"
SHA256 = "b8a8376811077954d82ca3fcf476f1ac3da3e8a68a4f4d71363008000a18b75d"
OUT = Path(__file__).resolve().parent.parent / "Pipeline" / "networks" / "realesr-animevideov3-2x.onnx"

FEATURES, CONVS, SCALE = 64, 16, 4


def _rebuild_tensor(storage, offset, size, stride, *_):
    return np.lib.stride_tricks.as_strided(
        storage[offset:], shape=size, strides=[s * storage.itemsize for s in stride]).copy()


def load_state(data: bytes) -> dict:
    archive = zipfile.ZipFile(io.BytesIO(data))
    root = archive.namelist()[0].split("/")[0]

    class Unpickler(pickle.Unpickler):
        def find_class(self, module, name):
            if (module, name) == ("torch._utils", "_rebuild_tensor_v2"):
                return _rebuild_tensor
            if (module, name) == ("torch", "FloatStorage"):
                return name
            if (module, name) == ("collections", "OrderedDict"):
                return collections.OrderedDict
            raise pickle.UnpicklingError(f"refusing {module}.{name}")

        def persistent_load(self, pid):
            _, kind, key, _location, _count = pid
            if kind != "FloatStorage":
                raise pickle.UnpicklingError(f"unexpected storage {kind}")
            return np.frombuffer(archive.read(f"{root}/data/{key}"), dtype="<f4")

    state = Unpickler(io.BytesIO(archive.read(f"{root}/data.pkl"))).load()
    return state.get("params_ema") or state["params"]


def build_graph(params: dict) -> onnx.ModelProto:
    # The input is read in two places. With one reader, the half-precision
    # conversion casts it once. With two, it named both casts alike, and ONNX
    # Runtime refused the file.
    nodes, weights = [helper.make_node("Identity", ["input"], ["frame"])], []
    x = "frame"
    for i in range(2 * CONVS + 3):
        out = f"body{i}"
        if i % 2 == 0:  # convolution
            w, b = f"body.{i}.weight", f"body.{i}.bias"
            weights += [numpy_helper.from_array(params[w], w), numpy_helper.from_array(params[b], b)]
            nodes.append(helper.make_node("Conv", [x, w, b], [out], kernel_shape=[3, 3],
                                          pads=[1, 1, 1, 1]))
        else:  # PReLU, one slope per channel
            s = f"body.{i}.weight"
            weights.append(numpy_helper.from_array(params[s].reshape(-1, 1, 1), s))
            nodes.append(helper.make_node("PRelu", [x, s], [out]))
        x = out
    weights.append(numpy_helper.from_array(np.array([1, 1, SCALE, SCALE], np.float32), "scales"))
    nodes += [
        helper.make_node("DepthToSpace", [x], ["drawn"], blocksize=SCALE, mode="CRD"),
        # The network draws the difference from the input scaled up by nearest
        # neighbour, as PyTorch's interpolate(mode="nearest") does it.
        helper.make_node("Resize", ["frame", "", "scales"], ["base"], mode="nearest",
                         coordinate_transformation_mode="asymmetric", nearest_mode="floor"),
        helper.make_node("Add", ["drawn", "base"], ["x4"]),
        helper.make_node("AveragePool", ["x4"], ["output"], kernel_shape=[2, 2], strides=[2, 2]),
    ]
    dims = ["N", 3, "H", "W"]
    graph = helper.make_graph(
        nodes, "realesr-animevideov3-2x",
        [helper.make_tensor_value_info("input", TensorProto.FLOAT, dims)],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, ["N", 3, "H2", "W2"])],
        weights)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)],
                              producer_name="Eternal2x tools/make_upscaler_model.py")
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model


def main() -> int:
    with urllib.request.urlopen(URL, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise SystemExit("The downloaded weights do not match their SHA-256.")
    model = float16.convert_float_to_float16(build_graph(load_state(data)), keep_io_types=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, str(OUT))
    print(f"Wrote {OUT} ({OUT.stat().st_size} bytes), "
          f"sha256 {hashlib.sha256(OUT.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
