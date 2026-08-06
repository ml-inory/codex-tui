"""Modal screens for codex-tui."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, ListItem, ListView, Static

from codex_tui.models import ModelEntry
from codex_tui.sessions import Session


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


class QuickSwitchScreen(ModalScreen[Session | None]):
    """Jump to any session by typing part of its title or project."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("down", "focus_list", "Choose"),
    ]

    def __init__(self, sessions: list[Session]) -> None:
        super().__init__()
        self.all_sessions = sessions
        self._filtered: list[Session] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="quick-switch-dialog"):
            yield Static("Switch session (type to filter)", classes="dialog-title")
            yield Input(
                placeholder="Session title or project…",
                id="quick-switch-input",
            )
            yield ListView(id="quick-switch-list")

    async def on_mount(self) -> None:
        await self._rebuild("")
        self.query_one("#quick-switch-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_focus_list(self) -> None:
        self.query_one("#quick-switch-list", ListView).focus()

    @on(Input.Changed, "#quick-switch-input")
    async def _on_filter_changed(self, event: Input.Changed) -> None:
        await self._rebuild(event.value)

    @on(Input.Submitted, "#quick-switch-input")
    def _on_submitted(self, event: Input.Submitted) -> None:
        list_view = self.query_one("#quick-switch-list", ListView)
        index = list_view.index
        if index is not None and 0 <= index < len(self._filtered):
            self.dismiss(self._filtered[index])
        elif self._filtered:
            self.dismiss(self._filtered[0])

    @on(ListView.Selected, "#quick-switch-list")
    def _on_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index >= len(self._filtered):
            return
        self.dismiss(self._filtered[index])

    async def _rebuild(self, query: str) -> None:
        needle = query.strip().lower()
        if needle:
            filtered = [
                session
                for session in self.all_sessions
                if needle in session.title.lower()
                or needle in session.project.lower()
                or needle in session.id.lower()
            ]
        else:
            filtered = list(self.all_sessions)
        self._filtered = filtered
        list_view = self.query_one("#quick-switch-list", ListView)
        await list_view.clear()
        for session in filtered:
            stamp = session.timestamp[5:16] if len(session.timestamp) >= 16 else ""
            label = f"{session.title}  |  {session.project}  |  {stamp}"
            await list_view.append(
                ListItem(Static(label, classes="switch-label"))
            )
        if filtered:
            list_view.index = 0
