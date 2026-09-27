---
name: Computer Control — Windows, Workspaces and Apps
description: Read and act on windows, workspaces and applications with the computer-control CLI — state/windows/active/workspaces/monitors, switch, focus, close, close-apps, move-window, wait-window, app-open, launch, which, dir, apps. Use when a task means arranging or starting software on the desktop: bringing a window forward, moving it to a workspace, waiting for an app, closing a set of windows.
---

# windows, workspaces and apps

Load the `computer-control` skill first: it holds the shared contract (JSON
stdout, `ERR[code]` on stderr with exit 1, global flags **before** the verb,
the `ok/verified/effect/rung` outcome keys, the full refusal table).

## Reads (safe, no side effects)

```bash
computer-control state          # workspaces + windows + active + pointer, one snapshot
computer-control windows        # [{"address","class","title","workspace","x","y","w","h","mapped","fullscreen","floating","pid"}]
computer-control active         # the focused window row, or null
computer-control workspaces     # [{"id","name","monitor","windows"}]
computer-control monitors       # [{"name","x","y","w","h","focused","scale"}]
computer-control apps           # desktop entries + running pid
```

`state` is the cheapest re-read: use it before every action sequence and
again after each verified step.

## Window targets (SPEC)

`class:ghostty` · `title:youtube` · `address:0x5561c4c50aa0` · bare `0x…` ·
bare name (case-insensitive substring of class **or** title).

Zero matches → `window-not-found`; several → `ambiguous-window` with the
candidates named. Never assume a first match: take `address:0x…` from
`windows` and re-target.

## Actions

| Verb | Does | Proves |
| --- | --- | --- |
| `switch N` | focus workspace N | active workspace read back → `confirmed`, else `switch-not-verified` |
| `focus SPEC` | focus one window | active window address read back → `confirmed`, else `focus-not-verified` |
| `close [SPEC]` | close SPEC, or the focused window when SPEC is omitted | the address is gone from the window list → `confirmed`, else `close-not-verified` |
| `move-window SPEC N` | move a window to workspace N (id, or `name:…`) | the window's workspace read back → `confirmed`, else `move-not-verified` |
| `wait-window [SPEC]` | block until a window appears (or `--gone`) | `confirmed` with the window row; `wait-timeout` when the deadline passes |

```bash
computer-control focus 'title:youtube'
computer-control close 'address:0x5561c4c50aa0'
computer-control move-window class:firefox 2
computer-control switch 3
computer-control wait-window --class firefox --timeout 20
```

`close` with no target and nothing focused dispatches a close of "the
focused window" and answers `ok:false, effect:"unverifiable"` — check
`active` first if you care which window dies.

`wait-window` flags:

- `--gone` — wait until the match is *gone* instead of appearing.
- `--new` — only a window that did not exist when the wait began counts.
- `--exclude A,B` — comma-separated addresses to ignore (already-known windows).
- `--title S` / `--class S` — case-insensitive substrings narrowing the match.
- `--stable N` — require N consecutive satisfied reads (default 1), so a
  window caught mid-open is not reported ready.
- `--timeout S` — this verb's own timeout; the global `--timeout` is the
  default when it is absent. On expiry: `wait-timeout`.

## Closing a set of windows

```bash
computer-control close-apps --kind terminal --dry-run     # preview: count + window rows
computer-control close-apps --kind browser --keep chrome  # spare anything whose class contains "chrome"
computer-control close-apps --kind all --match slack      # only windows whose class or title contains "slack"
```

- `--kind` is `terminal` (default), `browser`, `other`, or `all`. Anything
  else matches no window and reports `closed: []` — that is a silent no-op,
  so use one of the four.
- Each window is closed **by address**, re-read, and repeated for up to 3
  rounds. All gone → `verified: true, effect: "confirmed"` with `closed`.
  Survivors → `ok:false` with a `remaining` list — read it and act on the
  names rather than re-running blindly.
- The verb's own `--dry-run` gives this rich plan; the global `--dry-run`
  routes to it as well.

## Apps

```bash
computer-control apps | head -c 400            # what is installed and running
computer-control which firefox                 # {"name","path"} or ERR[not-found]
computer-control app-open firefox              # open, wait for its window, return the row
computer-control app-open foot --cwd ~/src --workspace 2
computer-control app-open firefox --dry-run    # {"dry_run","name","argv","command","terminal","workspace"}
computer-control launch --workspace 3 foot -- bash -lc 'htop'
computer-control launch --no-wait rsync -a a b # fire and forget: effect unverifiable
computer-control dir ~/src/proj --create       # {"path","existed","created"}
```

- `app-open NAME` resolves the desktop entry by exact name/id, else a unique
  substring. Nothing matches → `app-unknown`; several → `app-ambiguous`.
  It then waits for exactly **one** new window whose class/title carries the
  app's name → `confirmed` with `{"address","class","title","workspace",
  "geometry"}`. No window in time → `app-open-failed`; two →
  `ambiguous-window`.
- Terminal entries are wrapped in a detected terminal (`ERR[not-found]` when
  none is on PATH).
- `--cwd DIR` only works for a terminal program: a missing directory is
  `cwd-missing` (create it with `dir --create`), a GUI app is
  `cwd-unsupported`.
- `--workspace N` moves the new window to N when it opened elsewhere, and
  says so in `workspace`.
- `launch` takes an explicit argv, not a desktop entry; put its own flags
  **before** `PROGRAM`, everything after `PROGRAM` belongs to the program.
  Without `--no-wait` it waits for one new window whose basename matches.
- `dir` is the small filesystem helper: existing dir → `existed:true`,
  existing file → `not-a-dir`, `--create` → `created:true` after a verified
  `makedirs`.

## Recipes

**Bring a window forward and confirm**

```bash
computer-control state                                   # who exists, and the active address
computer-control focus 'title:youtube'                   # ok / ERR[focus-not-verified]
computer-control active                                  # the read-back: is it the one you meant?
```

**Open an app on a workspace and land there**

```bash
computer-control app-open firefox --workspace 2
computer-control switch 2
computer-control windows | grep -i firefox
```

**React to something that is still opening**

```bash
computer-control wait-window --new --class firefox --stable 2 --timeout 20
computer-control active-screen --window class:firefox --out /tmp/shot
```

**Sweep, then verify**

```bash
computer-control close-apps --kind terminal --dry-run     # exactly what dies
computer-control close-apps --kind terminal
computer-control windows                                  # read it back
```

## Refusals you will see here

`window-not-found` `ambiguous-window` `focus-not-verified`
`close-not-verified` `switch-not-verified` `move-not-verified`
`wait-timeout` `app-unknown` `app-ambiguous` `app-open-failed` `cwd-missing`
`cwd-unsupported` `not-a-dir` `create-failed` `create-not-verified`
`not-found` `bad-args`.

A `*-not-verified` refusal means Hyprland answered ok and did not apply the
dispatch: re-read `state` and reason about why (wrong workspace? window
closed underneath you?) instead of retrying the same call.
