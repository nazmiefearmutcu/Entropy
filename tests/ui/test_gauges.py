from entropy.ui.widgets.gauges import fill_cells

def test_full_and_partial_fill():
    assert fill_cells(1.0, 10) == "█" * 10
    assert fill_cells(0.0, 10) == " " * 10
    s = fill_cells(0.5, 10)            # 5 full blocks then spaces
    assert s.startswith("█" * 5) and len(s) == 10
