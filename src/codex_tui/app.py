"""Application shell for codex-tui."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import time

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Input

from codex_tui.overrides import Overrides
from codex_tui.models import load_model_catalog
from codex_tui.runner import CodexRunner, CodexRunError
from codex_tui.screens import ModelScreen, RenameScreen
from codex_tui.sessions import Session, SessionStore
from codex_tui.widgets import ChatView, Sidebar


class CodexTuiApp(App[None]):
    """Codex Desktop-like TUI: sidebar for projects/sessions, chat on the right."""

    CSS_PATH = "app.css"
    TITLE = "codex-tui"

    BINDINGS = [
        Binding("tab", "focus_next", "Next pane", show=True),
        Binding("shift+tab", "focus_previous", "Prev pane", show=True),
        # Ctrl/function keys so the actions work while the prompt input has
        # focus (single letters would be typed into the input instead).
        Binding("ctrl+n", "new_session", "New session", show=True),
        Binding("ctrl+d", "delete_session", "Delete session", show=True),
        Binding("ctrl+r", "rename_session", "Rename", show=True),
        Binding("f3", "pick_model", "Model", show=True),
        Binding("f5", "refresh_sessions", "Refresh", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(
        self,
        sessions_dir: Path | None = None,
        runner: CodexRunner | None = None,
        trash_dir: Path | None = None,
        overrides_path: Path | None = None,
        model_catalog_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.store = SessionStore(sessions_dir, trash_dir)
        self.overrides = Overrides.load(overrides_path)
        self.models = load_model_catalog(model_catalog_path)
        self.runner = runner or CodexRunner(
            sandbox=os.environ.get("CODEX_TUI_SANDBOX", "workspace-write")
        )
        self.current_session: Session | None = None
        self.current_project: str | None = None
        # Named to avoid Textual's internal `_running` app flag.
        self.turn_active = False
        self.pending_model: str | None = None
        self._pending_delete: Session | None = None
        self._pending_delete_at = 0.0

    def compose(self) -> ComposeResult:
        yield Sidebar(id="sidebar")
        yield ChatView(id="chat-view")
        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_sessions()
        self.set_focus(self.query_one(Input))

    async def action_new_session(self) -> None:
        if self.turn_active:
            return
        self.current_session = None
        self._pending_delete = None
        await self.query_one(ChatView).show_new_session(self.current_project)
        self.set_focus(self.query_one(Input))

    def action_pick_model(self) -> None:
        if self.turn_active:
            return
        if not self.models:
            self.notify("No models found in catalog", severity="warning")
            return
        current = (
            self.current_session.effective_model
            if self.current_session is not None
            else self.pending_model
        )

        def on_pick(model_slug: str | None) -> None:
            if model_slug == ModelScreen.CLEAR:
                if self.current_session is not None:
                    self.overrides.delete(self.current_session.id, "model")
                    self.run_worker(self.refresh_sessions())
                else:
                    self.pending_model = None
                self.notify("Using default model")
                return
            if model_slug is None:
                return
            if self.current_session is not None:
                self.overrides.set(self.current_session.id, "model", model_slug)
                self.run_worker(self.refresh_sessions())
            else:
                self.pending_model = model_slug
                self.notify(f"Model {model_slug} will apply to the new session")

        self.push_screen(ModelScreen(self.models, current), on_pick)

    def action_delete_session(self) -> None:
        if self.turn_active:
            return
        now = time.monotonic()
        if (
            self._pending_delete is None
            or self._pending_delete is not self.current_session
            or now - self._pending_delete_at > 4.0
        ):
            if self.current_session is None:
                self.notify("No session selected", severity="warning")
                return
            self._pending_delete = self.current_session
            self._pending_delete_at = now
            self.notify(
                f"Press d again to delete '{self.current_session.title[:40]}'",
                severity="warning",
                timeout=4,
            )
            return
        target = self.current_session
        self._pending_delete = None
        assert target is not None
        self.store.delete_session(target)
        self.notify("Session deleted")
        self.run_worker(self.refresh_sessions())

    async def action_refresh_sessions(self) -> None:
        await self.refresh_sessions()

    async def refresh_sessions(self) -> None:
        projects = self.store.list_projects()
        sidebar = self.query_one(Sidebar)
        await sidebar.set_projects(projects)
        if not projects:
            self.current_project = None
            self.current_session = None
            await self.query_one(ChatView).show_session(None)
            return
        self.current_project = projects[0]
        sessions = self.store.sessions_for_project(projects[0])
        for index, session in enumerate(sessions):
            sessions[index] = self.overrides.apply(session)
        await sidebar.set_sessions(sessions)
        if sessions:
            await self.open_session(sessions[0])
        else:
            self.current_session = None
            await self.query_one(ChatView).show_new_session(projects[0])

    async def open_session(self, session: Session) -> None:
        session = self.overrides.apply(session)
        self.current_session = session
        self.current_project = session.project
        await self.query_one(ChatView).show_session(session)

    def on_sidebar_project_selected(self, message: Sidebar.ProjectSelected) -> None:
        self.run_worker(self._project_selected(message.project), exclusive=True)

    async def _project_selected(self, project: str) -> None:
        self.current_project = project
        sessions = self.store.sessions_for_project(project)
        for index, session in enumerate(sessions):
            sessions[index] = self.overrides.apply(session)
        await self.query_one(Sidebar).set_sessions(sessions)
        if sessions:
            await self.open_session(sessions[0])
        else:
            self.current_session = None
            await self.query_one(ChatView).show_new_session(project)

    def on_sidebar_session_selected(self, message: Sidebar.SessionSelected) -> None:
        self.run_worker(self.open_session(message.session))

    def action_rename_session(self) -> None:
        if self.turn_active or self.current_session is None:
            self.notify("No session selected", severity="warning")
            return
        current = self.current_session

        def on_rename(new_title: str | None) -> None:
            if new_title:
                self.overrides.set(current.id, "title", new_title)
                self.run_worker(self.refresh_sessions())

        self.push_screen(RenameScreen(current.title), on_rename)

    @on(Input.Submitted, "#prompt-input")
    def _on_prompt_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        event.input.value = ""
        if prompt:
            self.run_worker(self._send_prompt(prompt), name="codex-turn", exclusive=True)

    async def _send_prompt(self, prompt: str) -> None:
        if self.turn_active:
            return
        self.turn_active = True
        chat = self.query_one(ChatView)
        await chat.set_running(True)
        await chat.add_user_message(prompt)

        project = self.current_project or str(Path.cwd())
        session_id = None
        if self.current_session is not None and self.current_session.project == project:
            session_id = self.current_session.id
        model = (
            self.current_session.effective_model
            if session_id and self.current_session is not None
            else self.pending_model
        )

        error: str | None = None
        thread_id: str | None = None
        try:
            async for event in self.runner.run_turn(
                project=project,
                prompt=prompt,
                session_id=session_id,
                model=model,
            ):
                if event.get("type") == "thread.started":
                    thread_id = str(event.get("thread_id") or "")
                if event.get("type") != "item.completed":
                    continue
                item = event.get("item") or {}
                if item.get("type") == "agent_message" and item.get("text"):
                    await chat.update_assistant_message(str(item["text"]))
        except CodexRunError as exc:
            error = str(exc)
        finally:
            self.turn_active = False
            if (
                session_id is None
                and thread_id
                and self.pending_model is not None
            ):
                self.overrides.set(thread_id, "model", self.pending_model)
                self.pending_model = None
            await chat.finish_assistant()
            await chat.set_running(False)
            await self.refresh_sessions()
            if error:
                await chat.show_error(error)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="codex-tui",
        description="A Codex Desktop-like terminal UI backed by the local codex CLI.",
    )
    parser.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write", "danger-full-access"),
        default=os.environ.get("CODEX_TUI_SANDBOX", "workspace-write"),
        help="Sandbox passed to `codex exec` (default: workspace-write).",
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Path to the codex CLI binary (default: codex).",
    )
    parser.add_argument(
        "--sessions-dir",
        default=None,
        help="Override the codex sessions directory (default: $CODEX_HOME/sessions).",
    )
    args = parser.parse_args()

    runner = CodexRunner(codex_bin=args.codex_bin, sandbox=args.sandbox)
    sessions_dir = Path(args.sessions_dir) if args.sessions_dir else None
    CodexTuiApp(sessions_dir=sessions_dir, runner=runner).run()
