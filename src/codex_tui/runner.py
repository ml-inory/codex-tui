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
    model: str | None = None,
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
        if model:
            command += ["-m", model]
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
    if model:
        command += ["-m", model]
    command += ["-C", project, prompt]
    return command


class CodexRunner:
    """Run one codex turn and yield its JSONL events."""

    def __init__(
        self,
        codex_bin: str = DEFAULT_CODEX_BIN,
        sandbox: str | None = None,
        model: str | None = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.sandbox = sandbox
        self.model = model

    @property
    def interactive(self) -> bool:
        """True when the runner keeps a live streaming backend."""
        return False

    async def start(self) -> None:
        """Lifecycle hook; the non-interactive runner needs no setup."""

    async def stop(self) -> None:
        """Lifecycle hook; the non-interactive runner needs no teardown."""

    async def interrupt(self, thread_id: str | None = None) -> None:
        """Lifecycle hook; ``codex exec`` turns cannot be interrupted."""

    async def run_turn(
        self,
        *,
        project: str,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[dict]:
        """Spawn codex and yield decoded events until the turn completes."""
        command = build_codex_command(
            project,
            prompt,
            session_id,
            self.codex_bin,
            self.sandbox,
            model,
        )
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=project,
            )
        except FileNotFoundError as exc:
            raise CodexRunError(
                f"codex executable not found: {self.codex_bin}"
            ) from exc
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
