"""The action log: one redacted JSONL line per action, fail-open.

A log write must never fail the action it describes, so a write swallows
every filesystem error. Typed text is recorded as a length plus a short
preview unless ``CCC_LOG_TEXT=full``; ``off`` drops it entirely. Reading
back is allowed to refuse: a follower that cannot read a log that exists
says so rather than polling it forever.
"""

from __future__ import annotations

import datetime
import json
import os
import time
from typing import Any, Iterator, Optional

from computer_control_cli.config import Config  # pi-lens-ignore: reportMissingImports
from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports

PREVIEW_CHARS = 8
# Reads that find the log but cannot open it: refuse rather than poll forever.
UNREADABLE_TRIES = 3


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
                   since: Optional[str] = None
                   ) -> tuple[list[dict], int, str, Optional[int]]:
        """(the last entries, the byte offset after them, a half-written tail,
        the inode that offset belongs to).

        One read answers all four: a follower must start where this read
        ended, or an entry appended in between is lost, and it must know
        which file it read or a replacement file looks like an append. The
        tail is a line the writer has not finished — it belongs to
        ``follow``'s buffer and not to the backlog, or finishing that line
        would emit only its second half.
        """
        try:
            with open(self.path(), "rb") as handle:
                data = handle.read()
                position = handle.tell()
                identity = os.fstat(handle.fileno()).st_ino
        except OSError:
            return [], 0, "", None
        cut = data.rfind(b"\n") + 1      # 0 when no line is terminated yet
        entries = []
        for line in data[:cut].decode("utf-8", errors="replace").splitlines():
            row = self._parse(line)
            if row is not None and self._kept(row, since):
                entries.append(row)
        pending = data[cut:].decode("utf-8", errors="replace")
        if pending:
            row = self._parse(pending)
            if row is not None and self._kept(row, since):
                entries.append(row)      # complete JSON that lacks its newline
                pending = ""
        count = max(0, int(count))
        rows = entries[-count:] if count else []
        return rows, position, pending, identity

    @staticmethod
    def _kept(row: dict, since: Optional[str]) -> bool:
        """Does ``since`` keep this entry?

        Timestamps carry a local UTC offset, so they are compared as times:
        across a DST change the text order is the wrong order.
        """
        stamp = str(row.get("ts", ""))
        if not since:
            return True
        try:
            return (datetime.datetime.fromisoformat(stamp)
                    >= datetime.datetime.fromisoformat(str(since)))
        except ValueError:
            return stamp >= str(since)

    def follow(self, count: int = 10, since: Optional[str] = None,
               poll: float = 0.25) -> Iterator[dict]:
        """Yield the last ``count`` entries, then every line appended after.

        The generator ends when its consumer stops iterating (Ctrl+C at the
        CLI). A replacement file (rotation) restarts it at the top and a
        truncation at the start; ``since`` keeps filtering appended entries;
        a log that exists but stays unreadable refuses instead of polling
        forever.
        """
        backlog, position, pending, identity = self._read_tail(count, since)
        for entry in backlog:
            yield entry
        failures = 0
        while True:
            try:
                info = os.stat(self.path())
            except OSError:
                time.sleep(poll)
                continue
            if info.st_ino != identity or info.st_size < position:
                identity, position, pending = info.st_ino, 0, ""
            if info.st_size <= position:
                time.sleep(poll)
                continue
            try:
                with open(self.path(), "rb") as handle:
                    handle.seek(position)
                    chunk = handle.read()
                    position = handle.tell()
            except OSError as error:
                failures += 1
                if failures >= UNREADABLE_TRIES:
                    raise ControlError("no-file",
                                       f"log: cannot read {self.path()!r}: "
                                       f"{error}")
                time.sleep(poll)
                continue
            failures = 0
            pending += chunk.decode("utf-8", errors="replace")
            while "\n" in pending:
                line, _, pending = pending.partition("\n")
                row = self._parse(line)
                if row is not None and self._kept(row, since):
                    yield row

    @staticmethod
    def render(entries: list[dict]) -> list[str]:
        """One human line per entry, for a terminal reading.

        Control characters are escaped: a line is data about an action,
        never an instruction to the terminal reading it.
        """
        rendered = []
        for entry in entries:
            status = "ok" if entry.get("ok") else "fail"
            code = f" {entry.get('code')}" if entry.get("code") else ""
            detail = f" {entry.get('detail')}" if entry.get("detail") else ""
            rendered.append(ActionLog.escape(
                f"{entry.get('ts', '')} {entry.get('action', '')} "
                f"{status}{code}{detail}"))
        return rendered

    @staticmethod
    def escape(text: str) -> str:
        """``\\xNN`` for every character a terminal would act on."""
        return "".join(char if char >= " " and char != "\x7f"
                       else f"\\x{ord(char):02x}" for char in str(text))

    @staticmethod
    def timestamp() -> str:
        return datetime.datetime.now().astimezone().isoformat(
            timespec="milliseconds")
