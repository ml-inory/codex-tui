import asyncio
import json
import time
from pathlib import Path

from textual.widgets import Input, ListView, Markdown, Static

from codex_tui.app import CodexTuiApp, run_cli
from codex_tui.runner import CodexRunError
from codex_tui.screens import ModelScreen, RenameScreen
from codex_tui.widgets import ChatView, Sidebar, WatchPane
from tests.helpers import make_session_file
from tests.helpers import make_many_message_session


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

    async def run_turn(
        self,
        *,
        project: str,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
    ):
        self.calls.append(
            {
                "project": project,
                "prompt": prompt,
                "session_id": session_id,
                "model": model,
            }
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


class StreamingFake:
    """Fake interactive runner that emits token-level deltas."""

    def __init__(self, sessions_dir: Path, session_id: str) -> None:
        self.sessions_dir = sessions_dir
        self.session_id = session_id
        self.calls: list[dict] = []
        self.interactive = True

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def interrupt(self) -> None:
        pass

    async def run_turn(
        self,
        *,
        project: str,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
    ):
        self.calls.append(
            {"project": project, "prompt": prompt, "session_id": session_id}
        )
        make_session_file(
            self.sessions_dir,
            session_id=session_id or self.session_id,
            cwd=project,
            timestamp="2026-08-07T04:00:00.000Z",
            user_text=prompt,
            assistant_text="Hello world",
        )
        yield {"type": "thread.started", "thread_id": "stream-1"}
        yield {"type": "agent_message.delta", "text": "Hel"}
        yield {"type": "agent_message.delta", "text": "lo "}
        yield {"type": "agent_message.delta", "text": "world"}
        yield {"type": "turn.completed", "status": "completed"}


class BrokenInteractive:
    """Interactive runner whose turn fails after a successful mount."""

    def __init__(self) -> None:
        self.interactive = True

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def run_turn(self, **kwargs):
        raise CodexRunError("backend exploded")
        yield  # pragma: no cover - makes this an async generator


class BackgroundFake:
    """Interactive-like runner that holds turns open until released."""

    def __init__(self, sessions_dir: Path | None = None) -> None:
        self.interactive = True
        self.release_all: asyncio.Event = asyncio.Event()
        self.calls: list[dict] = []
        self.sessions_dir = sessions_dir

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def interrupt(self, thread_id: str | None = None) -> None:
        pass

    async def run_turn(
        self,
        *,
        project: str,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
    ):
        self.calls.append(
            {"project": project, "prompt": prompt, "session_id": session_id}
        )
        sid = session_id or "99999999-9999-9999-9999-999999999999"
        yield {"type": "thread.started", "thread_id": sid}
        await self.release_all.wait()
        text = "done: ok"
        yield {"type": "agent_message.delta", "text": "done:", "thread_id": sid}
        yield {"type": "agent_message.delta", "text": " ok", "thread_id": sid}
        if self.sessions_dir is not None:
            make_session_file(
                self.sessions_dir,
                session_id=sid,
                cwd=project,
                user_text=prompt,
                assistant_text=text,
            )
        yield {"type": "turn.completed", "status": "completed", "thread_id": sid}


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


def test_background_turn_notifies_and_jump_opens_finished_session(
    tmp_path: Path,
) -> None:
    session_a = "11111111-1111-1111-1111-111111111111"
    session_b = "22222222-2222-2222-2222-222222222222"
    make_session_file(
        tmp_path,
        session_id=session_a,
        cwd="/proj/a",
        timestamp="2026-08-07T03:00:00.000Z",
        user_text="older",
    )
    make_session_file(
        tmp_path,
        session_id=session_b,
        cwd="/proj/a",
        timestamp="2026-08-07T04:00:00.000Z",
        user_text="newer",
    )
    fake = BackgroundFake()

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path, runner=fake)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._project_selected("/proj/a", select_id=session_a)
            await pilot.pause()
            prompt_input = app.query_one("#prompt-input", Input)
            prompt_input.value = "do it"
            await pilot.press("enter")
            assert await _wait_until(pilot, lambda: len(fake.calls) == 1)
            assert session_a in app._active_sessions
            assert app.turn_active

            # Browse another session while the first is still working.
            await app._project_selected("/proj/a", select_id=session_b)
            await pilot.pause()
            assert app.current_session is not None
            assert app.current_session.id == session_b
            assert not app._current_running()

            fake.release_all.set()
            assert await _wait_until(
                pilot, lambda: session_a in app._finished_sessions
            )
            assert app.current_session is not None
            assert app.current_session.id == session_b
            assert not app.turn_active

            # Jump to the finished session with the shortcut.
            await pilot.press("ctrl+g")
            assert await _wait_until(
                pilot,
                lambda: app.current_session is not None
                and app.current_session.id == session_a,
            )
            assert session_a not in app._finished_sessions

    _run(scenario())


