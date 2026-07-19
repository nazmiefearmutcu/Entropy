from __future__ import annotations

from entropy.ui.widgets.command_bar import CommandError, parse_command
from entropy_sidecar.contract import CommandResult
from entropy_sidecar.stream import SnapshotSource


def apply_command(source: SnapshotSource, text: str) -> CommandResult:
    parsed = parse_command(text)
    if isinstance(parsed, CommandError):
        return CommandResult(ok=False, message=parsed.message)
    verb, arg = parsed.verb, parsed.arg
    if verb in ("chart", "depth") and arg:
        source.set_focus(arg)
        return CommandResult(ok=True, message=f"focus {arg}")
    if verb == "source" and arg in ("sim", "live"):
        source.source = arg
        return CommandResult(ok=True, message=f"source {arg}")
    return CommandResult(ok=True, message=f"{verb} acknowledged")
