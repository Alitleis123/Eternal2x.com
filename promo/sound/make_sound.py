"""Sound design for the Eternal2x promo film (48 kHz stereo, 21.5 s).

Writes promo/film/assets/sound/eternal2x-sound.wav. Desert wind, sand and a coat
flutter are made here from noise. His footsteps, the two mouse clicks and the paper
sounds on the frame flips are Kenney CC0 samples in promo/sound/samples/. Needs
numpy and ffmpeg.
Times come from the composition (index.html) and from the walk clip's foot contacts:
frames 4, 32, 84 and 108 of the 24 fps clip (right, left, right, left).
"""
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SAMPLES = HERE / "samples"
OUT = HERE.parent / "film" / "assets" / "sound" / "eternal2x-sound.wav"

SR = 48000
DUR = 21.5
N = int(SR * DUR)


def db(v):
    return 10 ** (v / 20)


def curve(points, t):
    """Cosine-eased interpolation through (time, value) points; flat outside them."""
    ts = np.array([p[0] for p in points], float)
    vs = np.array([p[1] for p in points], float)
    i = np.clip(np.searchsorted(ts, t, side="right") - 1, 0, len(ts) - 2)
    u = np.clip((t - ts[i]) / (ts[i + 1] - ts[i]), 0, 1)
    u = 0.5 - 0.5 * np.cos(np.pi * u)
    return vs[i] + (vs[i + 1] - vs[i]) * u


def wobble(t, rate, seed):
    """Slow random movement between -1 and 1, about `rate` turns a second."""
    r = np.random.default_rng(seed)
    k = int(DUR * rate) + 6
    tk = (np.arange(k) - 2) / rate
    return curve(list(zip(tk, r.uniform(-1, 1, k))), t)


# ---- noise shaped frame by frame in the frequency domain --------------------------
NFFT, HOP = 2048, 512
F = np.fft.rfftfreq(NFFT, 1 / SR)
LOGF = np.log2(np.maximum(F, 1.0))
WIN = np.hanning(NFFT)
M = N // HOP + 5
TC = (np.arange(M) * HOP - NFFT / 2) / SR  # centre time of each frame


def bump(fc, octaves):
    """Gaussian band on a log-frequency axis, centred on fc (per frame), width in octaves."""
    return np.exp(-0.5 * ((LOGF[None, :] - np.log2(fc)[:, None]) / octaves) ** 2)


def synth(mag, seed):
    """Random-phase noise with the per-frame magnitude spectrum `mag` (M x bins)."""
    r = np.random.default_rng(seed)
    spec = mag * np.exp(2j * np.pi * r.random(mag.shape))
    out = np.zeros(M * HOP + NFFT)
    norm = np.zeros_like(out)
    frames = np.fft.irfft(spec, NFFT, axis=1) * WIN[None, :]
    for m in range(M):
        out[m * HOP:m * HOP + NFFT] += frames[m]
        norm[m * HOP:m * HOP + NFFT] += WIN ** 2
    out = (out / np.maximum(norm, 1e-3))[NFFT // 2:NFFT // 2 + N]
    # Scale so the desert part (0 to 5.6 s) has an RMS of 1; the shape over time stays.
    return out / np.sqrt(np.mean(out[:int(5.6 * SR)] ** 2))


t = np.arange(N) / SR

# Gust strength, 0 calm to 1 strong. A gust carries the divider in at 2.6 s, and the
# wind comes back under the logo at 17.9 s.
GUST = [(0, .35), (1.0, .62), (1.9, .40), (2.4, .45), (3.05, .90), (3.9, .48), (4.8, .62),
        (5.6, .45), (7, .32), (16.4, .32), (17.9, .74), (19.2, .46), (21.5, .30)]
g = np.clip(curve(GUST, TC) + 0.07 * wobble(TC, 0.9, 1) + 0.03 * wobble(TC, 3.1, 2), 0, 1)

fc = 280 + 900 * g
rumble = (1 / (1 + (F / 220) ** 2)) * (1 / (1 + (60 / np.maximum(F, 1)) ** 4))
wind_mag = (
    rumble[None, :] * (0.3 + 0.35 * g)[:, None]
    + 0.5 * (0.25 + g)[:, None] * bump(fc, 0.6)
    + 0.10 * (g ** 2)[:, None] * bump(fc * 1.75, 0.3)
)
flicker = np.clip(1 + 0.5 * wobble(TC, 14, 3), 0.3, 2)
sand_mag = 0.045 * ((0.15 + g) ** 1.6 * flicker)[:, None] * bump(np.full(M, 5200.0), 0.75)

wind = np.stack([synth(wind_mag, 10), synth(wind_mag, 11)], 1)
sand = np.stack([synth(sand_mag, 12), synth(sand_mag, 13)], 1)
# Mostly wide, with some centre so it isn't hollow on a phone speaker.
wind = 0.8 * wind + 0.35 * wind[:, ::-1]
sand = 0.85 * sand + 0.3 * sand[:, ::-1]

# His coat flapping: a mid band, beaten at an uneven 8 to 13 Hz, only while he walks.
coat = synth(0.5 * bump(np.full(M, 1100.0), 0.7), 14)
rate = 10.5 + 2.5 * wobble(t, 0.7, 4)
beat = (0.5 + 0.5 * np.sin(2 * np.pi * np.cumsum(rate) / SR)) ** 3
gs = np.interp(t, TC, g)
coat = coat * beat * gs * curve([(0, 0), (0.6, 1), (5.4, 1), (6.0, 0)], t)
coat = np.stack([coat * 0.9, coat * 1.0], 1)

# Overall wind level: full in the desert, low under the app, back for the logo.
level = curve([(0, -60), (0.55, 0), (5.6, 0), (6.2, -13), (16.4, -13), (17.8, -3),
               (19.4, -4), (21.45, -60)], t)
amb = (0.048 * wind + 0.016 * sand + 0.010 * coat) * db(level)[:, None]


# ---- samples ---------------------------------------------------------------------
def load(name):
    """A sample from samples/, decoded to 48 kHz stereo."""
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(SAMPLES / f"{name}.ogg"),
                          "-ar", str(SR), "-ac", "2", "-f", "s16le", "-"],
                         check=True, capture_output=True).stdout
    x = np.frombuffer(pcm, np.int16)
    return x.reshape(-1, 2).astype(np.float64) / 32768


