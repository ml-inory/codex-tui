from pathlib import Path

from codex_tui.sessions import (
    Message,
    Session,
    SessionStore,
    generate_title,
    is_injected_message,
    parse_session_file,
)
from tests.helpers import make_session_file


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


def test_title_override_and_effective_model(tmp_path: Path) -> None:
    path = make_session_file(tmp_path)

    session = parse_session_file(path)
    session.title_override = "Custom title"
    session.model_override = "deepseek-v4-pro"

    assert session.title == "Custom title"
    assert session.effective_model == "deepseek-v4-pro"
    assert session.model == "deepseek-v4-flash"  # parsed model stays intact


def test_clean_trash_deletes_files_and_counts(tmp_path: Path) -> None:
    trash = tmp_path / "trash"
    trash.mkdir()
    (trash / "a.jsonl").write_text("x", encoding="utf-8")
    (trash / "b.jsonl").write_text("y", encoding="utf-8")
    store = SessionStore(tmp_path, trash_dir=trash)

    assert store.clean_trash() == 2
    assert list(trash.iterdir()) == []
    assert store.clean_trash() == 0


def test_clean_trash_missing_dir_returns_zero(tmp_path: Path) -> None:
    store = SessionStore(tmp_path, trash_dir=tmp_path / "nope")
    assert store.clean_trash() == 0


def test_store_reparses_only_changed_files(tmp_path: Path) -> None:
    from tests.helpers import make_session_file

    path = make_session_file(
        tmp_path,
        session_id="11111111-1111-1111-1111-111111111111",
        cwd="/proj/a",
        user_text="first version",
    )
    store = SessionStore(tmp_path)
    assert store.list_sessions()[0].messages[0].content == "first version"

    # Same file, new content: the cache must notice and re-parse.
    content = path.read_text(encoding="utf-8")
    path.write_text(
        content.replace("first version", "second version"), encoding="utf-8"
    )
    assert store.list_sessions()[0].messages[0].content == "second version"


def test_is_injected_message_detects_system_context() -> None:
    assert is_injected_message("<environment_context>\n  <cwd>/tmp</cwd>")
    assert is_injected_message("<turn_aborted>\nThe user interrupted the turn")
    assert is_injected_message("<skill>\n<name>magnetar</name>")
    assert is_injected_message("<codex_internal_context source=\"goal\">")
    assert not is_injected_message("有没有需要提交的代码")
    assert not is_injected_message("<image name=\"x\"></image>C++和Python哪个好")


def test_generate_title_skips_injected_and_picks_first_question() -> None:
    messages = [
        Message("user", "<environment_context>\n  <cwd>/home/user</cwd>"),
        Message("user", "有没有需要提交的代码"),
        Message("assistant", "我先看看"),
    ]
    assert generate_title(messages) == "有没有需要提交的代码"


def test_generate_title_handles_image_lines_and_truncation() -> None:
    messages = [
        Message("user", "<image name=\"x\"></image>C++和Python哪个好"),
    ]
    assert generate_title(messages) == "C++和Python哪个好"

    long_message = Message("user", "很" * 60)
    title = generate_title([long_message])
    assert len(title) == 58 and title.endswith("…")


def test_title_falls_back_to_session_id_without_real_question() -> None:
    session = Session(
        id="019f0000-0000-0000-0000-000000000001",
        path=Path("/tmp/x.jsonl"),
        messages=[Message("user", "<turn_aborted>\nThe user interrupted")],
    )
    assert session.title == "Session 019f0000"
