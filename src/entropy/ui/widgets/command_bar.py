"""Bloomberg-style command line: ``:`` reveals a one-line input docked above
the status bar; Enter submits (the app executes the parsed command), Esc
hides. Hidden by default.

``parse_command`` is pure — text in, ``Command | CommandError`` out — so the
grammar is unit-testable without an app. Symbol/theme *existence* checks that
need app state (registered themes) stay in the app's executor; everything
knowable statically (verbs, arity, timeframe and source vocabularies) is
validated here.
"""

from __future__ import annotations

from typing import Any

import msgspec
from textual.binding import Binding
from textual.widgets import Input

from entropy.engine.timeframe import TIMEFRAMES

_SOURCES = ("sim", "live", "auto")

# verb -> (min_args, max_args)
_ARITY = {
    "chart": (1, 1),
    "watch": (1, 1),
    "unwatch": (1, 1),
    "tf": (1, 1),
    "theme": (1, 1),
    "source": (1, 1),
    "depth": (0, 1),
    "help": (0, 0),
}

_USAGE = "chart SYM · watch/unwatch SYM · tf 1m|5m|15m|1h|4h · theme NAME · " \
         "source sim|live|auto · depth [SYM] · help"


class Command(msgspec.Struct, frozen=True):
    """A validated command: canonical lowercase verb + normalized argument
    ("" for zero-arg verbs)."""

    verb: str
    arg: str = ""


class CommandError(msgspec.Struct, frozen=True):
    message: str


def normalize_symbol(raw: str) -> str:
    """Bare equity tickers uppercase; ``venue:RAW`` crypto canonicals keep the
    venue lowercase and uppercase the raw part (matching universe ids like
    ``binance-spot:BTCUSDT``)."""
    if ":" in raw:
        venue, sym = raw.split(":", 1)
        return f"{venue.lower()}:{sym.upper()}"
    return raw.upper()


def parse_command(text: str) -> Command | CommandError:
    """Parse one command line. Pure: no app state, no side effects."""
    parts = text.split()
    if not parts:
        return CommandError(f"empty command — {_USAGE}")
    verb, args = parts[0].lower(), parts[1:]
    arity = _ARITY.get(verb)
    if arity is None:
        return CommandError(f"unknown command {verb!r} — {_USAGE}")
    lo, hi = arity
    if not (lo <= len(args) <= hi):
        if hi == 0:
            expect = "no argument"
        elif lo == 0:          # variadic 0-or-1 verbs (depth): 0 args is valid too
            expect = "at most one argument"
        else:
            expect = "exactly one argument"
        return CommandError(f"{verb} takes {expect}")
    if verb == "help":
        return Command(verb=verb)
    if verb == "depth":
        # Zero-arg toggles the panel; one arg focuses that symbol (normalized
        # like chart/watch). Handled here because it is the only variadic verb.
        return Command(verb=verb, arg=normalize_symbol(args[0]) if args else "")
    arg = args[0]
    if verb in ("chart", "watch", "unwatch"):
        return Command(verb=verb, arg=normalize_symbol(arg))
    if verb == "tf":
        tf = arg.lower()
        if tf not in TIMEFRAMES:
            return CommandError(
                f"unknown timeframe {arg!r}; choose from {'|'.join(TIMEFRAMES)}"
            )
        return Command(verb=verb, arg=tf)
    if verb == "theme":
        return Command(verb=verb, arg=arg.lower())
    # source
    src = arg.lower()
    if src not in _SOURCES:
        return CommandError(f"unknown source {arg!r}; choose from {'|'.join(_SOURCES)}")
    return Command(verb=verb, arg=src)


class CommandBar(Input):
    """Hidden-by-default one-line command input docked above the StatusBar.

    The app reveals it via :meth:`show` (its ``:`` binding) and handles the
    bubbled ``Input.Submitted``; Esc hides it from anywhere inside the bar.
    """

    BINDINGS = [Binding("escape", "hide", "Hide", show=False)]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("placeholder", _USAGE)
        super().__init__(*args, **kwargs)
        # Hidden AND unfocusable: otherwise the invisible Input grabs the
        # app's initial auto-focus and swallows the very ':' keypress that
        # should reveal it.
        self.display = False
        self.can_focus = False

    def show(self) -> None:
        self.can_focus = True
        self.display = True
        self.value = ""
        self.focus()

    def hide(self) -> None:
        self.display = False
        self.value = ""
        self.can_focus = False
        self.blur()

    def action_hide(self) -> None:
        self.hide()
