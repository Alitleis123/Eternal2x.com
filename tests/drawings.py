"""Test drawings that look like what this plugin is for.

Flat fills with hard outlines behave very differently from the textured noise
most synthetic tests use, and they are what anime actually looks like.
"""

from __future__ import annotations

import cv2
import numpy as np

BACKGROUND = (235, 225, 215)


def cel(cx: int, cy: int, w: int = 320, h: int = 240) -> np.ndarray:
    """One frame of flat-shaded animation: fills, outlines, no texture."""
    img = np.full((h, w, 3), BACKGROUND, np.uint8)
    cv2.rectangle(img, (0, h - 60), (w, h), (180, 200, 170), -1)
    cv2.circle(img, (cx, cy), 34, (120, 160, 240), -1)
    cv2.circle(img, (cx, cy), 34, (20, 20, 20), 3)
    cv2.ellipse(img, (cx - 13, cy - 6), (6, 9), 0, 0, 360, (30, 30, 30), -1)
    cv2.ellipse(img, (cx + 13, cy - 6), (6, 9), 0, 0, 360, (30, 30, 30), -1)
    cv2.line(img, (cx - 10, cy + 16), (cx + 10, cy + 16), (40, 40, 40), 2)
    return img
