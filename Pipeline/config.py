"""Settings for the Eternal2x pipeline."""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class Eternal2xConfig:
    """Everything the pipeline needs, in one place.

    Defaults are tuned for hand-drawn animation, which is typically drawn on
    2s (each drawing held for two frames) with occasional 1s and 3s.
    """

    # --- duplicate detection ---------------------------------------------
    # A frame counts as a new drawing when its difference from the last new
    # drawing reaches this. It has to sit above codec noise but below the
    # smallest real change (a blink, a mouth flap), hence the small value and
    # the tile-based scorer, which is sensitive to localised change. A grainy
    # clip raises it to clear its own noise (Pipeline.dedupe.noise_threshold),
    # so this is the lowest it goes.
    duplicate_threshold: float = 0.004

    # Width the analysis runs at. Detection does not need full resolution and
    # this keeps long clips fast.
    analysis_width: int = 640

    # The clip has film grain: raise the threshold above its own noise floor,
    # so held frames are not each taken for a new drawing. Off by default,
    # since on clean animation it can merge fine real changes.
    grain: bool = False

    # Force a hold pattern instead of detecting it. 0 means auto-detect.
    # 2 = "on 2s", 3 = "on 3s".
    force_base_hold: int = 0

    # --- output -----------------------------------------------------------
    # Interpolation quality. "fast" uses a coarser optical flow preset. "ai"
    # uses the RIFE network in Pipeline/rife.py, slower but clean on fast motion.
    quality: str = "better"          # "fast" | "better" | "best" | "ai"

    # Blend weight for occluded regions, where forward and backward flow
    # disagree. Higher values favour a plain cross-dissolve, which is softer
    # but never tears.
    occlusion_softness: float = 0.5

    # How far the forward and backward warps may disagree before an in-between
    # is held instead of generated. Raising it smooths faster motion at the
    # risk of smearing; lowering it is more conservative.
    max_disagreement: float = 0.02

    interpolate_enabled: bool = True
    upscale_enabled: bool = True
    upscale_factor: int = 2

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Eternal2xConfig":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


# Kept so older saved settings and the previous CLI flags still load.
UpscaleConfig = Eternal2xConfig
