from entropy.engine.engine import Engine
from entropy.engine.events import NewHigh

S = 1_000_000_000


def test_first_tick_is_baseline_no_events():
    e = Engine()
    assert e.on_trade("AAA", 100.0, 1.0, "buy", 0) == []


def test_new_session_high_emitted():
    e = Engine()
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)         # baseline
    evs = e.on_trade("AAA", 101.0, 1.0, "buy", S)
    assert any(isinstance(x, NewHigh) for x in evs)


def test_snapshot_has_breadth_and_boards():
    e = Engine()
    e.on_trade("AAA", 100.0, 5.0, "buy", 0)
    e.on_trade("AAA", 101.0, 5.0, "buy", S)
    e.on_trade("BBB", 50.0, 5.0, "sell", S)
    snap = e.snapshot()
    assert snap.breadth.buy_pct >= 0 and snap.breadth.sell_pct >= 0
    assert isinstance(snap.new_highs, tuple)
    assert any(r.symbol == "AAA" for r in snap.top_movers)


def test_per_window_counts_and_ticker():
    e = Engine()
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)          # baseline
    e.on_trade("AAA", 101.0, 1.0, "buy", S)          # new highs across all windows
    e.on_trade("AAA", 102.0, 1.0, "buy", 2 * S)      # more new highs
    snap = e.snapshot()
    # every rolling window registered new-high activity for AAA
    assert snap.breadth.nh_counts["30s"] >= 2
    assert set(snap.breadth.nh_counts) == {"30s", "1m", "5m", "20m"}
    # ticker groups exist per window with AAA as a top symbol
    g30 = next(g for g in snap.ticker if g.window == "30s")
    assert g30.entries and g30.entries[0][0] == "AAA" and g30.entries[0][1] >= 2


def test_quote_returns_last_price_and_pct():
    e = Engine()
    assert e.quote("SPY") is None              # unseen
    e.on_trade("SPY", 100.0, 1.0, "buy", 0)    # baseline sets first price
    e.on_trade("SPY", 102.0, 1.0, "buy", S)
    q = e.quote("SPY")
    assert q is not None
    price, pct = q
    assert price == 102.0 and abs(pct - 2.0) < 1e-9   # (102-100)/100 * 100


def test_determinism_same_input_same_events():
    seq = [("AAA", 100.0, 1.0, "buy", 0), ("AAA", 102.0, 1.0, "buy", S),
           ("AAA", 99.0, 1.0, "sell", 2 * S)]
    a, b = Engine(), Engine()
    out_a = [a.on_trade(*t) for t in seq]
    out_b = [b.on_trade(*t) for t in seq]
    assert out_a == out_b
