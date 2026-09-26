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
| System | `system` (cpu, model, memory, swap, disk, network, battery, thermal) · `clipboard get\|set\|clear [--primary]` · `log [N] [--since TS] [--text]` |
| Windows / workspaces | `switch N` `focus SPEC` `close [SPEC]` `close-apps --kind K [--dry-run]` `move-window SPEC N` `wait-window [SPEC] [--gone] [--new] [--exclude A,B] [--title S] [--class S] [--stable N] [--timeout S]` |
| Apps | `apps` (entries + running processes) `which NAME` `dir PATH [--create]` `app-open NAME [--workspace N] [--cwd DIR] [--dry-run]` `launch [--workspace N] [--no-wait] PROGRAM [ARGS…]` |
| Pointer | `pos` `move X Y` `click [X Y] [--button L\|R\|M] [--double]` `scroll DIR AMOUNT [X Y]` `drag X1 Y1 X2 Y2 [--button B] [--duration MS]` |
| Keyboard | `key COMBO [--ydotool]` `key-down COMBO` `key-up COMBO` `type TEXT [--delay MS]` |
| AT-SPI | `a11y [--app S] [--max N]` `a11y-enable` `click --el N` |
| Audit | `audit [--no-probe] [--no-a11y]` |
| Batch | `sequence '[["pos"],["move","100","200"]]'` |

Global flags: `--version`, `--instance SIGNATURE`, `--display NAME` (target
another Hyprland instance or Wayland display).

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
python3 -m unittest                          # 282 hermetic checks, no desktop
CCC_LIVE=1 python3 -m unittest tests.test_live -v   # 12 live checks
pyright                                      # static gate
```

The live battery moves the pointer and back, captures the screen, round-trips
the clipboard, types into a scratch terminal it opens and closes, runs the
audit, and exercises AT-SPI. A missing prerequisite is a SKIP with its
reason, never a pass.
