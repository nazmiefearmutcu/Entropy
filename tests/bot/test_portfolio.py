from entropy.bot.portfolio import Portfolio, PositionSide


def test_open_long_then_mark_unrealized():
    p = Portfolio(starting_cash=100_000.0)
    p.open("SPY", PositionSide.LONG, qty=10.0, entry_px=100.0,
           stop_px=99.0, tp_px=102.0, ts_ns=1, fee=1.0)
    p.mark("SPY", 105.0)
    assert p.unrealized_pnl() == 50.0  # (105-100)*10
    assert p.equity() == 100_000.0 + 50.0 - 1.0  # minus entry fee


def test_close_long_realizes_pnl_net_of_fees():
    p = Portfolio(starting_cash=100_000.0)
    p.open("SPY", PositionSide.LONG, qty=10.0, entry_px=100.0,
           stop_px=99.0, tp_px=102.0, ts_ns=1, fee=1.0)
    realized = p.close("SPY", exit_px=110.0, ts_ns=2, fee=1.0)
    assert realized == 100.0 - 1.0  # gross (110-100)*10 minus exit fee
    assert "SPY" not in p.positions
    assert p.equity() == 100_000.0 - 1.0 + 99.0  # entry fee + realized


def test_short_unrealized_is_inverted():
    p = Portfolio(starting_cash=100_000.0)
    p.open("X", PositionSide.SHORT, qty=5.0, entry_px=50.0,
           stop_px=51.0, tp_px=48.0, ts_ns=1, fee=0.0)
    p.mark("X", 45.0)
    assert p.unrealized_pnl() == 25.0  # (50-45)*5


def test_exposure_is_gross_notional():
    p = Portfolio(starting_cash=100_000.0)
    p.open("A", PositionSide.LONG, qty=10.0, entry_px=10.0,
           stop_px=9.0, tp_px=11.0, ts_ns=1, fee=0.0)
    p.mark("A", 12.0)
    assert p.exposure() == 120.0  # 10 * 12 mark


def test_daily_pnl_and_reset():
    p = Portfolio(starting_cash=1000.0)
    p.open("A", PositionSide.LONG, qty=1.0, entry_px=100.0,
           stop_px=99.0, tp_px=101.0, ts_ns=1, fee=0.0)
    p.mark("A", 110.0)
    assert p.daily_pnl() == 10.0
    p.reset_day()
    assert p.daily_pnl() == 0.0


def test_snapshot_reports_positions_and_totals():
    p = Portfolio(starting_cash=1000.0)
    p.open("A", PositionSide.LONG, qty=2.0, entry_px=10.0,
           stop_px=9.0, tp_px=11.0, ts_ns=1, fee=0.0)
    p.mark("A", 12.0)
    snap = p.snapshot(ts_ns=5)
    assert snap.open_count == 1
    assert snap.positions[0].symbol == "A"
    assert snap.positions[0].unrealized_pnl == 4.0
    assert snap.equity == 1004.0
