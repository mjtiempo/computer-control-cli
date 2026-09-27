# computer-control-cli

Drive a Hyprland desktop from the command line: read the desktop, act on
windows and workspaces, open apps, move the pointer and type, drive AT-SPI
elements, and audit the machine — with refusals that name a machine-readable
code (`ERR[code]: message`) and actions that re-read the desktop before
claiming they worked.

Built test-first, in this order: **reads and info → workspace actions → apps →
pointer input → keyboard input**, then AT-SPI, audit and batching.

## Quick start

Nothing to install — run the checkout:

```bash
./computer-control state
./computer-control windows
./computer-control audit
./computer-control screen --out /tmp
```

Or install a link into a prefix:

```bash
./install.sh                 # ~/.local/bin/computer-control
./install.sh --prefix DIR    # DIR/bin
./install.sh --check         # is the link this repo? (exit 1 when not)
./install.sh --uninstall
```

## Verbs

| Group | Verbs |
| --- | --- |
| Desktop state | `state` `windows` `active` `workspaces` `monitors` `pos` |
| Screen | `screen [--monitor NAME] [--out DIR] [--no-cursor]` `region X Y W H` `active-screen [--window SPEC]` |
| System | `system` (cpu, model, memory, swap, disk, network, battery, thermal) · `clipboard get\|set\|clear\|types [--primary] [--type MIME] [--file PATH] [--out FILE]` · `log [N] [--since TS] [--text] [--follow]` |
| Windows / workspaces | `switch N` `focus SPEC` `close [SPEC]` `close-apps --kind K [--dry-run]` `move-window SPEC N` `wait-window [SPEC] [--gone] [--new] [--exclude A,B] [--title S] [--class S] [--stable N] [--timeout S]` |
| Apps | `apps` (entries + running processes) `which NAME` `dir PATH [--create]` `app-open NAME [--workspace N] [--cwd DIR] [--dry-run]` `launch [--workspace N] [--no-wait] [--timeout S] PROGRAM [ARGS…]` |
| Pointer | `pos` `move X Y` `click [X Y] [--button L\|R\|M] [--double]` `scroll DIR AMOUNT [X Y]` `drag X1 Y1 X2 Y2 [--button B] [--duration MS]` |
| Keyboard | `key COMBO [--ydotool]` `key-down COMBO` `key-up COMBO` `type TEXT [--delay MS]` |
| AT-SPI | `a11y [--app S] [--max N]` `a11y-enable` `click --el N` |
| Audit | `audit [--no-probe] [--no-a11y]` |
| Batch | `sequence '[["pos"],["move","100","200"]]'` |

