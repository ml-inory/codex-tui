import json
from pathlib import Path

from codex_tui.sessions import Session, SessionStore, parse_session_file


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


def test_parse_session_file_extracts_meta_and_messages(tmp_path: Path) -> None:
    path = make_session_file(tmp_path)

    session = parse_session_file(path)

    assert session.id == "019f0000-0000-0000-0000-000000000001"
    assert session.cwd == "/home/user/proj-a"
    assert session.timestamp == "2026-08-07T02:54:37.000Z"
    assert session.model == "deepseek-v4-flash"
    assert [(m.role, m.content) for m in session.messages] == [
        ("user", "First question"),
        ("assistant", "Hello there"),
    ]
    assert session.title == "First question"
    assert session.project == "/home/user/proj-a"


def test_parse_ignores_unknown_events_and_bad_lines(tmp_path: Path) -> None:
    path = make_session_file(tmp_path)

    session = parse_session_file(path)

    assert len(session.messages) == 2
    assert session.model == "deepseek-v4-flash"


def test_parse_fallback_id_from_filename(tmp_path: Path) -> None:
    path = make_session_file(tmp_path, session_id="")

    session = parse_session_file(path)

    assert session.id == path.stem


def test_store_scans_nested_layout_and_groups_by_project(tmp_path: Path) -> None:
    make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        timestamp="2026-08-07T02:00:00.000Z",
        user_text="older",
    )
    make_session_file(
        tmp_path,
        session_id="22222222-2222-2222-2222-222222222222",
        cwd="/proj/b",
        timestamp="2026-08-07T03:00:00.000Z",
        user_text="newer",
    )

    store = SessionStore(tmp_path)

    sessions = store.list_sessions()
    assert [s.id[0] for s in sessions] == ["2", "1"]
    assert store.list_projects() == ["/proj/b", "/proj/a"]
    assert [s.id for s in store.sessions_for_project("/proj/a")] == [
        "11111111-1111-1111-1111-111111111111"
    ]


def test_store_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert SessionStore(tmp_path / "nope").list_sessions() == []
    assert SessionStore(tmp_path / "nope").list_projects() == []
