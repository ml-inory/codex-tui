"""Reusable widgets for the codex-tui interface."""

from __future__ import annotations

import asyncio
import math
import os
import re
import time
from typing import Iterable
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.events import Click
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, ListItem, ListView, Static

from codex_tui.markdown import render_markdown
from codex_tui.sessions import (
    Message as SessionMessage,
    Session,
    is_injected_message,
)


DEFAULT_WINDOW = 80
MAX_TOOL_OUTPUT = 4000
_BACKGROUND_WAIT_INTERVAL = 0.6  # codex blinks the waiting bullet every 600 ms
_DIFF_MAX_LINES = 50
_TOOL_TAIL_LINES = 6  # long tool output collapses to its tail, click to expand
# Streaming text is re-rendered as a whole widget, so short bursts paint
# immediately while long replies coalesce into ~25 fps flushes instead of
# re-compositing the whole text on every delta (feels much faster over SSH).
_STREAM_INSTANT_LIMIT = 2000
_STREAM_FLUSH_INTERVAL = 0.04
_STREAM_CHUNK_SIZE = 8000  # chars per streaming chunk widget
# Lazy transcript rendering: only the visible tail is built on open, and older
# messages are prepended as the user scrolls toward the top.
_LAZY_CHUNK = 12
_LAZY_EDGE = 60  # rows from the top that trigger loading more
_LAZY_POLL_INTERVAL = 0.15

# opencode default dark palette (theme/assets/opencode.json in sst/opencode):
# running rows are bright text, completed rows are muted, failures are red.
TOOL_RUNNING_COLOR = "#eeeeee"  # theme.text
TOOL_SUCCESS_COLOR = "#808080"  # theme.textMuted (completed tool rows)
TOOL_FAILED_COLOR = "#e06c75"  # theme.error
TOOL_BODY_COLOR = "#808080"  # theme.textMuted
TOOL_DIFF_ADDED = "#4fd6be"
TOOL_DIFF_REMOVED = "#c53b53"
TOOL_DIFF_SIGN_ADDED = "#b8db87"
TOOL_DIFF_SIGN_REMOVED = "#e26a75"
TOOL_DIFF_ADDED_BG = "#20303b"
TOOL_DIFF_REMOVED_BG = "#37222c"

# codex verbs per tool kind: (in-progress, completed).
_TOOL_VERBS = {
    "command_execution": ("Running", "Ran"),
    "commandExecution": ("Running", "Ran"),
    "exec_command": ("Running", "Ran"),
    "file_change": ("Applying", "Applied"),
    "fileChange": ("Applying", "Applied"),
    "apply_patch": ("Applying", "Applied"),
    "web_search": ("Searching the web", "Searched the web"),
    "webSearch": ("Searching the web", "Searched the web"),
    "mcp_tool": ("Calling", "Called"),
    "mcpToolCall": ("Calling", "Called"),
}


def _tool_verb(tool: dict, running: bool) -> str:
    verbs = _TOOL_VERBS.get(tool.get("kind") or "", ("Running", "Ran"))
    return verbs[0] if running else verbs[1]


def _prefix_tool_output(text: str) -> str:
    """Indent tool output the way codex does: ``  └ line`` then ``    line``."""
    lines = text.splitlines()
    if not lines:
        return text
    return "\n".join([f"  └ {lines[0]}"] + [f"    {line}" for line in lines[1:]])


def _tail_tool_output(text: str) -> Text:
    """Indent output and keep only the last ``_TOOL_TAIL_LINES`` lines.

    Mirrors codex/opencode: long command output is collapsed to its tail with
    an omitted-lines marker; clicking the row toggles the full output.
    """
    lines = text.splitlines()
    if not lines:
        return Text("")
    if len(lines) <= _TOOL_TAIL_LINES:
        return Text(_prefix_tool_output(text))
    omitted = len(lines) - _TOOL_TAIL_LINES
    display = Text()
    display.append(
        f"  … +{omitted} line{'s' if omitted != 1 else ''} omitted"
        " — click to expand",
        style="dim",
    )
    display.append("\n")
    display.append(_prefix_tool_output("\n".join(lines[-_TOOL_TAIL_LINES:])))
    return display


def _full_tool_output(text: str) -> Text:
    return Text(_prefix_tool_output(text))


class BackgroundWaitStatus:
    """Blinking ``• Waiting for background terminal`` status line.

    Mirrors codex's status indicator: the bullet toggles between ``•`` and a
    dim ``◦`` every 600 ms while the model polls a background terminal.
    """

    def __init__(
        self, widget: Widget, status_id: str, updates_title: bool = False
    ) -> None:
        self._widget = widget
        self._status_id = status_id
        self._updates_title = updates_title
        self._timer = None
        self._on = False
        self._detail = ""

    def start(self, detail: str = "") -> None:
        """Show the blinking indicator, optionally with `` · <command>``."""
        self._detail = detail
        self._on = True
        self._render()
        if self._timer is None:
            self._timer = self._widget.set_interval(
                _BACKGROUND_WAIT_INTERVAL, self._toggle
            )

    def stop(self, fallback: str = "") -> None:
        """Hide the indicator and restore the previous status text."""
        self._detail = ""
        self._on = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._updates_title:
            self._widget.app.sub_title = ""
        self._widget.query_one(self._status_id, Static).update(fallback)

    def is_active(self) -> bool:
        """True while the blinking indicator is running."""
        return self._timer is not None

    def _toggle(self) -> None:
        self._on = not self._on
        self._render()

    def _render(self) -> None:
        text = Text()
        if self._on:
            text.append("•", style=f"bold {TOOL_RUNNING_COLOR}")
        else:
            text.append("◦", style="dim #808080")
        text.append(" Waiting for background terminal", style="bold")
        if self._detail:
            text.append(f" · {self._detail}", style="dim")
        if self._updates_title:
            self._widget.app.sub_title = text.plain
        self._widget.query_one(self._status_id, Static).update(text)


