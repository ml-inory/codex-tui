"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_codex_tui_home(tmp_path, monkeypatch):
    """Keep tests away from the developer's real ~/.codex-tui state.

    Several app tests exercise title/model overrides without passing an
    explicit overrides path; without isolation they read and write the real
    ``~/.codex-tui/overrides.json``.
    """
    monkeypatch.setenv(
        "CODEX_TUI_HOME",
        str(tmp_path / "codex-tui-home"),
    )
