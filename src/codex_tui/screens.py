"""Modal screens for codex-tui."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class RenameScreen(ModalScreen[str | None]):
    """Prompt for a new session title."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, current_title: str) -> None:
        super().__init__()
        self.current_title = current_title

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-dialog"):
            yield Static("Rename session", classes="dialog-title")
            yield Input(
                value=self.current_title,
                id="rename-input",
                placeholder="Session title",
            )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Save", id="rename-save", variant="primary")
                yield Button("Cancel", id="rename-cancel")

    def on_mount(self) -> None:
        self.query_one("#rename-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted, "#rename-input")
    def _on_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    @on(Button.Pressed, "#rename-save")
    def _on_save(self) -> None:
        self.dismiss(self.query_one("#rename-input", Input).value.strip())

    @on(Button.Pressed, "#rename-cancel")
    def _on_cancel(self) -> None:
        self.dismiss(None)