Global flags: `--version`, `--instance SIGNATURE`, `--display NAME` (target
another Hyprland instance or Wayland display), `--quiet` (stdout only; exit
codes and `ERR[code]` still apply, and a capture then needs `--out`),
`--dry-run` (mutating verbs report what they would do — verbs with their own
dry-run plan, like `close-apps` and `app-open`, produce that richer plan —
reads still read), and `--timeout SEC` (the default window-wait timeout; a
verb's own `--timeout` wins).

They go **before the VERB**: `computer-control --dry-run switch 2`. Everything
after the verb belongs to the verb, so a flag the verb does not have is
`ERR[bad-args]` rather than a global — and never a silent no-op. A `sequence`
step is a command line of its own, so it may carry them too, and they govern
that step alone.

The pointer and keyboard verbs take `--clearmodifiers` (release every
modifier first — both `shift`s, `ctrl`s, `alt`s and `super`s — so a stuck hold
cannot colour the action; this is ydotool-only, because wtype releases what it
pressed when it exits). `log --follow` tails the action log, one entry per
line, until interrupted.

Window targets are `class:`, `title:`, `address:0x…`, or a bare name. Several
matches refuse (`ERR[ambiguous-window]`); there is no silent first match.

## Contracts

* **Refusals** are `ERR[code]: message` on stderr with exit 1 (2 for a usage
  error), never a traceback for input a caller can produce. The complete code
  list is in `skill/computer-control/SKILL.md`.
* **Outcomes**: every action that changes the desktop answers with `ok`,
  `verified`, `effect` (`confirmed` / `unconfirmed` / `unverifiable`) and
  `rung`. `verified` means the effect was read back; pointer, keyboard and
  app-owned effects are honestly `unverifiable`.
* **Coordinates** are logical Hyprland pixels, 1:1 with screenshots
  (`grim -s 1`).

## AT-SPI

`a11y` walks the accessibility tree and prints element rows (`index`, `role`,
`name`, `value`, `enabled`, `selected`, `actions`, `frame`); `click --el N`
re-locates the element by identity in a fresh walk and invokes its first
AT-SPI action, falling back to an owner-checked pixel click when the element
has no action. Coverage is uneven by nature: GTK/Qt apps expose trees,
Chromium needs `--force-renderer-accessibility`, and terminals often expose
nothing — on such a desktop `a11y` is an honest empty list, not a failure.

## Clipboard

Text by default, and raw bytes for any MIME type. Every write is verified by
reading the selection back; an empty selection is empty data, not a refusal.

```bash
computer-control clipboard get                            # {"text": …}
computer-control clipboard set "hello"
computer-control clipboard types                          # ["text/plain", …]
computer-control clipboard set --file shot.png --type image/png
computer-control clipboard get --type image/png --out back.png
computer-control clipboard clear --primary
```

## Architecture

One class per responsibility; services receive their `Session` and `Config`
instead of reading globals. `Runner` and `Discoverer` are protocols, so the
whole stack runs against a double.

| Module | Class(es) | Responsibility |
| --- | --- | --- |
| `errors.py` | `ControlError` | the coded refusal (and `from_line`) |
| `outcome.py` | `Outcome` | `ok/verified/effect/rung`, invariants enforced |
| `coerce.py` | `Coerce` | safe parsing of foreign replies |
| `models.py` | `Monitor`, `Workspace`, `Window`, `WindowSpec` | domain rows |
| `config.py` | `Config` | env/paths, read once |
| `session.py` | `Session`, `ProcessRunner`, `SessionDiscoverer` | env discovery, hyprctl, dispatch, Lua quoting |
| `desktop.py` | `Desktop` | windows/workspaces/monitors + verified actions |
| `screen.py` | `Screen` | grim captures (full, region, window, monitor) |
| `system.py` | `SystemInfo` | /proc + /sys facts and the clipboard |
| `apps.py` | `Apps`, `DesktopEntry` | resolution, launch, placement, processes |
| `input.py` | `Input`, `Pointer`, `Keyboard`, `KeyMap`, `Combo` | pointer, keys, drag |
| `a11y.py` | `A11y` | AT-SPI tree and element actions |
| `atspi_bridge.py` | `AtspiBridge`, `TreeWalker`, `TreeSnapshot` | the bridge program run inside the session |
| `audit.py` | `Audit`, `CheckLog` | tools, capture 1:1, calibration, a11y |
| `log.py` | `ActionLog` | redacted, fail-open JSONL |
| `cli.py` | `Cli`, `ParserBuilder` | argv → service → JSON |

## Testing

```bash
python3 -m unittest                          # 340 hermetic checks, no desktop
CCC_LIVE=1 python3 -m unittest tests.test_live -v      # 12 service-level live checks
CCC_LIVE=1 python3 -m unittest tests.test_live_cli -v  # 26 CLI-level live checks
pyright                                      # static gate
```

The live batteries move the pointer and back, capture the screen, round-trip
the clipboard (text, primary and PNG), type into a scratch terminal it opens
and closes, run the audit, exercise AT-SPI, and drive **every verb through the
CLI as a subprocess**, restoring the cursor, clipboard and workspace
afterwards. A missing prerequisite is a SKIP with its reason, never a pass.