def test_send_in_second_session_while_first_runs(tmp_path: Path) -> None:
    session_a = "11111111-1111-1111-1111-111111111111"
    session_b = "22222222-2222-2222-2222-222222222222"
    make_session_file(
        tmp_path,
        session_id=session_a,
        cwd="/proj/a",
        timestamp="2026-08-07T03:00:00.000Z",
        user_text="older",
    )
    make_session_file(
        tmp_path,
        session_id=session_b,
        cwd="/proj/a",
        timestamp="2026-08-07T04:00:00.000Z",
        user_text="newer",
    )
    fake = BackgroundFake()

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path, runner=fake)
        async with app.run_test() as pilot:
            await pilot.pause()
            # Mount opens the newest session (B). Start a turn there.
            prompt_input = app.query_one("#prompt-input", Input)
            prompt_input.value = "first"
            await pilot.press("enter")
            assert await _wait_until(pilot, lambda: len(fake.calls) == 1)
            assert prompt_input.disabled is True

            # Switch to A and start a second turn while B still runs.
            await app._project_selected("/proj/a", select_id=session_a)
            await pilot.pause()
            assert prompt_input.disabled is False
            prompt_input.focus()
            prompt_input.value = "second"
            await pilot.press("enter")
            assert await _wait_until(pilot, lambda: len(fake.calls) == 2)
            assert {call["session_id"] for call in fake.calls} == {
                session_a,
                session_b,
            }
            assert app.turn_active

            fake.release_all.set()
            assert await _wait_until(pilot, lambda: not app.turn_active)
            # A is the session being viewed, so it is not marked finished;
            # only the background session B gets a completion marker.
            assert session_a not in app._finished_sessions
            assert session_b in app._finished_sessions
            assert await _wait_until(pilot, lambda: app._completed_turns == 2)
            assert prompt_input.disabled is False

    _run(scenario())


def test_mouse_select_and_copy_chat_text(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="What is 2+2?",
        assistant_text="It is **four**.",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.mouse_down("#chat-log", offset=(2, 1))
            await pilot.hover("#chat-log", offset=(40, 6))
            await pilot.mouse_up("#chat-log", offset=(40, 6))
            await pilot.pause()
            selected = app.screen.get_selected_text()
            assert selected is not None
            assert "What is 2+2?" in selected
            assert "It is four." in selected
            app.screen.action_copy_text()
            assert "What is 2+2?" in (app._clipboard or "")
            assert "It is four." in (app._clipboard or "")

    _run(scenario())


def test_copy_last_reply_and_conversation(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="<environment_context>\n  <cwd>/proj/a</cwd>",
        assistant_text="你好",
        extra_events=[
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "真实问题"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "这是回答"}],
                },
            },
        ],
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_copy_last_reply()
            assert app._clipboard == "这是回答"
            app.action_copy_conversation()
            assert "真实问题" in app._clipboard
            assert "这是回答" in app._clipboard
            assert "<environment_context>" not in app._clipboard

    _run(scenario())


