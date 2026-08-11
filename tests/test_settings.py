from pathlib import Path

from codex_tui.settings import AppSettings


def test_missing_corrupt_and_non_utf8_files_use_defaults(tmp_path: Path) -> None:
    assert AppSettings.load(tmp_path / "nope.json").project_mode == "short"

    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert AppSettings.load(bad).project_mode == "short"
    assert AppSettings.load(bad).sidebar_visible is True

    not_utf8 = tmp_path / "torn.json"
    not_utf8.write_bytes(b"\xc3\x28")
    settings = AppSettings.load(not_utf8)
    assert settings.project_mode == "short"
    assert settings.sidebar_visible is True


def test_known_projects_round_trip(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings = AppSettings.load(settings_path)
    settings.known_projects = ["/repo/a", "/repo/b"]
    settings.save()

    reloaded = AppSettings.load(settings_path)
    assert reloaded.known_projects == ["/repo/a", "/repo/b"]


def test_known_projects_filters_non_strings(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        '{"known_projects": ["/ok", 42, ""]}', encoding="utf-8"
    )
    assert AppSettings.load(settings_path).known_projects == ["/ok"]
