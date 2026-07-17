"""Symbol search modal: live fuzzy lookup over the unified equity+crypto universe.

``/`` opens it. Typing re-queries :class:`~entropy.data.universe.UniverseService`
on every keystroke; Enter (or selecting an option) sets the app's ``focus_symbol``
and closes; ``ctrl+w`` toggles the highlighted row in the persistent watchlist
(updating its ★ marker) and stays open; Esc dismisses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from entropy.data.universe import SymbolInfo

if TYPE_CHECKING:
    from entropy.ui.app import EntropyApp


class SearchScreen(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        # priority: Input binds ctrl+w to delete-word-left, which would swallow
        # the toggle while the search box is focused (plain "w" is unusable
        # inside a text input for the same reason).
        Binding("ctrl+w", "toggle_watch", "Watch/unwatch", priority=True),
        Binding("down", "move_down", "Next", show=False),
        Binding("up", "move_up", "Previous", show=False),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._results: dict[str, SymbolInfo] = {}

    @property
    def _entropy(self) -> EntropyApp:
        return cast("EntropyApp", self.app)

    def compose(self) -> ComposeResult:
        with Vertical(id="search-container"):
            yield Input(placeholder="Search symbols (ticker or name)…", id="search-input")
            yield OptionList(id="search-results")

    def on_mount(self) -> None:
        self._refresh_options()  # empty query -> curated defaults
        self.query_one("#search-input", Input).focus()

    def _refresh_options(self, keep: int | None = None) -> None:
        app = self._entropy
        query = self.query_one("#search-input", Input).value
        results = app._universe.search(query)
        self._results = {info.symbol: info for info in results}
        options = self.query_one("#search-results", OptionList)
        options.clear_options()
        for info in results:
            star = "★" if info.symbol in app._watchlist else " "
            prompt = Text(f"{star} {info.symbol} — {info.name}")
            prompt.append(f"  [{info.asset_class}]", style="dim")
            options.add_option(Option(prompt, id=info.symbol))
        if results:
            options.highlighted = min(keep if keep is not None else 0, len(results) - 1)

    def _highlighted_symbol(self) -> str | None:
        options = self.query_one("#search-results", OptionList)
        if options.highlighted is None or options.option_count == 0:
            return None
        return options.get_option_at_index(options.highlighted).id

    def _choose(self, symbol: str) -> None:
        self._entropy.focus_symbol = symbol
        self.app.pop_screen()

    # --- events ------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        self._refresh_options()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        symbol = self._highlighted_symbol()
        if symbol:
            self._choose(symbol)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self._choose(event.option.id)

    # --- actions -------------------------------------------------------------

    def action_toggle_watch(self) -> None:
        options = self.query_one("#search-results", OptionList)
        index = options.highlighted
        symbol = self._highlighted_symbol()
        info = self._results.get(symbol or "")
        if index is None or info is None:
            return
        self._entropy.toggle_watch(info)
        self._refresh_options(keep=index)  # redraw ★ markers, keep the cursor

    def _move_highlight(self, delta: int) -> None:
        options = self.query_one("#search-results", OptionList)
        if options.option_count == 0:
            return
        current = options.highlighted if options.highlighted is not None else 0
        options.highlighted = (current + delta) % options.option_count

    def action_move_down(self) -> None:
        self._move_highlight(1)

    def action_move_up(self) -> None:
        self._move_highlight(-1)

    async def action_dismiss(self, result: None = None) -> None:
        self.app.pop_screen()
