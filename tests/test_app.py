import asyncio
from pathlib import Path

from textual.widgets import ListView, Markdown, Static

from codex_tui.app import CodexTuiApp
from codex_tui.widgets import ChatView, Sidebar
from tests.helpers import make_session_file


def _run(coro):
    return asyncio.run(coro)


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