def test_sidebar_shows_deepest_dir_and_toggle_shows_full_path(
    tmp_path: Path,
) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/deep/nested",
        user_text="hello",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            project_list = app.query_one("#project-list", ListView)
            label = project_list.children[0].query_one(Static)
            assert str(label.content) == "nested"
            assert label.tooltip == "/proj/deep/nested"

            app.action_toggle_project_path()
            assert await _wait_until(
                pilot,
                lambda: str(project_list.children[0].query_one(Static).content)
                == "/proj/deep/nested",
            )
            app.action_toggle_project_path()
            assert await _wait_until(
                pilot,
                lambda: str(project_list.children[0].query_one(Static).content)
                == "nested",
            )

    _run(scenario())


def test_project_mode_persists(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    async def scenario() -> None:
        first = CodexTuiApp(sessions_dir=tmp_path, settings_path=settings_path)
        async with first.run_test() as pilot:
            await pilot.pause()
            first.action_toggle_project_path()
            await pilot.pause(0.2)
        assert settings_path.is_file()
        assert "full" in settings_path.read_text(encoding="utf-8")

        second = CodexTuiApp(sessions_dir=tmp_path, settings_path=settings_path)
        assert second.settings.project_mode == "full"

    _run(scenario())


def test_key_help_screen_lists_bindings(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("f1")
            assert await _wait_until(
                pilot,
                lambda: type(app.screen).__name__ == "KeyHelpScreen",
            )
            rows = app.screen.query_one("#key-help-list", ListView).children
            text = " ".join(
                str(row.query_one(Static).content) for row in rows
            )
            assert "ctrl+o" in text.lower()
            assert "f1" in text.lower()
            assert "Switch" in text
            await pilot.press("escape")
            assert await _wait_until(
                pilot,
                lambda: type(app.screen).__name__ == "Screen",
            )

    _run(scenario())


def test_toggle_sidebar_hides_and_persists(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"

    async def scenario() -> None:
        first = CodexTuiApp(sessions_dir=tmp_path, settings_path=settings_path)
        async with first.run_test() as pilot:
            await pilot.pause()
            sidebar = first.query_one(Sidebar)
            assert sidebar.display is True
            first.action_toggle_sidebar()
            await pilot.pause()
            assert sidebar.display is False
            assert settings_path.is_file()
            assert '"sidebar_visible": false' in settings_path.read_text(
                encoding="utf-8"
            )

        second = CodexTuiApp(sessions_dir=tmp_path, settings_path=settings_path)
        async with second.run_test() as pilot:
            await pilot.pause()
            assert second.query_one(Sidebar).display is False

    _run(scenario())


def test_split_picker_shows_watch_session_and_closes(tmp_path: Path) -> None:
    session_a = "11111111-1111-1111-1111-111111111111"
    session_b = "22222222-2222-2222-2222-222222222222"
    make_session_file(
        tmp_path,
        session_id=session_a,
        cwd="/proj/a",
        timestamp="2026-08-07T03:00:00.000Z",
        user_text="older",
    )
    make_session_file(
        tmp_path,
        session_id=session_b,
        cwd="/proj/a",
        timestamp="2026-08-07T04:00:00.000Z",
        user_text="newer",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            watch_pane = app.query_one(WatchPane)
            assert watch_pane.display is False
            await pilot.press("ctrl+backslash")
            assert await _wait_until(
                pilot,
                lambda: type(app.screen).__name__ == "QuickSwitchScreen",
            )
            quick_input = app.screen.query_one("#quick-switch-input", Input)
            quick_input.value = "older"
            assert await _wait_until(
                pilot,
                lambda: len(
                    app.screen.query_one("#quick-switch-list", ListView).children
                )
                == 1,
            )
            await pilot.press("enter")
            assert await _wait_until(pilot, lambda: watch_pane.display is True)
            header = app.query_one("#watch-header", Static)
            assert "/proj/a" in str(header.content)
            watch_log = app.query_one("#watch-log")
            assert any(
                "older" in str(s.content or "") for s in watch_log.query(Static)
            )

            await pilot.press("ctrl+backslash")
            assert await _wait_until(pilot, lambda: watch_pane.display is False)

    _run(scenario())


def test_swap_panes_swaps_active_and_watch(tmp_path: Path) -> None:
    session_a = "11111111-1111-1111-1111-111111111111"
    session_b = "22222222-2222-2222-2222-222222222222"
    make_session_file(
        tmp_path,
        session_id=session_a,
        cwd="/proj/a",
        timestamp="2026-08-07T03:00:00.000Z",
        user_text="older",
    )
    make_session_file(
        tmp_path,
        session_id=session_b,
        cwd="/proj/a",
        timestamp="2026-08-07T04:00:00.000Z",
        user_text="newer",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            # Active is B; watch A.
            await app._open_watch_pane(
                app.overrides.apply(
                    next(
                        s
                        for s in app.store.sessions_for_project("/proj/a")
                        if s.id == session_a
                    )
                )
            )
            await pilot.pause()
            assert app.current_session is not None
            assert app.current_session.id == session_b

            app.action_swap_panes()
            assert await _wait_until(
                pilot,
                lambda: app.current_session is not None
                and app.current_session.id == session_a,
            )
            assert app._watch_session is not None
            assert app._watch_session.id == session_b
            header = app.query_one("#watch-header", Static)
            assert "newer" in str(header.content)

    _run(scenario())


def test_watch_pane_streams_background_turn(tmp_path: Path) -> None:
    session_a = "11111111-1111-1111-1111-111111111111"
    session_b = "22222222-2222-2222-2222-222222222222"
    make_session_file(
        tmp_path,
        session_id=session_a,
        cwd="/proj/a",
        timestamp="2026-08-07T03:00:00.000Z",
        user_text="older",
    )
    make_session_file(
        tmp_path,
        session_id=session_b,
        cwd="/proj/a",
        timestamp="2026-08-07T04:00:00.000Z",
        user_text="newer",
    )
    fake = BackgroundFake(sessions_dir=tmp_path)

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path, runner=fake)
        async with app.run_test() as pilot:
            await pilot.pause()
            # Active is B; start a turn there, then switch to A and watch B.
            prompt_input = app.query_one("#prompt-input", Input)
            prompt_input.value = "first"
            await pilot.press("enter")
            assert await _wait_until(pilot, lambda: len(fake.calls) == 1)
            await app._project_selected("/proj/a", select_id=session_a)
            await pilot.pause()
            session_b_obj = app.overrides.apply(
                next(
                    s
                    for s in app.store.sessions_for_project("/proj/a")
                    if s.id == session_b
                )
            )
            await app._open_watch_pane(session_b_obj)
            await pilot.pause()
            watch_pane = app.query_one(WatchPane)
            assert watch_pane.display is True
            assert "Codex is working" in str(
                app.query_one("#watch-status", Static).content
            )

            fake.release_all.set()
            assert await _wait_until(
                pilot, lambda: app._completed_turns == 1
            )
            watch_log = app.query_one("#watch-log")
            markdowns = list(watch_log.query(Markdown))
            assert any(
                "done: ok" in (md.source or "") for md in markdowns
            )
            assert "Codex is working" not in str(
                app.query_one("#watch-status", Static).content
            )

    _run(scenario())


def test_sidebar_project_and_session_selection_events(tmp_path: Path) -> None:
    session_a = "11111111-1111-1111-1111-111111111111"
    session_b = "22222222-2222-2222-2222-222222222222"
    make_session_file(
        tmp_path,
        session_id=session_a,
        cwd="/proj/a",
        timestamp="2026-08-07T03:00:00.000Z",
        user_text="older",
    )
    make_session_file(
        tmp_path,
        session_id=session_b,
        cwd="/proj/b",
        timestamp="2026-08-07T04:00:00.000Z",
        user_text="newer",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            project_list = app.query_one("#project-list", ListView)
            # Select the second project (mirrors clicking a row below the top).
            project_list.index = 1
            await pilot.pause()
            project_list.focus()
            await pilot.press("enter")
            assert await _wait_until(
                pilot, lambda: app.current_project == "/proj/a"
            )

            session_list = app.query_one("#session-list", ListView)
            session_list.focus()
            await pilot.press("enter")
            assert await _wait_until(
                pilot,
                lambda: app.current_session is not None
                and app.current_session.id == session_a,
            )

    _run(scenario())


def test_agents_md_injection_hidden_from_chat_and_title(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="# AGENTS.md instructions for /proj/a\n\n<INSTRUCTIONS>\n说明",
        assistant_text="你好",
        extra_events=[
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "真实问题"}],
                },
            }
        ],
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current_session is not None
            assert app.current_session.title == "真实问题"
            statics = list(app.query_one("#chat-log").query(Static))
            rendered = " ".join(str(s.content or "") for s in statics)
            assert "AGENTS.md" not in rendered
            assert "真实问题" in rendered
            assert "你好" in rendered

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


def test_send_prompt_streams_deltas_and_renders_markdown(tmp_path: Path) -> None:
    fake = StreamingFake(tmp_path, session_id="99999999-9999-9999-9999-999999999999")

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path, runner=fake)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt_input = app.query_one("#prompt-input", Input)
            prompt_input.value = "stream me"
            await pilot.press("enter")
            assert await _wait_until(pilot, lambda: bool(fake.calls))
            assert await _wait_until(pilot, lambda: not app.turn_active)
            chat_log = app.query_one("#chat-log")
            markdowns = list(chat_log.query(Markdown))
            assert len(markdowns) == 1
            assert "Hello world" in (markdowns[0].source or "")

    _run(scenario())


def test_broken_interactive_turn_falls_back_to_exec(tmp_path: Path) -> None:
    fallback = FakeCodex(tmp_path, session_id="99999999-9999-9999-9999-999999999999")

    async def scenario() -> None:
        app = CodexTuiApp(
            sessions_dir=tmp_path,
            runner=BrokenInteractive(),
            fallback_runner=fallback,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt_input = app.query_one("#prompt-input", Input)
            prompt_input.value = "please retry"
            await pilot.press("enter")
            assert await _wait_until(pilot, lambda: bool(fallback.calls))
            assert await _wait_until(pilot, lambda: not app.turn_active)
            markdowns = list(app.query_one("#chat-log").query(Markdown))
            assert len(markdowns) == 1
            assert "Hi from codex" in (markdowns[0].source or "")

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


def test_delete_session_via_ctrl_d_twice(tmp_path: Path) -> None:
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
            # Focus outside the input so Ctrl+D reaches the app binding
            # (inside the input Ctrl+D deletes a character).
            app.query_one("#session-list", ListView).focus()
            await pilot.pause()

            await pilot.press("ctrl+d")
            await pilot.pause()
            assert app._pending_delete is not None
            assert len(list((tmp_path / "2026").rglob("*.jsonl"))) == 1

            await pilot.press("ctrl+d")
            assert await _wait_until(
                pilot, lambda: not list((tmp_path / "2026").rglob("*.jsonl"))
            )
            assert len(list((tmp_path / "trash").rglob("*.jsonl"))) == 1

    _run(scenario())


def test_rename_session_via_modal_updates_ui_and_persists(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="Old title",
    )
    overrides_path = tmp_path / "overrides.json"

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path, overrides_path=overrides_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current_session is not None
            assert app.current_session.title == "Old title"

            await pilot.press("ctrl+r")
            await pilot.pause()
            rename_input = app.screen.query_one("#rename-input", Input)
            assert rename_input.value == "Old title"
            rename_input.value = "Renamed Session"
            await pilot.press("enter")

            assert await _wait_until(
                pilot,
                lambda: app.current_session is not None
                and app.current_session.title == "Renamed Session",
            )
            header = app.query_one("#chat-header", Static)
            assert "Renamed Session" in str(header.content)

            data = json.loads(overrides_path.read_text(encoding="utf-8"))
            assert (
                data["11111111-1111-1111-1111-111111111111"]["title"]
                == "Renamed Session"
            )

    _run(scenario())


def test_rename_persists_across_app_instances(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="Before rename",
    )
    overrides_path = tmp_path / "overrides.json"

    async def scenario() -> None:
        first = CodexTuiApp(sessions_dir=tmp_path, overrides_path=overrides_path)
        async with first.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+r")
            await pilot.pause()
            first.screen.query_one("#rename-input", Input).value = "Persistent Name"
            await pilot.press("enter")
            assert await _wait_until(
                pilot,
                lambda: first.current_session is not None
                and first.current_session.title == "Persistent Name",
            )

        second = CodexTuiApp(sessions_dir=tmp_path, overrides_path=overrides_path)
        async with second.run_test() as pilot:
            await pilot.pause()
            assert second.current_session is not None
            assert second.current_session.title == "Persistent Name"

    _run(scenario())


def test_ctrl_bindings_work_while_input_focused(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="hi",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.focused is app.query_one("#prompt-input", Input)

            await pilot.press("ctrl+r")
            await pilot.pause()
            assert isinstance(app.screen, RenameScreen)
            await pilot.press("escape")
            await pilot.pause()

            await pilot.press("ctrl+n")
            await pilot.pause()
            assert app.current_session is None

    _run(scenario())


def test_model_picker_sets_override_for_session(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="hi",
    )
    catalog = tmp_path / "models.json"
    catalog.write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "model-a", "display_name": "Model A"},
                    {"slug": "model-b", "display_name": "Model B"},
                ]
            }
        ),
        encoding="utf-8",
    )
    overrides_path = tmp_path / "overrides.json"
    fake = FakeCodex(tmp_path, session_id="11111111-1111-1111-1111-111111111111")

    async def scenario() -> None:
        app = CodexTuiApp(
            sessions_dir=tmp_path,
            overrides_path=overrides_path,
            model_catalog_path=catalog,
            runner=fake,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("f3")
            await pilot.pause()
            model_list = app.screen.query_one("#model-list", ListView)
            assert len(model_list.children) == 2

            await pilot.press("down", "down", "enter")
            await pilot.pause()
            assert await _wait_until(
                pilot,
                lambda: app.current_session is not None
                and app.current_session.effective_model == "model-b",
            )
            header = app.query_one("#chat-header", Static)
            assert "model-b" in str(header.content)

            prompt_input = app.query_one("#prompt-input", Input)
            prompt_input.focus()
            await pilot.pause()
            prompt_input.value = "go"
            await pilot.press("enter")
            assert await _wait_until(pilot, lambda: not app.turn_active)
            assert fake.calls[0]["model"] == "model-b"

    _run(scenario())


def test_model_picker_applies_to_new_session(tmp_path: Path) -> None:
    catalog = tmp_path / "models.json"
    catalog.write_text(
        json.dumps({"models": [{"slug": "model-new", "display_name": "New Model"}]}),
        encoding="utf-8",
    )
    overrides_path = tmp_path / "overrides.json"
    fake = FakeCodex(tmp_path, session_id="99999999-9999-9999-9999-999999999999")

    async def scenario() -> None:
        app = CodexTuiApp(
            sessions_dir=tmp_path,
            overrides_path=overrides_path,
            model_catalog_path=catalog,
            runner=fake,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current_session is None

            await pilot.press("f3")
            await pilot.pause()
            model_list = app.screen.query_one("#model-list", ListView)
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert app.pending_model == "model-new"

            prompt_input = app.query_one("#prompt-input", Input)
            prompt_input.focus()
            await pilot.pause()
            prompt_input.value = "start"
            await pilot.press("enter")
            assert await _wait_until(pilot, lambda: not app.turn_active)

            assert fake.calls[0]["model"] == "model-new"
            assert fake.calls[0]["session_id"] is None
            data = json.loads(overrides_path.read_text(encoding="utf-8"))
            assert data["99999999-9999-9999-9999-999999999999"]["model"] == "model-new"

    _run(scenario())


def test_model_clear_removes_override(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="hi",
    )
    catalog = tmp_path / "models.json"
    catalog.write_text(
        json.dumps({"models": [{"slug": "model-a", "display_name": "Model A"}]}),
        encoding="utf-8",
    )
    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text(
        json.dumps({"11111111-1111-1111-1111-111111111111": {"model": "model-a"}}),
        encoding="utf-8",
    )

    async def scenario() -> None:
        app = CodexTuiApp(
            sessions_dir=tmp_path,
            overrides_path=overrides_path,
            model_catalog_path=catalog,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current_session is not None
            assert app.current_session.effective_model == "model-a"

            await pilot.press("f3")
            await pilot.pause()
            assert await _wait_until(pilot, lambda: isinstance(app.screen, ModelScreen))
            await pilot.click("#model-clear")
            await pilot.pause()

            assert await _wait_until(
                pilot,
                lambda: app.current_session is not None
                and app.current_session.model_override is None,
            )
            data = json.loads(overrides_path.read_text(encoding="utf-8"))
            assert "model" not in data["11111111-1111-1111-1111-111111111111"]

    _run(scenario())


def test_run_cli_clean_trash(tmp_path: Path, monkeypatch, capsys) -> None:
    trash = tmp_path / "trash"
    trash.mkdir()
    (trash / "old.jsonl").write_text("x", encoding="utf-8")
    monkeypatch.setenv("CODEX_TUI_HOME", str(tmp_path))

    code = run_cli(["--clean-trash"])

    assert code == 0
    assert list(trash.iterdir()) == []
    assert "Removed 1" in capsys.readouterr().out


def test_long_chat_is_windowed_and_f7_loads_earlier(tmp_path: Path) -> None:
    make_many_message_session(tmp_path, n=120)

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            chat = app.query_one("#chat-log")
            rendered = " ".join(str(s.content or "") for s in chat.query(Static))
            assert "回答119" in rendered  # newest message visible
            assert "问题0" not in rendered  # oldest windowed out

            await pilot.press("f7")
            await pilot.pause()
            rendered = " ".join(str(s.content or "") for s in chat.query(Static))
            assert "问题0" in rendered

    _run(scenario())


def test_backfill_titles_for_injected_context_sessions(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="<environment_context>\n  <cwd>/proj/a</cwd>",
        assistant_text="ok",
        extra_events=[],
    )
    # Append a real question after the injected context.
    session_path = (
        tmp_path
        / "2026"
        / "08"
        / "07"
        / "rollout-2026-08-07T02-54-37-11111111-1111-1111-1111-111111111111.jsonl"
    )
    session_path.write_text(
        session_path.read_text(encoding="utf-8")
        + json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "这是第一个问题"}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    make_session_file(
        tmp_path,
        session_id="22222222-2222-2222-2222-222222222222",
        cwd="/proj/b",
        timestamp="2026-08-07T03:00:00.000Z",
        user_text="正常标题",
    )
    overrides_path = tmp_path / "overrides.json"

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path, overrides_path=overrides_path)
        async with app.run_test() as pilot:
            await pilot.pause()

            assert overrides_path.is_file()
            data = json.loads(overrides_path.read_text(encoding="utf-8"))
            assert (
                data["11111111-1111-1111-1111-111111111111"]["title"]
                == "这是第一个问题"
            )
            assert "22222222-2222-2222-2222-222222222222" not in data

    _run(scenario())


def test_injected_context_is_hidden_from_chat(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="<environment_context>\n  <cwd>/proj/a</cwd>",
        assistant_text="你好",
        extra_events=[
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "真实问题"}],
                },
            }
        ],
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            statics = list(app.query_one("#chat-log").query(Static))
            rendered = " ".join(str(s.content or "") for s in statics)
            assert "<environment_context>" not in rendered
            assert "真实问题" in rendered
            assert "你好" in rendered

    _run(scenario())


