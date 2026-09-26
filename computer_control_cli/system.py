"""System information and the clipboard.

Read-only facts about this machine (from ``/proc`` and the standard library)
plus wl-clipboard access. Nothing here touches the desktop, so every method
is a read or a plain command, and a failure is a coded refusal.
"""

from __future__ import annotations

import os
import shutil
import socket
import time
from typing import Any

from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports

CLIPBOARD_TIMEOUT = 5
CLIPBOARD_VERIFY_SECONDS = 1.5
CLIPBOARD_POLL_SECONDS = 0.05


class SystemInfo:
    """This machine: cpu, memory, uptime, disks — and the clipboard."""

    def __init__(self, session: Session, proc: str = "/proc"):
        self.session = session
        self.proc = proc

    # ---------------------------------------------------------------- reads
    def sysinfo(self) -> dict[str, Any]:
        uptime = self._proc("uptime").split()
        load = self._proc("loadavg").split()
        return {
            "hostname": self._hostname(),
            "kernel": self._kernel(),
            "cpu_count": os.cpu_count() or 1,
            "uptime_s": self._int(uptime[0] if uptime else 0),
            "load": {
                "1": self._float(load[0] if len(load) > 0 else 0),
                "5": self._float(load[1] if len(load) > 1 else 0),
                "15": self._float(load[2] if len(load) > 2 else 0),
            },
            "memory": self._meminfo(self._proc("meminfo")),
            "disk": self._disk("/"),
        }

    def _proc(self, name: str) -> str:
        try:
            with open(os.path.join(self.proc, name), encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return ""

    @staticmethod
    def _hostname() -> str:
        try:
            return socket.gethostname()
        except OSError:
            return ""

    @staticmethod
    def _kernel() -> str:
        try:
            return os.uname().release
        except OSError:
            return ""

    @classmethod
    def _meminfo(cls, text: str) -> dict[str, int]:
        fields = {"MemTotal": 0, "MemAvailable": 0}
        for line in text.splitlines():
            parts = line.split(":")
            if len(parts) == 2 and parts[0].strip() in fields:
                fields[parts[0].strip()] = cls._int(
                    parts[1].strip().split(" ")[0])
        return {"total_kb": fields["MemTotal"],
                "available_kb": fields["MemAvailable"]}

    @classmethod
    def _disk(cls, mount: str) -> dict[str, int]:
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            return {"total": 0, "used": 0, "free": 0}
        return {"total": int(usage.total), "used": int(usage.used),
                "free": int(usage.free)}

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _float(value: Any) -> float:
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------ clipboard
    def clipboard_get(self) -> str:
        proc = self.session.run(["wl-paste", "--no-newline"],
                                timeout=CLIPBOARD_TIMEOUT)
        if proc.returncode != 0:
            raise ControlError(
                "clipboard", f"wl-paste failed: {(proc.stderr or '').strip()}")
        return proc.stdout

    def clipboard_set(self, text: str) -> None:
        """Put ``text`` on the clipboard and wait until it is really there.

        ``wl-copy`` forks and keeps serving the selection, so the write is
        not done when the spawn returns: the clipboard is read back until it
        holds the text, and a write that never takes is a refusal. An empty
        string clears the selection rather than copying nothing (``wl-copy``
        refuses empty input, which made restoring an empty clipboard fail).
        """
        if text == "":
            self.session.run(["wl-copy", "--clear"],
                             timeout=CLIPBOARD_TIMEOUT)
            return
        self.session.spawn(["wl-copy"], input=text)
        deadline = time.time() + CLIPBOARD_VERIFY_SECONDS
        while time.time() < deadline:
            try:
                if self.clipboard_get() == text:
                    return
            except ControlError:
                pass
            time.sleep(CLIPBOARD_POLL_SECONDS)
        raise ControlError(
            "clipboard",
            "wl-copy did not take the selection within "
            f"{CLIPBOARD_VERIFY_SECONDS:g}s")
