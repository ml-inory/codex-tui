import json
from pathlib import Path

from codex_tui.overrides import Overrides
from codex_tui.sessions import Session


def test_roundtrip_set_get_save_load(tmp_path: Path) -> None:
    path = tmp_path / "overrides.json"

    overrides = Overrides.load(path)
    overrides.set("session-1", "title", "My session")
    overrides.set("session-1", "model", "deepseek-v4-pro")

    assert path.is_file()
    reloaded = Overrides.load(path)
    assert reloaded.get("session-1", "title") == "My session"
    assert reloaded.get("session-1", "model") == "deepseek-v4-pro"


def test_missing_and_corrupt_files_are_tolerated(tmp_path: Path) -> None:
    assert Overrides.load(tmp_path / "nope.json").data == {}

    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert Overrides.load(bad).data == {}

    not_a_dict = tmp_path / "list.json"
    not_a_dict.write_text("[]", encoding="utf-8")
    assert Overrides.load(not_a_dict).data == {}


def test_apply_copies_overrides_onto_session() -> None:
    session = Session(id="s1", path=Path("/tmp/x.jsonl"))
    overrides = Overrides(path=Path("/tmp/overrides.json"))
    overrides.data = {"s1": {"title": "Renamed", "model": "other-model"}}

    overrides.apply(session)

    assert session.title_override == "Renamed"
    assert session.model_override == "other-model"
    assert session.title == "Renamed"
    assert session.effective_model == "other-model"


def test_saved_file_is_readable_json(tmp_path: Path) -> None:
    path = tmp_path / "overrides.json"
    Overrides.load(path).set("s1", "title", "你好")

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["s1"]["title"] == "你好"
