# Upflow2x test suite

Runs the whole plugin without opening DaVinci Resolve.

```bash
./test.sh                  # everything (creates .venv on first run)
./test.sh -k markers       # just the marker tests
./test.sh -x               # stop at the first failure
./test.sh tests/test_lua_ui.py -v
```

## How it manages to test Resolve stages

`tests/fake_resolve.py` implements the Resolve scripting API — Resolve,
ProjectManager, Project, Timeline, TimelineItem, MediaPoolItem — and is
installed into `sys.modules` as `DaVinciResolveScript`, which is exactly the
module the stages import. The stages cannot tell the difference.

The fake is deliberately **honest about what Resolve does not have**. It has a
`strict` flag:

| `strict` | Behaviour | What it proves |
|----------|-----------|----------------|
| `True` (default) | Only methods the real Resolve API exposes | What happens when you click the button for real |
| `False` | Also adds `TimelineItem.SetStart`/`SetEnd`/`SetClipProperty`, `Timeline.SplitClip`, `Timeline.GetSelectedItems` | Whether the stage's own logic is correct, assuming those calls worked |

That split is why a stage can have all its arithmetic tests pass and still fail
its `..._on_the_real_api_surface` test: the logic is right, but the API call it
depends on does not exist.

## How the Lua UI is tested

`tests/lua_harness.lua` stubs Fusion's `UIManager` / `UIDispatcher` and a
Resolve object tree, then loads the real `Installer/Eternal2x.lua` under an
embedded Lua runtime (`lupa`). Tests click real buttons and inspect the exact
shell command the plugin would run — including running that command for real to
confirm it resolves the `Stages` package.

`tests/test_installer.py` performs a full install into a temporary HOME, so the
install → launch → click chain is covered.

`tests/test_end_to_end.py` goes further and really runs the commands. The Lua
builds a shell command, the shell runs Python, Python renders a clip and writes
a status file, and the panel reads it all back and imports the result. Set
`H.real_execute = true` on the harness to opt into that.

Note that the panel does the Resolve import itself rather than letting the
Python process do it. A process spawned from Lua can only import
`DaVinciResolveScript` if the user has external scripting configured, whereas
the panel already holds a connection.

## Videos

`make_anime` (in `conftest.py`) writes lossless clips that behave like
hand-drawn animation: a texture translated a little further per drawing, with
each drawing held for a chosen number of frames. Because the exact timing is
known, the rebuilt output can be checked frame by frame rather than eyeballed.
`make_video` does the same for arbitrary per-frame motion.

The texture matters to the cut detector, which tracks it to tell motion from a
change of shot. The in-betweens themselves are drawn by a plain blend standing
in for the RIFE network (`fake_rife.py`), so no test downloads the model.
`test_rife.py` tests the real network, and skips those tests when its model is
not on disk.

## Layout

| File | Covers |
|------|--------|
| `test_dedupe.py` | held-frame detection, hold patterns, rebuild planning |
| `test_interpolate.py` | in-betweens around the real drawings, stills, cuts, upscale |
| `test_rife.py` | the network: its runtime, its model, the GPU, what it draws |
| `test_render.py` | video in, smooth video out, at every output format |
| `test_resolve_bridge.py` | connecting, importing, appending, Super Scale, probing |
| `test_resolve_smooth.py` | the stage the buttons run, end to end |
| `test_resolve_update.py` | auto-update over a real local HTTP server |
| `test_installer.py` | install locations, config contract, installer parity |
| `test_lua_ui.py` | the panel: handlers, commands, progress, persistence |
| `test_edge_cases.py` | tiny clips, static shots, odd rates and sizes, broken input |
| `test_end_to_end.py` | install, open panel, click, render, import, for real |
| `test_packaging.py` | release parity, update metadata, encodings, docs |
