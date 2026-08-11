"""Thin async wrapper around the codex CLI's non-interactive JSON mode."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from typing import AsyncIterator


DEFAULT_CODEX_BIN = "codex"

# Item types whose lifecycle should be surfaced in the chat as tool activity.
TOOL_ITEM_TYPES = ("command_execution", "file_change")

# Shells that codex treats as a thin launcher around the real command. The
# model usually emits ``/bin/bash -lc '<script>'``, which the TUI unwraps so
# the chat shows the command a user would actually type.
_SHELL_WRAPPER_NAMES = {"bash", "sh", "zsh"}
_EXPLORING_ACTION_KINDS = {"read", "listFiles", "search"}


class CodexRunError(RuntimeError):
    """Raised when the codex CLI exits unsuccessfully."""


def strip_shell_wrapper(command: str) -> str:
    """Return the display form of an exec command, unwrapping shell launchers.

    Mirrors codex's ``strip_bash_lc_and_escape``: when the command is a
    ``<shell> -lc '<script>'`` / ``<shell> -c '<script>'`` invocation for
    bash/sh/zsh, only the inner script is shown. Everything else is returned
    unchanged.
    """
    if not command:
        return command
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if (
        len(parts) == 3
        and parts[1] in ("-lc", "-c")
        and os.path.basename(parts[0]) in _SHELL_WRAPPER_NAMES
    ):
        return parts[2]
    return command


def normalize_command_actions(actions: list | None) -> list[dict]:
    """Normalize codex ``commandActions`` to ``{kind, command, name, ...}``."""
    normalized: list[dict] = []
    for action in actions or []:
        if not isinstance(action, dict):
            continue
        normalized.append(
            {
                "kind": action.get("type") or action.get("kind") or "unknown",
                "command": action.get("command") or "",
                "name": action.get("name"),
                "query": action.get("query"),
                "path": action.get("path"),
            }
        )
    return normalized


def is_exploring(actions: list[dict]) -> bool:
    """True when every parsed action is a read-only exploration."""
    return bool(actions) and all(
        action.get("kind") in _EXPLORING_ACTION_KINDS for action in actions
    )


def file_change_kind(change: dict) -> str:
    """Extract the change kind, handling both ``"update"`` and ``{"type": ...}``."""
    kind = change.get("kind")
    if isinstance(kind, dict):
        return kind.get("type") or ""
    return kind or ""


def normalize_file_changes(changes: list | None) -> list[dict]:
    """Normalize ``changes`` to ``{path, kind, diff}`` entries."""
    normalized: list[dict] = []
    for change in changes or []:
        if not isinstance(change, dict) or not change.get("path"):
            continue
        normalized.append(
            {
                "path": str(change.get("path")),
                "kind": file_change_kind(change),
                "diff": change.get("diff") or "",
            }
        )
    return normalized


def normalize_exec_tool_event(event: dict) -> dict | None:
    """Turn an exec-mode tool item into a unified ``tool.*`` event.

    ``codex exec --json`` emits ``item.started``/``item.completed`` lines whose
    ``item.type`` is ``command_execution`` or ``file_change``. The TUI renders
    those as live tool activity, so they are normalized to the same
    ``tool.started``/``tool.completed`` shape the app-server backend yields.
    Returns ``None`` for items that are not tool activity.
    """
    item = event.get("item") or {}
    if not isinstance(item, dict) or item.get("type") not in TOOL_ITEM_TYPES:
        return None
    started = event.get("type") == "item.started"
    item_type = item.get("type")
    tool: dict = {
        "id": item.get("id"),
        "kind": item_type,
        "status": "failed",
        "exit_code": None,
    }
    status = item.get("status")
    if status in ("in_progress", "inProgress"):
        tool["status"] = "running"
    elif status in ("completed", "succeeded"):
        tool["status"] = "completed"
    if item_type == "command_execution":
        tool["label"] = "exec_command"
        tool["detail"] = strip_shell_wrapper(item.get("command") or "")
        tool["exit_code"] = item.get("exit_code")
        tool["source"] = item.get("source") or "agent"
        tool["process_id"] = item.get("process_id") or item.get("processId")
        actions = normalize_command_actions(
            item.get("command_actions") or item.get("commandActions")
        )
        tool["actions"] = actions
        tool["exploring"] = is_exploring(actions)
        if not started:
            tool["output"] = item.get("aggregated_output") or ""
    else:  # file_change
        changes = item.get("changes") or []
        paths = sorted(
            {str(change.get("path")) for change in changes if change.get("path")}
        )
        tool["label"] = "apply_patch"
        tool["detail"] = ", ".join(paths)
        tool["changes"] = normalize_file_changes(changes)
    return {
        "type": "tool.started" if started else "tool.completed",
        "tool": tool,
    }


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
        # `codex exec resume` has no `-s/--sandbox` flag; the sandbox is
        # passed as a config override instead (same value names).
        if sandbox:
            command += ["-c", f'sandbox_mode="{sandbox}"']
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
                tool_event = normalize_exec_tool_event(event)
                if tool_event is not None:
                    yield tool_event
                    continue
                yield event
            returncode = await process.wait()
            if returncode != 0:
                raise CodexRunError(f"codex exited with status {returncode}")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
