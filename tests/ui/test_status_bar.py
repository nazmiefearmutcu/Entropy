# tests/ui/test_status_bar.py
from entropy.ui.widgets.status_bar import format_telemetry


def test_telemetry_line():
    line = format_telemetry(raw_hz=4323, prev30s=3.10, snap_drops=99566, spikes=229,
                            accel="accelerating", dropped=0)
    assert "raw: 4323 Hz" in line and "spikes: 229" in line and "snap-drops: 99566" in line


def test_telemetry_accel_label_and_dropped():
    line = format_telemetry(raw_hz=10, prev30s=1.0, snap_drops=0, spikes=0,
                            accel="accelerating", dropped=5)
    assert "● Accelerating" in line and "dropped: 5" in line
    steady = format_telemetry(raw_hz=10, prev30s=1.0, snap_drops=0, spikes=0,
                              accel="steady", dropped=0)
    assert "● Steady" in steady and "dropped" not in steady
