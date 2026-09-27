---
name: Computer Control
description: Drive this machine's Hyprland desktop with the computer-control CLI — read state, workspaces, windows, monitors and system facts; switch workspaces, focus, close and move windows; open apps; screenshot; click, type and press keys; clipboard; AT-SPI elements. Use when a task needs the desktop itself (a window, workspace, key chord, clipboard, screenshot, pointer or app action) rather than a file or a shell command.
---

# computer-control — the desktop as a command surface

One desktop action is one `computer-control <verb>` process run on the machine
it drives: `computer-control state`, or `./computer-control state` from the
repo checkout.

- Info verbs print one line of JSON on stdout; a refusal prints
  `ERR[code]: message` on stderr and exits 1. Exit 2 is a usage error
  (argparse usage on stderr, e.g. an unknown verb), 130 is an interrupt.
- Coordinates are **logical Hyprland pixels**, 1:1 with the screenshots
  (`grim -s 1`), so an X Y read from a screenshot clicks that pixel.
- `<verb> --help` is the local truth for a verb's flags; this skill is the
  map, not a substitute for it.

## The loop

1. `state` — workspaces, windows, the active window, the pointer. Re-read it
   before every action sequence; the desktop drifts.
2. `screen --out DIR` (or `region`, `active-screen`, `screen --monitor NAME`)
   — look at the image.
3. Act: `focus`, `switch`, `click`, `key`, `type`, `app-open`.
4. **Verify after each step** — `active`, `windows`, or another screenshot.
   Never assume a click or a keystroke landed: click/key/type are honestly
   `unverifiable`.

## The outcome contract

Every verb that changes the desktop answers with four keys plus details:

| Key | Meaning |
| --- | --- |
| `ok` | the action is done, as far as this verb can tell |
| `verified` | the verb **read the desktop back** and saw the effect |
| `effect` | `confirmed` (present) · `unconfirmed` (absent, a failure) · `unverifiable` (no read can prove it) |
| `rung` | how the effect was produced: `dispatch`, `px`, `input`, `app`, `poll`, `ax` |

`verified` is exactly `effect == "confirmed"`. `unverifiable` is the honest
answer for pointer, keyboard and app-owned effects — it is **not** a failure.
A read-back that does not see the effect is a refusal (`*-not-verified`), not
an `ok`.

| Verbs | Effect |
| --- | --- |
| `switch` `focus` `close` `close-apps` `move-window` `wait-window` `move` `app-open` `a11y-enable` | `confirmed` when the read-back saw it |
| `launch` (waits) | `confirmed` on the new window; `--no-wait` is `unverifiable` |
| `click` `scroll` `drag` `key` `key-down` `key-up` `type` | `unverifiable` — no read can prove an event landed |
| `clipboard set/clear`, `dir --create` | verified by reading back (the selection, the directory) |

## Global flags go BEFORE the VERB

`--version` · `--instance SIGNATURE` · `--display NAME` · `--quiet` ·
`--dry-run` · `--timeout SEC`

```bash
computer-control --dry-run switch 2     # right
computer-control switch 2 --dry-run     # ERR[bad-args]: the verb has no such flag
```

Everything after the verb belongs to the verb, so a flag the verb does not
have is `ERR[bad-args]`, never a silent no-op.

- `--quiet` suppresses stdout only; refusals stay on stderr, exit codes do
  not change, and a capture under it needs `--out`.
- `--dry-run` makes a mutating verb report what it would do without doing it.
  Verbs with their own plan (`close-apps --dry-run`, `app-open --dry-run`)
  produce that richer plan; the rest answer
  `{"dry_run":true,"argv":["computer-control", …]}`. Reads still read.
  Mutating: `switch` `focus` `close` `close-apps` `move-window` `launch`
  `app-open` `move` `click` `scroll` `drag` `key` `key-down` `key-up` `type`
  `a11y-enable` `audit`, plus `clipboard set|clear`, `dir --create`, and any
  capture or `clipboard get` given `--out`.
- `--timeout SEC` is the default window-wait timeout (15 s); a verb's own
  `--timeout` wins.
- `--instance` / `--display` target another Hyprland instance or Wayland
  display; the rest of the call runs against it.

## Window targets

`focus` `close` `move-window` `wait-window` `active-screen` take a SPEC:

- `class:ghostty` · `title:youtube` · `address:0x5561c4c50aa0` · bare name
  (substring of class **or** title) · a bare `0x…` is an address.
- Several matches refuse `ERR[ambiguous-window]` — there is never a silent
  first match. Re-target with `address:0x…` from `windows`.
- No match is `ERR[window-not-found]`.

## Choosing a target: act at the highest tier that works

1. **Window actions** (`focus`, `close`, `move-window`, `wait-window`) —
   exact and verified by address.
2. **AT-SPI** (`a11y`, `click --el N`) — element-level, no coordinates. Only
   apps that expose a tree list anything. See the `computer-control-a11y`
   skill.
3. **Pixels** — screenshot, then `click X Y`. See `computer-control-screen`
   and `computer-control-input`.

## Verb index (all 35)

