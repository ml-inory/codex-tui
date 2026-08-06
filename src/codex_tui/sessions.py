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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_CODEX_HOME = Path.home() / ".codex"
TEXT_ITEM_TYPES = ("input_text", "output_text", "text")


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

    @property
    def project(self) -> str:
        """Working directory this session belongs to, used as the project key."""
        return self.cwd or "?"

    @property
    def title(self) -> str:
        """Short human-readable title derived from the first user message."""
        for message in self.messages:
            if message.role != "user":
                continue
            first_line = message.content.strip().splitlines()[0] if message.content.strip() else ""
            if first_line:
                return first_line[:60]
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


def parse_session_file(path: Path) -> Session:
    """Parse one Codex session JSONL file into a :class:`Session`."""
    session = Session(id="", path=path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return session

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


class SessionStore:
    """Scan ``$CODEX_HOME/sessions`` and expose projects and sessions."""

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self.sessions_dir = sessions_dir or _codex_home() / "sessions"

    def list_sessions(self) -> list[Session]:
        """All sessions, newest first."""
        if not self.sessions_dir.is_dir():
            return []
        sessions: list[Session] = []
        for path in sorted(self.sessions_dir.rglob("*.jsonl")):
            session = parse_session_file(path)
            if session.id:
                sessions.append(session)
        sessions.sort(key=lambda s: s.timestamp, reverse=True)
        return sessions

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
