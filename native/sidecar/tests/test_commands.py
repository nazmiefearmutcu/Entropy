from entropy_sidecar.commands import apply_command
from entropy_sidecar.stream import SnapshotSource


def test_chart_sets_focus():
    src = SnapshotSource()
    res = apply_command(src, "chart aapl")
    assert res.ok is True
    assert src._focus == "AAPL"


def test_depth_with_symbol_sets_focus():
    src = SnapshotSource()
    assert apply_command(src, "depth tsla").ok is True
    assert src._focus == "TSLA"


def test_unknown_verb_reports_error():
    src = SnapshotSource()
    res = apply_command(src, "frobnicate x")
    assert res.ok is False and "unknown" in res.message.lower()
