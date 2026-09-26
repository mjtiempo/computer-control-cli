"""The action log: one redacted JSONL line per action, fail-open.

A log write must never fail the action it describes, so every filesystem
error is swallowed. Typed text is recorded as a length plus a short preview
unless ``CCC_LOG_TEXT=full``; ``off`` drops it entirely.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from computer_control_cli.config import Config  # pi-lens-ignore: reportMissingImports

PREVIEW_CHARS = 8


class ActionLog:
    """Append-only JSONL history of what ran, and how it ended."""

    def __init__(self, config: Optional[Config] = None):
        self._config = config or Config()

    def path(self) -> str:
        return self._config.log_path()

    def write(self, action: str, ok: bool = True,
              args: Optional[list] = None, code: Optional[str] = None,
              detail: Optional[str] = None, text: Optional[str] = None) -> None:
        if not self._config.log_enabled():
            return
        entry: dict[str, Any] = {"ts": self.timestamp(), "action": action,
                                 "ok": bool(ok)}
        if args is not None:
            entry["args"] = list(args)
        if code:
            entry["code"] = code
        if detail:
            entry["detail"] = detail
        entry.update(self.text_fields(text))
        try:
            directory = os.path.dirname(self.path())
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(self.path(), "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, separators=(",", ":")) + "\n")
        except OSError:
            pass  # fail-open: the log must never fail the action

    def text_fields(self, text: Optional[str]) -> dict[str, Any]:
        if text is None:
            return {}
        mode = self._config.log_text()
        if mode == "off":
            return {}
        if mode == "full":
            return {"text": text}
        return {"text_chars": len(text), "text_preview": text[:PREVIEW_CHARS]}

    def entries(self, count: int = 50,
                since: Optional[str] = None) -> list[dict]:
        """The last ``count`` entries, oldest first; bad lines are skipped.

        ``since`` keeps entries whose ISO timestamp sorts at or after it.
        """
        try:
            with open(self.path(), encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            return []
        entries = []
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            if since and str(row.get("ts", "")) < str(since):
                continue
            entries.append(row)
        return entries[-max(1, int(count)):]

    @staticmethod
    def render(entries: list[dict]) -> list[str]:
        """One human line per entry, for a terminal reading."""
        rendered = []
        for entry in entries:
            status = "ok" if entry.get("ok") else "fail"
            code = f" {entry.get('code')}" if entry.get("code") else ""
            detail = f" {entry.get('detail')}" if entry.get("detail") else ""
            rendered.append(f"{entry.get('ts', '')} "
                            f"{entry.get('action', '')} {status}{code}{detail}")
        return rendered

    @staticmethod
    def timestamp() -> str:
        import datetime
        return datetime.datetime.now().astimezone().isoformat(
            timespec="milliseconds")
