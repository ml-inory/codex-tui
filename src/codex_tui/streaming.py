"""Interactive, streaming backend powered by ``codex app-server``.

Instead of scraping the ratatui full-screen redraws of the interactive CLI
(which are slow and fragile), we speak the same JSON-RPC protocol Codex
Desktop uses. The server keeps a thread alive across turns and pushes
``item/agentMessage/delta`` notifications as the model streams its reply, so
the TUI can render text incrementally like the native CLI.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

from codex_tui.runner import CodexRunError, DEFAULT_CODEX_BIN


CONNECT_TIMEOUT = 30.0
_SERVER_EXITED = "_server_exited"


def _auto_approval_response(method: str, params: dict[str, Any]) -> dict:
    """Best-effort auto-approval for server-initiated approval requests.

    The app-server only sends these when the approval policy asks; we always
    start threads with ``approvalPolicy: "never"`` (matching the non-interactive
    ``codex exec`` behaviour the TUI used before), so these should not appear.
    Responding keeps a turn alive if the policy ever slips through.
    """
    if method == "item/commandExecution/requestApproval":
        return {"result": {"decision": "accept"}}
    if method == "item/fileChange/requestApproval":
        return {"result": {"decision": "accept"}}
    if method == "item/permissions/requestApproval":
        return {
            "result": {
                "permissions": params.get("permissions") or {},
                "scope": "session",
            }
        }
    if method in ("execCommandApproval", "applyPatchApproval"):
        return {"result": {"decision": "approved"}}
    return {
        "error": {"code": -32601, "message": f"unsupported server request {method}"}
    }


class AppServerClient:
    """Minimal JSON-RPC client for ``codex app-server --stdio``.

    Requests carry an integer ``id`` and are matched against responses; server
    notifications (``method`` without ``id``) land on an internal queue that
    :meth:`run_turn` consumes until the turn completes.
    """

    def __init__(
        self,
        codex_bin: str = DEFAULT_CODEX_BIN,
        cwd: str | None = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._notifications: asyncio.Queue[tuple[str, dict[str, Any]]] = (
            asyncio.Queue()
        )
        # threadId -> queue; turns subscribe so concurrent turns on different
        # threads never steal each other's notifications.
        self._listeners: dict[
            str, asyncio.Queue[tuple[str, dict[str, Any]]]
        ] = {}
        self._next_id = 1
        self._closed = False

    async def connect(self) -> None:
        """Spawn the app-server and complete the initialize handshake."""
        if self._proc is not None:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                self.codex_bin,
                "app-server",
                "--stdio",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=self.cwd,
            )
        except FileNotFoundError as exc:
            raise CodexRunError(
                f"codex executable not found: {self.codex_bin}"
            ) from exc
        self._proc = proc
        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            await self.request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "codex-tui",
                        "title": None,
                        "version": "0.1.0",
                    },
                    "capabilities": None,
                },
                timeout=CONNECT_TIMEOUT,
            )
            await self._write({"method": "initialized"})
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        """Terminate the app-server and fail any pending requests."""
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        pending = list(self._pending.values())
        self._pending.clear()
        for fut in pending:
            if not fut.done():
                fut.set_exception(CodexRunError("codex app-server closed"))
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except (asyncio.TimeoutError, ProcessLookupError):
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = CONNECT_TIMEOUT,
    ) -> dict[str, Any]:
        """Send a request and await its response object (with ``id``/``result``)."""
        if self._proc is None:
            raise CodexRunError("codex app-server is not connected")
        msg_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await self._write(
                {"id": msg_id, "method": method, "params": params or {}}
            )
            response = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(msg_id, None)
            raise CodexRunError(f"codex app-server timed out on {method}") from exc
        if "error" in response:
            raise CodexRunError(
                f"{method} failed: {json.dumps(response['error'], ensure_ascii=False)}"
            )
        return response.get("result") or {}

    async def _write(self, obj: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise CodexRunError("codex app-server is not connected")
        payload = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            self._proc.stdin.write(payload)
            try:
                await self._proc.stdin.drain()
            except (ConnectionError, BrokenPipeError, OSError) as exc:
                raise CodexRunError("codex app-server stdin closed") from exc

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        buffer = b""
        try:
            while True:
                chunk = await self._proc.stdout.read(65536)
                if not chunk:
                    break
                buffer += chunk
                lines = buffer.split(b"\n")
                buffer = lines.pop()
                for line in lines:
                    if line.strip():
                        self._handle_line(line)
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        if self._closed:
            return
        # Wake up any turn still waiting for notifications so it can fail
        # instead of blocking forever after the server died.
        self._notifications.put_nowait((_SERVER_EXITED, {}))
        exc = CodexRunError("codex app-server exited unexpectedly")
        pending = list(self._pending.values())
        self._pending.clear()
        for fut in pending:
            if not fut.done():
                fut.set_exception(exc)

    def _handle_line(self, raw: bytes) -> None:
        """Dispatch one JSON line: response, server request, or notification."""
        try:
            obj = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(obj, dict):
            return
        if "id" in obj:
            if "method" in obj:
                self._handle_server_request(obj)
                return
            fut = self._pending.pop(obj.get("id"), None)
            if fut is not None and not fut.done():
                fut.set_result(obj)
            return
        method = obj.get("method")
        if not method:
            return
        params = obj.get("params") or {}
        if method == _SERVER_EXITED:
            for queue in list(self._listeners.values()):
                queue.put_nowait((_SERVER_EXITED, {}))
            self._notifications.put_nowait((method, obj.get("params") or {}))
            return
        thread_id = params.get("threadId")
        queue = self._listeners.get(thread_id) if thread_id else None
        if queue is not None:
            queue.put_nowait((method, params))
        else:
            self._notifications.put_nowait((method, params))

    def subscribe(self, thread_id: str) -> asyncio.Queue:
        """Register a per-thread notification queue."""
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self._listeners[thread_id] = queue
        return queue

    def unsubscribe(self, thread_id: str) -> None:
        self._listeners.pop(thread_id, None)

    async def consume_stream(
        self,
        queue: asyncio.Queue,
        turn_id: str,
    ) -> AsyncIterator[TurnEvent]:
        """Turn notifications on one thread into streamed events."""
        while True:
            method, params = await queue.get()
            if method == _SERVER_EXITED:
                raise CodexRunError("codex app-server exited during the turn")
            if params.get("turnId") and params["turnId"] != turn_id:
                continue
            if method == "item/agentMessage/delta":
                delta = params.get("delta") or ""
                if delta:
                    yield TurnEvent(
                        "delta",
                        text=delta,
                        item_id=params.get("itemId"),
                        turn_id=turn_id,
                    )
            elif method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agent_message":
                    yield TurnEvent(
                        "item_completed",
                        text=item.get("text") or "",
                        item_id=item.get("id"),
                        turn_id=turn_id,
                    )
            elif method == "turn/completed":
                yield TurnEvent(
                    "turn_completed",
                    turn_id=params.get("turnId") or turn_id,
                    status=params.get("status"),
                )
                return

    def _handle_server_request(self, obj: dict[str, Any]) -> None:
        msg_id = obj.get("id")
        method = obj.get("method") or ""
        params = obj.get("params") or {}
        response = _auto_approval_response(method, params)
        asyncio.create_task(self._write({"id": msg_id, **response}))

    async def start_thread(
        self,
        *,
        cwd: str,
        sandbox: str | None = None,
        model: str | None = None,
    ) -> str:
        """Create a new thread and return its id."""
        params: dict[str, Any] = {"cwd": cwd, "approvalPolicy": "never"}
        if sandbox:
            params["sandbox"] = sandbox
        if model:
            params["model"] = model
        result = await self.request("thread/start", params)
        thread = result.get("thread") or {}
        thread_id = thread.get("id")
        if not thread_id:
            raise CodexRunError("thread/start returned no thread id")
        return str(thread_id)

    async def resume_thread(
        self,
        thread_id: str,
        *,
        cwd: str,
        sandbox: str | None = None,
        model: str | None = None,
    ) -> str:
        """Resume an existing session thread by id; returns the thread id."""
        params: dict[str, Any] = {
            "threadId": thread_id,
            "cwd": cwd,
            "approvalPolicy": "never",
        }
        if sandbox:
            params["sandbox"] = sandbox
        if model:
            params["model"] = model
        result = await self.request("thread/resume", params)
        thread = result.get("thread") or {}
        resumed_id = thread.get("id") or thread_id
        return str(resumed_id)

    async def start_turn(
        self,
        thread_id: str,
        prompt: str,
        *,
        model: str | None = None,
    ) -> str:
        """Begin a turn and return its id."""
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt, "text_elements": []}],
        }
        if model:
            params["model"] = model
        result = await self.request("turn/start", params)
        turn = result.get("turn") or {}
        turn_id = turn.get("id")
        if not turn_id:
            raise CodexRunError("turn/start returned no turn id")
        return str(turn_id)

    async def interrupt(self, thread_id: str, turn_id: str) -> None:
        """Ask the server to stop the current turn."""
        await self.request(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
            timeout=10,
        )


@dataclass
class TurnEvent:
    """One streamed event produced while a turn runs."""

    kind: str  # "delta" | "item_completed" | "turn_completed"
    text: str = ""
    item_id: str | None = None
    turn_id: str | None = None
    status: str | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.kind == "delta":
            return {
                "type": "agent_message.delta",
                "text": self.text,
                "item_id": self.item_id,
                "turn_id": self.turn_id,
            }
        if self.kind == "item_completed":
            return {
                "type": "item.completed",
                "item": {
                    "id": self.item_id,
                    "type": "agent_message",
                    "text": self.text,
                },
                "turn_id": self.turn_id,
            }
        return {
            "type": "turn.completed",
            "turn_id": self.turn_id,
            "status": self.status,
        }


class InteractiveCodexRunner:
    """Persistent, streaming alternative to :class:`CodexRunner`.

    One app-server process stays alive for the whole TUI session. New threads
    are created with ``thread/start``; existing conversations are resumed with
    ``thread/resume``; every message is a ``turn/start`` on the live thread, so
    there is no per-message CLI cold start.
    """

    def __init__(
        self,
        codex_bin: str = DEFAULT_CODEX_BIN,
        sandbox: str | None = None,
        model: str | None = None,
        client_factory=None,
    ) -> None:
        self.codex_bin = codex_bin
        self.sandbox = sandbox
        self.model = model
        self._client_factory = client_factory or (
            lambda: AppServerClient(codex_bin=codex_bin)
        )
        self._client: AppServerClient | None = None
        self._thread_id: str | None = None
        self._turn_id: str | None = None
        # threadId -> active turn id, for background multi-session turns.
        self._turns: dict[str, str] = {}

    @property
    def interactive(self) -> bool:
        return True

    async def start(self) -> None:
        if self._client is None:
            self._client = self._client_factory()
            await self._client.connect()

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
        self._thread_id = None
        self._turn_id = None
        self._turns.clear()

    async def interrupt(self, thread_id: str | None = None) -> None:
        if self._client is None:
            return
        if thread_id is None:
            thread_id = self._thread_id
        turn_id = self._turns.get(thread_id or "")
        if thread_id and turn_id:
            try:
                await self._client.interrupt(thread_id, turn_id)
            except CodexRunError:
                pass

    async def run_turn(
        self,
        *,
        project: str,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
    ) -> AsyncIterator[dict]:
        if self._client is None:
            await self.start()
        assert self._client is not None
        client = self._client
        if session_id:
            thread_id = await client.resume_thread(
                session_id,
                cwd=project,
                sandbox=self.sandbox,
                model=model,
            )
        else:
            thread_id = await client.start_thread(
                cwd=project,
                sandbox=self.sandbox,
                model=model,
            )
        self._thread_id = thread_id
        self._turn_id = None
        yield {"type": "thread.started", "thread_id": thread_id}

        queue = client.subscribe(thread_id)
        try:
            turn_id = await client.start_turn(thread_id, prompt, model=model)
            self._turn_id = turn_id
            self._turns[thread_id] = turn_id
            async for event in client.consume_stream(queue, turn_id):
                yield {**event.to_dict(), "thread_id": thread_id}
                if event.kind == "turn_completed":
                    self._turn_id = None
                    self._turns.pop(thread_id, None)
        except BaseException:
            self._turn_id = None
            self._turns.pop(thread_id, None)
            raise
        finally:
            client.unsubscribe(thread_id)
