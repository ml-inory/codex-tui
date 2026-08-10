"""Read and parse Codex session transcripts from ``$CODEX_HOME/sessions``.

The Codex CLI persists one JSONL file per conversation under
``~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl``. Each line is
an event: ``session_meta``, ``turn_context``, ``response_item``, ``event_msg``,
or ``compacted``. This module models that data and tolerates unknown event
types so newer CLI versions do not break the UI.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CODEX_HOME = Path.home() / ".codex"
DEFAULT_CODEX_TUI_HOME = Path.home() / ".codex-tui"
TEXT_ITEM_TYPES = ("input_text", "output_text", "text")

# System-injected user messages that carry no user intent.
INJECTED_PREFIXES: tuple[str, ...] = (
    "<environment_context",
    "<permissions instructions",
    "<collaboration_mode",
    "<skills_instructions",
    "<multi_agent_mode",
    "<turn_aborted",
    "<skill",
    "<codex_internal_context",
    "<system",
    "# AGENTS.md",
)

IMAGE_TAG_RE = re.compile(r"<image[^>]*>(?:</image>)?")


@dataclass
class Message:
    """One user or assistant turn in a conversation."""

    role: str
    content: str
    item_id: str | None = None


@dataclass
class Session:
    """A single Codex conversation persisted as a JSONL transcript."""

    id: str
    path: Path
    timestamp: str = ""
    cwd: str = ""
    model: str | None = None
    messages: list[Message] = field(default_factory=list)
    title_override: str | None = None
    model_override: str | None = None

    @property
    def project(self) -> str:
        """Working directory this session belongs to, used as the project key."""
        return self.cwd or "?"

    @property
    def effective_model(self) -> str | None:
        """Model used for display/runner: per-session override or parsed model."""
        return self.model_override or self.model

    @property
    def title(self) -> str:
        """Short human-readable title: override, first real question, or id."""
        if self.title_override:
            return self.title_override
        generated = generate_title(self.messages)
        if generated:
            return generated
        return f"Session {self.id[:8]}"


def _extract_text(content: Any) -> str:
    """Extract plain text from a response item ``content`` field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in TEXT_ITEM_TYPES:
                parts.append(item.get("text") or "")
        return "".join(parts)
    return ""


def is_injected_message(content: str) -> bool:
    """True for system-injected user messages (context, skills, aborts...)."""
    stripped = content.lstrip()
    return stripped.startswith(INJECTED_PREFIXES)


def generate_title(messages: list[Message]) -> str:
    """Short title from the first real user message, skipping injected context."""
    for message in messages:
        if message.role != "user" or is_injected_message(message.content):
            continue
        for line in message.content.splitlines():
            line = IMAGE_TAG_RE.sub("", line).strip()
            if not line:
                continue
            return line[:57] + "…" if len(line) > 57 else line
    return ""


def parse_session_file(path: Path) -> Session:
    """Parse one Codex session JSONL file into a :class:`Session`."""
    session = Session(id="", path=path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        # Unreadable or torn (mid-write) transcripts must not crash the UI.
        lines = []

    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            continue

        event_type = event.get("type")
        if event_type == "session_meta":
            session.id = payload.get("session_id") or payload.get("id") or session.id
            session.cwd = payload.get("cwd") or session.cwd
            session.timestamp = payload.get("timestamp") or session.timestamp
        elif event_type == "turn_context":
            if payload.get("model"):
                session.model = payload["model"]
        elif event_type == "response_item":
            item_type = payload.get("type")
            role = payload.get("role")
            if item_type == "message" and role in ("user", "assistant"):
                content = _extract_text(payload.get("content"))
                if content:
                    session.messages.append(
                        Message(role=role, content=content, item_id=payload.get("id"))
                    )
        # Unknown or UI-only event types (event_msg, compacted, reasoning,
        # function calls, ...) are intentionally ignored.

    if not session.id:
        session.id = path.stem
    return session


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(DEFAULT_CODEX_HOME)))


def _codex_tui_home() -> Path:
    return Path(os.environ.get("CODEX_TUI_HOME", str(DEFAULT_CODEX_TUI_HOME)))


class SessionStore:
    """Scan ``$CODEX_HOME/sessions`` and expose projects and sessions."""

    def __init__(
        self,
        sessions_dir: Path | None = None,
        trash_dir: Path | None = None,
    ) -> None:
        self.sessions_dir = sessions_dir or _codex_home() / "sessions"
        self.trash_dir = trash_dir or _codex_tui_home() / "trash"
        # Parsed sessions keyed by (mtime_ns, size) so repeated scans stay cheap.
        self._cache: dict[str, tuple[tuple[int, int], Session]] = {}

    def list_sessions(self) -> list[Session]:
        """All sessions, newest first."""
        if not self.sessions_dir.is_dir():
            return []
        sessions: list[Session] = []
        for path in sorted(self.sessions_dir.rglob("*.jsonl")):
            if path.is_relative_to(self.trash_dir):
                continue
            session = self._cached_session(path)
            if session.id:
                sessions.append(session)
        sessions.sort(key=lambda s: s.timestamp, reverse=True)
        return sessions

    def _cached_session(self, path: Path) -> Session:
        """Parse only when the file changed since the last scan."""
        try:
            stat = path.stat()
            key = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            self._cache.pop(str(path), None)
            return Session(id="", path=path)
        cached = self._cache.get(str(path))
        if cached is not None and cached[0] == key:
            return cached[1]
        session = parse_session_file(path)
        self._cache[str(path)] = (key, session)
        return session

    def list_projects(self) -> list[str]:
        """Distinct project directories, ordered by most recent session first."""
        projects: list[str] = []
        seen: set[str] = set()
        for session in self.list_sessions():
            project = session.project
            if project not in seen:
                seen.add(project)
                projects.append(project)
        return projects

    def sessions_for_project(self, project: str) -> list[Session]:
        """Sessions belonging to one project, newest first."""
        return [s for s in self.list_sessions() if s.project == project]

    def delete_session(self, session: Session) -> None:
        """Move a session transcript to the trash dir (recoverable delete)."""
        if not session.path.is_file():
            return
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        destination = self.trash_dir / f"{session.id}-{session.path.name}"
        shutil.move(str(session.path), str(destination))
        self._cache.pop(str(session.path), None)

    def clean_trash(self) -> int:
        """Permanently delete trashed transcripts; return the number removed."""
        if not self.trash_dir.is_dir():
            return 0
        removed = 0
        for path in self.trash_dir.glob("*.jsonl"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed
