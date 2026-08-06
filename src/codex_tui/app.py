"""Application shell for codex-tui."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
import time

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Input, ListView

from codex_tui.overrides import Overrides
from codex_tui.models import load_model_catalog
from codex_tui.runner import CodexRunner, CodexRunError
from codex_tui.screens import ModelScreen, QuickSwitchScreen, RenameScreen
from codex_tui.sessions import (
    INJECTED_PREFIXES,
    Session,
    SessionStore,
    generate_title,
    is_injected_message,
)
from codex_tui.streaming import InteractiveCodexRunner
from codex_tui.widgets import ChatLog, ChatView, Sidebar


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
        Binding("f7", "load_earlier", "Earlier", show=True),
        Binding("ctrl+o", "quick_switch", "Switch", show=True),
        Binding(
            "ctrl+up,alt+up",
            "previous_session",
            "Prev session",
            show=True,
        ),
        Binding(
            "ctrl+down,alt+down",
            "next_session",
            "Next session",
            show=True,
        ),
        Binding("ctrl+g", "jump_finished", "Finished", show=True),
        Binding("ctrl+y", "copy_last_reply", "Copy reply", show=True),
        Binding(
            "ctrl+shift+y",
            "copy_conversation",
            "Copy chat",
            show=True,
        ),
        Binding("escape", "interrupt_turn", "Interrupt", show=True),
        Binding("q", "quit", "Quit", show=True),
    ]

    def __init__(
        self,
        sessions_dir: Path | None = None,
        runner: CodexRunner | None = None,
        trash_dir: Path | None = None,
        overrides_path: Path | None = None,
        model_catalog_path: Path | None = None,
        fallback_runner: CodexRunner | None = None,
    ) -> None:
        super().__init__()
        self.store = SessionStore(sessions_dir, trash_dir)
        self.overrides = Overrides.load(overrides_path)
        self.models = load_model_catalog(model_catalog_path)
        self.runner = runner or CodexRunner(
            sandbox=os.environ.get("CODEX_TUI_SANDBOX", "workspace-write")
        )
        self.fallback_runner = fallback_runner
        self.current_session: Session | None = None
        self.current_project: str | None = None
        self._project_sessions: list[Session] = []
        # Background turns: several sessions may run at once. Keys are
        # session ids, or "new:<project>" while a brand-new thread is still
        # being created.
        self._active_sessions: set[str] = set()
        self._active_new_views: set[str] = set()
        # Which stream (by thread id) is currently rendered in the chat view.
        self._view_stream_key: str | None = None
        self._stream_buffers: dict[str, str] = {}
        # Session id -> title of turns that finished while not being viewed.
        self._finished_sessions: dict[str, str] = {}
        # Serializes session-view mutations: with background turns several
        # workers finish concurrently and must not interleave DOM updates.
        self._view_lock = asyncio.Lock()
        # Number of turn workers that fully finished; lets tests wait for the
        # worker (including its final refresh) to complete before teardown.
        self._completed_turns = 0
        self.pending_model: str | None = None
        self._pending_delete: Session | None = None
        self._pending_delete_at = 0.0

    @property
    def turn_active(self) -> bool:
        """True when any session currently has a running turn."""
        return bool(self._active_sessions or self._active_new_views)

    def _current_running(self) -> bool:
        return (
            self.current_session is not None
            and self.current_session.id in self._active_sessions
        )

    def compose(self) -> ComposeResult:
        yield Sidebar(id="sidebar")
        yield ChatView(id="chat-view")
        yield Footer()

    async def on_mount(self) -> None:
        await self._backfill_titles()
        if getattr(self.runner, "interactive", False):
            try:
                await self.runner.start()
            except (CodexRunError, OSError) as exc:
                if self.fallback_runner is not None:
                    self.notify(
                        f"Streaming backend unavailable ({exc}); using exec mode",
                        severity="warning",
                        timeout=8,
                    )
                    self.runner = self.fallback_runner
                else:
                    self.notify(
                        f"Streaming backend failed: {exc}",
                        severity="error",
                        timeout=8,
                    )
        await self.refresh_sessions()
        self.set_focus(self.query_one(Input))

    async def on_unmount(self) -> None:
        stop = getattr(self.runner, "stop", None)
        if stop is not None:
            await stop()

    async def _backfill_titles(self) -> None:
        """Persist meaningful titles for sessions whose first user message was
        system-injected context (idempotent; skipped once an override exists)."""
        for session in self.store.list_sessions():
            if session.title_override:
                continue
            first_user = next(
                (m for m in session.messages if m.role == "user"), None
            )
            if first_user is None:
                continue
            raw_first_line = (
                first_user.content.lstrip().splitlines()[0]
                if first_user.content.strip()
                else ""
            )
            meaningful = bool(raw_first_line) and not raw_first_line.startswith(
                INJECTED_PREFIXES
            )
            if meaningful:
                continue
            generated = generate_title(session.messages)
            if generated:
                self.overrides.set(session.id, "title", generated)

    async def action_new_session(self) -> None:
        if self._current_running():
            self.notify("当前会话正在运行，先切走再新建", severity="warning")
            return
        self.current_session = None
        self._pending_delete = None
        await self._show_new_session_view(self.current_project)
        self.set_focus(self.query_one(Input))

    def action_pick_model(self) -> None:
        if self._current_running():
            self.notify("当前会话正在运行", severity="warning")
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

    async def action_load_earlier(self) -> None:
        if self._current_running():
            self.notify("当前会话正在运行", severity="warning")
            return
        loaded = await self.query_one(ChatLog).load_earlier()
        if not loaded:
            self.notify("Already at the beginning", severity="warning")

    def action_delete_session(self) -> None:
        if self._current_running():
            self.notify("当前会话正在运行，不能删除", severity="warning")
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
        async with self._view_lock:
            await self._refresh_sessions_locked()

    async def _refresh_sessions_locked(self) -> None:
        projects = self.store.list_projects()
        sidebar = self.query_one(Sidebar)
        await sidebar.set_projects(projects)
        if not projects:
            self.current_project = None
            self.current_session = None
            self._project_sessions = []
            await self.query_one(ChatView).show_session(None)
            await self.query_one(ChatView).set_running(False)
            return
        if self.current_project is None or self.current_project not in projects:
            self.current_project = projects[0]
        sessions = self.store.sessions_for_project(self.current_project)
        for index, session in enumerate(sessions):
            sessions[index] = self.overrides.apply(session)
        self._project_sessions = sessions
        await sidebar.set_sessions(sessions, finished=set(self._finished_sessions))
        if sessions:
            await self._open_preferred_session_locked(sessions)
        else:
            self.current_session = None
            await self._show_new_session_view_locked(self.current_project)

    async def open_session(self, session: Session) -> None:
        async with self._view_lock:
            await self._open_session_locked(session)

    async def _open_session_locked(self, session: Session) -> None:
        focus_input = self.focused is self.query_one("#prompt-input")
        session = self.overrides.apply(session)
        self.current_session = session
        self.current_project = session.project
        await self.query_one(ChatView).show_session(session)
        was_finished = self._finished_sessions.pop(session.id, None) is not None
        self._view_stream_key = None
        if session.id in self._stream_buffers:
            self._view_stream_key = session.id
            chat_log = self.query_one(ChatLog)
            await chat_log.begin_assistant_message()
            await chat_log.update_assistant_message(self._stream_buffers[session.id])
        await self.query_one(ChatView).set_running(
            session.id in self._active_sessions
        )
        self._sync_sidebar_selection(session)
        if was_finished:
            await self._rerender_session_list_locked()
        if focus_input:
            self.set_focus(self.query_one(Input))

    def on_sidebar_project_selected(self, message: Sidebar.ProjectSelected) -> None:
        self.run_worker(self._project_selected(message.project), exclusive=True)

    async def _project_selected(
        self, project: str, select_id: str | None = None
    ) -> None:
        async with self._view_lock:
            await self._project_selected_locked(project, select_id)

    async def _project_selected_locked(
        self, project: str, select_id: str | None = None
    ) -> None:
        self.current_project = project
        sessions = self.store.sessions_for_project(project)
        for index, session in enumerate(sessions):
            sessions[index] = self.overrides.apply(session)
        self._project_sessions = sessions
        await self.query_one(Sidebar).set_sessions(
            sessions, finished=set(self._finished_sessions)
        )
        if sessions:
            preferred = select_id
            if preferred is None and self.current_session is not None:
                preferred = self.current_session.id
            target = next(
                (s for s in sessions if s.id == preferred), None
            )
            await self._open_preferred_session_locked(sessions, preferred=target)
        else:
            self.current_session = None
            await self._show_new_session_view_locked(project)

    async def _open_preferred_session_locked(
        self,
        sessions: list[Session],
        preferred: Session | None = None,
    ) -> None:
        """Open the preferred session (or the newest) and sync the sidebar."""
        if preferred is None and self.current_session is not None:
            preferred = next(
                (s for s in sessions if s.id == self.current_session.id),
                None,
            )
        target = preferred or sessions[0]
        await self._open_session_locked(target)

    def _sync_sidebar_selection(self, session: Session) -> None:
        session_list = self.query_one("#session-list", ListView)
        for index, candidate in enumerate(self._project_sessions):
            if candidate.id == session.id:
                session_list.index = index
                return

    async def _show_new_session_view(self, project: str | None) -> None:
        async with self._view_lock:
            await self._show_new_session_view_locked(project)

    async def _show_new_session_view_locked(self, project: str | None) -> None:
        """Render the empty new-session view and reflect its running state."""
        await self.query_one(ChatView).show_new_session(project)
        key = f"new:{project}" if project else None
        if key is not None and key in self._active_new_views:
            self._view_stream_key = key
            await self.query_one(ChatView).set_running(True)
        else:
            self._view_stream_key = None
            await self.query_one(ChatView).set_running(False)

    async def _rerender_session_list_locked(self) -> None:
        """Redraw the session list with current finished markers (locked)."""
        if self.current_project is None:
            return
        sessions = [
            self.overrides.apply(session)
            for session in self.store.sessions_for_project(self.current_project)
        ]
        self._project_sessions = sessions
        await self.query_one(Sidebar).set_sessions(
            sessions, finished=set(self._finished_sessions)
        )

    async def _mark_finished(self, session_id: str) -> None:
        session = next(
            (s for s in self.store.list_sessions() if s.id == session_id),
            None,
        )
        title = session.title if session is not None else session_id[:8]
        was_current = (
            self.current_session is not None
            and self.current_session.id == session_id
        )
        if was_current:
            self._finished_sessions.pop(session_id, None)
        else:
            self._finished_sessions[session_id] = title
            self.notify(f"会话完成：{title}", timeout=6)
            async with self._view_lock:
                await self._rerender_session_list_locked()

    def on_sidebar_session_selected(self, message: Sidebar.SessionSelected) -> None:
        self.run_worker(self.open_session(message.session))

    def action_rename_session(self) -> None:
        if self.current_session is None:
            self.notify("No session selected", severity="warning")
            return
        if self._current_running():
            self.notify("当前会话正在运行，不能重命名", severity="warning")
            return
        current = self.current_session

        def on_rename(new_title: str | None) -> None:
            if new_title:
                self.overrides.set(current.id, "title", new_title)
                self.run_worker(self.refresh_sessions())

        self.push_screen(RenameScreen(current.title), on_rename)

    def action_interrupt_turn(self) -> None:
        if not self._current_running():
            return
        assert self.current_session is not None
        session_id = self.current_session.id
        self.notify("Interrupting…")
        self.run_worker(
            self._interrupt_turn(session_id), name="codex-interrupt"
        )

    async def _interrupt_turn(self, session_id: str) -> None:
        await self.runner.interrupt(thread_id=session_id)

    def action_quick_switch(self) -> None:
        sessions = [
            self.overrides.apply(session)
            for session in self.store.list_sessions()
        ][:200]
        if not sessions:
            self.notify("No sessions yet", severity="warning")
            return
        self.push_screen(QuickSwitchScreen(sessions), self._on_quick_switch)

    def _on_quick_switch(self, session: Session | None) -> None:
        if session is None:
            return
        if session.project == self.current_project:
            self.run_worker(self.open_session(session))
        else:
            self.run_worker(
                self._project_selected(session.project, select_id=session.id)
            )

    def _session_index(self) -> int:
        if self.current_session is None:
            return -1
        for index, session in enumerate(self._project_sessions):
            if session.id == self.current_session.id:
                return index
        return -1

    def action_next_session(self) -> None:
        self._cycle_session(1)

    def action_previous_session(self) -> None:
        self._cycle_session(-1)

    def _cycle_session(self, delta: int) -> None:
        if not self._project_sessions:
            return
        current = self._session_index()
        if current < 0:
            target = 0
        else:
            target = (current + delta) % len(self._project_sessions)
        session = self._project_sessions[target]
        self.run_worker(self.open_session(session))

    def action_jump_finished(self) -> None:
        """Jump to the most recently finished background session."""
        if not self._finished_sessions:
            self.notify("没有已完成的会话", severity="warning")
            return
        session_id, title = next(reversed(self._finished_sessions.items()))
        session = next(
            (s for s in self.store.list_sessions() if s.id == session_id),
            None,
        )
        if session is None:
            self._finished_sessions.pop(session_id, None)
            self.notify("该会话已不存在", severity="warning")
            return
        self.notify(f"已跳转：{title}", timeout=4)
        if session.project == self.current_project:
            self.run_worker(self.open_session(session))
        else:
            self.run_worker(
                self._project_selected(session.project, select_id=session.id)
            )

    def action_copy_last_reply(self) -> None:
        """Copy the current session's most recent Codex reply."""
        if self.current_session is None:
            self.notify("没有选中会话", severity="warning")
            return
        text = next(
            (
                message.content
                for message in reversed(self.current_session.messages)
                if message.role == "assistant"
            ),
            None,
        )
        if not text:
            self.notify("当前会话还没有 Codex 回复", severity="warning")
            return
        self.copy_to_clipboard(text)
        self.notify("已复制最后一条回复", timeout=3)

    def action_copy_conversation(self) -> None:
        """Copy the whole conversation as readable text."""
        if self.current_session is None:
            self.notify("没有选中会话", severity="warning")
            return
        parts: list[str] = []
        for message in self.current_session.messages:
            if message.role == "user" and is_injected_message(message.content):
                continue
            label = "You" if message.role == "user" else "Codex"
            parts.append(f"## {label}\n\n{message.content}")
        text = "\n\n---\n\n".join(parts)
        if not text.strip():
            self.notify("会话还没有内容", severity="warning")
            return
        self.copy_to_clipboard(text)
        self.notify(f"已复制整个会话（{len(parts)} 条消息）", timeout=3)

    @on(Input.Submitted, "#prompt-input")
    def _on_prompt_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        event.input.value = ""
        if prompt:
            self.run_worker(self._send_prompt(prompt))

    async def _send_prompt(self, prompt: str) -> None:
        project = self.current_project or str(Path.cwd())
        session_id = None
        if self.current_session is not None and self.current_session.project == project:
            session_id = self.current_session.id
        if session_id is not None:
            if session_id in self._active_sessions:
                self.notify("该会话正在运行中", severity="warning")
                return
        else:
            new_key = f"new:{project}"
            if new_key in self._active_new_views:
                self.notify("该会话正在启动中", severity="warning")
                return
        model = (
            self.current_session.effective_model
            if session_id and self.current_session is not None
            else self.pending_model
        )

        # Mark the turn active before the first await so a second Enter cannot
        # sneak a duplicate turn through.
        stream_key = session_id or f"new:{project}"
        self._view_stream_key = stream_key
        if session_id is None:
            self._active_new_views.add(f"new:{project}")
        else:
            self._active_sessions.add(session_id)

        chat = self.query_one(ChatView)
        await chat.add_user_message(prompt)
        await chat.set_running(True)

        error: str | None = None
        thread_id: str | None = None
        got_delta = False
        try:
            while True:
                try:
                    async for event in self.runner.run_turn(
                        project=project,
                        prompt=prompt,
                        session_id=session_id,
                        model=model,
                    ):
                        event_type = event.get("type")
                        if event_type == "thread.started":
                            thread_id = str(event.get("thread_id") or "")
                            if session_id is None and thread_id:
                                self._active_new_views.discard(
                                    f"new:{project}"
                                )
                                self._active_sessions.add(thread_id)
                                if self._view_stream_key == f"new:{project}":
                                    self._view_stream_key = thread_id
                        elif event_type == "agent_message.delta":
                            text = event.get("text") or ""
                            if text:
                                got_delta = True
                                key = thread_id or stream_key
                                self._stream_buffers[key] = (
                                    self._stream_buffers.get(key, "") + text
                                )
                                if self._view_stream_key == key:
                                    await chat.append_assistant_delta(text)
                        elif event_type == "item.completed":
                            item = event.get("item") or {}
                            if (
                                item.get("type") == "agent_message"
                                and item.get("text")
                                and not got_delta
                            ):
                                key = thread_id or stream_key
                                self._stream_buffers[key] = str(item["text"])
                                if self._view_stream_key == key:
                                    await chat.update_assistant_message(
                                        str(item["text"])
                                    )
                    break
                except CodexRunError as exc:
                    if (
                        getattr(self.runner, "interactive", False)
                        and self.fallback_runner is not None
                        and not got_delta
                        and thread_id is None
                    ):
                        await self.runner.stop()
                        self.runner = self.fallback_runner
                        self.notify(
                            "Streaming backend failed; retrying with codex exec",
                            severity="warning",
                            timeout=8,
                        )
                        continue
                    error = str(exc)
                    break
        finally:
            final_key = thread_id or session_id or f"new:{project}"
            if session_id is not None:
                self._active_sessions.discard(session_id)
            if thread_id:
                self._active_sessions.discard(thread_id)
            self._active_new_views.discard(f"new:{project}")
            if self._view_stream_key in (stream_key, final_key):
                await chat.finish_assistant()
            self._view_stream_key = None
            self._stream_buffers.pop(final_key, None)
            if (
                session_id is None
                and thread_id
                and self.pending_model is not None
            ):
                self.overrides.set(thread_id, "model", self.pending_model)
                self.pending_model = None
            await self.refresh_sessions()
            if error:
                await chat.show_error(error)
            if thread_id:
                await self._mark_finished(thread_id)
            if (
                self.current_session is not None
                and not self.current_session.title_override
            ):
                generated = generate_title(self.current_session.messages)
                if generated:
                    self.overrides.set(self.current_session.id, "title", generated)
                    await self.refresh_sessions()
            self._completed_turns += 1


