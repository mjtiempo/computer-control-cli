# computer-control-cli

Drive a Hyprland desktop from the command line: read the desktop, act on
windows and workspaces, open apps, move the pointer and type — with refusals
that name a machine-readable code (`ERR[code]: message`) and actions that
re-read the desktop before claiming they worked.

Built test-first, in this order: **reads and info → workspace actions → apps →
pointer input → keyboard input.**

## Quick start

Nothing to install — run the checkout:

```bash
./computer-control state
./computer-control windows
./computer-control monitors
./computer-control system
./computer-control screen --out /tmp
```

Or through the package:

```bash
python3 -m computer_control_cli state
pip install -e . && computer-control state
```

## Verbs

| Group | Verbs |
| --- | --- |
| Desktop state | `state` `windows` `active` `workspaces` `monitors` |
| Screen | `screen [--out DIR] [--no-cursor]` `region X Y W H [--out DIR]` `active-screen` |
| System | `system` (cpu, memory, uptime, disk) · `clipboard get\|set TEXT` · `log [N]` |
| Windows / workspaces | `switch N` `focus SPEC` `close [SPEC]` `close-apps --kind K [--dry-run]` `move-window SPEC N` `wait-window SPEC [--gone] [--timeout S]` |
| Apps | `apps` `which NAME` `dir PATH [--create]` `app-open NAME [--workspace N] [--cwd DIR] [--dry-run]` `launch PROGRAM [ARGS…] [--no-wait]` |
| Pointer | `pos` `move X Y` `click [X Y] [--button L\|R\|M] [--double]` `scroll DIR AMOUNT [X Y]` |
| Keyboard | `key COMBO [--ydotool]` `type TEXT` |

Window targets are `class:`, `title:`, `address:0x…`, or a bare name. Several
matches refuse (`ERR[ambiguous-window]`); there is no silent first match.

## Contracts

* **Refusals** are `ERR[code]: message` on stderr with exit 1 (2 for a usage
  error), never a traceback for input a caller can produce.
* **Outcomes**: every action that changes the desktop answers with `ok`,
  `verified`, `effect` (`confirmed` / `unconfirmed` / `unverifiable`) and
  `rung`. `verified` means the effect was read back; pointer, keyboard and
  app-owned effects are honestly `unverifiable`.
* **Coordinates** are logical Hyprland pixels, 1:1 with screenshots
  (`grim -s 1`).

## Architecture

One class per responsibility; services receive their `Session` and `Config`
instead of reading globals. `Runner` and `Discoverer` are protocols, so the
whole stack runs against a double.

| Module | Class(es) | Responsibility |
| --- | --- | --- |
| `errors.py` | `ControlError` | the coded refusal |
| `outcome.py` | `Outcome` | `ok/verified/effect/rung`, invariants enforced |
| `coerce.py` | `Coerce` | safe parsing of foreign replies |
| `models.py` | `Monitor`, `Workspace`, `Window`, `WindowSpec` | domain rows |
| `config.py` | `Config` | env/paths, read once |
| `session.py` | `Session`, `ProcessRunner`, `SessionDiscoverer` | env discovery, hyprctl, dispatch, Lua quoting |
| `desktop.py` | `Desktop` | windows/workspaces/monitors + verified actions |
| `screen.py` | `Screen` | grim captures and pruning |
| `system.py` | `SystemInfo` | /proc facts and the clipboard |
| `apps.py` | `Apps`, `DesktopEntry` | resolution, launch, placement |
| `input.py` | `Input`, `Pointer`, `Keyboard`, `KeyMap`, `Combo` | pointer and key events |
| `log.py` | `ActionLog` | redacted, fail-open JSONL |
| `cli.py` | `Cli`, `ParserBuilder` | argv → service → JSON |

## Testing

```bash
python3 -m unittest                          # 186 hermetic checks, no desktop
CCC_LIVE=1 python3 -m unittest tests.test_live -v   # 9 live checks, real desktop
pyright                                      # static gate
```

The live battery moves the pointer and back, captures the screen, round-trips
the clipboard, and types into a scratch terminal it opens and closes. A
missing prerequisite is a SKIP with its reason, never a pass.
