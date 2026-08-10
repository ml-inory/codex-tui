"""Parse the codex model catalog for the model picker."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_CODEX_HOME = Path.home() / ".codex"
DEFAULT_MODEL_CATALOG = "models.json"


@dataclass(frozen=True)
class ModelEntry:
    """One selectable model from the catalog."""

    slug: str
    display_name: str


def _catalog_path(path: Path | None) -> Path:
    if path is not None:
        return path
    home = Path(os.environ.get("CODEX_HOME", str(DEFAULT_CODEX_HOME)))
    return home / DEFAULT_MODEL_CATALOG


def load_model_catalog(path: Path | None = None) -> list[ModelEntry]:
    """Return the catalog models, or ``[]`` if the file is missing/corrupt."""
    catalog_path = _catalog_path(path)
    try:
        raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    models = raw.get("models")
    if not isinstance(models, list):
        return []
    entries: list[ModelEntry] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        slug = model.get("slug")
        if not slug:
            continue
        display_name = model.get("display_name") or slug
        entries.append(ModelEntry(slug=str(slug), display_name=str(display_name)))
    return entries
