---
name: Computer Control — Pointer, Keyboard and Clipboard
description: Move and click the pointer, scroll, drag, press key chords, hold and release keys, type text, and read or write the clipboard with the computer-control CLI — move, click, scroll, drag, key, key-down, key-up, type, clipboard. Use when a task needs raw input at the desktop: clicking a coordinate, typing into the focused window, a shortcut chord, a middle-click paste, or moving data through the clipboard.
---

# pointer, keyboard and clipboard

Load the `computer-control` skill first: shared contract, global flags before
the verb, refusal table. Coordinates are logical pixels, 1:1 with the
screenshots (`computer-control-screen` skill).

## The honesty rule

Only `move` can prove itself: it reads the cursor back and refuses when the
landing point is wrong. `click` `scroll` `drag` `key` `key-down` `key-up`
`type` all answer `ok:true, verified:false, effect:"unverifiable"` — the
event was sent, and no read can prove what it did. **After any of them,
verify with a screenshot, `active`, `windows` or `wait-window`.**

## Pointer

```bash
computer-control pos                                  # {"x","y"}
computer-control move 640 400                         # verified warp (confirmed), or ERR[move-not-verified]
computer-control click                                # click at the current pointer
computer-control click 640 400                        # warp, then left-click
computer-control click 640 400 --double --button R    # double right-click
computer-control scroll down 5                        # at the pointer
computer-control scroll up 3 1200 700                 # after warping to X Y
computer-control drag 300 300 900 600 --duration 600  # press, move, release (release always happens)
```

- `--button` is `L` (default), `R` or `M`; anything else is `bad-args`.
- `scroll` direction is `up` `down` `left` `right`; `AMOUNT` is 1..10000
  ticks. Out of range → `bad-args`.
- `move` refuses **before** any side effect when the point is outside the
  monitors' bounding box, on a hole between monitors, or does not settle
  within 2 px — always `move-not-verified`, never a silent clamp. Coordinates
  come from `monitors`, `state` or a capture, not from guesswork.
- `drag` moves in 12 interpolated steps over `--duration` ms (default 300)
  and releases the button in a `finally`, so a failed move cannot leave the
  button stuck down. It also verifies the pointer actually reached the end
  point: a drag the absolute device range or a monitor edge clamps refuses
  `drag-not-verified` (the button is still released) instead of reporting
  coordinates it never reached. Drag within one monitor.

## Keyboard

```bash
computer-control key ctrl+t                           # browser tab (wtype route)
computer-control key alt+tab
computer-control key super+d                          # super needs the ydotool route; the CLI picks it
computer-control key ctrl+shift+t --ydotool           # force the uinput route
computer-control key-down shift                       # hold
computer-control key-up shift                         # release — always pair these
computer-control type 'hello world'                   # into the FOCUSED window
computer-control type $'line one\nline two' --delay 30
```

- **Chord syntax**: `mod+mod+key`, e.g. `ctrl+alt+shift+del`. Modifiers are
  `ctrl` (`control`), `alt`, `shift`, `super` (`win`, `mod`). Exactly one
  non-modifier key: `ctrl+shift+a` is fine, `ctrl+a+b` is `bad-combo`, an
  empty combo is `bad-combo`.
- **Key names**: `a`–`z`, `0`–`9`, and
  `return` `enter` `space` `tab` `escape` `esc` `backspace` `delete` `del`
  `insert` `up` `down` `left` `right` `home` `end` `pageup` `pagedown`
  `print` `pause` `capslock` `menu` `f1`…`f12` `kp_enter` `kp_add`
  `kp_subtract` `minus` `equal` `bracketleft` `bracketright` `semicolon`
  `apostrophe` `grave` `backslash` `comma` `period` `slash`.
  An unknown name is `key-unknown` (a typo in your arguments, not a desktop
  problem).
- **Route** (`rung: "input"`, detail `route`): plain `ctrl`/`alt`/`shift`
  chords with a known key go through **wtype**, which has real-keyboard
  semantics (Chromium sees `ctrl+t` as a tab, not a window). `super` chords
  and keys wtype has no name for go through **ydotool** (uinput), and
  `--ydotool` forces it. The `details.route` in the reply tells you which
  ran.
- `key-down` / `key-up` are always ydotool: its virtual keyboard keeps state
  across calls, wtype releases everything when it exits. **Every hold must be
  released** in the same plan, or the next action inherits a held modifier.
- `type` sends through wtype: a real newline and a literal `\n` both mean
  Return; `--delay MS` is the per-keystroke delay. Text typed and clipboard
  text are redacted in the action log (length + preview), never stored.
- Focus first: `type` and `key` go to whatever `active` says, so
  `computer-control focus SPEC` (then `active` to confirm) before typing.

## `--clearmodifiers`

Every pointer and keyboard verb takes `--clearmodifiers`: it releases both
sides of shift, ctrl, alt and super **before** the action, so a modifier left
held by an earlier `key-down` (or by the user) cannot colour this one. It is
ydotool-only — wtype already releases what it pressed when it exits. Reach
for it when an action behaves as if Shift were down, or after any
`key-down` you did not pair. It needs a real action to attach to:
`key --clearmodifiers shift` is `bad-combo`, because a combo needs a key.

## Clipboard

```bash
computer-control clipboard get                        # {"text","primary"}
computer-control clipboard set 'hello world'
computer-control clipboard types                      # ["text/plain", …]
computer-control clipboard get --primary              # the primary selection (middle-click paste)
computer-control clipboard set --file shot.png --type image/png
computer-control clipboard get --type image/png --out back.png    # {"path","bytes"}
computer-control clipboard clear --primary
```

- Text by default; `--type MIME` plus `--file`/`--out` moves raw bytes for
  any MIME type (images, HTML, …). A binary `get` without `--out` writes the
  bytes to stdout.
- Every write is **verified by reading the selection back**; an empty
  selection is empty data, not a refusal.
- `set` needs `TEXT` or `--file` (`bad-args`), and an unreadable/unwritable
  file is `no-file`. A wl-clipboard failure is `clipboard`.

## Recipes

**Click what you see** (see `computer-control-screen` for the capture):

```bash
computer-control screen --out /tmp/shots      # read the path from the JSON
# …look at the image, take X Y…
computer-control click X Y
computer-control active                       # or another screenshot: did anything happen?
```

**Type into an app**

```bash
computer-control focus class:firefox
computer-control type 'computer control cli'
computer-control key Return
computer-control active-screen --window class:firefox --out /tmp/shots   # verify by looking
```

**Move data between apps**

```bash
computer-control clipboard set "$(cat /tmp/report.txt)"
computer-control focus class:libreoffice
computer-control key ctrl+v
```

**A held chord, safely**

```bash
computer-control key-down shift
computer-control click 800 400          # extend-select
computer-control key-up shift
computer-control key --clearmodifiers ctrl+a      # release anything held, then the chord
```

## Refusals you will see here

`bad-args` (bad button, bad scroll direction/amount, one coordinate of a
pair, `set` with no data) · `bad-combo` `key-unknown` (chord syntax) ·
`type` (wtype failed) · `ydotool` (uinput device refused — the tool's own
message is in the text) · `move-not-verified` (point off-desktop or the warp
did not settle) · `clipboard` `no-file`.