def _filter(x, h_of_f):
    """Zero-phase filter, padded 50 ms each side so the ringing cannot wrap around."""
    pad = int(0.05 * SR)
    x = np.pad(x, ((pad, pad), (0, 0)))
    f = np.maximum(np.fft.rfftfreq(len(x), 1 / SR), 1e-3)
    return np.fft.irfft(np.fft.rfft(x, axis=0) * h_of_f(f)[:, None], len(x), axis=0)


def lowpass(x, fc, order=2):
    return _filter(x, lambda f: 1 / np.sqrt(1 + (f / fc) ** (2 * order)))


def highpass(x, fc, order=2):
    return _filter(x, lambda f: 1 / np.sqrt(1 + (fc / f) ** (2 * order)))


def speed(x, s):
    """Play at speed s (below 1 is slower and lower)."""
    idx = np.arange(0, len(x) - 1, s)
    return np.stack([np.interp(idx, np.arange(len(x)), x[:, c]) for c in range(2)], 1)


def onset(x):
    """Where the hit is: the loudest 5 ms, less a 4 ms attack."""
    k = int(0.005 * SR)
    env = np.convolve(np.abs(x).max(1), np.ones(k) / k, "same")
    return max(int(np.argmax(env)) - int(0.004 * SR), 0)


def pan(x, p):
    """Constant-power pan, p from -1 left to 1 right."""
    a = (p + 1) * np.pi / 4
    m = x.mean(1)
    return np.stack([m * np.cos(a), m * np.sin(a)], 1) * np.sqrt(2)


out = amb.copy()


def place(x, at, gain_db=0.0, p=0.0, align=True):
    """Mix x so its onset lands at `at` seconds."""
    x = pan(x, p) * db(gain_db)
    fade_in, fade_out = int(0.002 * SR), int(0.01 * SR)  # no clicks at the sample's edges
    x[:fade_in] *= np.linspace(0, 1, fade_in)[:, None]
    x[-fade_out:] *= np.linspace(1, 0, fade_out)[:, None]
    start = int(round(at * SR)) - (onset(x) if align else 0)
    a, b = max(start, 0), min(start + len(x), N)
    out[a:b] += x[a - start:b - start]


def sand_spray(dur, seed):
    """Grit kicked up off the sand: a short, grainy, decaying hiss."""
    r = np.random.default_rng(seed)
    n = int(dur * SR)
    grains = r.standard_normal((n, 2)) * (r.random((n, 1)) < 0.25)
    env = np.exp(-np.arange(n) / (0.045 * SR))[:, None]
    return highpass(lowpass(grains * env, 6500), 1800)


SLOW = 0.84  # he walks at half speed on screen, so his steps sit a little lower and heavier
thud = speed(highpass(lowpass(load("footstep_concrete_000"), 900), 55), SLOW)
for i, (at, gain, p, crunch) in enumerate([(0.167, -6, 0.12, "footstep_snow_003"),
                                            (1.333, -3, -0.12, "footstep_snow_001"),
                                            (3.500, -1, 0.12, "footstep_snow_002"),
                                            (4.500, 0, -0.12, "footstep_snow_004")]):
    place(thud, at, gain - 8, p)
    place(speed(highpass(lowpass(load(crunch), 7000), 350), SLOW), at + 0.012, gain + 4, p)
    place(sand_spray(0.22, 20 + i), at + 0.03, gain - 14, p)

# Two clicks for each button: press, then a quieter release.
body = 0.35 * lowpass(load("click1"), 5000)
press = np.pad(load("mouseclick1"), ((0, len(body)), (0, 0)))[:len(body)] + body
for at, p in [(6.9, -0.25), (8.4, -0.35)]:
    place(press, at, -6, p)
    place(speed(press, 1.12), at + 0.11, -12, p)

# A drawing laid down for each tile that turns into a new in-between.
for (at, p, name) in [(11.6, -0.25, "card-place-1"), (12.13, 0.08, "card-place-2"),
                      (12.65, 0.4, "card-place-4")]:
    place(highpass(lowpass(load(name), 7000), 150), at, -9, p)

# ---- master ----------------------------------------------------------------------
out *= curve([(0, 1), (21.3, 1), (21.5, 0)], t)[:, None]
peak = np.abs(out).max()
out *= db(-1.5) / peak
pcm = (np.clip(out, -1, 1) * 32767).astype(np.int16)
raw = Path(tempfile.mkdtemp()) / "raw.wav"
with wave.open(str(raw), "wb") as w:
    w.setnchannels(2)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())
# Lift by 5 dB and hold the footstep peaks under -1.7 dBFS, so the film plays about as
# loud as the old one (-20 LUFS).
OUT.parent.mkdir(parents=True, exist_ok=True)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af",
                "volume=5dB,alimiter=limit=0.82:attack=2:release=80:level=false",
                "-ar", str(SR), str(OUT)], check=True)
raw.unlink()
raw.parent.rmdir()
print("wrote", OUT)
