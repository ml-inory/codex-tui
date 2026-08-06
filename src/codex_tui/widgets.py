"""Reusable widgets for the codex-tui interface."""

from __future__ import annotations

from typing import Iterable

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, ListItem, ListView, Markdown, Static

from codex_tui.sessions import Session


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

    async def set_sessions(self, sessions: Iterable[Session]) -> None:
        self._session_by_item = {}
        session_list = self.query_one("#session-list", ListView)
        await session_list.clear()
        if not sessions:
            await self._show_hint("No sessions for this project yet.")
            return
        for index, session in enumerate(sessions):
            label = f"{session.timestamp[11:16] if len(session.timestamp) >= 16 else ''}  {session.title}"
            item = ListItem(Static(label, classes="session-label"))
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
        self._pending_markdown: Markdown | None = None

    async def render_session(self, session: Session) -> None:
        await self.clear_chat()
        for message in session.messages:
            await self.add_message(message.role, message.content)
        self.scroll_end(animate=False)

    async def clear_chat(self) -> None:
        await self.remove_children(self.children)

    async def add_message(self, role: str, content: str) -> None:
        if role == "user":
            row = Vertical(
                Static("You", classes="role user"),
                Static(content, classes="bubble user"),
                classes="row user",
            )
        else:
            row = Vertical(
                Static("Codex", classes="role assistant"),
                Markdown(content, classes="md"),
                classes="row assistant",
            )
        await self.mount(row)
        self.scroll_end(animate=False)

    async def add_user_message(self, content: str) -> None:
        await self.add_message("user", content)

    async def begin_assistant_message(self) -> None:
        markdown = Markdown("", classes="md")
        row = Vertical(
            Static("Codex", classes="role assistant"),
            markdown,
            classes="row assistant",
        )
        await self.mount(row)
        self._pending_markdown = markdown
        self.scroll_end(animate=False)

    async def update_assistant_message(self, text: str) -> None:
        if self._pending_markdown is None:
            await self.begin_assistant_message()
        assert self._pending_markdown is not None
        self._pending_markdown.update(text)
        self.scroll_end(animate=False)

    async def finish_assistant_message(self) -> None:
        self._pending_markdown = None


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
        model = session.model or "codex"
        header.update(f"{session.project}  |  {session.title}  |  {model}")
        await chat_log.render_session(session)

    async def show_new_session(self, project: str | None) -> None:
        """Show the empty state for a brand-new conversation."""
        self.query_one("#chat-header", Static).update(
            f"New session{' in ' + project if project else ''} — type a message to start"
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

    async def finish_assistant(self) -> None:
        await self.query_one(ChatLog).finish_assistant_message()
