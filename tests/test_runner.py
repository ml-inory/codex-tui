import asyncio

from codex_tui.runner import CodexRunError, CodexRunner
from codex_tui.runner import build_codex_command, strip_shell_wrapper


class FakeStream:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __aiter__(self):
        async def gen():
            for line in self._lines:
                yield line

        return gen()


class FakeProcess:
    def __init__(self, stdout: FakeStream) -> None:
        self.stdout = stdout
        self.returncode = None

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = 9


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


def test_sandbox_flag_added_for_new_and_config_override_for_resume() -> None:
    new_command = build_codex_command("/proj/a", "hi", sandbox="workspace-write")
    assert new_command[new_command.index("-s") + 1] == "workspace-write"

    resume_command = build_codex_command(
        "/proj/a", "hi", session_id="abc-123", sandbox="read-only"
    )
    # `codex exec resume` rejects `-s`; the sandbox is passed as a config
    # override with the same value names.
    assert "-s" not in resume_command
    assert 'sandbox_mode="read-only"' in resume_command


def test_danger_full_access_sandbox_flows_to_new_and_resume() -> None:
    """Yolo mode's sandbox value reaches both codex exec paths."""
    new_command = build_codex_command("/proj/a", "hi", sandbox="danger-full-access")
    assert new_command[new_command.index("-s") + 1] == "danger-full-access"

    resume_command = build_codex_command(
        "/proj/a",
        "hi",
        session_id="abc-123",
        sandbox="danger-full-access",
    )
    assert "-s" not in resume_command
    assert 'sandbox_mode="danger-full-access"' in resume_command


def test_strip_shell_wrapper_unwraps_shell_launchers() -> None:
    assert strip_shell_wrapper("bash -lc 'echo hello world'") == "echo hello world"
    assert strip_shell_wrapper("bash -c 'echo hello'") == "echo hello"
    assert strip_shell_wrapper("/bin/bash -lc 'echo hello'") == "echo hello"
    assert strip_shell_wrapper("/usr/bin/zsh -lc 'echo hello'") == "echo hello"
    assert strip_shell_wrapper("sh -lc 'echo hello'") == "echo hello"
    # Non-wrapper commands (including quotes that split into more args) are
    # kept exactly as-is.
    assert strip_shell_wrapper("ls -la") == "ls -la"
    assert strip_shell_wrapper("echo 'a b' c") == "echo 'a b' c"
    assert strip_shell_wrapper("bash -lc echo hello") == "bash -lc echo hello"
    # Unparseable input is left untouched rather than mangled.
    assert strip_shell_wrapper("echo 'unterminated") == "echo 'unterminated"
    assert strip_shell_wrapper("") == ""


def test_exec_runner_normalizes_tool_items(monkeypatch) -> None:
    lines = [
        b'{"type":"thread.started","thread_id":"t-1"}\n',
        b'{"type":"item.started","item":{"id":"i0","type":"command_execution",'
        b'"command":"bash -lc \'ls -la\'","status":"in_progress"}}\n',
        b'{"type":"item.completed","item":{"id":"i0","type":"command_execution",'
        b'"command":"bash -lc \'ls -la\'","exit_code":0,"status":"completed",'
        b'"aggregated_output":"file1\\nfile2\\n"}}\n',
        b'{"type":"item.started","item":{"id":"i1","type":"file_change",'
        b'"changes":[{"path":"/a.txt","kind":"update"}],"status":"in_progress"}}\n',
        b'{"type":"item.completed","item":{"id":"i1","type":"file_change",'
        b'"changes":[{"path":"/a.txt","kind":"update"}],"status":"completed"}}\n',
        b'{"type":"item.completed","item":{"id":"i2","type":"agent_message",'
        b'"text":"done"}}\n',
        b'{"type":"turn.completed"}\n',
    ]

    async def fake_create_subprocess_exec(*args, **kwargs) -> FakeProcess:
        return FakeProcess(FakeStream(lines))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    async def collect() -> list[dict]:
        runner = CodexRunner(sandbox="read-only")
        return [event async for event in runner.run_turn(project="/p", prompt="hi")]

    events = asyncio.run(collect())
    tool_events = [e for e in events if e["type"].startswith("tool.")]
    assert tool_events == [
        {
            "type": "tool.started",
            "tool": {
                "id": "i0",
                "kind": "command_execution",
                "status": "running",
                "exit_code": None,
                "label": "exec_command",
                "detail": "ls -la",
                "source": "agent",
                "process_id": None,
                "actions": [],
                "exploring": False,
            },
        },
        {
            "type": "tool.completed",
            "tool": {
                "id": "i0",
                "kind": "command_execution",
                "status": "completed",
                "exit_code": 0,
                "label": "exec_command",
                "detail": "ls -la",
                "output": "file1\nfile2\n",
                "source": "agent",
                "process_id": None,
                "actions": [],
                "exploring": False,
            },
        },
        {
            "type": "tool.started",
            "tool": {
                "id": "i1",
                "kind": "file_change",
                "status": "running",
                "exit_code": None,
                "label": "apply_patch",
                "detail": "/a.txt",
                "changes": [
                    {"path": "/a.txt", "kind": "update", "diff": ""}
                ],
            },
        },
        {
            "type": "tool.completed",
            "tool": {
                "id": "i1",
                "kind": "file_change",
                "status": "completed",
                "exit_code": None,
                "label": "apply_patch",
                "detail": "/a.txt",
                "changes": [
                    {"path": "/a.txt", "kind": "update", "diff": ""}
                ],
            },
        },
    ]
    # Non-tool events still pass through untouched.
    assert events[0] == {"type": "thread.started", "thread_id": "t-1"}
    assert events[-2]["type"] == "item.completed"
    assert events[-2]["item"]["text"] == "done"


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
