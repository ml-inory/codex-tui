import json
from pathlib import Path

from codex_tui.models import load_model_catalog


def _write_catalog(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "models": [
                    {"slug": "model-a", "display_name": "Model A"},
                    {"slug": "model-b"},
                    {"not": "a model"},
                    "junk",
                ]
            }
        ),
        encoding="utf-8",
    )


def test_load_models_parses_slugs_and_names(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    _write_catalog(path)

    entries = load_model_catalog(path)

    assert [(e.slug, e.display_name) for e in entries] == [
        ("model-a", "Model A"),
        ("model-b", "model-b"),
    ]


def test_missing_corrupt_and_non_dict_files_are_empty(tmp_path: Path) -> None:
    assert load_model_catalog(tmp_path / "nope.json") == []

    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    assert load_model_catalog(bad) == []

    not_a_dict = tmp_path / "list.json"
    not_a_dict.write_text("[]", encoding="utf-8")
    assert load_model_catalog(not_a_dict) == []
