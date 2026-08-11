"""App-level display settings persisted under the codex-tui home dir."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_CODEX_TUI_HOME = Path.home() / ".codex-tui"
DEFAULT_SETTINGS_FILE = "settings.json"


def _settings_path(path: Path | None) -> Path:
    if path is not None:
        return path
    home = Path(os.environ.get("CODEX_TUI_HOME", str(DEFAULT_CODEX_TUI_HOME)))
    return home / DEFAULT_SETTINGS_FILE


@dataclass
class AppSettings:
    """Small JSON file for non-session UI preferences."""

    path: Path
    project_mode: str = "short"  # "short" shows the deepest dir, "full" the path
    sidebar_visible: bool = True
    # Directories added by the user that have no sessions yet (e.g. a fresh
    # clone). Merged into the project list so they are selectable/workable.
    known_projects: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppSettings":
        settings = cls(path=_settings_path(path))
        if not settings.path.is_file():
            return settings
        try:
            raw = json.loads(settings.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return settings
        if isinstance(raw, dict):
            mode = raw.get("project_mode")
            if mode in ("short", "full"):
                settings.project_mode = mode
            visible = raw.get("sidebar_visible")
            if isinstance(visible, bool):
                settings.sidebar_visible = visible
            known = raw.get("known_projects")
            if isinstance(known, list):
                settings.known_projects = [
                    project
                    for project in known
                    if isinstance(project, str) and project
                ]
        return settings

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {
                    "project_mode": self.project_mode,
                    "sidebar_visible": self.sidebar_visible,
                    "known_projects": self.known_projects,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
