"""Thin async wrapper around the codex CLI's non-interactive JSON mode."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator


DEFAULT_CODEX_BIN = "codex"


class CodexRunError(RuntimeError):
    """Raised when the codex CLI exits unsuccessfully."""


def build_codex_command(
    project: str,
    prompt: str,
    session_id: str | None = None,
    codex_bin: str = DEFAULT_CODEX_BIN,
    sandbox: str | None = None,
) -> list[str]:
    """Build the ``codex exec`` command line for a new or resumed turn."""
    if session_id:
        command = [
            codex_bin,
            "exec",
            "resume",
            "--json",
            "--skip-git-repo-check",
        ]
        if sandbox:
            command += ["-s", sandbox]
        command += [session_id, prompt]
        return command
    command = [
        codex_bin,
        "exec",
        "--json",
        "--skip-git-repo-check",
    ]
    if sandbox:
        command += ["-s", sandbox]
    command += ["-C", project, prompt]
    return command


class CodexRunner:
    """Run one codex turn and yield its JSONL events."""

    def __init__(
        self,
        codex_bin: str = DEFAULT_CODEX_BIN,
        sandbox: str | None = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.sandbox = sandbox

    async def run_turn(
        self,
        *,
        project: str,
        prompt: str,
        session_id: str | None = None,
    ) -> AsyncIterator[dict]:
        """Spawn codex and yield decoded events until the turn completes."""
        command = build_codex_command(
            project,
            prompt,
            session_id,
            self.codex_bin,
            self.sandbox,
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=project,
        )
        assert process.stdout is not None
        try:
            async for raw_line in process.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield event
            returncode = await process.wait()
            if returncode != 0:
                raise CodexRunError(f"codex exited with status {returncode}")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