class WorkingSpinner:
    """Blinking ``• Codex is working…`` status line while a turn runs.

    Mirrors codex CLI's activity indicator: on truecolor terminals the bullet
    breathes through a 2s brightness sweep; elsewhere it blinks ``•``/``◦``
    every 600 ms like the background-terminal wait row.
    """

    # Sidebar running-session markers keep the braille frames.
    FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    INTERVAL = 0.12
    BREATH_SECONDS = 2.0
    BLINK_INTERVAL = 0.6
    _BREATH_DIM = "#484848"
    _BREATH_BRIGHT = "#eeeeee"

    def __init__(
        self, widget: Widget, status_id: str, updates_title: bool = False
    ) -> None:
        self._widget = widget
        self._status_id = status_id
        self._updates_title = updates_title
        self._timer = None
        self._started = time.monotonic()

    def start(self) -> None:
        """Show the spinner (idempotent; keeps its current phase)."""
        self._render()
        if self._timer is None:
            self._timer = self._widget.set_interval(
                WorkingSpinner.INTERVAL, self._tick
            )

    def stop(self, fallback: str = "") -> None:
        """Hide the spinner and restore the previous status text."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._updates_title:
            self._widget.app.sub_title = ""
        self._widget.query_one(self._status_id, Static).update(fallback)

    def _tick(self) -> None:
        self._render()

    @staticmethod
    def _truecolor() -> bool:
        """Heuristic used by codex CLI: COLORTERM or TERM advertise 24-bit."""
        colorterm = os.environ.get("COLORTERM") or ""
        term = os.environ.get("TERM") or ""
        return colorterm in ("truecolor", "24bit") or "direct" in term

    @staticmethod
    def _blend_hex(low: str, high: str, t: float) -> str:
        """Linear RGB blend between two hex colors, ``t`` in 0..1."""

        def rgb(hex_color: str) -> tuple[int, int, int]:
            hex_color = hex_color.lstrip("#")
            return tuple(
                int(hex_color[i : i + 2], 16) for i in (0, 2, 4)
            )

        a, b = rgb(low), rgb(high)
        blended = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
        return f"#{blended[0]:02x}{blended[1]:02x}{blended[2]:02x}"

    def _render(self) -> None:
        text = Text()
        elapsed = time.monotonic() - self._started
        if self._truecolor():
            phase = (elapsed % self.BREATH_SECONDS) / self.BREATH_SECONDS
            intensity = 0.5 * (1.0 + math.cos(2 * math.pi * phase))
            bullet = "•"
            bullet_style = (
                f"bold {self._blend_hex(self._BREATH_DIM, self._BREATH_BRIGHT, intensity)}"
            )
        else:
            on = int(elapsed / self.BLINK_INTERVAL) % 2 == 0
            bullet = "•" if on else "◦"
            bullet_style = (
                f"bold {TOOL_RUNNING_COLOR}"
                if on
                else f"dim {TOOL_RUNNING_COLOR}"
            )
        text.append(bullet, style=bullet_style)
        text.append(" Codex is working…", style="bold")
        if self._updates_title:
            self._widget.app.sub_title = text.plain
        self._widget.query_one(self._status_id, Static).update(text)


class Sidebar(Widget):
    """Left panel: project list on top, session list below."""

    class ProjectSelected(Message):
        """A project in the list was selected."""

        def __init__(self, project: str) -> None:
            super().__init__()
            self.project = project

    class SessionSelected(Message):
        """A session in the list was selected."""

        def __init__(self, session: Session) -> None:
            super().__init__()
            self.session = session

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self._projects: list[str] = []
        self._session_by_item: dict[str, Session] = {}
        self._session_statics: dict[str, Static] = {}
        self._session_base: dict[str, str] = {}
        self._running_ids: set[str] = set()
        self._running_timer = None
        self._running_frame = 0

    def compose(self) -> ComposeResult:
        yield Static("PROJECTS", classes="section-title")
        yield ListView(id="project-list")
        yield Static("SESSIONS", classes="section-title")
        yield ListView(id="session-list")
        yield Static(
            "No projects yet — press f4 to add a directory.",
            id="sidebar-hint",
            classes="muted",
        )

    @on(ListView.Selected, "#project-list")
    def _project_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index >= len(self._projects):
            return
        self.post_message(self.ProjectSelected(self._projects[index]))

    @on(ListView.Selected, "#session-list")
    def _session_selected(self, event: ListView.Selected) -> None:
        session = self._session_by_item.get(event.item.id or "")
        if session is not None:
            self.post_message(self.SessionSelected(session))

    async def set_projects(
        self,
        projects: Iterable[str],
        project_mode: str = "short",
    ) -> None:
        self._projects = list(projects)
        project_list = self.query_one("#project-list", ListView)
        await project_list.clear()
        if not self._projects:
            await self._show_hint("No projects yet. Start a session to see it here.")
            await self.query_one("#session-list", ListView).clear()
            return
        await self._show_hint("")
        for project in self._projects:
            label = self._project_label(project, project_mode)
            static = Static(label, classes="project-label")
            static.tooltip = project
            item = ListItem(static)
            await project_list.append(item)
        project_list.index = 0

    @staticmethod
    def _project_label(project: str, project_mode: str) -> str:
        if project_mode == "full":
            return project
        name = Path(project).name
        return name or project

    async def set_sessions(
        self,
        sessions: Iterable[Session],
        finished: set[str] | None = None,
        running: set[str] | None = None,
    ) -> None:
        finished = finished or set()
        self._running_ids = set(running or ())
        self._session_by_item = {}
        self._session_statics = {}
        self._session_base = {}
        session_list = self.query_one("#session-list", ListView)
        items: list[ListItem] = []
        for index, session in enumerate(sessions):
            marker = "● " if session.id in finished else ""
            base = (
                f"{session.timestamp[11:16] if len(session.timestamp) >= 16 else ''}"
                f"  {session.title}"
            )
            classes = "session-label"
            if session.id in finished:
                classes += " finished"
            elif session.id in self._running_ids:
                classes += " running"
            static = Static(f"{marker}{base}", classes=classes)
            item = ListItem(static)
            item.id = f"item-{index}"
            self._session_by_item[item.id] = session
            self._session_statics[session.id] = static
            self._session_base[session.id] = base
            items.append(item)
        await session_list.clear()
        if items:
            await session_list.extend(items)
        else:
            await self._show_hint("No sessions for this project yet.")
        session_list.index = 0
        self._sync_running_timer()
        self._tick_running()

    def set_running_sessions(self, running: set[str]) -> None:
        """Update which sessions show an animated working spinner."""
        self._running_ids = set(running)
        for session_id, static in self._session_statics.items():
            classes = set(static.classes)
            if session_id in self._running_ids:
                classes.add("running")
            else:
                classes.discard("running")
            static.set_classes(" ".join(sorted(classes)))
        self._sync_running_timer()
        self._tick_running()

    def _sync_running_timer(self) -> None:
        if self._running_ids:
            if self._running_timer is None:
                self._running_timer = self.set_interval(
                    WorkingSpinner.INTERVAL, self._tick_running
                )
        elif self._running_timer is not None:
            self._running_timer.stop()
            self._running_timer = None

    def _tick_running(self) -> None:
        frame = WorkingSpinner.FRAMES[self._running_frame]
        self._running_frame = (self._running_frame + 1) % len(WorkingSpinner.FRAMES)
        for session_id in self._running_ids:
            static = self._session_statics.get(session_id)
            base = self._session_base.get(session_id, "")
            if static is not None:
                static.update(f"{frame} {base}")

    async def _show_hint(self, text: str) -> None:
        hint = self.query_one("#sidebar-hint", Static)
        hint.update(text)
        hint.display = bool(text)


class ChatLog(VerticalScroll):
    """Scrollable conversation area rendered as user bubbles and markdown."""

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self._pending_stream: Static | None = None
        self._pending_stream_chunks: list[Static] = []
        self._pending_label: Static | None = None
        self._pending_text = ""
        self._stream_flush_timer = None
        self._pending_tool_label: Static | None = None
        self._pending_tool_output: Static | None = None
        self._pending_tool_text = ""
        self._tool_flush_timer = None
        self._lazy_timer = None
        self._loading_older = False
        self._interacted_items: set[str] = set()
        self._messages: list[SessionMessage] = []
        self._window = DEFAULT_WINDOW
        # One persistent container per rendered session. Switching sessions
        # only toggles ``display``, so the layout is computed once per session
        # instead of on every switch.
        self._containers: dict[str, VerticalScroll] = {}
        self._active_id: str | None = None
        self._rendered_id: str | None = None
        self._rendered_signature: tuple[int, str, int] | None = None

    def on_mount(self) -> None:
        self._lazy_timer = self.set_interval(_LAZY_POLL_INTERVAL, self._lazy_check)

    async def _ensure_active_container(self) -> VerticalScroll:
        """Return the container for the current view, mounting it on demand."""
        if self._active_id is not None and self._active_id in self._containers:
            return self._containers[self._active_id]
        container = VerticalScroll(id="session-new")
        await self.mount(container)
        container._window = DEFAULT_WINDOW
        container._start = 0
        container._end = 0
        container._msg_keys: list[tuple] = []
        self._containers[""] = container
        self._active_id = ""
        return container

    async def render_session(self, session: Session) -> None:
        self._messages = [
            message
            for message in session.messages
            if not (message.role == "user" and is_injected_message(message.content))
        ]
        container = self._containers.get(session.id)
        if container is None:
            container = VerticalScroll(id=f"session-{session.id}")
            await self.mount(container)
            self._containers[session.id] = container
            container._start = 0
            container._end = 0
            container._msg_keys: list[tuple] = []
        self._active_id = session.id
        for cid, cached in self._containers.items():
            if cid != session.id:
                cached.display = False
        container.display = True
        stale_stream = (
            self._pending_stream is not None
            and self._pending_stream in container.children
        )
        if stale_stream or not container.children:
            await self._render_window()
        elif not await self._sync_rendered_range(container):
            await self._render_window()
        self._rendered_signature = self._messages_signature()
        self._rendered_id = session.id
        if not container.children:
            container.scroll_end(animate=False)

    @staticmethod
    def _msg_key(message) -> tuple:
        """Cheap fingerprint of one message for lazy-range bookkeeping."""
        return (
            message.role,
            message.item_id or "",
            len(message.content),
            message.content[:80],
        )

    def _messages_signature(self) -> tuple:
        return tuple(self._msg_key(m) for m in self._messages)

    async def _sync_rendered_range(self, container: VerticalScroll) -> bool:
        """Update the rendered range when the conversation only grew.

        Returns ``True`` when the existing rows are still valid (possibly after
        appending the new tail) and ``False`` when a full re-render is needed.
        """
        new_keys = self._messages_signature()
        start = getattr(container, "_start", 0)
        rendered_keys = getattr(container, "_msg_keys", [])
        if not rendered_keys:
            return False
        if new_keys[start : start + len(rendered_keys)] != rendered_keys:
            return False
        end = start + len(rendered_keys)
        if len(new_keys) > end:
            was_at_end = container.is_vertical_scroll_end
            rows: list[Widget] = []
            for message in self._messages[end:]:
                rows.extend(self._build_rows(message.role, message.content))
            for index in range(0, len(rows), 20):
                await container.mount(*rows[index : index + 20])
                await asyncio.sleep(0)
            if rows:
                container.refresh(layout=True)
            container._msg_keys = new_keys[start:]
            container._end = len(new_keys)
            if was_at_end:
                container.scroll_end(animate=False)
        return True

    async def _render_window(self) -> None:
        """Build the visible tail of the transcript (lazily, not the whole log)."""
        await self.clear_chat()
        container = await self._ensure_active_container()
        end = len(self._messages)
        start = max(0, end - _LAZY_CHUNK)
        rows: list[Widget] = []
        for message in self._messages[start:]:
            rows.extend(self._build_rows(message.role, message.content))
        if rows:
            await container.mount(*rows)
            container.refresh(layout=True)
        container._start = start
        container._end = end
        container._msg_keys = [
            self._msg_key(m) for m in self._messages[start:end]
        ]
        container.scroll_end(animate=False)
        await self._fill_viewport(container)

    async def _fill_viewport(self, container: VerticalScroll) -> None:
        """Prepend older messages until the screen is filled.

        ``virtual_size`` is only valid after a layout pass, so the fill is
        driven by the mounted widget count (each row is at least one widget);
        tall Markdown only makes the content taller, which is harmless.
        """
        viewport = container.size.height or 24
        for _ in range(200):
            if getattr(container, "_start", 0) <= 0:
                return
            if len(container.children) >= viewport:
                return
            await self._prepend_more(container, keep_position=False)
            await asyncio.sleep(0)

    async def load_earlier(self) -> bool:
        """Load every earlier message at once (F7); False when already at top."""
        container = await self._ensure_active_container()
        if getattr(container, "_start", 0) <= 0:
            return False
        await self._prepend_more(
            container, keep_position=True, count=container._start
        )
        return True

    async def _prepend_more(
        self,
        container: VerticalScroll,
        keep_position: bool,
        count: int = _LAZY_CHUNK,
    ) -> None:
        """Insert older message rows above the current ones."""
        if self._loading_older or getattr(container, "_start", 0) <= 0:
            return
        self._loading_older = True
        try:
            old_height = container.virtual_size.height
            old_scroll = container.scroll_y
            start = container._start
            new_start = max(0, start - count)
            batches: list[list[Widget]] = []
            for message_start in range(new_start, start, 20):
                batch: list[Widget] = []
                for message in self._messages[
                    message_start : min(message_start + 20, start)
                ]:
                    batch.extend(
                        self._build_rows(message.role, message.content)
                    )
                batches.append(batch)
            # Mount oldest-first so the final row order stays chronological.
            for batch in reversed(batches):
                if batch:
                    await container.mount(*batch, before=0)
                    await asyncio.sleep(0)
            if any(batches):
                container.refresh(layout=True)
                await asyncio.sleep(0)  # let layout settle so the delta is real
                if keep_position:
                    delta = container.virtual_size.height - old_height
                    if delta:
                        container.release_anchor()
                        container.scroll_to(
                            y=old_scroll + delta, animate=False, immediate=True
                        )
            container._start = new_start
            container._msg_keys = [
                self._msg_key(m)
                for m in self._messages[new_start : container._end]
            ]
        finally:
            self._loading_older = False

    async def _lazy_check(self) -> None:
        """Auto-load older messages when the user scrolls near the top."""
        container = self._containers.get(self._active_id or "")
        if container is None or not container.display or self._loading_older:
            return
        if getattr(container, "_start", 0) <= 0:
            return
        if container.scroll_y <= _LAZY_EDGE:
            await self._prepend_more(container, keep_position=True)

    async def clear_chat(self) -> None:
        container = await self._ensure_active_container()
        await container.remove_children(container.children)
        container._signature = None
        container._start = 0
        container._end = 0
        container._msg_keys = []
        self._pending_stream = None
        self._pending_stream_chunks = []
        self._pending_label = None
        self._pending_text = ""
        self._stop_stream_flush()
        self._pending_tool_label = None
        self._pending_tool_output = None
        self._pending_tool_text = ""
        self._rendered_id = None
        self._rendered_signature = None

    def visible_widgets(self) -> list[Widget]:
        """Rows of the currently visible session container (test/selection)."""
        container = self._containers.get(self._active_id or "")
        if container is None:
            return []
        return list(container.children)

    def _build_rows(self, role: str, content: str) -> list[Widget]:
        if role == "user":
            # opencode style: cyan "You" label, plain text.
            return [
                Static("You", classes="user-label"),
                Static(escape(content), classes="user-body"),
            ]
        # opencode style: secondary-blue "Codex" label above the markdown body.
        return [
            Static("Codex", classes="assistant-label"),
            Static(render_markdown(content), classes="assistant-body"),
        ]

    async def add_message(self, role: str, content: str) -> None:
        container = await self._ensure_active_container()
        # A new turn starts from the transcript again: drop any previously
        # rendered rows so the new message is not appended to a stale view.
        if container.children:
            await container.remove_children(container.children)
            self._pending_stream = None
            self._pending_stream_chunks = []
            self._pending_label = None
            self._pending_text = ""
            self._pending_tool_label = None
            self._pending_tool_output = None
            self._pending_tool_text = ""
            container._start = 0
            container._end = 0
            container._msg_keys = []
            self._stop_stream_flush()
            self._stop_tool_flush()
        await container.mount(*self._build_rows(role, content))
        container.scroll_end(animate=False)

    async def add_user_message(self, content: str) -> None:
        await self.add_message("user", content)

    async def begin_assistant_message(self) -> None:
        """Open a streaming row for the assistant's reply.

        While the model streams we render plain text (``Text`` is not parsed
        as markup) and only parse Markdown once at the end, so token-level
        updates stay cheap.
        """
        container = await self._ensure_active_container()
        if self._pending_stream is not None:
            # Never leave a duplicate streaming row behind: drop any
            # previously opened stream (it may belong to an earlier render
            # of this or another session).
            for widget in (self._pending_label, *self._pending_stream_chunks):
                if widget is not None:
                    try:
                        await widget.remove()
                    except Exception:
                        pass
            self._pending_label = None
            self._pending_stream = None
            self._pending_stream_chunks = []
            self._pending_text = ""
        label = Static("Codex", classes="assistant-label")
        await container.mount(label)
        self._pending_label = label
        stream = Static(Text(""), classes="assistant-body streaming")
        await container.mount(stream)
        self._pending_stream = stream
        self._pending_stream_chunks = [stream]
        self._pending_text = ""
        container.scroll_end(animate=False)

    async def append_assistant_delta(self, delta: str) -> None:
        """Append a streamed delta; only the tail chunk is re-rendered."""
        if self._pending_stream is None:
            await self.begin_assistant_message()
        self._pending_text += delta
        if len(self._pending_text) < _STREAM_INSTANT_LIMIT:
            await self._flush_pending_stream()
        else:
            self._schedule_stream_flush()

    async def update_assistant_message(self, text: str) -> None:
        """Set the whole assistant body at once (non-streaming fallback)."""
        if self._pending_stream is None:
            await self.begin_assistant_message()
        self._stop_stream_flush()
        self._pending_text = text
        await self._flush_pending_stream()

    async def finish_assistant_message(self) -> None:
        """Render the finished assistant message as Markdown."""
        if self._pending_stream is not None:
            self._stop_stream_flush()
            body = self._pending_text
            for chunk in self._pending_stream_chunks:
                try:
                    await chunk.remove()
                except Exception:
                    pass
            self._pending_stream_chunks = []
            self._pending_stream = None
            self._pending_label = None
            self._pending_text = ""
            markdown = Static(render_markdown(body), classes="assistant-body")
            container = await self._ensure_active_container()
            await container.mount(markdown)
            container.scroll_end(animate=False)

    def _schedule_stream_flush(self) -> None:
        """Coalesce deltas so the tail chunk is re-rendered at ~25 fps max."""
        if self._stream_flush_timer is None:
            self._stream_flush_timer = self.set_timer(
                _STREAM_FLUSH_INTERVAL, self._flush_pending_stream
            )

    def _stop_stream_flush(self) -> None:
        if self._stream_flush_timer is not None:
            self._stream_flush_timer.stop()
            self._stream_flush_timer = None

    async def _flush_pending_stream(self) -> None:
        """Sync the chunk widgets to the accumulated text.

        Chunks are immutable once full, so long replies only ever re-render a
        bounded tail widget instead of the whole message on every delta.
        """
        if self._pending_stream is None or not self._pending_stream_chunks:
            return
        self._stream_flush_timer = None
        container = await self._ensure_active_container()
        chunks_needed = max(
            1,
            (len(self._pending_text) + _STREAM_CHUNK_SIZE - 1)
            // _STREAM_CHUNK_SIZE,
        )
        while len(self._pending_stream_chunks) < chunks_needed:
            index = len(self._pending_stream_chunks)
            if index > 0:
                # Finalize the previous chunk's slice before starting a new
                # one (it was the growing tail until this point).
                previous_start = (index - 1) * _STREAM_CHUNK_SIZE
                self._pending_stream_chunks[-1].update(
                    Text(
                        self._pending_text[
                            previous_start : index * _STREAM_CHUNK_SIZE
                        ]
                    )
                )
            chunk = Static(Text(""), classes="assistant-body streaming")
            self._pending_stream_chunks.append(chunk)
            await container.mount(chunk)
        tail_start = (chunks_needed - 1) * _STREAM_CHUNK_SIZE
        self._pending_stream_chunks[-1].update(
            Text(self._pending_text[tail_start:])
        )
        self._pending_stream = self._pending_stream_chunks[-1]
        container.scroll_end(animate=False)

    async def _clear_pending_tool(self) -> None:
        """Drop any unfinished tool row (duplicate/aborted tool calls)."""
        for widget in (self._pending_tool_label, self._pending_tool_output):
            if widget is not None:
                try:
                    await widget.remove()
                except Exception:
                    pass
        self._pending_tool_label = None
        self._pending_tool_output = None
        self._pending_tool_text = ""
        self._stop_stream_flush()
        self._stop_tool_flush()

    async def begin_tool_call(self, tool: dict) -> None:
        """Show a live tool row in codex style (Running/Exploring/Applying)."""
        container = await self._ensure_active_container()
        await self._clear_pending_tool()
        label = Static(
            self._tool_running_header(tool),
            classes="tool-label running",
        )
        await container.mount(label)
        self._pending_tool_label = label
        container.scroll_end(animate=False)

    async def append_tool_output(self, text: str) -> None:
        """Stream a chunk of tool output under the running tool row."""
        if self._pending_tool_label is None:
            return
        self._pending_tool_text += text
        if len(self._pending_tool_text) > MAX_TOOL_OUTPUT:
            self._pending_tool_text = (
                "…" + self._pending_tool_text[-(MAX_TOOL_OUTPUT - 1) :]
            )
        if len(self._pending_tool_text) < _STREAM_INSTANT_LIMIT:
            container = await self._ensure_active_container()
            if self._pending_tool_output is None:
                self._pending_tool_output = Static(
                    _tail_tool_output(self._pending_tool_text),
                    classes="tool-output",
                )
                self._pending_tool_output._full_text = self._pending_tool_text
                self._pending_tool_output._tail_mode = True
                await container.mount(self._pending_tool_output)
            else:
                self._pending_tool_output._full_text = self._pending_tool_text
                self._pending_tool_output.update(
                    _full_tool_output(self._pending_tool_text)
                    if not self._pending_tool_output._tail_mode
                    else _tail_tool_output(self._pending_tool_text)
                )
            container.scroll_end(animate=False)
        else:
            self._schedule_tool_flush()

    def _schedule_tool_flush(self) -> None:
        """Coalesce tool output deltas, same as the assistant stream."""
        if self._tool_flush_timer is None:
            self._tool_flush_timer = self.set_timer(
                _STREAM_FLUSH_INTERVAL, self._flush_pending_tool
            )

    def _stop_tool_flush(self) -> None:
        if self._tool_flush_timer is not None:
            self._tool_flush_timer.stop()
            self._tool_flush_timer = None

    async def _flush_pending_tool(self) -> None:
        """Render everything accumulated since the last tool-output flush."""
        self._tool_flush_timer = None
        if self._pending_tool_label is None:
            return
        container = await self._ensure_active_container()
        if self._pending_tool_output is None:
            self._pending_tool_output = Static(
                _tail_tool_output(self._pending_tool_text),
                classes="tool-output",
            )
            self._pending_tool_output._full_text = self._pending_tool_text
            self._pending_tool_output._tail_mode = True
            await container.mount(self._pending_tool_output)
        else:
            self._pending_tool_output._full_text = self._pending_tool_text
            self._pending_tool_output.update(
                _full_tool_output(self._pending_tool_text)
                if not self._pending_tool_output._tail_mode
                else _tail_tool_output(self._pending_tool_text)
            )
        container.scroll_end(animate=False)

    async def finish_tool_call(self, tool: dict) -> None:
        """Mark the running tool row as ``• Ran`` in green or red."""
        if self._pending_tool_label is None:
            return
        self._stop_tool_flush()
        ok = tool.get("status") == "completed"
        if tool.get("source") in (
            "unifiedExecInteraction",
            "unified_exec_interaction",
        ):
            interacted = tool.get("id") in self._interacted_items
            self._pending_tool_label.update(
                self._background_terminal_header(tool, interacted=interacted)
            )
        elif tool.get("exploring"):
            self._pending_tool_label.update(
                self._exploring_header_text(tool, running=False)
            )
        elif self._is_file_change(tool):
            self._pending_tool_label.update(
                self._file_change_header_text(tool, running=False)
            )
        else:
            self._pending_tool_label.update(
                self._tool_header_text(tool, running=False)
            )
        self._pending_tool_label.set_classes(
            "tool-label " + ("completed" if ok else "failed")
        )
        is_exec = tool.get("kind") in ("command_execution", "commandExecution")
        # Exec mode never streams output; show the final aggregated output
        # (or codex's "(no output)" marker) when nothing streamed live.
        if not self._pending_tool_text:
            output = tool.get("output") or ""
            if len(output) > MAX_TOOL_OUTPUT:
                output = "…" + output[-(MAX_TOOL_OUTPUT - 1) :]
            if not output and is_exec:
                output = "(no output)"
            if output:
                self._pending_tool_output = Static(
                    _tail_tool_output(output), classes="tool-output"
                )
                self._pending_tool_output._full_text = output
                self._pending_tool_output._tail_mode = True
                container = await self._ensure_active_container()
                await container.mount(self._pending_tool_output)
                container.scroll_end(animate=False)
        self._pending_tool_label = None
        self._pending_tool_output = None

    def mark_tool_interaction(self, item_id: str | None) -> None:
        """Remember that the model sent stdin to a background terminal."""
        if item_id:
            self._interacted_items.add(item_id)

    @on(Click, ".tool-output")
    def _on_tool_output_click(self, event: Click) -> None:
        """Toggle a collapsed tool-output row between tail and full output."""
        static = event.widget
        full_text = getattr(static, "_full_text", None)
        if full_text is None:
            return
        if self.app.screen.get_selected_text():
            return  # the user is dragging to select text, not expanding
        if getattr(static, "_tail_mode", True):
            static.update(_full_tool_output(full_text))
            static._tail_mode = False
        else:
            static.update(_tail_tool_output(full_text))
            static._tail_mode = True
        event.stop()

    def _tool_header_text(self, tool: dict, running: bool) -> Text:
        """Build a codex-style tool header, e.g. ``• Running ls -la``."""
        if running:
            bullet_style = f"bold {TOOL_RUNNING_COLOR}"
            row_style = TOOL_RUNNING_COLOR
        elif tool.get("status") == "completed":
            bullet_style = f"bold {TOOL_SUCCESS_COLOR}"
            row_style = TOOL_SUCCESS_COLOR
        else:
            bullet_style = f"bold {TOOL_FAILED_COLOR}"
            row_style = TOOL_FAILED_COLOR
        header = Text()
        header.append("•", style=bullet_style)
        verb = _tool_verb(tool, running)
        header.append(f" {verb}", style=f"bold {row_style}")
        detail = tool.get("detail") or ""
        if detail:
            separator = " for" if (
                not running
                and tool.get("kind") in ("web_search", "webSearch")
            ) else ""
            header.append(f"{separator} {detail}", style=row_style)
        return header

    def _background_terminal_header(self, tool: dict, interacted: bool) -> Text:
        """Build codex's background-terminal cell, e.g. ``• Waited for ...``."""
        header = Text()
        if interacted:
            header.append("↳ ", style="dim")
            header.append("Interacted with background terminal", style="bold")
        else:
            ok = tool.get("status") == "completed"
            bullet_style = (
                f"bold {TOOL_SUCCESS_COLOR}" if ok else f"bold {TOOL_FAILED_COLOR}"
            )
            header.append("•", style=bullet_style)
            header.append(" Waited for background terminal", style="bold")
        detail = tool.get("detail") or ""
        if detail:
            header.append(f" · {detail}", style="dim")
        return header

    def _tool_running_header(self, tool: dict) -> Text:
        """Pick the codex-style running header for the current tool row."""
        if tool.get("exploring"):
            return self._exploring_header_text(tool, running=True)
        if self._is_file_change(tool):
            return self._file_change_header_text(tool, running=True)
        return self._tool_header_text(tool, running=True)

    @staticmethod
    def _is_file_change(tool: dict) -> bool:
        return tool.get("kind") in ("file_change", "fileChange")

    def _exploring_header_text(self, tool: dict, running: bool) -> Text:
        """Build codex's exploring cell, e.g. ``• Explored`` + sub-actions."""
        header = Text()
        if running:
            header.append("•", style=f"bold {TOOL_RUNNING_COLOR}")
            header.append(" Exploring", style=f"bold {TOOL_RUNNING_COLOR}")
        else:
            header.append("•", style=f"bold {TOOL_SUCCESS_COLOR}")
            header.append(" Explored", style=f"bold {TOOL_SUCCESS_COLOR}")
        actions = tool.get("actions") or []
        for index, action in enumerate(actions):
            header.append("\n" + ("  └ " if index == 0 else "    "))
            header.append(self._exploring_action_text(action))
        return header

    @staticmethod
    def _exploring_action_text(action: dict) -> Text:
        """Render one action line, e.g. ``Search <q> in <path>`` (muted title)."""
        kind = action.get("kind")
        title = {"read": "Read", "listFiles": "List", "search": "Search"}.get(
            kind, "Run"
        )
        line = Text(title, style=TOOL_BODY_COLOR)
        if kind == "search":
            query = action.get("query")
            path = action.get("path")
            if query:
                line.append(f" {query}")
            if path:
                line.append(" in ", style="dim")
                line.append(path)
            elif not query:
                line.append(f" {action.get('command') or ''}")
        elif kind == "read":
            line.append(f" {action.get('name') or action.get('path') or ''}")
        elif kind == "listFiles":
            line.append(f" {action.get('path') or action.get('command') or ''}")
        else:
            line.append(f" {action.get('command') or ''}")
        return line

    def _file_change_header_text(self, tool: dict, running: bool) -> Text:
        """Build codex's patch cell, e.g. ``• Edited <path> (+18 -4)`` + diff."""
        changes = tool.get("changes") or []
        if running:
            header = Text()
            header.append("•", style=f"bold {TOOL_RUNNING_COLOR}")
            header.append(" Applying", style=f"bold {TOOL_RUNNING_COLOR}")
            detail = tool.get("detail") or ""
            if detail:
                header.append(f" {detail}", style=TOOL_RUNNING_COLOR)
            return header
        has_diff = any((change.get("diff") or "") for change in changes)
        if not changes or not has_diff:
            # Exec-mode fallback (no diff payload): keep the simple style.
            ok = tool.get("status") == "completed"
            header = Text()
            header.append(
                "•",
                style=(
                    f"bold {TOOL_SUCCESS_COLOR}"
                    if ok
                    else f"bold {TOOL_FAILED_COLOR}"
                ),
            )
            header.append(" Applied", style="bold")
            detail = tool.get("detail") or ""
            if detail:
                header.append(f" {detail}", style=TOOL_BODY_COLOR)
            return header

        header = Text()
        header.append("• ", style=f"bold {TOOL_SUCCESS_COLOR}")
        total_added = total_removed = 0
        counts = [self._change_line_counts(change) for change in changes]
        total_added = sum(added for added, _ in counts)
        total_removed = sum(removed for _, removed in counts)
        if len(changes) == 1:
            change = changes[0]
            kind = change.get("kind")
            verb = (
                "Added"
                if kind == "add"
                else "Deleted"
                if kind == "delete"
                else "Edited"
            )
            header.append(verb, style="bold")
            header.append(f" {change.get('path')} ", style=TOOL_BODY_COLOR)
            header.append(self._line_count_text(total_added, total_removed))
            diff = change.get("diff") or ""
            if diff:
                header.append("\n")
                header.append(
                    self._diff_block_text(self._truncate_diff(diff))
                )
            return header

        header.append("Edited", style="bold")
        header.append(f" {len(changes)} files ", style=TOOL_BODY_COLOR)
        header.append(self._line_count_text(total_added, total_removed))
        for index, (change, (added, removed)) in enumerate(zip(changes, counts)):
            if index > 0:
                header.append("\n\n")
            header.append("  └ ", style="dim")
            header.append(change.get("path"), style=TOOL_BODY_COLOR)
            header.append(" ")
            header.append(self._line_count_text(added, removed))
            diff = change.get("diff") or ""
            if diff:
                header.append("\n")
                header.append(
                    self._diff_block_text(self._truncate_diff(diff))
                )
        return header

    @staticmethod
    def _change_line_counts(change: dict) -> tuple[int, int]:
        """Count ``+``/``-`` lines in a unified diff (codex line_counts)."""
        added = removed = 0
        for raw in (change.get("diff") or "").splitlines():
            if raw.startswith("+") and not raw.startswith("+++"):
                added += 1
            elif raw.startswith("-") and not raw.startswith("---"):
                removed += 1
        return added, removed

    @staticmethod
    def _truncate_diff(diff: str) -> str:
        """Cap the preview so huge patches cannot flood the chat row."""
        lines = diff.splitlines()
        if len(lines) <= _DIFF_MAX_LINES:
            return diff
        head = lines[: _DIFF_MAX_LINES - 5]
        tail = lines[-5:]
        omitted = len(lines) - len(head) - len(tail)
        return "\n".join(head + [f"… +{omitted} lines omitted"] + tail)

    @staticmethod
    def _line_count_text(added: int, removed: int) -> Text:
        text = Text("(")
        text.append(f"+{added}", style=TOOL_DIFF_ADDED)
        text.append(" ")
        text.append(f"-{removed}", style=TOOL_DIFF_REMOVED)
        text.append(")")
        return text

    @staticmethod
    def _diff_block_text(diff: str) -> Text:
        """Render a unified diff: ``+`` green / ``-`` red, dim line-number gutter."""
        lines = diff.splitlines()
        entries: list[tuple[str, int | None, str]] = []
        old_ln = new_ln = 0
        max_ln = 0
        for raw in lines:
            if raw.startswith("@@"):
                # codex renders hunks without the ``@@`` header line.
                match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
                if match:
                    old_ln, new_ln = int(match.group(1)), int(match.group(2))
                continue
            elif raw.startswith(("+++", "---", "diff ", "index ")):
                entries.append((raw, None, "meta"))
            elif raw.startswith("+"):
                entries.append((raw, new_ln, "add"))
                max_ln = max(max_ln, new_ln)
                new_ln += 1
            elif raw.startswith("-"):
                entries.append((raw, old_ln, "del"))
                max_ln = max(max_ln, old_ln)
                old_ln += 1
            elif raw.startswith("\\"):
                entries.append((raw, None, "no_newline"))
            else:
                entries.append((raw, new_ln, "ctx"))
                max_ln = max(max_ln, new_ln)
                old_ln += 1
                new_ln += 1

        width = len(str(max_ln))
        rendered: list[Text] = []
        for raw, line_number, kind in entries:
            line = Text()
            if kind in ("add", "del"):
                background = (
                    TOOL_DIFF_ADDED_BG if kind == "add" else TOOL_DIFF_REMOVED_BG
                )
                foreground = (
                    TOOL_DIFF_ADDED if kind == "add" else TOOL_DIFF_REMOVED
                )
                sign = (
                    TOOL_DIFF_SIGN_ADDED
                    if kind == "add"
                    else TOOL_DIFF_SIGN_REMOVED
                )
                line.append(
                    f"    {line_number:>{width}} ",
                    style=f"dim on {background}",
                )
                line.append(
                    "+" if kind == "add" else "-",
                    style=f"bold {sign} on {background}",
                )
                line.append(raw[1:], style=f"{foreground} on {background}")
            elif kind == "ctx":
                line.append(f"    {line_number:>{width}}  ")
                line.append(raw[1:] if raw.startswith(" ") else raw)
            elif kind == "no_newline":
                line.append("    ")
                line.append(raw, style="dim italic")
            else:  # meta (+++ / --- / diff / index)
                line.append("    ")
                line.append(raw, style="dim")
            rendered.append(line)

        block = Text()
        for index, line in enumerate(rendered):
            if index:
                block.append("\n")
            block.append(line)
        return block


