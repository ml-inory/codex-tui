from pathlib import Path

from codex_tui.sessions import Session, SessionStore, parse_session_file
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
