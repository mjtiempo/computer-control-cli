"""The action log: one redacted JSONL line per action, fail-open.

A log write must never fail the action it describes, so every filesystem
error is swallowed. Typed text is recorded as a length plus a short preview
unless ``CCC_LOG_TEXT=full``; ``off`` drops it entirely.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Iterator, Optional

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
        return self._read_tail(count, since)[0]

    @staticmethod
    def _parse(line: str) -> Optional[dict]:
        try:
            row = json.loads(line)
        except ValueError:
            return None
        return row if isinstance(row, dict) else None

    def _read_tail(self, count: int,
                   since: Optional[str] = None) -> tuple[list[dict], int]:
        """(the last entries, the byte offset after reading them).

        One read answers both: a follower must start where this read ended,
        or an entry appended in between is lost.
        """
        try:
            with open(self.path(), "rb") as handle:
                data = handle.read()
                position = handle.tell()
        except OSError:
            return [], 0
        entries = []
        for line in data.decode("utf-8", errors="replace").splitlines():
            row = self._parse(line)
            if row is None:
                continue
            if since and str(row.get("ts", "")) < str(since):
                continue
            entries.append(row)
        return entries[-max(1, int(count)):], position

    def follow(self, count: int = 10, since: Optional[str] = None,
               poll: float = 0.25) -> Iterator[dict]:
        """Yield the last ``count`` entries, then every line appended after.

        The generator ends when its consumer stops iterating (Ctrl+C at the
        CLI). Truncation or rotation resets it to the start of the file.
        """
        backlog, position = self._read_tail(count, since)
        pending = ""
        for entry in backlog:
            yield entry
        while True:
            try:
                size = os.path.getsize(self.path())
            except OSError:
                time.sleep(poll)
                continue
            if size < position:
                position, pending = 0, ""
            if size > position:
                try:
                    with open(self.path(), "rb") as handle:
                        handle.seek(position)
                        chunk = handle.read()
                        position = handle.tell()
                except OSError:
                    time.sleep(poll)
                    continue
                pending += chunk.decode("utf-8", errors="replace")
                while "\n" in pending:
                    line, _, pending = pending.partition("\n")
                    row = self._parse(line)
                    if row is not None:
                        yield row
                continue
            time.sleep(poll)

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
