---
name: Computer Control — Screen and Coordinates
description: Screenshot the desktop with the computer-control CLI — screen, region, active-screen, --monitor, --out, --no-cursor, logical pixel coordinates, multi-monitor layout. Use when a task needs to see the desktop: locate a button, a window or a status by looking at pixels, crop an area, capture one monitor or one window, or turn what is on screen into click coordinates.
---

# screen and coordinates

Load the `computer-control` skill first: shared contract, global flags before
the verb, refusal table.

## The coordinate model

- Captures are taken with `grim -s 1`: **logical pixels**, 1:1 with the
  desktop you click on. A point at `X,Y` in the image is `X,Y` to `move`,
  `click` and `scroll` — no scaling to undo, even with a HiDPI monitor.
- `monitors` gives the layout as logical rects:
  `[{"name","x","y","w","h","focused","scale"}]`. The desktop bounding box is
  the union of those rects; a point inside the union but on **no** monitor is
  not desktop, and `move` there refuses `move-not-verified` rather than
  clamping.
- The cursor is included in a capture unless you pass `--no-cursor`.

## Verbs

```bash
computer-control screen --out /tmp/shots                     # whole desktop
computer-control screen --monitor HDMI-A-1 --out /tmp/shots  # one monitor (exact name, else unique substring)
computer-control region 100 200 640 400 --out /tmp/shots     # crop: X Y W H in logical pixels
computer-control active-screen --out /tmp/shots              # the focused window's rect
computer-control active-screen --window class:firefox --out /tmp/shots
```

- `--out DIR` writes `<prefix>_<epoch-millis>.png` into DIR and returns
  `{"path","width","height"}`; prefixes are `screen`, `monitor`, `region`,
  `active`. The directory is created if needed, and only the **20 newest**
  captures with that prefix are kept — older ones are pruned, so do not
  treat `--out` as durable storage.
- `--no-cursor` keeps your own pointer out of the picture.
- Unknown or several monitors → `monitor-not-found` / `ambiguous-monitor`
  (the message lists the names it has).
- No focused window (or one with no size) → `window-not-found`.

## Always capture to a file

Without `--out`, the PNG bytes go to **stdout** — fine for a shell pipe, not
something to hand to a model as text. The working pattern:

```bash
computer-control screen --out /tmp/shots
# → {"path":"/tmp/shots/screen_1790560000123.png","width":3840,"height":1080}
```

Read that path with the file/image tool. Under `--quiet` a capture with no
`--out` is refused (`bad-args`), because it would be written and dropped;
inside a `sequence`, a screenshot step must carry `--out DIR` too.

## Recipe: look, then point

1. `computer-control state` — which window is active, where it sits
   (`x y w h`), and the workspace.
2. `computer-control active-screen --window class:firefox --out /tmp/shots`
   — crop to the window you care about instead of the whole desktop.
3. Read the image; find the control; take its pixel position **in the
   full-desktop coordinate space**. For a crop at window origin `(wx, wy)`,
   a point `(cx, cy)` inside the crop is `(wx + cx, wy + cy)` — or just
   click inside the region using the region's own origin from the JSON.
4. `computer-control click X Y` (see the `computer-control-input` skill), then
   **verify** with `active`, another screenshot, or `wait-window` — a click is
   `effect: "unverifiable"`, so a screenshot is the only honest proof.
5. Re-read `state` before the next step: windows move, and a stale coordinate
   clicks whatever moved there.

## Recipe: sanity-check the machine

```bash
computer-control audit                 # screenshot-1to1 must pass: image size == logical desktop
computer-control audit --no-probe      # without warping the cursor for calibration
```

`audit` warps the pointer to two probe points and restores it (see the core
skill); `capabilities.screenshot` reports the image size against the logical
desktop, which is exactly the invariant the coordinates above depend on.

## Refusals you will see here

`capture` (grim failed, could not save, or the bytes are not a PNG) ·
`monitor-not-found` `ambiguous-monitor` · `window-not-found` ·
`bad-args` (capture under `--quiet` without `--out`, or a `sequence`
screenshot step without `--out`).
