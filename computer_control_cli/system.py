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
from typing import Any, Union

from computer_control_cli.errors import ControlError  # pi-lens-ignore: reportMissingImports
from computer_control_cli.session import Session  # pi-lens-ignore: reportMissingImports

CLIPBOARD_TIMEOUT = 5
CLIPBOARD_VERIFY_SECONDS = 1.5
CLIPBOARD_POLL_SECONDS = 0.05


class SystemInfo:
    """This machine: cpu, memory, uptime, disks — and the clipboard."""

    def __init__(self, session: Session, proc: str = "/proc",
                 sys: str = "/sys",
                 verify_seconds: float = CLIPBOARD_VERIFY_SECONDS):
        self.session = session
        self.proc = proc
        self.sys = sys
        self.verify_seconds = verify_seconds

    # ---------------------------------------------------------------- reads
    def sysinfo(self) -> dict[str, Any]:
        uptime = self._proc("uptime").split()
        load = self._proc("loadavg").split()
        return {
            "hostname": self._hostname(),
            "kernel": self._kernel(),
            "cpu_count": os.cpu_count() or 1,
            "cpu_model": self._cpu_model(),
            "uptime_s": self._int(uptime[0] if uptime else 0),
            "processes": self._process_count(),
            "load": {
                "1": self._float(load[0] if len(load) > 0 else 0),
                "5": self._float(load[1] if len(load) > 1 else 0),
                "15": self._float(load[2] if len(load) > 2 else 0),
            },
            "memory": self._meminfo(self._proc("meminfo")),
            "swap": self._swap(self._proc("meminfo")),
            "disk": self._disk("/"),
            "network": self._network(),
            "battery": self._battery(),
            "thermal": self._thermal(),
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
    def _swap(cls, text: str) -> dict[str, int]:
        fields = {"SwapTotal": 0, "SwapFree": 0}
        for line in text.splitlines():
            parts = line.split(":")
            if len(parts) == 2 and parts[0].strip() in fields:
                fields[parts[0].strip()] = cls._int(
                    parts[1].strip().split(" ")[0])
        return {"total_kb": fields["SwapTotal"], "free_kb": fields["SwapFree"]}

    def _cpu_model(self) -> str:
        for line in self._proc("cpuinfo").splitlines():
            if ":" in line and line.split(":", 1)[0].strip() == "model name":
                return line.split(":", 1)[1].strip()
        return ""

    def _process_count(self) -> int:
        try:
            return sum(1 for name in os.listdir(self.proc) if name.isdigit())
        except OSError:
            return 0

    def _sys_file(self, *parts: str) -> str:
        try:
            with open(os.path.join(self.sys, *parts), encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def _network(self) -> list[dict]:
        """Interfaces with their state and the byte counters /proc/net/dev."""
        counters: dict[str, tuple] = {}
        for line in self._proc("net/dev").splitlines():
            name, _, rest = line.partition(":")
            name = name.strip()
            fields = rest.split()
            if name and len(fields) >= 16:
                counters[name] = (self._int(fields[0]), self._int(fields[8]))
        rows = []
        try:
            names = sorted(os.listdir(os.path.join(self.sys, "class", "net")))
        except OSError:
            return []
        for name in names:
            rx, tx = counters.get(name, (0, 0))
            rows.append({"name": name,
                         "state": self._sys_file("class", "net", name,
                                                 "operstate") or "unknown",
                         "rx_bytes": rx, "tx_bytes": tx})
        return rows

    def _battery(self) -> list[dict]:
        return [{"name": os.path.basename(path), "capacity": self._int(
                    self._read_abs(os.path.join(path, "capacity"))),
                 "status": self._read_abs(os.path.join(path, "status"))}
                for path in self._glob(self.sys, "class", "power_supply", "BAT*")]

    def _thermal(self) -> list[dict]:
        rows = []
        for path in self._glob(self.sys, "class", "thermal", "thermal_zone*"):
            raw = self._read_abs(os.path.join(path, "temp"))
            if raw:
                rows.append({"zone": os.path.basename(path),
                             "temp_c": round(self._float(raw) / 1000.0, 1)})
        return rows

    @staticmethod
    def _glob(*parts: str) -> list[str]:
        import glob
        return sorted(glob.glob(os.path.join(*parts)))

    @staticmethod
    def _read_abs(path: str) -> str:
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""

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
    def clipboard_types(self, primary: bool = False) -> list[str]:
        """The MIME types the current selection offers (empty when none)."""
        argv = (["wl-paste"] + (["--primary"] if primary else [])
                + ["--list-types"])
        proc = self.session.run(argv, timeout=CLIPBOARD_TIMEOUT)
        if proc.returncode != 0:
            if self._empty_selection(proc.stderr or ""):
                return []
            raise ControlError(
                "clipboard",
                f"wl-paste --list-types failed: "
                f"{(proc.stderr or '').strip()}")
        return [line.strip() for line in (proc.stdout or "").splitlines()
                if line.strip()]

    def clipboard_get(self, primary: bool = False, mime: str = "",
                      binary: bool = False) -> Union[str, bytes]:
        """Read the selection: text by default, raw bytes for a ``mime``.

        An empty selection is empty data, not a failure: ``wl-paste`` exits 1
        with "Nothing is copied", which is the clipboard saying it holds
        nothing.
        """
        argv = ["wl-paste"] + (["--primary"] if primary else [])
        if mime:
            argv += ["--type", str(mime)]
        elif not binary:
            argv += ["--no-newline"]
        proc = self.session.run(argv, timeout=CLIPBOARD_TIMEOUT,
                                binary=binary)
        if proc.returncode != 0:
            if self._empty_selection(proc.stderr or ""):
                return b"" if binary else ""
            raise ControlError(
                "clipboard", f"wl-paste failed: {(proc.stderr or '').strip()}")
        return proc.stdout

    @staticmethod
    def _empty_selection(stderr: str) -> bool:
        return str(stderr).lower().strip().startswith("nothing is copied")

    def clipboard_set(self, data: Union[str, bytes], primary: bool = False,
                      mime: str = "") -> None:
        """Put text or bytes on a selection, and wait until it is really there.

        ``wl-copy`` forks and keeps serving the selection, so the write is not
        done when the spawn returns: the selection is read back until it holds
        the data, and a write that never takes is a refusal. Bytes are sent
        raw (with ``--type``) so images survive; an empty string clears the
        selection rather than copying nothing.
        """
        if data == "" or data == b"":
            self.clipboard_clear(primary)
            return
        binary = isinstance(data, bytes)
        argv = (["wl-copy"] + (["--primary"] if primary else [])
                + (["--type", str(mime)] if mime else []))
        self.session.spawn(argv, input=data, binary=binary)
        expected = data if binary else str(data).encode()
        deadline = time.time() + max(0.0, float(self.verify_seconds))
        while time.time() < deadline:
            try:
                if mime:
                    got: Union[str, bytes] = self.clipboard_get(
                        primary=primary, mime=mime, binary=True)
                else:
                    got = self.clipboard_get(primary=primary)
                if isinstance(got, str):
                    got = got.encode()
                if got == expected:
                    return
            except ControlError:
                pass
            time.sleep(CLIPBOARD_POLL_SECONDS)
        raise ControlError(
            "clipboard",
            "wl-copy did not take the selection within "
            f"{self.verify_seconds:g}s")

    def clipboard_clear(self, primary: bool = False) -> None:
        argv = ["wl-copy"] + (["--primary"] if primary else []) + ["--clear"]
        proc = self.session.run(argv, timeout=CLIPBOARD_TIMEOUT)
        if proc.returncode != 0:
            raise ControlError(
                "clipboard", f"wl-copy --clear failed: "
                f"{(proc.stderr or '').strip()}")
