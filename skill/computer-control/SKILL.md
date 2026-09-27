---
name: computer-control
description: Drive a Hyprland desktop with the computer-control CLI — read state, switch workspaces, focus/close/move windows, open apps, capture screens, click, type, and drive AT-SPI elements. Use when a task needs the desktop itself (a window, workspace, key chord, clipboard, screenshot or pointer action) rather than a file or a shell.
---

# computer-control — the desktop as a command surface

Every desktop action is one `computer-control <verb>` call run on the machine
it drives. Info verbs print JSON; a refusal prints `ERR[code]: message` on
stderr and exits 1 (2 for a usage error). Coordinates are **logical pixels**,
1:1 with the screenshots.

## The loop

1. `state` — workspaces, windows, the active window, the pointer. Re-read it
   before every action sequence; the desktop drifts.
2. `screen --out DIR` (or `region`, `active-screen`, `screen --monitor NAME`)
   — look at the image.
3. Act: `focus`, `switch`, `click`, `key`, `type`, `app-open`.
4. **Verify after each step** — `active`, `windows`, or another screenshot.
   Never assume a click or a keystroke landed: click/key/type are honestly
   `unverifiable`.

## Verbs

| Task | Verb |
| --- | --- |
| Snapshot | `state` `windows` `active` `workspaces` `monitors` `pos` |
| Screen | `screen [--monitor NAME] [--out DIR] [--no-cursor]` `region X Y W H` `active-screen [--window SPEC]` |
| System | `system` `clipboard get\|set\|clear\|types [--primary] [--type MIME] [--file PATH] [--out FILE]` `log [N] [--since TS] [--text]` |
| Windows | `switch N` `focus SPEC` `close [SPEC]` `close-apps --kind terminal\|browser\|other\|all [--dry-run]` `move-window SPEC N` `wait-window [SPEC] [--gone] [--new] [--exclude A,B] [--title S] [--class S] [--stable N] [--timeout S]` |
| Apps | `apps` `which NAME` `dir PATH [--create]` `app-open NAME [--workspace N] [--cwd DIR] [--dry-run]` `launch [--workspace N] [--no-wait] PROGRAM [ARGS…]` |
| Pointer | `pos` `move X Y` `click [X Y] [--button L\|R\|M] [--double]` `scroll DIR AMOUNT [X Y]` `drag X1 Y1 X2 Y2 [--button B] [--duration MS]` |
| Keyboard | `key COMBO [--ydotool]` `key-down COMBO` `key-up COMBO` `type TEXT [--delay MS]` |
| AT-SPI | `a11y [--app S] [--max N]` `a11y-enable` `click --el N` |
| Audit | `audit [--no-probe] [--no-a11y]` |
| Batch | `sequence '[["pos"],["move","100","200"]]'` |

Window targets are `class:`, `title:`, `address:0x…`, or a bare name. Several
matches refuse `ERR[ambiguous-window]`; there is no silent first match.

## Targets, in order of preference

1. **Window actions** (`focus`, `close`, `move-window`, `wait-window`) — exact
   and verified; they name the window by address.
2. **AT-SPI** (`a11y`, `click --el N`) — element-level, no coordinates. Only
   apps that expose a tree (most GTK/Qt) list anything; Chromium needs
   `--force-renderer-accessibility`, and terminals often expose nothing. A
   gone element refuses `ERR[stale-tree]`; re-dump and re-pick. A px fallback
   is owner-checked (`ERR[element-owner-mismatch]` when the focused window is
   not the element's app).
3. **Pixels** — screenshot, then `click X Y`. `move` is verified against the
   cursor read-back; a point outside the monitors refuses
   `ERR[move-not-verified]`, never a clamp.

## Refusals

Branch on the code, never the prose. Every code the CLI can raise:

| Code | Means |
| --- | --- |
| `bad-args` `bad-combo` `bad-json` `key-unknown` | your arguments: fix the call |
| `no-session` | no live Hyprland socket; run as the desktop user |
| `not-found` `subprocess-failed` `subprocess-timeout` | a helper is missing, failed, or wedged |
| `hyprctl` `dispatch` | the compositor's own command failed |
| `type` `ydotool` | the keyboard or pointer device refused |
| `capture` `clipboard` | grim/wl-clipboard failed or the capture is not a PNG |
| `window-not-found` `ambiguous-window` | the target names none, or several — use `address:0x…` |
| `focus-not-verified` `close-not-verified` `switch-not-verified` `move-not-verified` | the compositor answered ok and did not apply: re-read state |
| `wait-timeout` | the window did not appear (or did not go away) in time |
| `monitor-not-found` `ambiguous-monitor` | no monitor, or several, match the name |
| `app-unknown` `app-ambiguous` `app-open-failed` | the app entry is unclear or opened no window |
| `cwd-missing` `cwd-unsupported` | the directory does not exist, or the app cannot start in one |
| `not-a-dir` `create-failed` `create-not-verified` | `dir` found a file, could not create, or cannot verify |
| `no-file` | a clipboard input/output file could not be read or written |
| `atspi` | the AT-SPI bridge failed (a walk, or an action) |
| `stale-tree` | the dumped element is gone: re-dump with `a11y` and re-pick |
| `ambiguous-element` | several nodes share the identity: re-pick or click by pixels |
| `element-owner-mismatch` | the element's app is not the focused window; a px click would aim wrong |

## Rules

- Batch independent calls; let a refusal stop the plan (a `sequence` stops at
  the first refusal).
- Global flags: `--quiet` (stdout only; a capture then needs `--out`),
  `--dry-run` (mutating verbs report what they would do; `close-apps`/
  `app-open` give their own richer plan), `--timeout SEC` (default window-wait
  timeout; a verb's own wins). They go **before the VERB** — a flag the verb
  does not have after it is `ERR[bad-args]`, and a `sequence` step takes them
  for that step alone.
- Pointer and keyboard verbs take `--clearmodifiers` (release every modifier
  first; ydotool-only — wtype cannot hold).
- `log --follow` tails the action log, one entry per line, until interrupted.
- Preview a destructive sweep with the global `--dry-run` before it runs.
- `ERR[focus-not-verified]` / `ERR[close-not-verified]` /
  `ERR[switch-not-verified]` / `ERR[move-not-verified]` mean the compositor
  answered ok and did not apply: re-read state, do not retry blindly.
- `system` reports cpu, memory, swap, disks, network, battery, thermal.
- `audit` is the health check: session, tools, screenshot 1:1, pointer
  calibration, a11y coverage.
