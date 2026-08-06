import json
from pathlib import Path


def make_session_file(
    root: Path,
    *,
    session_id: str = "019f0000-0000-0000-0000-000000000001",
    cwd: str = "/home/user/proj-a",
    timestamp: str = "2026-08-07T02:54:37.000Z",
    model: str = "deepseek-v4-flash",
    user_text: str = "First question",
    assistant_text: str = "Hello there",
    extra_events: list[dict] | None = None,
) -> Path:
    """Build a realistic session JSONL file in a temp tree."""
    day_dir = root / "2026" / "08" / "07"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"rollout-2026-08-07T02-54-37-{session_id}.jsonl"

    events = [
        {
            "timestamp": timestamp,
            "type": "session_meta",
            "payload": {
                "session_id": session_id,
                "timestamp": timestamp,
                "cwd": cwd,
                "model_provider": "deepseek",
            },
        },
        {
            "type": "turn_context",
            "payload": {
                "turn_id": "turn-1",
                "cwd": cwd,
                "model": model,
            },
        },
        {
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": "turn-1"},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": user_text}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "reasoning",
                "id": "reason-1",
                "summary": [],
                "content": [{"type": "reasoning_text", "text": "thinking..."}],
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "id": "call-1",
                "name": "exec_command",
                "arguments": "{}",
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": assistant_text}],
            },
        },
        {"type": "future_event_type", "payload": {"anything": True}},
        "not-json{",
    ]
    events.extend(extra_events or [])
    path.write_text(
        "\n".join(json.dumps(e) for e in events if isinstance(e, dict)) + "\n",
        encoding="utf-8",
    )
    return path


def make_many_message_session(root: Path, n: int, *, cwd: str = "/proj/a") -> Path:
    """Build a session with n alternating user/assistant messages."""
    session_id = "aaaaaaaa-1111-1111-1111-111111111111"
    day_dir = root / "2026" / "08" / "07"
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / f"rollout-2026-08-07T02-54-37-{session_id}.jsonl"
    events = [
        {
            "type": "session_meta",
            "payload": {
                "session_id": session_id,
                "cwd": cwd,
                "timestamp": "2026-08-07T02:54:37.000Z",
            },
        }
    ]
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        text = f"问题{i}" if role == "user" else f"回答{i}"
        events.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": role,
                    "content": [
                        {
                            "type": "input_text" if role == "user" else "output_text",
                            "text": text,
                        }
                    ],
                },
            }
        )
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )
    return path
