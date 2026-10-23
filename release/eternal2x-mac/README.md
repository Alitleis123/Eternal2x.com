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

Every drawing starts on the same frame it did in the source, so cuts and lip
sync stay where the edit put them.

The result has the same length and frame rate as the clip you started with, so
it drops straight back into your edit.

## What it does

- Finds duplicated frames and works out whether the clip is on 1s, 2s or 3s.
- Rebuilds the motion with optical flow, generating the in-between frames that
  were never drawn.
- Keeps deliberate held poses still, instead of smearing them into drift.
- Cuts stay hard. Blending across a scene change would show both shots at once,
  so it snaps instead.
- Upscales 2x using Resolve's Super Scale.

It works like Twixtor without the warping. Where forward and backward motion
disagree, which is where optical flow normally tears, it fades to a soft
dissolve rather than inventing a broken frame.

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

The render runs in the background and the panel shows progress, so Resolve
stays usable while it works. When it finishes, the rebuilt clip is imported and
added to the end of your timeline.

Only the part of the file the timeline clip uses is analysed and rebuilt. To
reach it, the file is still decoded from its start, once for Analyse and again
for the render, so a short cut late in a long episode shows Seeking for a
while first. The panel shows the range it will use under the clip's name, or
Whole file when Resolve does not report one.

### Settings

| Setting | What it does |
|---|---|
| **Quality** | `Fast` for a quick look, `Better` for normal work, `Best` tracks fine detail like eyes and fingers at the cost of speed. |
| **Hold pattern** | Leave on `Auto detect`. Force `On 2s` or `On 3s` if a clip has unusual timing that trips the detector. |
| **Output** | `Image sequence` is lossless and always imports. `MP4` is a single compact file but lossy. `AVI` is a lossless single file that some Resolve builds will not import. |
| **Upscale 2x** | Applies Resolve's Super Scale to the imported clip. |
| **Interpolate** | Off means de-duplicate only, without generating in-betweens. |

Settings are remembered between sessions.

### What it will not do

If a clip has no duplicated frames, Eternal2x says so and stops. Live action
and animation already on 1s have nothing to recover, and smoothing them would
only invent motion that was never there.

## From the command line

Every button is a thin wrapper around one module, so you can script it:

```
python -m Stages.resolve_smooth --video shot.mov --analyse
python -m Stages.resolve_smooth --video shot.mov --quality best --format mp4
python -m Stages.resolve_smooth --video shot.mov --base-hold 3 --no-interpolate
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
before being applied. Restart Resolve afterwards.

## Troubleshooting

| Problem | Fix |
|---|---|
| Resolve says you need Studio | Since 19.1 the free edition blocks script panels. Eternal2x needs Studio. |
| Clicking Eternal2x does nothing, or "Plugin folder not configured" | The plugin folder moved after installing, and Workspace → Console says `UI script not found`. Re-run `python Installer/install_eternal2x.py` from where it is now. |
| "Could not reach Resolve" | Open the panel from Workspace → Scripts, not from a terminal. |
| "No clip under the playhead" | Move the playhead over the clip, then press **Use Clip at Playhead**. |
| "No duplicate frames. This clip is already on 1s." | The clip has no duplicated frames. It is live action, or already on 1s. |
| Progress seems stuck | Press **Refresh Progress**. If Python stopped, the panel says so and shows its last error. If a run was interrupted, press **Reset**. |
| Resolve would not import the render | The render still finished. The panel prints where it is, so drag it into your media pool. Switching Output to `Image sequence` avoids this. |
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

## Questions

Email `Justlighttbusiness@gmail.com`
