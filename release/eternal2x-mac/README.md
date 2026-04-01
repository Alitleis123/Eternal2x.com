# Eternal2x (DaVinci Resolve Smart Upscale)

Eternal2x is a creator-friendly smart upscale workflow for DaVinci Resolve. It detects motion, lets you refine cut points via timeline markers, and then runs a clean, minimal pipeline to prep clips for 2x upscale and interpolation. The UI is intentionally simple: 4 buttons and 1 slider.

## What It Does
- Detects motion and places clearly labeled `[DSU]` markers on the selected clip or timeline for quick preview and manual adjustment.
- Cuts at marker positions and converts resulting clips into 1-frame segments (for precise interpolation control).
- Regroups the timeline to remove gaps and make the sequence continuous.
- Runs a final pass: fixed 2x upscale + interpolation gated by sensitivity.

## UI
- Buttons: `Detect`, `Sequence`, `Regroup`, `Upscale and Interpolate`, `Check for Updates`
- Slider: `Interpolate Sensitivity` (higher = less interpolation, lower = more)

## Requirements
- DaVinci Resolve 18+ (Free or Studio)
- Python 3.8+ installed and available on your system PATH ([Download Python](https://www.python.org/downloads/))

## Install

### Option A: One-Click Installer (Recommended)

1. Extract the downloaded zip.
2. Double-click **`Eternal2xInstaller.exe`** (Windows) or run the installer app (macOS).
3. The installer automatically:
   - Verifies Python is installed (opens python.org if not)
   - Installs required Python packages (`numpy`, `opencv-python`)
   - Copies the plugin into DaVinci Resolve's scripts folder
   - Writes the configuration file
4. Restart DaVinci Resolve.
5. Open: **Workspace → Scripts → Eternal2x**

### Option B: Manual Install

If you prefer to install manually or the GUI installer doesn't work:

1. Install Python packages:
   ```
   pip install numpy opencv-python
   ```

2. Run the install script from the plugin folder:
   ```
   python Installer/install_eternal2x.py
   ```

3. Restart DaVinci Resolve.
4. Open: **Workspace → Scripts → Eternal2x**

### Install Locations

The installer copies files to:
- **Windows:** `%APPDATA%\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Comp\`
- **macOS:** `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Comp/`

> **Important:** Do not move or rename the plugin folder after installing. The config file stores the path to this folder. If you move it, re-run the installer.

## Auto-Update Behavior
- Eternal2x checks for updates automatically at startup (configurable).
- If a newer version exists for your platform, it downloads and applies update files.
- You can also trigger this manually using `Check for Updates` in the plugin UI.
- After an update is applied, restart Resolve to load the new version.
- Re-run the installer only if you move the plugin folder or the Scripts folder is cleared.

## Quick Start
1. Open a timeline and select the clip you want to process.
2. Open **Workspace → Scripts → Eternal2x**.
3. Click **Detect** to analyze motion and place markers. Adjust any markers that need fine-tuning directly on the timeline.
4. Click **Sequence** to cut at marker positions and generate 1-frame segments.
5. Click **Regroup** to remove gaps and make the sequence continuous.
6. Set **Interpolate Sensitivity** and click **Upscale + Interpolate**.

## How It Works (Under the Hood)
- Motion scores are computed per frame from the clip using tile-based detail analysis.
- Frames above the sensitivity threshold are grouped into segments. Tiny bursts are filtered out and nearby segments are merged.
- Marker positions (after your manual edits) are the source of truth for cutting.
- Upscale is fixed at 2x. Optical Flow interpolation is applied only to segments with detected motion; static segments use Nearest for speed.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Missing repo root" in status bar | Re-run `python Installer/install_eternal2x.py` |
| "Could not import DaVinciResolveScript" | Make sure you launched the script from inside Resolve (Workspace → Scripts) |
| Detect is slow on long clips | This is expected for high-frame-count clips. The sensitivity slider does not affect speed. |
| No markers appear after Detect | Lower the sensitivity slider and try again — your clip may have very subtle motion. |
| Python not found | Make sure Python is installed and on your system PATH. On macOS, use `python3`. |

## Questions
Email `Justlighttbusiness@gmail.com`
