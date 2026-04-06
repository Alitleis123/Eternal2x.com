# Stages/motion_score.py
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple, Union

import numpy as np
import cv2

from Pipeline.config import UpscaleConfig


def _open_video(video_path: Path) -> cv2.VideoCapture:
    """Open video with Unicode filename support on Windows."""
    cap = cv2.VideoCapture(str(video_path))
    if cap.isOpened():
        return cap

    # Workaround: OpenCV on Windows can't handle non-ASCII filenames.
    # Try opening via a short 8.3 path or a temp symlink/copy.
    if sys.platform.startswith("win"):
        try:
            short = os.path.normpath(str(video_path))
            # Try Windows short path via ctypes
            import ctypes
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.kernel32.GetShortPathNameW(short, buf, 512)
            if buf.value and buf.value != short:
                cap2 = cv2.VideoCapture(buf.value)
                if cap2.isOpened():
                    return cap2
                cap2.release()
        except Exception:
            pass

    # Last resort: create a temp symlink with ASCII name (avoids copying large files)
    suffix = video_path.suffix or ".mp4"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, prefix="e2x_", delete=False)
    tmp_path = tmp.name
    tmp.close()
    os.unlink(tmp_path)
    try:
        os.symlink(str(video_path.resolve()), tmp_path)
    except OSError:
        # Symlinks may require elevated privileges on Windows; fall back to binary copy
        with open(video_path, "rb") as src, open(tmp_path, "wb") as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
    cap3 = cv2.VideoCapture(tmp_path)
    if cap3.isOpened():
        cap3._temp_path = tmp_path  # type: ignore
        return cap3
    cap3.release()
    os.unlink(tmp_path)
    raise FileNotFoundError(f"Could not open video: {video_path}")


def _parse_tile_grid(tile_grid: Union[int, tuple, list, str]) -> Tuple[int, int]:
    # Accept 8, (8,8), "8x8", "8,8"
    if isinstance(tile_grid, int):
        return max(1, tile_grid), max(1, tile_grid)
    if isinstance(tile_grid, (tuple, list)) and len(tile_grid) == 2:
        return max(1, int(tile_grid[0])), max(1, int(tile_grid[1]))
    if isinstance(tile_grid, str):
        s = tile_grid.lower().replace(" ", "")
        if "x" in s:
            a, b = s.split("x", 1)
            return max(1, int(a)), max(1, int(b))
        if "," in s:
            a, b = s.split(",", 1)
            return max(1, int(a)), max(1, int(b))
        v = int(s)
        return max(1, v), max(1, v)
    return 8, 8


def _preprocess(frame_bgr: np.ndarray, max_width: int = 640) -> np.ndarray:
    """Grayscale + optional downscale for speed."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    if max_width and gray.shape[1] > max_width:
        h, w = gray.shape[:2]
        scale = max_width / float(w)
        gray = cv2.resize(gray, (max_width, int(h * scale)), interpolation=cv2.INTER_AREA)
    return gray


def score_global(prev_gray: np.ndarray, curr_gray: np.ndarray) -> float:
    """Whole-frame motion score in [0, ~1]."""
    diff = cv2.absdiff(prev_gray, curr_gray)
    diff = cv2.GaussianBlur(diff, (5, 5), 0)
    return float(diff.mean()) / 255.0


def score_detail(prev_gray: np.ndarray, curr_gray: np.ndarray, tile_grid) -> float:
    """
    Tile-based score: compute mean diff per tile, then average of the top 15% tiles.
    Good for small localized motion (hair/blinks).
    """
    diff = cv2.absdiff(prev_gray, curr_gray)
    diff = cv2.GaussianBlur(diff, (5, 5), 0)

    gx, gy = _parse_tile_grid(tile_grid)
    h, w = diff.shape[:2]
    tw, th = max(1, w // gx), max(1, h // gy)

    vals = []
    for ty in range(gy):
        y0 = ty * th
        y1 = (ty + 1) * th if ty < gy - 1 else h
        for tx in range(gx):
            x0 = tx * tw
            x1 = (tx + 1) * tw if tx < gx - 1 else w
            tile = diff[y0:y1, x0:x1]
            vals.append(float(tile.mean()) / 255.0)

    v = np.array(vals, dtype=np.float32)
    k = max(1, int(np.ceil(len(v) * 0.15)))   # top 15%
    topk = np.partition(v, -k)[-k:]
    return float(topk.mean())


def compute_motion_scores(
    video_path: Path,
    cfg: UpscaleConfig,
    *,
    max_width: int = 640
) -> Tuple[List[float], float]:
    """
    Returns (scores_per_frame, fps).

    Uses cfg.sample_every_n:
      - only *scores* every Nth frame (faster)
      - repeats that score for the skipped frames
      - divides by N so scores stay closer to per-frame scale
    """
    import sys as _sys

    cap = _open_video(video_path)

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if fps <= 0:
        fps = 30.0  # fallback

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    n = max(1, int(getattr(cfg, "sample_every_n", 1)))
    mode = str(getattr(cfg, "motion_mode", "detail")).lower()
    tile_grid = getattr(cfg, "tile_grid", (8, 8))

    ret, first = cap.read()
    if not ret:
        raise RuntimeError(f"Could not read first frame: {video_path}")

    prev = _preprocess(first, max_width=max_width)
    scores: List[float] = [0.0]  # frame 0 has no previous frame
    last_pct = -1

    while True:
        grabbed = 0

        # Grab n frames quickly, decode only the last one
        for _ in range(n):
            ok = cap.grab()
            if not ok:
                break
            grabbed += 1

        if grabbed == 0:
            break

        ret2, frame = cap.retrieve()
        if not ret2:
            break

        curr = _preprocess(frame, max_width=max_width)

        raw = score_global(prev, curr) if mode == "global" else score_detail(prev, curr, tile_grid)
        score = raw / grabbed
        scores.extend([score] * grabbed)

        prev = curr

        # Progress reporting
        if total_frames > 0:
            pct = int(len(scores) * 100 / total_frames)
            if pct != last_pct and pct % 5 == 0:
                print(f"[PROGRESS] {pct}%", flush=True)
                last_pct = pct

    cap.release()
    # Clean up temp file if one was created for Unicode workaround
    temp_path = getattr(cap, "_temp_path", None)
    if temp_path:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
    return scores, fps
