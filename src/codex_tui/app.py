"""Application shell for codex-tui."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer

from codex_tui.sessions import Session, SessionStore
from codex_tui.widgets import ChatView, Sidebar


class CodexTuiApp(App[None]):
    """Codex Desktop-like TUI: sidebar for projects/sessions, chat on the right."""

    CSS_PATH = "app.css"
    TITLE = "codex-tui"

    BINDINGS = [
        Binding("tab", "focus_next", "Next pane", show=True),
        Binding("shift+tab", "focus_previous", "Prev pane", show=True),
        Binding("r", "refresh_sessions", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(self, sessions_dir: Path | None = None) -> None:
        super().__init__()
        self.store = SessionStore(sessions_dir)
        self.current_session: Session | None = None

    def compose(self) -> ComposeResult:
        yield Sidebar(id="sidebar")
        yield ChatView(id="chat-view")
        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_sessions()

    async def action_refresh_sessions(self) -> None:
        await self.refresh_sessions()

    async def refresh_sessions(self) -> None:
        projects = self.store.list_projects()
        sidebar = self.query_one(Sidebar)
        await sidebar.set_projects(projects)
        if projects:
            sessions = self.store.sessions_for_project(projects[0])
            await sidebar.set_sessions(sessions)
            if sessions:
                await self.open_session(sessions[0])

    async def open_session(self, session: Session) -> None:
        self.current_session = session
        await self.query_one(ChatView).show_session(session)

    def on_sidebar_project_selected(self, message: Sidebar.ProjectSelected) -> None:
        self.run_worker(self._project_selected(message.project), exclusive=True)

    async def _project_selected(self, project: str) -> None:
        sessions = self.store.sessions_for_project(project)
        await self.query_one(Sidebar).set_sessions(sessions)
        if sessions:
            await self.open_session(sessions[0])

    def on_sidebar_session_selected(self, message: Sidebar.SessionSelected) -> None:
        self.run_worker(self.open_session(message.session))


def main() -> None:
    CodexTuiApp().run()
