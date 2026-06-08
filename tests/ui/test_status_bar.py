# tests/ui/test_status_bar.py
from entropy.ui.widgets.status_bar import format_telemetry


def test_telemetry_line():
    line = format_telemetry(raw_hz=4323, prev30s=3.10, snap_drops=99566, spikes=229,
                            accel="accelerating", dropped=0)
    assert "raw: 4323 Hz" in line and "spikes: 229" in line and "snap-drops: 99566" in line
