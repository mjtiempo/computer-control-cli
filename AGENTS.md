# AGENTS.md — how to work in computer-control-cli

## What this is

A CLI that drives a Hyprland desktop: reads and info first, then workspace and
window actions, apps, pointer input, keyboard input. The user-facing contract
is the verb table in `README.md` plus the `ERR[code]` / `Outcome` contracts.

## Order of work (do not reorder)

1. reads and info (`state`, `windows`, `workspaces`, `monitors`, `screen`,
   `system`, `pos`)
2. workspace and window actions (`switch`, `focus`, `close`, `close-apps`,
   `move-window`, `wait-window`)
3. apps (`apps`, `which`, `dir`, `app-open`, `launch`)
4. pointer (`move`, `click`, `scroll`)
5. keyboard (`key`, `type`)

## Test-driven development

* Write the test module first, run it (red), then implement (green), then keep
  both passing. Every new behavior gets a check in the same change.
* Hermetic tests drive the whole stack through `Session` injection: the
  doubles live in `tests/fakes.py` (`FakeRunner`, `FakeHyprland`,
  `FakeInputRunner`, `FakeDiscoverer`). Never mock at a lower seam than
  `Runner` — the cursor model in `FakeInputRunner` is what makes warp
  verification testable.
* The live battery is opt-in: `CCC_LIVE=1 python3 -m unittest tests.test_live`.
  It is allowed to move the pointer, open a scratch window and use the
  clipboard, and it must restore/clean up in `finally`.

## Object-oriented rules

* No module-level functions. A behavior belongs to the class that owns it:
  parsing to the model (`WindowSpec.parse`), coercion to `Coerce`, quoting to
  `Session.lua_string`, argv to `ParserBuilder`.
* Services are constructed once in `Cli` and injected for tests; nothing reads
  `os.environ` outside `Config` / `SessionDiscoverer`.
* Prefer a small class to a bag of related free functions (`KeyMap` holds the
  key tables; `Pointer` and `Keyboard` hold the device calls).
* A one-off function is allowed only when it belongs to no class and is used
  once — there are none today, keep it that way.

## Gates (authoritative)

```bash
python3 -m unittest          # hermetic, all green
pyright                      # 0 errors (pyrightconfig.json, repo venv)
CCC_LIVE=1 python3 -m unittest tests.test_live -v
```

`python -m py_compile` and `python3 -m unittest` are the real import gates. The
pi-lens fallback index cannot resolve this package from a lone file, so
intra-package imports carry `# pi-lens-ignore: reportMissingImports`; that
comment is inert for pyright and exists only to keep the editor quiet.

## Conventions

* Effects are read back. Hyprland answers `ok` to dispatches it did not apply,
  so `Desktop` re-reads before reporting `confirmed` and refuses with
  `*-not-verified` otherwise.
* Ambiguity refuses; name the candidates.
* A failure a caller can produce is a `ControlError` with an actionable code,
  never a traceback.
* `ActionLog` redacts typed text and clipboard content (length + preview) and
  never fails the action it describes.
