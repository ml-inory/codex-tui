"""Session-level display overrides stored in a sidecar JSON file.

Codex owns the session transcripts under ``$CODEX_HOME/sessions``. This module
keeps user-level display metadata (renamed titles, chosen models) in a separate
file so codex's own files are never modified.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from codex_tui.sessions import Session


DEFAULT_CODEX_TUI_HOME = Path.home() / ".codex-tui"
DEFAULT_OVERRIDES_FILE = "overrides.json"


def _overrides_path(path: Path | None) -> Path:
    if path is not None:
        return path
    home = Path(os.environ.get("CODEX_TUI_HOME", str(DEFAULT_CODEX_TUI_HOME)))
    return home / DEFAULT_OVERRIDES_FILE


@dataclass
class Overrides:
    """Loads and persists per-session display overrides."""

    path: Path
    data: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Overrides":
        overrides = cls(path=_overrides_path(path))
        if not overrides.path.is_file():
            return overrides
        try:
            raw = json.loads(overrides.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return overrides
        if not isinstance(raw, dict):
            return overrides
        for session_id, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            overrides.data[str(session_id)] = {
                str(key): str(value) for key, value in entry.items()
            }
        return overrides

    def get(self, session_id: str, key: str) -> str | None:
        entry = self.data.get(session_id)
        if entry is None:
            return None
        return entry.get(key)

    def set(self, session_id: str, key: str, value: str) -> None:
        self.data.setdefault(session_id, {})[key] = value
        self.save()

    def delete(self, session_id: str, key: str) -> None:
        """Remove one override key (e.g. clearing a model choice)."""
        entry = self.data.get(session_id)
        if entry is not None and key in entry:
            del entry[key]
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def apply(self, session: Session) -> Session:
        """Copy this session's overrides onto a parsed :class:`Session`."""
        session.title_override = self.get(session.id, "title")
        session.model_override = self.get(session.id, "model")
        return session
