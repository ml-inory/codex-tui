"""Reusable widgets for the codex-tui interface."""

from __future__ import annotations

from typing import Iterable
from pathlib import Path

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, ListItem, ListView, Markdown, Static

from codex_tui.sessions import (
    Message as SessionMessage,
    Session,
    is_injected_message,
)


DEFAULT_WINDOW = 80


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

    def compose(self) -> ComposeResult:
        yield Static("PROJECTS", classes="section-title")
        yield ListView(id="project-list")
        yield Static("SESSIONS", classes="section-title")
        yield ListView(id="session-list")
        yield Static(
            "No projects yet. Start a session to see it here.",
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
    ) -> None:
        finished = finished or set()
        self._session_by_item = {}
        session_list = self.query_one("#session-list", ListView)
        await session_list.clear()
        if not sessions:
            await self._show_hint("No sessions for this project yet.")
            return
        for index, session in enumerate(sessions):
            marker = "● " if session.id in finished else ""
            label = (
                f"{marker}"
                f"{session.timestamp[11:16] if len(session.timestamp) >= 16 else ''}"
                f"  {session.title}"
            )
            classes = "session-label"
            if session.id in finished:
                classes += " finished"
            item = ListItem(Static(label, classes=classes))
            item.id = f"item-{index}"
            self._session_by_item[item.id] = session
            await session_list.append(item)
        session_list.index = 0

    async def _show_hint(self, text: str) -> None:
        hint = self.query_one("#sidebar-hint", Static)
        hint.update(text)
        hint.display = bool(text)


class ChatLog(VerticalScroll):
    """Scrollable conversation area rendered as user bubbles and markdown."""

    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self._pending_stream: Static | None = None
        self._pending_label: Static | None = None
        self._pending_text = ""
        self._messages: list[SessionMessage] = []
        self._window = DEFAULT_WINDOW
        # One persistent container per rendered session. Switching sessions
        # only toggles ``display``, so the layout is computed once per session
        # instead of on every switch.
        self._containers: dict[str, VerticalScroll] = {}
        self._active_id: str | None = None
        self._rendered_id: str | None = None
        self._rendered_signature: tuple[int, str, int] | None = None

    async def _ensure_active_container(self) -> VerticalScroll:
        """Return the container for the current view, mounting it on demand."""
        if self._active_id is not None and self._active_id in self._containers:
            return self._containers[self._active_id]
        container = VerticalScroll(id="session-new")
        await self.mount(container)
        container._window = DEFAULT_WINDOW
        self._containers[""] = container
        self._active_id = ""
        return container

    async def render_session(self, session: Session) -> None:
        self._messages = [
            message
            for message in session.messages
            if not (message.role == "user" and is_injected_message(message.content))
        ]
        old_id = self._rendered_id
        container = self._containers.get(session.id)
        if container is None:
            container = VerticalScroll(id=f"session-{session.id}")
            await self.mount(container)
            self._containers[session.id] = container
            container._window = DEFAULT_WINDOW
        self._active_id = session.id
        for cid, cached in self._containers.items():
            if cid != session.id:
                cached.display = False
        container.display = True
        self._window = container._window
        signature = self._message_signature()
        container_signature = getattr(container, "_signature", None)
        stale_stream = (
            self._pending_stream is not None
            and self._pending_stream in container.children
        )
        if (
            stale_stream
            or container_signature != signature
            or not container.children
        ):
            await self._render_window()
        container._signature = signature
        self._rendered_signature = signature
        self._rendered_id = session.id
        container.scroll_end(animate=False)

    def _message_signature(self) -> tuple[int, str, int]:
        """Cheap fingerprint of the rendered message window."""
        if not self._messages:
            return (0, "", 0)
        window = self._messages[-self._window :]
        last = window[-1]
        return (len(window), last.item_id or "", len(last.content))

    async def _render_window(self) -> None:
        await self.clear_chat()
        container = await self._ensure_active_container()
        start = max(0, len(self._messages) - self._window)
        rows: list[Widget] = []
        if start > 0:
            rows.append(
                Static(
                    f"[dim]▲ {start} 条更早消息 · 按 F7 加载[/]",
                    id="earlier-hint",
                    classes="earlier-hint",
                )
            )
        for message in self._messages[start:]:
            rows.extend(self._build_rows(message.role, message.content))
        if rows:
            await container.mount(*rows)
            container.refresh(layout=True)
        container.scroll_end(animate=False)
        container._window = self._window

    async def load_earlier(self) -> bool:
        """Expand the visible window upward; returns True if more was loaded."""
        if len(self._messages) <= self._window:
            return False
        old_start = max(0, len(self._messages) - self._window)
        self._window = min(len(self._messages), self._window + DEFAULT_WINDOW)
        await self._render_window()
        new_start = max(0, len(self._messages) - self._window)
        offset = old_start - new_start
        widgets = [
            child
            for child in (await self._ensure_active_container()).children
            if not (isinstance(child, Static) and child.id == "earlier-hint")
        ]
        if 0 <= offset < len(widgets):
            self.scroll_to_widget(widgets[offset], animate=False)
        else:
            self.scroll_end(animate=False)
        return True

    async def clear_chat(self) -> None:
        container = await self._ensure_active_container()
        await container.remove_children(container.children)
        container._signature = None
        self._pending_stream = None
        self._pending_label = None
        self._pending_text = ""
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
            # Codex CLI style: cyan "You" label, plain text.
            return [
                Static("You", classes="user-label"),
                Static(escape(content), classes="user-body"),
            ]
        # Codex CLI style: magenta "Codex" label above the markdown body.
        return [
            Static("Codex", classes="assistant-label"),
            Markdown(content, classes="assistant-body"),
        ]

    async def add_message(self, role: str, content: str) -> None:
        container = await self._ensure_active_container()
        # A new turn starts from the transcript again: drop any previously
        # rendered rows so the new message is not appended to a stale view.
        if container.children:
            await container.remove_children(container.children)
            self._pending_stream = None
            self._pending_label = None
            self._pending_text = ""
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
            for widget in (self._pending_label, self._pending_stream):
                if widget is not None:
                    try:
                        await widget.remove()
                    except Exception:
                        pass
            self._pending_label = None
            self._pending_stream = None
            self._pending_text = ""
        label = Static("Codex", classes="assistant-label")
        await container.mount(label)
        self._pending_label = label
        stream = Static(Text(""), classes="assistant-body streaming")
        await container.mount(stream)
        self._pending_stream = stream
        self._pending_text = ""
        container.scroll_end(animate=False)

    async def append_assistant_delta(self, delta: str) -> None:
        """Append a streamed chunk to the open assistant message."""
        if self._pending_stream is None:
            await self.begin_assistant_message()
        assert self._pending_stream is not None
        container = await self._ensure_active_container()
        self._pending_text += delta
        self._pending_stream.update(Text(self._pending_text))
        container.scroll_end(animate=False)

    async def update_assistant_message(self, text: str) -> None:
        """Set the whole assistant body at once (non-streaming fallback)."""
        if self._pending_stream is None:
            await self.begin_assistant_message()
        assert self._pending_stream is not None
        container = await self._ensure_active_container()
        self._pending_text = text
        self._pending_stream.update(Text(text))
        container.scroll_end(animate=False)

    async def finish_assistant_message(self) -> None:
        """Render the finished assistant message as Markdown."""
        if self._pending_stream is not None:
            body = self._pending_text
            await self._pending_stream.remove()
            self._pending_stream = None
            self._pending_label = None
            self._pending_text = ""
            markdown = Markdown(body, classes="assistant-body")
            container = await self._ensure_active_container()
            await container.mount(markdown)
            container.scroll_end(animate=False)


