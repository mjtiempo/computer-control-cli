# AGENTS.md — how to work in computer-control-cli

## What this is

A CLI that drives a Hyprland desktop: reads and info first, then workspace and
window actions, apps, pointer input, keyboard input, AT-SPI element control,
and a machine audit. The user-facing contract is the verb table in
`README.md` plus the `ERR[code]` / `Outcome` contracts.

## Order of work (do not reorder)

1. reads and info (`state`, `windows`, `workspaces`, `monitors`, `screen`,
   `system`, `pos`, `log`)
2. workspace and window actions (`switch`, `focus`, `close`, `close-apps`,
   `move-window`, `wait-window`)
3. apps (`apps`, `which`, `dir`, `app-open`, `launch`)
4. pointer (`move`, `click`, `scroll`, `drag`)
5. keyboard (`key`, `key-down`, `key-up`, `type`)
6. AT-SPI (`a11y`, `a11y-enable`, `click --el`) and `audit`

## Test-driven development

* Write the test module first, run it (red), then implement (green), then keep
  both passing. Every new behavior gets a check in the same change.
* Hermetic tests drive the whole stack through `Session` injection: the
  doubles live in `tests/fakes.py` (`FakeRunner`, `FakeHyprland`,
  `FakeInputRunner`, `FakeDiscoverer`). Never mock at a lower seam than
  `Runner` — the cursor model in `FakeInputRunner` is what makes warp
  verification, drag, and the audit's calibration check testable.
* The live batter is opt-in: `CCC_LIVE=1 python3 -m unittest tests.test_live
  tests.test_live_cli`. `tests/test_live.py` drives the services;
  `tests/test_live_cli.py` drives every verb through the CLI as a subprocess
  and is the one to extend when a verb is added. Both may move the pointer,
  open scratch windows and use the clipboard, and both restore/clean up in
  `finally`/`tearDownClass`.
* `tests/test_skill.py` is a drift gate: every CLI verb and every refusal code
  the package can raise must appear in `skill/computer-control/SKILL.md`.
  A new verb or code without a skill line fails the suite.

## Object-oriented rules

* No module-level functions. A behavior belongs to the class that owns it:
  parsing to the model (`WindowSpec.parse`), coercion to `Coerce`, quoting to
  `Session.lua_string`, argv to `ParserBuilder`, checks to `Audit`.
* The one exception is `atspi_bridge.py`: it IS a program (run as
  `python3 atspi_bridge.py index|click`), so it ends in the
  `if __name__ == "__main__"` guard, like `__main__.py`. It must import
  PyGObject lazily so the module itself stays importable for tests.
* Services are constructed once in `Cli.build_services` and injected for
  tests; nothing reads `os.environ` outside `Config` / `SessionDiscoverer`.
* Prefer a small class to a bag of related free functions (`KeyMap` holds the
  key tables; `Pointer` and `Keyboard` hold the device calls; `CheckLog`
  holds the audit rows).
* A one-off function is allowed only when it belongs to no class and is used
  once — there are none today, keep it that way.

## Gates (authoritative)

```bash
python3 -m unittest          # hermetic, all green
pyright                      # 0 errors (pyrightconfig.json, repo venv)
CCC_LIVE=1 python3 -m unittest tests.test_live -v
./install.sh --check         # the installed link is this repo
```

`python3 -m unittest` and `pyright` are the real import gates. The pi-lens
fallback index cannot resolve this package from a lone file, so intra-package
imports carry `# pi-lens-ignore: reportMissingImports`; that comment is inert
for pyright and exists only to keep the editor quiet.

## Conventions

* Effects are read back. Hyprland answers `ok` to dispatches it did not apply,
  so `Desktop` re-reads before reporting `confirmed` and refuses with
  `*-not-verified` otherwise.
* `--dry-run` acts at the CLI boundary: a mutating verb returns a plan without
  calling its service (a verb with a native `--dry-run`, like `close-apps`,
  produces its own richer plan); reads still read.
* `--quiet` suppresses stdout only; refusals stay on stderr and exit codes do
  not change. A capture under `--quiet` needs `--out`.
* Global `--timeout` is the default window-wait timeout; the verb's own
  `--timeout` wins. The global flag uses its own argparse dest
  (`timeout_default`), because a subparser's default for the same dest
  silently overwrites the parent's value.
* `--clearmodifiers` releases every modifier through ydotool before an input
  action: wtype releases what it pressed when the process exits, so only
  ydotool's state can be held (or cleared) across calls.
* Ambiguity refuses; name the candidates.
* A failure a caller can produce is a `ControlError` with an actionable code,
  never a traceback. A child program's `ERR[code]` line is re-raised verbatim
  (`ControlError.from_line`), so the caller branches on the same code.
* `ActionLog` redacts typed text and clipboard content (length + preview) and
  never fails the action it describes.
* AT-SPI coverage is uneven (Chromium needs `--force-renderer-accessibility`;
  terminals often expose nothing). An empty `a11y` list is a true answer, not
  a bug; a live test that depends on rows SKIPs with that reason.