def test_new_session_gets_auto_title_from_first_message(tmp_path: Path) -> None:
    overrides_path = tmp_path / "overrides.json"
    fake = FakeCodex(tmp_path, session_id="99999999-9999-9999-9999-999999999999")

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path, overrides_path=overrides_path, runner=fake)
        async with app.run_test() as pilot:
            await pilot.pause()
            prompt_input = app.query_one("#prompt-input", Input)
            prompt_input.focus()
            await pilot.pause()
            prompt_input.value = "帮我看看这个仓库"
            await pilot.press("enter")

            assert await _wait_until(
                pilot,
                lambda: overrides_path.exists()
                and "帮我看看这个仓库"
                in overrides_path.read_text(encoding="utf-8"),
            )
            data = json.loads(overrides_path.read_text(encoding="utf-8"))
            assert data["99999999-9999-9999-9999-999999999999"]["title"] == "帮我看看这个仓库"

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


def test_quick_switch_jumps_across_projects(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        timestamp="2026-08-07T03:00:00.000Z",
        user_text="hello alpha",
    )
    make_session_file(
        tmp_path,
        session_id="22222222-2222-2222-2222-222222222222",
        cwd="/proj/b",
        timestamp="2026-08-07T04:00:00.000Z",
        user_text="world beta",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current_project == "/proj/b"
            assert app.current_session is not None
            assert app.current_session.id == "22222222-2222-2222-2222-222222222222"
            await pilot.press("ctrl+o")
            assert await _wait_until(
                pilot,
                lambda: type(app.screen).__name__ == "QuickSwitchScreen",
            )
            quick_input = app.screen.query_one("#quick-switch-input", Input)
            quick_input.value = "alpha"
            assert await _wait_until(
                pilot,
                lambda: len(
                    app.screen.query_one("#quick-switch-list", ListView).children
                )
                == 1,
            )
            await pilot.press("enter")
            assert await _wait_until(
                pilot,
                lambda: app.current_project == "/proj/a"
                and app.current_session is not None
                and app.current_session.id == "11111111-1111-1111-1111-111111111111",
            )

    _run(scenario())


def test_session_cycle_keys_move_through_sessions(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        timestamp="2026-08-07T03:00:00.000Z",
        user_text="older",
    )
    make_session_file(
        tmp_path,
        session_id="22222222-2222-2222-2222-222222222222",
        cwd="/proj/a",
        timestamp="2026-08-07T04:00:00.000Z",
        user_text="newer",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.current_session is not None
            assert app.current_session.id == "22222222-2222-2222-2222-222222222222"
            await pilot.press("ctrl+down")
            await pilot.pause()
            assert app.current_session is not None
            assert app.current_session.id == "11111111-1111-1111-1111-111111111111"
            await pilot.press("ctrl+down")
            await pilot.pause()
            assert app.current_session is not None
            assert app.current_session.id == "22222222-2222-2222-2222-222222222222"
            await pilot.press("ctrl+up")
            await pilot.pause()
            assert app.current_session is not None
            assert app.current_session.id == "11111111-1111-1111-1111-111111111111"

    _run(scenario())


def test_refresh_keeps_current_session_selected(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        timestamp="2026-08-07T03:00:00.000Z",
        user_text="older",
    )
    make_session_file(
        tmp_path,
        session_id="22222222-2222-2222-2222-222222222222",
        cwd="/proj/a",
        timestamp="2026-08-07T04:00:00.000Z",
        user_text="newer",
    )

    async def scenario() -> None:
        app = CodexTuiApp(sessions_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._project_selected("/proj/a", select_id="11111111-1111-1111-1111-111111111111")
            await pilot.pause()
            assert app.current_session is not None
            assert app.current_session.id == "11111111-1111-1111-1111-111111111111"
            await app.refresh_sessions()
            await pilot.pause()
            assert app.current_session is not None
            assert app.current_session.id == "11111111-1111-1111-1111-111111111111"

    _run(scenario())