def run_cli(argv: list[str] | None = None) -> int:
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
        "--mode",
        choices=("auto", "interactive", "exec"),
        default=os.environ.get("CODEX_TUI_MODE", "auto"),
        help=(
            "Turn backend: interactive uses `codex app-server` for streaming "
            "deltas, exec uses one-shot `codex exec --json` (default: auto)."
        ),
    )
    parser.add_argument(
        "--sessions-dir",
        default=None,
        help="Override the codex sessions directory (default: $CODEX_HOME/sessions).",
    )
    parser.add_argument(
        "--clean-trash",
        action="store_true",
        help="Permanently delete trashed session transcripts and exit.",
    )
    args = parser.parse_args(argv)

    if args.clean_trash:
        store = SessionStore(
            sessions_dir=Path(args.sessions_dir) if args.sessions_dir else None
        )
        removed = store.clean_trash()
        print(f"Removed {removed} trashed session file(s) from {store.trash_dir}")
        return 0

    fallback = None
    if args.mode in ("auto", "interactive"):
        runner = InteractiveCodexRunner(
            codex_bin=args.codex_bin, sandbox=args.sandbox
        )
        fallback = CodexRunner(codex_bin=args.codex_bin, sandbox=args.sandbox)
    else:
        runner = CodexRunner(codex_bin=args.codex_bin, sandbox=args.sandbox)
    sessions_dir = Path(args.sessions_dir) if args.sessions_dir else None
    CodexTuiApp(
        sessions_dir=sessions_dir,
        runner=runner,
        fallback_runner=fallback,
    ).run()
    return 0


def main() -> None:
    raise SystemExit(run_cli(sys.argv[1:]))
