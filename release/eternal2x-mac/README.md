# Eternal2x

[![CI](https://github.com/Alitleis123/Eternal2x.com/actions/workflows/ci.yml/badge.svg)](https://github.com/Alitleis123/Eternal2x.com/actions/workflows/ci.yml)

Smooth 2x for hand-drawn animation, inside DaVinci Resolve Studio.

Anime is drawn on 2s or 3s: the artist draws twelve pictures a second, or
eight, and each one is held for two or three frames to fill 24fps. That is why anime looks
stepped when you slow it down, and why running it straight through a frame
interpolator does nothing useful. Most neighbouring frames are identical, so
there is no motion to interpolate between them.

Eternal2x recovers the real drawings first, then rebuilds the shot.

```
source     A A B B C C C D D        9 frames, held on 2s
drawings   A   B   C     D          4 unique drawings
rebuilt    A a B b C C c D D        in-betweens generated, C keeps its longer hold
```

![Six frames of a walk from the open movie Hero. As drawn, each drawing is held for two frames. From Eternal2x, each repeat is replaced by a new in-between.](docs/assets/demo/frames.jpg)

*Six frames of a walk from [Hero](https://studio.blender.org/projects/hero/)
(Blender Foundation, CC BY 4.0). The [site](https://eternal2x.com) has the
moving version, with a divider to drag between the two.*

Every drawing starts on the same frame it did in the source, so cuts and lip
sync stay where the edit put them.

The result has the same length and frame rate as the clip you started with, so
it drops straight back into your edit.

## What it does

- Finds duplicated frames and works out whether the clip is on 1s, 2s or 3s.
  It counts a change of colour, a slow fade or a slow pan as motion rather
  than a hold. With **Grainy source** ticked, it sees through film grain.
- Draws the in-between frames that were never drawn with the RIFE network,
  run on your own machine, on the GPU on Windows. Moving characters stay in
  one piece.
- Never shows a doubled or smeared in-between. The optical flow settings hold
  the real drawing wherever one would ghost.
- Keeps deliberate held poses still, instead of smearing them into drift.
- Cuts stay hard. Blending across a scene change would show both shots at once,
  so it snaps instead.
- Upscales 2x using Resolve's Super Scale.

It does a job like Twixtor's, but finds the held drawings first, so it only
generates the frames that were never drawn. With the optical flow settings,
wherever forward and backward motion disagree, which is where optical flow
normally tears or doubles a line, it keeps the real drawing instead of
inventing a broken frame, so fast motion is smoothed less. The AI setting,
the default, draws those frames too.

## Requirements

- **DaVinci Resolve Studio** 18 or newer. Since 19.1 the free edition blocks
  script panels like this one, so it cannot open Eternal2x. The 2x upscale uses
  Super Scale, which is Studio only too.
- Python 3.8+ on your PATH, from [python.org](https://www.python.org/downloads/).

## Install

### With the installer window

1. Extract the download somewhere permanent.
2. From inside the extracted folder, run `python Installer/gui_installer.py`
   (`python3` on macOS) and click **Install**.
3. It installs `numpy` and `opencv-python`, copies the plugin into Resolve's
   scripts folder and writes the config.
4. Restart Resolve and open **Workspace → Scripts → Eternal2x**.

If the window does not open, your Python was built without Tk. Use the manual
install below, which does the same job.

### Manual

```
python -m pip install numpy opencv-python
python Installer/install_eternal2x.py
```

Use `python3` on macOS. The installer records the Python that runs it, so
install the packages into that same one. Then restart Resolve.

The installer writes to Resolve's Utility scripts folder, which Resolve lists
under Workspace → Scripts on every page:

- **Windows:** `%APPDATA%\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\`
- **macOS:** `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/`

Earlier versions used the Comp folder, which Resolve only lists on the Fusion
page. Re-running the installer moves it.

The config records where the plugin folder is, so re-run the installer if you
move it.

## Using it

1. Move the playhead over a clip on your timeline. Resolve does not tell
   scripts which clip is selected, so Eternal2x works on the clip under the
   playhead, read again each time you press a button.
2. Open **Workspace → Scripts → Eternal2x**.
3. Click **Analyse**. It reads the clip and tells you what it found, for
   example `1438 frames, 719 unique drawings (719 held, 50%). Animated on 2s.`
   Nothing is changed, so this is safe to run on anything.
4. Click **Smooth Clip**.

The render runs in the background and the panel shows progress and the time
left, so Resolve stays usable while it works. When it finishes, the rebuilt
clip is imported and placed on the track above the original, starting on the
same frame, so it lines up with your edit and audio. It goes on the lowest
track that is empty there, or a new track named Eternal2x. Your other clips are
never covered or moved. If you move the original while it renders, the result
follows it. If another timeline is open when it finishes, or the original was
deleted or re-trimmed, it goes at the end instead. An image sequence whose
frame rate Resolve would not set stays in the media pool, since it would play
at the wrong speed.

Then:

- **Show Original** switches the smoothed clip off so the original shows
  through, and **Show Eternal2x** switches it back. Scrub or play to compare.
- **Show in Folder** opens the render's location.

Only the part of the file the timeline clip uses is analysed and rebuilt. To
reach it, the file is still decoded from its start, once for Analyse and again
for the render, so a short cut late in a long episode shows Seeking for a
while first. The panel shows the range it will use under the clip's name, or
Whole file when Resolve does not report one.

### Settings

| Setting | What it does |
|---|---|
| **Quality** | `AI`, the default, draws every in-between with the RIFE network, so moving characters stay solid. On Windows it runs on the GPU through DirectML, about 0.13 s an in-between at 2K on a Radeon, and falls back to the CPU, about ten times slower. Its first run installs ONNX Runtime and downloads the model, about 50 MB, so it needs a connection once; without one, the render uses `Best` and says so. `Fast`, `Better` and `Best` track pixels instead. They never show a doubled or smeared in-between: wherever one would ghost they keep the drawing as it is, so on fast motion they smooth much less. |
| **Hold pattern** | Leave on `Auto detect`. Force `On 2s` or `On 3s` if a clip has unusual timing that trips the detector. |
| **Output** | `Image sequence` is lossless and the most reliable to import. `MP4` is a single compact file but lossy. `MKV` is a lossless single file. All three are 8-bit RGB: transparency and 10-bit colour in the source are not kept. MP4 and MKV need an even width and height. |
| **Upscale 2x** | Applies Resolve's Super Scale to the imported clip. It only shows on a timeline bigger than the clip, such as 3840x2160 for 1080p footage. |
| **Interpolate** | Off skips the in-betweens, so every drawing comes back on its original frame. Use it with Upscale 2x to upscale only. |
| **Grainy source** | For film grain, which makes every held frame differ a little, so they all look new. It raises the threshold above the clip's own grain. Leave it off for clean animation, where it can merge fine real changes. Analyse suggests it when every frame differs in a way that looks like grain. |

Settings are remembered between sessions.

### What it will not do

If a clip has no duplicated frames, Eternal2x says so and stops. Live action
and animation already on 1s have nothing to recover, and smoothing them would
only invent motion that was never there.

It also refuses a clip with a variable frame rate, as phone and screen
recordings often have. Every rebuilt frame lasts the same time, so such a clip
would come out a different length and drift against its audio. Render it to a
constant frame rate first, from Resolve or with
`ffmpeg -i clip.mp4 -r 24 clip_cfr.mp4` at your timeline's rate.

## From the command line

Every button is a thin wrapper around one module, so you can script it:

```
python -m Stages.resolve_smooth --video shot.mov --analyse
python -m Stages.resolve_smooth --video shot.mov --quality best --format mp4
python -m Stages.resolve_smooth --video shot.mov --base-hold 3 --no-interpolate
python -m Stages.resolve_smooth --video grainy.mov --grain --quality ai
```

`--video` skips Resolve entirely and writes the result to an `Eternal2x` folder
beside the source, or to `--output-dir`. The 2x upscale is Resolve's Super
Scale, applied when the panel imports the result, so a `--video` run is always
at source resolution and `--no-upscale` only matters inside Resolve.

Add `--json` and stdout carries a single JSON summary, errors included, while
progress goes to stderr.

## Updates

Eternal2x checks for updates at startup, or on demand with the
**Check for Updates** button. Updates are verified against a SHA-256 checksum
and checked for every file the plugin needs before being applied. If an
update fails partway, the files it replaced are put back. Restart Resolve
afterwards.

## Troubleshooting

| Problem | Fix |
|---|---|
| Resolve says you need Studio | Since 19.1 the free edition blocks script panels. Eternal2x needs Studio. |
| Clicking Eternal2x does nothing, or "Plugin folder not configured" | The plugin folder moved after installing, and Workspace → Console says `UI script not found`. Re-run `python Installer/install_eternal2x.py` from where it is now. |
| "Could not reach Resolve" | Open the panel from Workspace → Scripts, not from a terminal. |
| "No clip under the playhead" | Move the playhead over the clip, then press **Use Clip at Playhead**. |
| "No duplicate frames. This clip is already on 1s." | The clip has no duplicated frames. It is live action, or already on 1s. |
| "Every frame differs, but the differences look like grain" | Tick **Grainy source** and analyse again. Heavy grain on a clip with long holds can still hide the duplicates. |
| "...has a variable frame rate" | Render the clip to a constant frame rate first, from Resolve or with ffmpeg, as the message says. |
| Progress seems stuck | Press **Refresh Progress**. If Python stopped, the panel says so and shows its last error. If a run was interrupted, press **Reset**. |
| Resolve would not import the render | The render still finished. The panel prints where it is, so drag it into your media pool. Switching Output to `Image sequence` usually avoids this. |
| Upscale skipped | Super Scale needs Resolve Studio. |
| Something else | Email the panel's status text and the `.eternal2x_last_run.log` file from the plugin folder. |

## Development

```
./test.sh
```

360+ tests, no Resolve required. The suite fakes the Resolve scripting API and
runs the Lua panel under an embedded Lua runtime, so buttons, commands and the
full render pipeline are all covered headlessly. See `tests/README.md`.

Tests run on Linux, macOS and Windows against Python 3.9 and 3.12.

To see which scripting API methods a Resolve build offers, turn on
Preferences → System → General → External scripting using → Local, set up
the environment variables from Blackmagic's scripting README, then run
`python -m Stages.resolve_probe` from the plugin folder.

To cut a release, bump `VERSION` and push a matching tag:

```
git tag v0.3.1 && git push origin v0.3.1
```

The release workflow builds the archives, publishes them, checks the published
downloads really resolve, and refreshes `update/latest.json`. The tag is derived
from `VERSION` in one place, so the archives, the tag and the update feed cannot
drift apart.

To build locally without publishing:

```
python Installer/build_release.py            # build the folders and zips
python Installer/build_release.py --publish  # build, tag, upload, verify
python Installer/build_release.py --verify   # check the live feed is sound
```

`update/latest.json` always describes the release that is live, so only
`--publish` writes it, and only once the uploaded files match the build. A
plain build never touches it. Files already on a release are never replaced.
If you change anything that ships after a release goes out, bump `VERSION` and
cut a new one rather than re-uploading.

Archives are byte reproducible, so rebuilding a tag on any machine gives the
same checksums as the published ones.

## Credits

The AI setting runs [Practical-RIFE](https://github.com/hzwer/Practical-RIFE)
4.25 by Zhewei Huang, under the MIT licence, in the ONNX export made by
[vs-mlrt](https://github.com/AmusementClub/vs-mlrt). The model is downloaded
the first time the setting is used and checked against its SHA-256. It is not
part of the Eternal2x download.

## Questions

Email `Justlighttbusiness@gmail.com`
