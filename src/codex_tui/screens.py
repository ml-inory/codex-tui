"""Modal screens for codex-tui."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, ListItem, ListView, Static

from codex_tui.models import ModelEntry


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


class ModelScreen(ModalScreen[str | None]):
    """Pick a model from the codex catalog."""

    BINDINGS = [("escape", "cancel", "Cancel")]
    CLEAR = ""

    def __init__(
        self,
        models: list[ModelEntry],
        current: str | None = None,
    ) -> None:
        super().__init__()
        self.models = models
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="model-dialog"):
            yield Static("Select model", classes="dialog-title")
            yield ListView(id="model-list")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Clear (use default)", id="model-clear")
                yield Button("Cancel", id="model-cancel")

    async def on_mount(self) -> None:
        model_list = self.query_one("#model-list", ListView)
        for index, entry in enumerate(self.models):
            label = (
                entry.display_name
                if entry.display_name != entry.slug
                else entry.slug
            )
            await model_list.append(ListItem(Static(label, classes="model-label")))
        # Note: index is intentionally left unset so mounting does not fire
        # ListView.Selected (which would dismiss the modal immediately).
        model_list.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(ListView.Selected, "#model-list")
    def _on_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index >= len(self.models):
            return
        self.dismiss(self.models[index].slug)

    @on(Button.Pressed, "#model-clear")
    def _on_clear(self) -> None:
        self.dismiss(self.CLEAR)

    @on(Button.Pressed, "#model-cancel")
    def _on_cancel(self) -> None:
        self.dismiss(None)
