"""The ``:`` command grammar, executed against a live SnapshotSource.

Every verb the parser admits is implemented here. The previous version ended in
``return CommandResult(ok=True, message=f"{verb} acknowledged")``, so ``:tf 1h``
reported success while changing nothing — the UI believed a timeframe switch
that never happened. Anything this layer cannot genuinely do now answers
ok=False and says why.
"""

from __future__ import annotations

from entropy.ui.widgets.command_bar import CommandError, parse_command
from entropy_sidecar.contract import THEMES, CommandResult
from entropy_sidecar.stream import SnapshotSource

_HELP = (
    "chart SYM · watch/unwatch SYM · tf 1m|5m|15m|1h|4h · theme NAME · "
    "source sim|live|auto · depth [SYM] · help"
)


def _settings_result(
    problems: list[str], persist_error: str, message: str
) -> CommandResult:
    """One shape for every settings-mutating verb.

    A validation failure changed nothing (ok=False). A persistence failure DID
    change the running config, so it stays ok=True but names the disk problem
    rather than pretending the setting is durable.
    """
    if problems:
        return CommandResult(ok=False, message="settings unchanged", problems=problems)
    if persist_error:
        return CommandResult(ok=True, message=f"{message} ({persist_error})")
    return CommandResult(ok=True, message=message)


def apply_command(source: SnapshotSource, text: str) -> CommandResult:
    parsed = parse_command(text)
    if isinstance(parsed, CommandError):
        return CommandResult(ok=False, message=parsed.message)
    verb, arg = parsed.verb, parsed.arg

    if verb == "chart":
        source.set_focus(arg)
        return CommandResult(ok=True, message=f"focus {source.focus}")

    if verb == "depth":
        if arg:
            # `depth SYM` focuses the symbol AND makes sure the ladder is on.
            source.set_focus(arg)
            problems, persist = source.patch_app(show_depth=True)
            return _settings_result(problems, persist, f"depth on {source.focus}")
        problems, persist = source.patch_app(show_depth=not source.cfg.show_depth)
        state = "on" if source.cfg.show_depth else "off"
        return _settings_result(problems, persist, f"depth {state}")

    if verb == "watch":
        ok, message = source.add_watch(arg)
        return CommandResult(ok=ok, message=message)

    if verb == "unwatch":
        ok, message = source.remove_watch(arg)
        return CommandResult(ok=ok, message=message)

    if verb == "tf":
        problems, persist = source.patch_app(timeframe=arg)
        return _settings_result(problems, persist, f"timeframe {arg}")

    if verb == "theme":
        if arg not in THEMES:
            return CommandResult(
                ok=False, message=f"unknown theme {arg!r}; choose from {'|'.join(THEMES)}"
            )
        problems, persist = source.patch_app(theme=arg)
        return _settings_result(problems, persist, f"theme {arg}")

    if verb == "source":
        problems, persist = source.patch_app(equity_source=arg)
        return _settings_result(problems, persist, f"equity source {arg}")

    if verb == "help":
        return CommandResult(ok=True, message=_HELP)

    # parse_command admits no other verb today; if one is added upstream this
    # must keep telling the truth instead of acknowledging a no-op.
    return CommandResult(ok=False, message=f"{verb!r} is parsed but not implemented here")