class ChatView(Widget):
    """Right panel: header, chat log, and prompt input."""

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self.yolo = False
        self._running = False
        self._background_commands: dict[str, str] = {}
        self._bg_wait: BackgroundWaitStatus | None = None
        self._spinner: WorkingSpinner | None = None

    def compose(self) -> ComposeResult:
        yield Static("Select a project and session", id="chat-header")
        yield Static("", id="chat-status")
        yield ChatLog(id="chat-log")
        yield Input(
            placeholder="Message Codex… (Enter to send, /help for commands)",
            id="prompt-input",
        )

    async def show_session(self, session: Session | None) -> None:
        header = self.query_one("#chat-header", Static)
        chat_log = self.query_one(ChatLog)
        if session is None:
            suffix = "  |  ⚠ YOLO" if self.yolo else ""
            header.update(f"Select a project and session{suffix}")
            self._stop_background_wait()
            await chat_log.clear_chat()
            return
        model = session.effective_model or "codex"
        suffix = "  |  ⚠ YOLO" if self.yolo else ""
        header.update(f"{session.project}  |  {session.title}  |  {model}{suffix}")
        self._stop_background_wait()
        await chat_log.render_session(session)

    async def show_new_session(self, project: str | None, model: str | None = None) -> None:
        """Show the empty state for a brand-new conversation."""
        suffix = f" (model: {model})" if model else ""
        suffix += "  |  ⚠ YOLO" if self.yolo else ""
        self.query_one("#chat-header", Static).update(
            f"New session{' in ' + project if project else ''} — type a message to start{suffix}"
        )
        self._stop_background_wait()
        self.query_one("#chat-status", Static).update("")
        await self.query_one(ChatLog).clear_chat()

    async def set_running(self, running: bool) -> None:
        self._running = running
        if not running:
            self._stop_background_wait()
            self._stop_spinner()
            self.query_one("#chat-status", Static).update("")
        else:
            self._start_spinner()
        self.query_one(Input).disabled = running

    async def show_error(self, text: str) -> None:
        self.query_one("#chat-status", Static).update(f"Error: {text}")

    async def add_user_message(self, content: str) -> None:
        await self.query_one(ChatLog).add_user_message(content)

    async def update_assistant_message(self, text: str) -> None:
        await self.query_one(ChatLog).update_assistant_message(text)

    async def append_assistant_delta(self, delta: str) -> None:
        await self.query_one(ChatLog).append_assistant_delta(delta)

    async def finish_assistant(self) -> None:
        await self.query_one(ChatLog).finish_assistant_message()

    async def begin_tool_call(self, tool: dict) -> None:
        process_id = tool.get("process_id")
        if process_id:
            self._background_commands[process_id] = tool.get("detail") or ""
        await self.query_one(ChatLog).begin_tool_call(tool)

    async def append_tool_output(self, text: str) -> None:
        await self.query_one(ChatLog).append_tool_output(text)

    async def finish_tool_call(self, tool: dict) -> None:
        if tool.get("source") in (
            "unifiedExecInteraction",
            "unified_exec_interaction",
        ):
            self._stop_background_wait()
        await self.query_one(ChatLog).finish_tool_call(tool)

    async def clear_pending_tool(self) -> None:
        self._stop_background_wait()
        await self.query_one(ChatLog)._clear_pending_tool()

    def begin_background_waiting(self, process_id: str | None = None) -> None:
        """Show the blinking ``Waiting for background terminal`` status."""
        self._stop_spinner()
        if self._bg_wait is None:
            self._bg_wait = BackgroundWaitStatus(
                self, "#chat-status", updates_title=True
            )
        detail = ""
        if process_id:
            detail = self._background_commands.get(process_id, "")
        self._bg_wait.start(detail)

    def end_background_waiting(self, item_id: str | None = None) -> None:
        """Stop blinking and remember an interaction, if one was sent."""
        if item_id:
            self.query_one(ChatLog).mark_tool_interaction(item_id)
        self._stop_background_wait()

    def _stop_background_wait(self) -> None:
        if self._bg_wait is not None:
            self._bg_wait.stop("")
        if self._running:
            self._start_spinner()

    def _start_spinner(self) -> None:
        if self._bg_wait is not None and self._bg_wait.is_active():
            return  # the waiting indicator owns the status line
        if self._spinner is None:
            self._spinner = WorkingSpinner(
                self, "#chat-status", updates_title=True
            )
        self._spinner.start()

    def _stop_spinner(self) -> None:
        if self._spinner is not None:
            self._spinner.stop("")


