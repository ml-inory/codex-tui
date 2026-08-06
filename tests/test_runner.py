import asyncio

from codex_tui.runner import CodexRunError, CodexRunner
from codex_tui.runner import build_codex_command


def test_new_session_command_shape() -> None:
    command = build_codex_command("/proj/a", "hello world")

    assert command == [
        "codex",
        "exec",
        "--json",
        "--skip-git-repo-check",
        "-C",
        "/proj/a",
        "hello world",
    ]


def test_resume_command_shape() -> None:
    command = build_codex_command("/proj/a", "again", session_id="abc-123")

    assert command == [
        "codex",
        "exec",
        "resume",
        "--json",
        "--skip-git-repo-check",
        "abc-123",
        "again",
    ]


def test_custom_binary_and_project_quoted_as_one_arg() -> None:
    command = build_codex_command("/proj with space", "hi", codex_bin="my-codex")

    assert command[:2] == ["my-codex", "exec"]
    assert "/proj with space" in command


def test_sandbox_flag_added_for_new_and_resume() -> None:
    new_command = build_codex_command("/proj/a", "hi", sandbox="workspace-write")
    assert new_command[new_command.index("-s") + 1] == "workspace-write"

    resume_command = build_codex_command(
        "/proj/a", "hi", session_id="abc-123", sandbox="read-only"
    )
    assert resume_command[resume_command.index("-s") + 1] == "read-only"


def test_model_flag_added_for_new_and_resume() -> None:
    new_command = build_codex_command("/proj/a", "hi", model="deepseek-v4-pro")
    assert new_command[new_command.index("-m") + 1] == "deepseek-v4-pro"

    resume_command = build_codex_command(
        "/proj/a",
        "hi",
        session_id="abc-123",
        model="model-b",
        sandbox="read-only",
    )
    assert resume_command[resume_command.index("-m") + 1] == "model-b"


def test_missing_codex_binary_raises_clear_error() -> None:
    async def scenario() -> None:
        runner = CodexRunner(codex_bin="/nonexistent/codex-binary-xyz")
        try:
            async for _event in runner.run_turn(project="/tmp", prompt="hi"):
                pass
        except CodexRunError as exc:
            assert "codex executable not found" in str(exc)
            assert "/nonexistent/codex-binary-xyz" in str(exc)
            return
        raise AssertionError("expected CodexRunError")

    asyncio.run(scenario())
