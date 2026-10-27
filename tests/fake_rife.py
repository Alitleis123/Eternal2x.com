"""A stand-in for the RIFE network, so tests never download its model.

It keeps everything Pipeline/rife.py decides for itself: real drawings pass
through untouched, identical drawings stay still and a cut holds. Only the
network's own frame is replaced, by a plain blend of the two drawings.
"""

from __future__ import annotations

import cv2

from Pipeline import rife


class StandInRife(rife.Rife):
    made = 0

    def __init__(self, progress=None):
        StandInRife.made += 1
        self.on_gpu = False

    def _run(self, a, b, t):
        return cv2.addWeighted(a, 1.0 - t, b, t, 0)


# Written over Pipeline/rife.py in a copied plugin, for the tests that run
# the real stage in a separate Python, which this module cannot reach.
MODULE = '''"""Test stand-in for the RIFE network: a plain blend, no model."""
import cv2

from Pipeline import _rife_real
from Pipeline._rife_real import AIUnavailable, SourceError  # noqa: F401


class Rife(_rife_real.Rife):
    def __init__(self, progress=None):
        self.on_gpu = False

    def _run(self, a, b, t):
        return cv2.addWeighted(a, 1.0 - t, b, t, 0)
'''

# Written over Pipeline/upscaler.py in a copied plugin, which imports what the
# stand-in rife.py above leaves out. It sees no GPU, as on a Mac, so the render
# stays at the clip's size and Super Scale does the upscale.
UPSCALER_MODULE = '''"""Test stand-in for the upscaler: there is no GPU here."""
from Pipeline.rife import AIUnavailable


class Upscaler:
    def __init__(self, progress=None):
        raise AIUnavailable("There is no GPU runtime here.")
'''
