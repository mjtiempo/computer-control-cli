"""Resolved configuration: the one place that reads the environment."""

from __future__ import annotations

import os
from typing import Mapping, Optional

LOG_TEXT_MODES = ("off", "preview", "full")


class Config:
    """Paths and switches a service needs, resolved once at construction."""

    def __init__(self, env: Optional[Mapping[str, str]] = None):
        self._env = dict(os.environ if env is None else env)

    def env(self, name: str, default: str = "") -> str:
        return self._env.get(name, default)

    def state_dir(self) -> str:
        base = self._env.get("XDG_STATE_HOME")
        if not base:
            base = os.path.join(self._env.get("HOME", os.path.expanduser("~")),
                                ".local", "state")
        return os.path.join(base, "computer-control")

    def log_path(self) -> str:
        override = self._env.get("CCC_LOG")
        if override:
            return override
        return os.path.join(self.state_dir(), "actions.jsonl")

    def log_enabled(self) -> bool:
        """``CCC_LOG=off`` turns the action log off entirely."""
        return self._env.get("CCC_LOG", "").strip().lower() != "off"

    def log_text(self) -> str:
        """How typed text is recorded: ``off``, ``preview`` (default) or ``full``."""
        mode = self._env.get("CCC_LOG_TEXT", "").lower()
        return mode if mode in LOG_TEXT_MODES else "preview"
