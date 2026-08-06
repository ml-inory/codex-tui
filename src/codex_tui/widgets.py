"""Reusable widgets for the codex-tui interface."""

from __future__ import annotations

from typing import Iterable

from rich.markup import escape
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, ListItem, ListView, Markdown, Static

from codex_tui.sessions import Message, Session, is_injected_message


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

    async def set_projects(self, projects: Iterable[str]) -> None:
        self._projects = list(projects)
        project_list = self.query_one("#project-list", ListView)
        await project_list.clear()
        if not self._projects:
            await self._show_hint("No projects yet. Start a session to see it here.")
            await self.query_one("#session-list", ListView).clear()
            return
        await self._show_hint("")
        for project in self._projects:
            await project_list.append(ListItem(Static(project, classes="project-label")))
        project_list.index = 0

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
        self._pending_text = ""
        self._messages: list[Message] = []
        self._window = DEFAULT_WINDOW

    async def render_session(self, session: Session) -> None:
        self._messages = [
            message
            for message in session.messages
            if not (message.role == "user" and is_injected_message(message.content))
        ]
        self._window = DEFAULT_WINDOW
        await self._render_window()

    async def _render_window(self) -> None:
        await self.clear_chat()
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
            rows.append(self._build_row(message.role, message.content))
        if rows:
            await self.mount(*rows)
            self.refresh(layout=True)
        self.scroll_end(animate=False)

    async def load_earlier(self) -> bool:
        """Expand the visible window upward; returns True if more was loaded."""
        if len(self._messages) <= self._window:
            return False
        old_start = max(0, len(self._messages) - self._window)
        self._window += DEFAULT_WINDOW
        await self._render_window()
        new_start = max(0, len(self._messages) - self._window)
        offset = old_start - new_start
        widgets = [
            child
            for child in self.children
            if not (isinstance(child, Static) and child.id == "earlier-hint")
        ]
        if 0 <= offset < len(widgets):
            self.scroll_to_widget(widgets[offset], animate=False)
        else:
            self.scroll_end(animate=False)
        return True

    async def clear_chat(self) -> None:
        await self.remove_children(self.children)
        self._pending_stream = None
        self._pending_text = ""

    def _build_row(self, role: str, content: str) -> Widget:
        if role == "user":
            # Flat widgets are required: nested containers inside a
            # VerticalScroll collapse to one line in Textual 8.2.8 once the
            # content exceeds the viewport (long conversations).
            return Static(f"[b]You[/b]\n{escape(content)}", classes="bubble user")
        return Markdown(f"**Codex**\n\n{content}", classes="md")

    async def add_message(self, role: str, content: str) -> None:
        await self.mount(self._build_row(role, content))
        self.scroll_end(animate=False)

    async def add_user_message(self, content: str) -> None:
        await self.add_message("user", content)

    async def begin_assistant_message(self) -> None:
        """Open a streaming row for the assistant's reply.

        While the model streams we render plain text (``Text`` is not parsed
        as markup) and only parse Markdown once at the end, so token-level
        updates stay cheap.
        """
        stream = Static(Text(""), classes="bubble streaming")
        await self.mount(stream)
        self._pending_stream = stream
        self._pending_text = ""
        self.scroll_end(animate=False)

    async def append_assistant_delta(self, delta: str) -> None:
        """Append a streamed chunk to the open assistant message."""
        if self._pending_stream is None:
            await self.begin_assistant_message()
        assert self._pending_stream is not None
        self._pending_text += delta
        self._pending_stream.update(Text(self._pending_text))
        self.scroll_end(animate=False)

    async def update_assistant_message(self, text: str) -> None:
        """Set the whole assistant body at once (non-streaming fallback)."""
        if self._pending_stream is None:
            await self.begin_assistant_message()
        assert self._pending_stream is not None
        self._pending_text = text
        self._pending_stream.update(Text(text))
        self.scroll_end(animate=False)

    async def finish_assistant_message(self) -> None:
        """Render the finished assistant message as Markdown."""
        if self._pending_stream is not None:
            body = self._pending_text
            await self._pending_stream.remove()
            self._pending_stream = None
            self._pending_text = ""
            markdown = Markdown(
                f"**Codex**\n\n{body}" if body else "**Codex**", classes="md"
            )
            await self.mount(markdown)
            self.scroll_end(animate=False)


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