| Group | Verbs |
| --- | --- |
| Reads | `state` `windows` `active` `workspaces` `monitors` `pos` `system` `apps` `log` `audit` |
| Screen | `screen [--monitor NAME] [--out DIR] [--no-cursor]` `region X Y W H` `active-screen [--window SPEC]` |
| Windows | `switch N` `focus SPEC` `close [SPEC]` `close-apps --kind K [--match A] [--keep B] [--dry-run]` `move-window SPEC N` `wait-window [SPEC] [--gone] [--new] [--exclude A,B] [--title S] [--class S] [--stable N] [--timeout S]` |
| Apps | `which NAME` `dir PATH [--create]` `app-open NAME [--workspace N] [--cwd DIR] [--timeout S] [--dry-run]` `launch [--workspace N] [--no-wait] [--timeout S] PROGRAM [ARGS…]` |
| Pointer | `move X Y` `click [X Y] [--button L\|R\|M] [--double] [--el N]` `scroll DIR AMOUNT [X Y]` `drag X1 Y1 X2 Y2 [--button B] [--duration MS]` |
| Keyboard | `key COMBO [--ydotool]` `key-down COMBO` `key-up COMBO` `type TEXT [--delay MS]` |
| AT-SPI | `a11y [--app S] [--max N]` `a11y-enable` |
| Data | `clipboard get\|set\|clear\|types [--primary] [--type MIME] [--file PATH] [--out FILE]` |
| Batch | `sequence '[["pos"],["move","100","200"]]'` |

Detail per group: `computer-control-windows`, `computer-control-screen`,
`computer-control-input`, `computer-control-a11y`.

## Reads and diagnostics

- `state` → `{"active_workspace", "workspaces", "windows", "active", "pointer"}`
  — a window row is `{"address","class","title","workspace","x","y","w","h","mapped","fullscreen","floating","pid"}`.
- `windows` `→ [row]`, `active` → row or `null`, `workspaces` →
  `[{"id","name","monitor","windows"}]`, `monitors` →
  `[{"name","x","y","w","h","focused","scale"}]`, `pos` → `{"x","y"}`.
- `system` → hostname, kernel, cpu, uptime, load, memory, swap, disk,
  network, battery, thermal.
- `apps` → desktop entries with their running process: `{"name","id",
  "command","terminal","wm_class","kind","running","pid"}`.
- `log [N] [--since TS] [--text]` — the last N actions from the action log
  (`{"ts","action","ok","args",…}`). Typed text and clipboard content are
  redacted to a length + preview; `log --follow` tails it one line per entry
  until interrupted (and refuses `--quiet`, since it would print nothing).
- `audit [--no-probe] [--no-a11y]` — the health check: session, required
  tools, screenshot 1:1, pointer calibration, a11y coverage. The calibration
  probe **warps the cursor to two points and restores it**; `--no-probe`
  skips that. A failed critical check makes it exit 1 with the rows intact.

## Refusals — branch on the code, never the prose

Every code the CLI can raise (`ERR[code]: message` on stderr, exit 1):

| Code | Means |
| --- | --- |
| `bad-args` `bad-combo` `bad-json` `key-unknown` | your arguments: fix the call |
| `no-session` | no live Hyprland socket; run as the desktop user |
| `not-found` `subprocess-failed` `subprocess-timeout` | a helper is missing, failed, or wedged |
| `hyprctl` `dispatch` | the compositor's own command failed |
| `type` `ydotool` | the keyboard or pointer device refused |
| `capture` `clipboard` | grim/wl-clipboard failed, or the capture is not a PNG |
| `window-not-found` `ambiguous-window` | the target names none, or several — use `address:0x…` |
| `focus-not-verified` `close-not-verified` `switch-not-verified` `move-not-verified` | the compositor answered ok and did not apply: re-read state, do not retry blindly |
| `wait-timeout` | the window did not appear (or did not go away) in time |
| `monitor-not-found` `ambiguous-monitor` | no monitor, or several, match the name |
| `app-unknown` `app-ambiguous` `app-open-failed` | the app entry is unclear, or it opened no window |
| `cwd-missing` `cwd-unsupported` | the directory does not exist, or the app cannot start in one |
| `not-a-dir` `create-failed` `create-not-verified` | `dir` found a file, could not create, or cannot verify |
| `no-file` | a clipboard input/output file could not be read or written |
| `atspi` | the AT-SPI bridge failed (a walk, or an action) |
| `stale-tree` | the dumped element is gone: re-dump with `a11y` and re-pick |
| `ambiguous-element` | several nodes share the identity: re-pick, or click by pixels |
| `element-owner-mismatch` | the element's app is not the focused window; a px click would aim wrong |

## Rules

- Batch independent calls; let a refusal stop the plan. `sequence` takes a
  JSON array of argv arrays, runs them in order and prints one array of
  results — but it **stops at the first refusal**, exits 1 with that step's
  `ERR[code]` and prints nothing: a failing step discards the earlier
  results, so do not use `sequence` to accumulate work you cannot redo. Each
  step is a command line of its own, so it may carry global flags, and they
  govern that step alone. A screenshot step in a `sequence` must use `--out
  DIR` (`ERR[bad-args]` otherwise, since bytes cannot be returned
  mid-batch).
- Every mutating call is logged to a redacted JSONL action log; a failure
  never fails the action it describes.
- Ambiguity refuses and names the candidates. Never pick "the first match".
- A failure a caller can produce is `ERR[code]`, never a traceback. If you
  see a traceback, that is a bug in the CLI — report the code and message.
- Unknown verb or misplaced global flag → exit 2 with usage on stderr; the
  hint names the global flags and where they go.
