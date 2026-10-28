"""Rebuild the site film, docs/assets/demo/promo.mp4, from promo/film and promo/sound.

Needs Python with numpy, ffmpeg on PATH, and Node (npx fetches HyperFrames 0.8.193).
Run from anywhere:

    python promo/build.py
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILM = HERE / "film"
SOUND = FILM / "assets" / "sound" / "upflow2x-sound.wav"
RENDER = HERE / "out" / "film.render.mp4"
TARGET = HERE.parent / "docs" / "assets" / "demo" / "promo.mp4"


def run(cmd, **kw):
    print(">", " ".join(map(str, cmd)), flush=True)
    subprocess.run(cmd, check=True, **kw)


def main():
    run([sys.executable, str(HERE / "sound" / "make_sound.py")])
    RENDER.parent.mkdir(exist_ok=True)
    npx = "npx.cmd" if os.name == "nt" else "npx"
    run([npx, "--yes", "hyperframes@0.8.193", "render", "--quality", "high",
         "--output", str(RENDER)], cwd=FILM, env=dict(os.environ, HYPERFRAMES_NO_TELEMETRY="1"))
    # The sound is taken from the wav, not the render, so it is encoded only once.
    run(["ffmpeg", "-v", "error", "-y", "-i", str(RENDER), "-i", str(SOUND),
         "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-crf", "24", "-preset", "slow",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-shortest",
         "-movflags", "+faststart", str(TARGET)])
    print("wrote", TARGET)


if __name__ == "__main__":
    main()
