import asyncio
import json
from types import SimpleNamespace

import pytest

from codex_tui.runner import CodexRunError
from codex_tui.streaming import (
    _SERVER_EXITED,
    AppServerClient,
    InteractiveCodexRunner,
)


def _run(coro):
    return asyncio.run(coro)


async def _wait_for_listener(fake, thread_id: str) -> None:
    for _ in range(500):
        if thread_id in fake.client._listeners:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"turn never subscribed for {thread_id}")


class FakeAppServer:
    """In-process stand-in for the codex app-server stdio protocol."""

    def __init__(self) -> None:
        self.client = AppServerClient()
        self.client._proc = object()  # satisfy the "connected" check
        self.sent: list[dict] = []
        self.notifications: asyncio.Queue = asyncio.Queue()
        self.threads: dict[str, dict] = {}

    def attach(self) -> AppServerClient:
        async def fake_write(obj: dict) -> None:
            self.sent.append(obj)
            method = obj.get("method")
            msg_id = obj.get("id")
            if method == "initialize":
                self._resolve(msg_id, {"result": {"userAgent": "fake"}})
            elif method == "thread/start":
                tid = "thread-1"
                self.threads[tid] = {"cwd": obj["params"].get("cwd")}
                self._resolve(
                    msg_id,
                    {"result": {"thread": {"id": tid, "cwd": obj["params"].get("cwd")}}},
                )
            elif method == "thread/resume":
                tid = obj["params"]["threadId"]
                self._resolve(msg_id, {"result": {"thread": {"id": tid}}})
            elif method == "turn/start":
                self._resolve(
                    msg_id,
                    {"result": {"turn": {"id": "turn-1", "status": "inProgress"}}},
                )

        self.client._write = fake_write  # type: ignore[method-assign]
        return self.client

    def _resolve(self, msg_id, payload) -> None:
        fut = self.client._pending.pop(msg_id)
        fut.set_result(payload)

    def push(self, method: str, params: dict) -> None:
        if method == _SERVER_EXITED:
            for queue in list(self.client._listeners.values()):
                queue.put_nowait((method, params))
            self.client._notifications.put_nowait((method, params))
            return
        thread_id = params.get("threadId")
        queue = self.client._listeners.get(thread_id) if thread_id else None
        if queue is not None:
            queue.put_nowait((method, params))
        else:
            self.client._notifications.put_nowait((method, params))


def test_handle_line_resolves_pending_response() -> None:
    async def scenario() -> None:
        client = AppServerClient()
        fut = asyncio.get_running_loop().create_future()
        client._pending[7] = fut
        client._handle_line(json.dumps({"id": 7, "result": {"ok": True}}).encode())
        assert (await fut)["result"]["ok"] is True

    _run(scenario())


def test_handle_line_queues_notifications() -> None:
    async def scenario() -> None:
        client = AppServerClient()
        client._handle_line(
            json.dumps(
                {
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": "t", "turnId": "u", "itemId": "i", "delta": "Hi"},
                }
            ).encode()
        )
        method, params = await client._notifications.get()
        assert method == "item/agentMessage/delta"
        assert params["delta"] == "Hi"

    _run(scenario())


def test_server_request_gets_auto_approved() -> None:
    async def scenario() -> None:
        fake = FakeAppServer()
        client = fake.attach()
        responses: list[dict] = []

        async def spy_write(obj: dict) -> None:
            responses.append(obj)

        original_write = client._write
        client._write = spy_write  # type: ignore[method-assign]
        client._handle_line(
            json.dumps(
                {
                    "id": 42,
                    "method": "item/commandExecution/requestApproval",
                    "params": {"threadId": "t", "turnId": "u", "itemId": "i"},
                }
            ).encode()
        )
        await asyncio.sleep(0)
        assert responses == [{"id": 42, "result": {"decision": "accept"}}]
        client._write = original_write  # type: ignore[method-assign]

    _run(scenario())


def test_start_thread_sends_expected_params() -> None:
    fake = FakeAppServer()
    client = fake.attach()

    async def scenario() -> None:
        thread_id = await client.start_thread(
            cwd="/proj/x", sandbox="workspace-write", model="deepseek"
        )
        assert thread_id == "thread-1"
        request = fake.sent[0]
        assert request["method"] == "thread/start"
        assert request["params"]["cwd"] == "/proj/x"
        assert request["params"]["sandbox"] == "workspace-write"
        assert request["params"]["model"] == "deepseek"
        assert request["params"]["approvalPolicy"] == "never"

    _run(scenario())


def test_interactive_runner_streams_deltas_and_completes() -> None:
    fake = FakeAppServer()

    async def scenario() -> None:
        runner = InteractiveCodexRunner(client_factory=fake.attach)
        events: list[dict] = []

        async def collect() -> None:
            events.extend(
                [
                    event
                    async for event in runner.run_turn(
                        project="/proj/x", prompt="hi", session_id=None, model="m"
                    )
                ]
            )

        task = asyncio.create_task(collect())
        await _wait_for_listener(fake, "thread-1")
        fake.push(
            "item/agentMessage/delta",
            {"threadId": "thread-1", "turnId": "turn-1", "itemId": "i1", "delta": "Hel"},
        )
        fake.push(
            "item/agentMessage/delta",
            {"threadId": "thread-1", "turnId": "turn-1", "itemId": "i1", "delta": "lo"},
        )
        fake.push(
            "item/completed",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"id": "i1", "type": "agent_message", "text": "Hello"},
            },
        )
        fake.push(
            "turn/completed",
            {"threadId": "thread-1", "turnId": "turn-1", "status": "completed"},
        )
        await asyncio.wait_for(task, timeout=5)
        assert events[0] == {"type": "thread.started", "thread_id": "thread-1"}
        assert events[1] == {
            "type": "agent_message.delta",
            "text": "Hel",
            "item_id": "i1",
            "turn_id": "turn-1",
            "thread_id": "thread-1",
        }
        assert events[2]["type"] == "agent_message.delta"
        assert events[2]["text"] == "lo"
        assert events[3]["type"] == "item.completed"
        assert events[3]["item"]["text"] == "Hello"
        assert events[4]["type"] == "turn.completed"
        assert runner._turn_id is None

    _run(scenario())


