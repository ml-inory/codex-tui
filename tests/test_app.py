import asyncio
import time
from pathlib import Path

from textual.widgets import Input, ListView, Markdown, Static

from codex_tui.app import CodexTuiApp
from codex_tui.runner import CodexRunError
from codex_tui.widgets import ChatView, Sidebar
from tests.helpers import make_session_file


def _run(coro):
    return asyncio.run(coro)


async def _wait_until(pilot, predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await pilot.pause()
        if predicate():
            return True
    return False


class FakeCodex:
    """Fake CodexRunner that persists a turn like the real CLI would."""

    def __init__(self, sessions_dir: Path, session_id: str) -> None:
        self.sessions_dir = sessions_dir
        self.session_id = session_id
        self.calls: list[dict] = []
        self.error: str | None = None
        self._release: asyncio.Event | None = None

    async def run_turn(self, *, project: str, prompt: str, session_id: str | None = None):
        self.calls.append(
            {"project": project, "prompt": prompt, "session_id": session_id}
        )
        if self.error:
            raise CodexRunError(self.error)
        if self._release is not None:
            # Hold the turn open so the test can observe the running state.
            await self._release.wait()
        sid = session_id or self.session_id
        make_session_file(
            self.sessions_dir,
            session_id=sid,
            cwd=project,
            timestamp="2026-08-07T04:00:00.000Z",
            user_text=prompt,
            assistant_text="Hi from codex",
        )
        yield {"type": "thread.started", "thread_id": sid}
        yield {
            "type": "item.completed",
            "item": {"id": "item_1", "type": "agent_message", "text": "Hi from codex"},
        }
        yield {"type": "turn.completed"}


def test_app_mounts_empty_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one(Sidebar) is not None
            assert app.query_one(ChatView) is not None
            assert len(app.query_one("#project-list", ListView).children) == 0
            hint = app.query_one("#sidebar-hint", Static)
            assert hint.display and "No projects" in str(hint.content)

    _run(scenario())


def test_send_prompt_starts_new_session_and_renders_reply(tmp_path: Path) -> None:
    fake = FakeCodex(tmp_path, session_id="99999999-9999-9999-9999-999999999999")

    async def scenario() -> None:
        fake._release = asyncio.Event()
        app = CodexTuiApp(sessions_dir=tmp_path, runner=fake)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt_input = app.query_one("#prompt-input", Input)
            prompt_input.focus()
            await pilot.pause()
            prompt_input.value = "hello there"
            await pilot.press("enter")

            assert await _wait_until(pilot, lambda: bool(fake.calls))
            await pilot.pause()
            assert prompt_input.disabled is True
            statics = list(app.query_one("#chat-log").query(Static))
            assert any("hello there" in str(s.content or "") for s in statics)

            fake._release.set()
            assert await _wait_until(pilot, lambda: not app.turn_active)
            assert fake.calls[0]["session_id"] is None
            assert fake.calls[0]["prompt"] == "hello there"
            assert app.current_session is not None
            assert app.current_session.title == "hello there"

            markdowns = list(app.query_one("#chat-log").query(Markdown))
            assert len(markdowns) == 1

    _run(scenario())


def test_send_prompt_resumes_current_session(tmp_path: Path) -> None:
    session_id = "11111111-1111-1111-1111-111111111111"
    make_session_file(
        tmp_path,
        session_id=session_id,
        cwd="/proj/a",
        user_text="first",
    )
    fake = FakeCodex(tmp_path, session_id=session_id)

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path, runner=fake)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current_session is not None
            prompt_input = app.query_one("#prompt-input", Input)
            prompt_input.focus()
            await pilot.pause()
            prompt_input.value = "continue"
            await pilot.press("enter")

            assert await _wait_until(pilot, lambda: not app.turn_active)
            assert fake.calls[0]["session_id"] == session_id
            assert fake.calls[0]["project"] == "/proj/a"

    _run(scenario())


def test_send_prompt_reports_error_and_reenables_input(tmp_path: Path) -> None:
    fake = FakeCodex(tmp_path, session_id="99999999-9999-9999-9999-999999999999")
    fake.error = "codex exited with status 1"

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path, runner=fake)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt_input = app.query_one("#prompt-input", Input)
            prompt_input.focus()
            await pilot.pause()
            prompt_input.value = "boom"
            await pilot.press("enter")

            assert await _wait_until(pilot, lambda: not app.turn_active)
            assert prompt_input.disabled is False
            status = app.query_one("#chat-status", Static)
            assert "codex exited with status 1" in str(status.content)

    _run(scenario())


def test_new_session_action_clears_chat_and_focuses_input(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="old conversation",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current_session is not None

            await app.action_new_session()
            await pilot.pause()

            assert app.current_session is None
            assert app.focused is app.query_one("#prompt-input", Input)
            assert len(app.query_one("#chat-log").children) == 0

    _run(scenario())


def test_delete_session_moves_file_to_trash_after_confirm(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="to be deleted",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path, trash_dir=tmp_path / "trash")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current_session is not None

            # First press only arms the confirmation.
            app.action_delete_session()
            await pilot.pause()
            assert len(list(tmp_path.rglob("*.jsonl"))) == 1

            # Second press deletes (moves to trash).
            app.action_delete_session()
            assert await _wait_until(
                pilot, lambda: not list((tmp_path / "2026").rglob("*.jsonl"))
            )
            assert len(list((tmp_path / "trash").rglob("*.jsonl"))) == 1
            assert await _wait_until(pilot, lambda: app.current_session is None)

    _run(scenario())


def test_single_delete_press_does_not_delete(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="keep me",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path, trash_dir=tmp_path / "trash")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_delete_session()
            await pilot.pause()
            await app.action_new_session()  # switching away cancels the pending delete
            await pilot.pause()

            assert app._pending_delete is None
            assert len(list(tmp_path.rglob("*.jsonl"))) == 1

    _run(scenario())


def test_app_lists_projects_and_sessions(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="hello world",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            project_list = app.query_one("#project-list", ListView)
            assert len(project_list.children) == 1
            session_list = app.query_one("#session-list", ListView)
            assert len(session_list.children) == 1

    _run(scenario())


def test_selecting_session_renders_conversation(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="What is 2+2?",
        assistant_text="## Answer\n\nIt is **4**.",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            project_list = app.query_one("#project-list", ListView)
            project_list.index = 0
            await pilot.pause()
            session_list = app.query_one("#session-list", ListView)
            session_list.index = 0
            await pilot.pause()

            chat_log = app.query_one("#chat-log")
            statics = list(chat_log.query(Static))
            assert any("What is 2+2?" in str(s.content or "") for s in statics)
            assert len(list(chat_log.query(Markdown))) == 1
            assert app.current_session is not None
            assert app.current_session.id == "11111111-1111-1111-1111-111111111111"

    _run(scenario())


def test_multiple_projects_group_sessions(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="a1",
    )
    make_session_file(
        tmp_path,
        session_id="22222222-2222-2222-2222-222222222222",
        cwd="/proj/b",
        timestamp="2026-08-07T03:00:00.000Z",
        user_text="b1",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            project_list = app.query_one("#project-list", ListView)
            assert len(project_list.children) == 2
            project_list.index = 1
            await pilot.pause()
            session_list = app.query_one("#session-list", ListView)
            assert len(session_list.children) == 1
            session_list.index = 0
            await pilot.pause()
            assert app.current_session is not None
            assert app.current_session.project == "/proj/b"

    _run(scenario())
