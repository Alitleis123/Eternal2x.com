"""Settings for the Upflow2x pipeline."""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class Upflow2xConfig:
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
    # Draw the in-betweens with the RIFE network in Pipeline/rife.py. Off keeps
    # every drawing on its own frame and generates nothing, to upscale only.
    interpolate_enabled: bool = True
    upscale_enabled: bool = True
    upscale_factor: int = 2
    # Upscale 2x with the network in Pipeline/upscaler.py. Where it cannot run,
    # the render stays at the clip's size and Resolve's Super Scale does it.
    network_upscale: bool = False
    # How many times longer the render is than the clip. 1 drops back into the
    # edit in place of the original. 3 draws three frames for every one at the
    # same rate, so it plays in slow motion, ready to time remap.
    slow_motion: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Upflow2xConfig":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


# Kept so older saved settings and the previous CLI flags still load.
UpscaleConfig = Upflow2xConfig
