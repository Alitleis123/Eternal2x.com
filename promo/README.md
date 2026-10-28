# The site film

`docs/assets/demo/promo.mp4` is built from this folder. It is 34 s, 1920x1080 at 24 fps, and shows how to use Upflow2x on one clip, the Hero walk: open the panel, mark the drawings, press Render, and the result lands on the track above.

- `film/` is a HyperFrames composition. Every scene, caption and timing is in `film/index.html`.
- `sound/make_sound.py` makes the soundtrack. The wind, sand and his coat are made from noise. The footsteps, clicks, key taps and paper sounds come from `sound/samples/`.
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

The site shows `docs/assets/demo/promo-resolve.jpg` as the film's poster. It is the film's frame at 5.8 s. Its line "How to use it in Resolve" sits above the middle, so the site's play button doesn't cover it. Make it again if that part of the film changes.

## The footage

- `walk_drawn.mp4` is the first 113 frames of the Hero walk as drawn, held on 4s.
- `walk_upflow.mp4` is Upflow2x's render of it, with Upscaling and Interpolate on and a marker on 25 drawings. The render is 337 frames, 3x slower than the clip. The film plays every third frame, so it runs at the clip's own speed and each frame lines up with the same frame of `walk_drawn.mp4`.
- `resolve.jpg` and `panel.png` are screenshots of that render in DaVinci Resolve Studio 20.3.1. The film's camera moves over the one screenshot.
- `cover_markers.png` and `cover_v2.png` are cut from the same screenshot with the markers and the render taken out, so the film can show them appear. `film/index.html` lists where they sit.

## Timing the sound

The sound times in `make_sound.py` follow `film/index.html`:

- the clicks on Workspace at 8.05 s and on Render at 20.05 s
- the panel opening at 8.6 s
- a key tap for each marker as the wipe uncovers it, from 11.4 s over 2.6 s
- the render landing on V2 at 23.3 s

His feet land on frames 4, 32, 84 and 108 of the walk clip, which plays from 0 s and again from 25.5 s. If the walk changes, find the new contacts frame by frame and move the steps.

## Licences

- The walk comes from [Hero](https://studio.blender.org/projects/hero/) by the Blender Foundation, CC BY 4.0. `walk_upflow.mp4` is an Upflow2x render of it.
- `resolve.jpg`, `panel.png` and the two covers are screenshots of DaVinci Resolve Studio 20.3.1 running Upflow2x.
- The samples are by [Kenney](https://kenney.nl/), CC0.
- Geist and Geist Mono are under the SIL Open Font License 1.1, in `film/assets/fonts/LICENSE`.
- GSAP 3.14.2 is under GreenSock's [standard no-charge licence](https://gsap.com/standard-license).
