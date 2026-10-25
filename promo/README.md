# The site film

`docs/assets/demo/promo.mp4` is built from this folder. It is 21.5 s, 1920x1080 at 24 fps.

- `film/` is a HyperFrames composition. Every scene, caption and timing is in `film/index.html`.
- `sound/make_sound.py` makes the soundtrack. The wind, sand and his coat are made from noise. The footsteps, clicks and paper sounds come from `sound/samples/`.
- `build.py` runs both and writes `docs/assets/demo/promo.mp4`.

## Rebuilding

```
python promo/build.py
```

You need Python with numpy, ffmpeg on PATH, and Node. npx downloads HyperFrames 0.8.193 the first time.

To watch the film while editing it, build the sound once, then start the preview:

```
python promo/sound/make_sound.py
cd promo/film
npx --yes hyperframes@0.8.193 preview
```

The poster, `docs/assets/demo/promo.jpg`, is frame 0. Only make it again if frame 0 changes:

```
ffmpeg -i docs/assets/demo/promo.mp4 -frames:v 1 -q:v 2 docs/assets/demo/promo.jpg
```

## Timing the sound

The sound times in `make_sound.py` follow `film/index.html`:

- the cursor presses at 6.9 and 8.4 s
- the frame flips at 11.6, 12.13 and 12.65 s
- the logo at 17.91 s

His feet land on frames 4, 32, 84 and 108 of the walk clip, which starts at 0 s. If the walk changes, find the new contacts frame by frame and move the steps.

## Licences

- The walk and the frames come from [Hero](https://studio.blender.org/projects/hero/) by the Blender Foundation, CC BY 4.0. `walk_e2x.mp4` and `out*.jpg` are Eternal2x renders of it.
- `panel_*.png`, `resolve_bg.jpg` and `resolve_result.png` are screenshots of DaVinci Resolve Studio 20.3.1 running Eternal2x.
- The samples are by [Kenney](https://kenney.nl/), CC0.
- Inter is under the SIL Open Font License 1.1, in `film/assets/fonts/LICENSE`.
- GSAP 3.14.2 is under GreenSock's [standard no-charge licence](https://gsap.com/standard-license).