def test_interactive_runner_resumes_existing_session() -> None:
    fake = FakeAppServer()

    async def scenario() -> None:
        runner = InteractiveCodexRunner(client_factory=fake.attach)
        events: list[dict] = []

        async def collect() -> None:
            events.extend(
                [
                    event
                    async for event in runner.run_turn(
                        project="/proj/x",
                        prompt="again",
                        session_id="019f-abc",
                        model=None,
                    )
                ]
            )

        task = asyncio.create_task(collect())
        await _wait_for_listener(fake, "019f-abc")
        fake.push(
            "turn/completed",
            {"threadId": "019f-abc", "turnId": "turn-1", "status": "completed"},
        )
        await asyncio.wait_for(task, timeout=5)
        resume = fake.sent[0]
        assert resume["method"] == "thread/resume"
        assert resume["params"]["threadId"] == "019f-abc"
        assert events[0]["thread_id"] == "019f-abc"

    _run(scenario())


def test_interactive_runner_fails_when_server_exits_mid_turn() -> None:
    fake = FakeAppServer()

    async def scenario() -> None:
        runner = InteractiveCodexRunner(client_factory=fake.attach)

        async def collect() -> None:
            async for _ in runner.run_turn(
                project="/proj/x", prompt="hi", session_id=None, model=None
            ):
                pass

        task = asyncio.create_task(collect())
        await _wait_for_listener(fake, "thread-1")
        fake.push(_SERVER_EXITED, {})
        with pytest.raises(CodexRunError, match="exited during"):
            await asyncio.wait_for(task, timeout=5)

    _run(scenario())


def test_read_loop_wakes_listeners_when_server_exits() -> None:
    """The real EOF path must wake subscribed turns, not just the unused
    global notification queue (otherwise a dead server hangs the turn)."""

    async def scenario() -> None:
        client = AppServerClient()
        client._proc = SimpleNamespace()

        class FakeStdout:
            async def read(self, size: int) -> bytes:
                return b""  # immediate EOF: the server process died

        client._proc.stdout = FakeStdout()
        task = asyncio.create_task(client._read_loop())
        queue = client.subscribe("thread-1")
        method, _params = await asyncio.wait_for(queue.get(), timeout=2)
        assert method == _SERVER_EXITED
        await task

    _run(scenario())


def test_two_threads_stream_independently() -> None:
    """Concurrent turns on different sessions must not steal deltas."""
    fake = FakeAppServer()

    async def scenario() -> None:
        runner = InteractiveCodexRunner(client_factory=fake.attach)
        results: list[list[dict]] = []

        async def consume(session_id: str, prompt: str) -> None:
            events = [
                event
                async for event in runner.run_turn(
                    project="/proj/x",
                    prompt=prompt,
                    session_id=session_id,
                    model=None,
                )
            ]
            results.append(events)

        task_a = asyncio.create_task(consume("sess-a", "hi"))
        task_b = asyncio.create_task(consume("sess-b", "yo"))
        for _ in range(500):
            if len(fake.client._listeners) >= 2:
                break
            await asyncio.sleep(0)
        assert len(fake.client._listeners) == 2
        fake.push(
            "item/agentMessage/delta",
            {"threadId": "sess-a", "turnId": "turn-1", "itemId": "i1", "delta": "A1"},
        )
        fake.push(
            "item/agentMessage/delta",
            {"threadId": "sess-b", "turnId": "turn-1", "itemId": "i1", "delta": "B1"},
        )
        fake.push(
            "item/agentMessage/delta",
            {"threadId": "sess-a", "turnId": "turn-1", "itemId": "i1", "delta": "A2"},
        )
        fake.push(
            "turn/completed",
            {"threadId": "sess-b", "turnId": "turn-1", "status": "completed"},
        )
        fake.push(
            "turn/completed",
            {"threadId": "sess-a", "turnId": "turn-1", "status": "completed"},
        )
        await asyncio.wait_for(
            asyncio.gather(task_a, task_b),
            timeout=5,
        )
        by_thread = {
            events[0]["thread_id"]: [e.get("text") for e in events if e.get("type") == "agent_message.delta"]
            for events in results
        }
        assert by_thread == {"sess-a": ["A1", "A2"], "sess-b": ["B1"]}

    _run(scenario())


def test_request_error_raises_codex_run_error() -> None:
    async def scenario() -> None:
        client = AppServerClient()
        client._proc = object()
        error = {"code": -32000, "message": "boom"}

        async def fake_write(obj: dict) -> None:
            client._pending[obj["id"]].set_result(
                {"id": obj["id"], "error": error}
            )

        client._write = fake_write  # type: ignore[method-assign]
        with pytest.raises(CodexRunError, match="boom"):
            await client.request("thread/start", {"cwd": "/x"})

    _run(scenario())
