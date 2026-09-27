---
name: Computer Control — AT-SPI Elements
description: Drive accessibility elements with the computer-control CLI — a11y tree dumps, a11y-enable, click --el N, element identity, stale-tree and owner checks. Use when a task should act on a named control inside an app (a button, menu item, list row, text field) without pixel coordinates, or when the desktop's accessibility tree is the reliable way to read what an application exposes.
---

# AT-SPI elements

Load the `computer-control` skill first: shared contract, global flags before
the verb, refusal table.

AT-SPI gives element-level control with **no coordinates**: dump the tree,
pick a row, tell the CLI to act on it.

## Verbs

```bash
computer-control a11y                          # JSON rows for every app the session exposes
computer-control a11y --app nautilus --max 50  # filter by application substring, cap the row count
computer-control a11y-enable                   # turn the session's AT-SPI flag on, read it back
computer-control click --el 17                 # act on row 17 (right-click: --button R)
```

A row looks like:

```json
{"index":17,"app":"org.gnome.Nautilus","role":"push button",
 "name":"Open","value":null,"enabled":true,"selected":false,
 "actions":["click"],"frame":[12,36,96,28]}
```

- `index` is the position in the **full** walk, and it is what `--el` takes —
  even when `--max` or `--app` hid some rows from the dump you read.
- `actions` is what the element can do; `frame` is its rect **relative to its
  window** (Wayland AT-SPI extents are window-relative; the CLI adds the
  focused window's origin itself).
- `[]` is a **true answer**: nothing on this desktop exposes a tree. It is
  not a failure and not a reason to retry.

## Coverage — read this before debugging an empty list

- GTK/Qt apps expose trees reliably.
- Chromium/Chrome/Electron need `--force-renderer-accessibility` on the app
  itself; without it they may expose nothing useful.
- Terminals often expose nothing at all.
- `a11y-enable` flips the session's `org.a11y.Status.IsEnabled` property and
  reads it back (`confirmed`); some apps only start exposing a tree after it
  is on, and usually only for apps launched afterwards.

So: an empty `a11y` list means "no app here publishes a tree", and the right
move is to fall back to pixels (`computer-control-screen` +
`computer-control-input`), not to hammer the bridge.

## How `click --el N` decides

1. A **fresh** walk re-locates the row by identity `(role, name, actions)` —
   the dump you read may be stale, and the index alone is not trusted.
   Several nodes sharing that identity → `ambiguous-element` (re-pick, or
   click by pixels).
2. The element is gone from the fresh walk → `stale-tree` (re-dump with
   `a11y`, re-pick — the desktop changed under you).
3. It has an AT-SPI action → `doAction`, `rung: "ax"`, `effect:
   "unverifiable"`. A failed action is `atspi`.
4. No action → **pixel fallback**: the element's `frame` centre plus the
   focused window's origin, warped to and clicked (`rung: "px"`,
   `route: "px-fallback"`). Before clicking, the CLI checks that the element's
   application plausibly owns the **focused** window; if not, it refuses
   `element-owner-mismatch` rather than aiming one window's coordinates at
   another. Fix by focusing the right window (`focus SPEC`) or by clicking by
   pixels yourself.

`click --el` does not take `--double` (AT-SPI has no double-click action) —
that combination is `bad-args`; double-click by pixels instead.

## Recipe: act on a named control

```bash
computer-control focus class:nautilus              # right window focused (avoids element-owner-mismatch)
computer-control a11y --app nautilus --max 80      # find the row: read role/name/value/actions
computer-control click --el 17                     # act on it
computer-control active-screen --window class:nautilus --out /tmp/shots   # verify by looking
```

Re-dump after anything that changes the UI: a new dialog, a filtered list, a
page navigation. Identities are re-resolved on every click, so a stale dump
surfaces as `stale-tree` (or `ambiguous-element`), never as a click on the
wrong control.

## Refusals you will see here

`atspi` (the bridge walk/action failed, or it returned no JSON) ·
`stale-tree` (dumped element is gone: re-dump) · `ambiguous-element`
(several nodes share the identity) · `element-owner-mismatch` (the element
belongs to another app than the focused window) · `bad-args` (e.g. `--el`
with coordinates, or `--el` with `--double`).

An empty list is not one of them: `[]` with exit 0 is the honest answer for
a desktop that exposes no tree.