class WatchPane(Widget):
    """Read-only split view of a second session next to the active chat."""

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self._running = False
        self._background_commands: dict[str, str] = {}
        self._bg_wait: BackgroundWaitStatus | None = None
        self._spinner: WorkingSpinner | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="watch-header", classes="watch-header")
        yield Static("", id="watch-status", classes="watch-status")
        yield ChatLog(id="watch-log")

    async def show_session(self, session: Session | None) -> None:
        header = self.query_one("#watch-header", Static)
        status = self.query_one("#watch-status", Static)
        chat_log = self.query_one("#watch-log", ChatLog)
        if session is None:
            header.update("")
            status.update("")
            self._stop_background_wait()
            await chat_log.clear_chat()
            return
        model = session.effective_model or "codex"
        header.update(f"{session.project}  |  {session.title}  |  {model}")
        status.update("")
        self._stop_background_wait()
        await chat_log.render_session(session)

    async def set_running(self, running: bool) -> None:
        self._running = running
        if not running:
            self._stop_background_wait()
            self._stop_spinner()
            self.query_one("#watch-status", Static).update("")
        else:
            self._start_spinner()

    async def begin_stream(self, text: str) -> None:
        """Render the already-accumulated stream for the watched session."""
        chat_log = self.query_one("#watch-log", ChatLog)
        await chat_log.begin_assistant_message()
        if text:
            await chat_log.update_assistant_message(text)

    async def append_assistant_delta(self, delta: str) -> None:
        await self.query_one("#watch-log", ChatLog).append_assistant_delta(delta)

    async def finish_assistant(self) -> None:
        await self.query_one("#watch-log", ChatLog).finish_assistant_message()

    async def begin_tool_call(self, tool: dict) -> None:
        process_id = tool.get("process_id")
        if process_id:
            self._background_commands[process_id] = tool.get("detail") or ""
        await self.query_one("#watch-log", ChatLog).begin_tool_call(tool)

    async def append_tool_output(self, text: str) -> None:
        await self.query_one("#watch-log", ChatLog).append_tool_output(text)

    async def finish_tool_call(self, tool: dict) -> None:
        if tool.get("source") in (
            "unifiedExecInteraction",
            "unified_exec_interaction",
        ):
            self._stop_background_wait()
        await self.query_one("#watch-log", ChatLog).finish_tool_call(tool)

    def begin_background_waiting(self, process_id: str | None = None) -> None:
        """Show the blinking ``Waiting for background terminal`` status."""
        self._stop_spinner()
        if self._bg_wait is None:
            self._bg_wait = BackgroundWaitStatus(self, "#watch-status")
        detail = ""
        if process_id:
            detail = self._background_commands.get(process_id, "")
        self._bg_wait.start(detail)

    def end_background_waiting(self, item_id: str | None = None) -> None:
        """Stop blinking and remember an interaction, if one was sent."""
        if item_id:
            self.query_one("#watch-log", ChatLog).mark_tool_interaction(item_id)
        self._stop_background_wait()

    def _stop_background_wait(self) -> None:
        if self._bg_wait is not None:
            self._bg_wait.stop("")
        if self._running:
            self._start_spinner()

    def _start_spinner(self) -> None:
        if self._bg_wait is not None and self._bg_wait.is_active():
            return  # the waiting indicator owns the status line
        if self._spinner is None:
            self._spinner = WorkingSpinner(self, "#watch-status")
        self._spinner.start()

    def _stop_spinner(self) -> None:
        if self._spinner is not None:
            self._spinner.stop("")