class ChatView(Widget):
    """Right panel: header, chat log, and prompt input."""

    def compose(self) -> ComposeResult:
        yield Static("Select a project and session", id="chat-header")
        yield Static("", id="chat-status")
        yield ChatLog(id="chat-log")
        yield Input(placeholder="Message Codex… (Enter to send)", id="prompt-input")

    async def show_session(self, session: Session | None) -> None:
        header = self.query_one("#chat-header", Static)
        chat_log = self.query_one(ChatLog)
        if session is None:
            header.update("Select a project and session")
            await chat_log.clear_chat()
            return
        model = session.effective_model or "codex"
        header.update(f"{session.project}  |  {session.title}  |  {model}")
        await chat_log.render_session(session)

    async def show_new_session(self, project: str | None, model: str | None = None) -> None:
        """Show the empty state for a brand-new conversation."""
        suffix = f" (model: {model})" if model else ""
        self.query_one("#chat-header", Static).update(
            f"New session{' in ' + project if project else ''} — type a message to start{suffix}"
        )
        self.query_one("#chat-status", Static).update("")
        await self.query_one(ChatLog).clear_chat()

    async def set_running(self, running: bool) -> None:
        status = self.query_one("#chat-status", Static)
        status.update("Codex is working…" if running else "")
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


class WatchPane(Widget):
    """Read-only split view of a second session next to the active chat."""

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
            await chat_log.clear_chat()
            return
        model = session.effective_model or "codex"
        header.update(f"{session.project}  |  {session.title}  |  {model}")
        status.update("")
        await chat_log.render_session(session)

    async def set_running(self, running: bool) -> None:
        self.query_one("#watch-status", Static).update(
            "Codex is working…" if running else ""
        )

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
