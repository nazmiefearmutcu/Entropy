from entropy.engine.engine import Engine
from entropy.engine.events import NewHigh, NewLow

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


def test_prev_extreme_is_none_after_window_gap():
    # After a gap longer than the window span, the old extreme has expired, so a
    # new extreme must report prev_extreme=None (not the stale pre-eviction value).
    e = Engine()  # legacy second-scale: 30s / 1m / 5m rolling windows
    e.on_trade("AAA", 500.0, 1.0, "buy", 0)                # baseline at 500
    # 60s later -> 500 evicted from 30s (w0) win
    evs = e.on_trade("AAA", 100.0, 1.0, "sell", 60 * S)
    nl_w0 = next(x for x in evs if isinstance(x, NewLow) and x.window.value == "w0")
    assert nl_w0.prev_extreme is None                      # not the stale 500.0


# --- gap re-seed rule: an emptied window compares against the tape's last trade
# price, so only the direction-consistent event fires (none on a flat re-trade).

def test_gap_reseed_drop_emits_only_new_low_not_phantom_high():
    # Idle gap > w0 span empties BOTH the max and min deques; the next trade
    # would then read as "first in window" for both. A DROP 500 -> 100 must NOT
    # fabricate a NewHigh@100 alongside the legit NewLow.
    e = Engine()  # legacy: w0=30s
    e.on_trade("AAA", 500.0, 1.0, "buy", 0)
    evs = e.on_trade("AAA", 100.0, 1.0, "sell", 60 * S)
    w0 = [x for x in evs if getattr(x, "window", None) is not None and x.window.value == "w0"]
    assert any(isinstance(x, NewLow) for x in w0)
    assert not any(isinstance(x, NewHigh) for x in w0)


def test_gap_reseed_rise_emits_only_new_high_not_phantom_low():
    e = Engine()  # legacy: w0=30s
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)
    evs = e.on_trade("AAA", 500.0, 1.0, "buy", 60 * S)
    w0 = [x for x in evs if getattr(x, "window", None) is not None and x.window.value == "w0"]
    assert any(isinstance(x, NewHigh) for x in w0)
    assert not any(isinstance(x, NewLow) for x in w0)


def test_gap_reseed_flat_retrade_emits_no_window_events():
    # Re-trading the exact same price after the window emptied is not a new
    # extreme in either direction (mirrors the STRICT >/< rule for live windows).
    e = Engine()  # legacy: w0=30s
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)
    evs = e.on_trade("AAA", 100.0, 1.0, "buy", 60 * S)
    w0 = [x for x in evs if getattr(x, "window", None) is not None and x.window.value == "w0"]
    assert w0 == []


def test_gap_reseed_does_not_inflate_counts():
    # The phantom event also bumped nh_count/nl_count and the per-window ticker
    # deques; after the fix a post-gap drop only counts on the low side for w0.
    e = Engine()
    e.on_trade("AAA", 500.0, 1.0, "buy", 0)
    e.on_trade("AAA", 100.0, 1.0, "sell", 60 * S)
    snap = e.snapshot()
    assert snap.breadth.nh_counts["30s"] == 0
    assert snap.breadth.nl_counts["30s"] == 1


# --- shared breadth clock: per-symbol clamped timestamps must not regress the
# breadth meters (VolumeMeter/RateMeter assume globally sorted input).

def test_breadth_clock_is_globally_monotone_across_symbols():
    # Interleaved symbols with a regressed timestamp: BBB's first trade arrives
    # stamped BEFORE AAA's latest. Feeding the raw (per-symbol-clamped) ts into
    # the shared VolumeMeter builds an out-of-order bucket that later gets
    # evicted together with its in-order blocker — BBB's recent sell volume
    # silently vanishes from the gauges.
    e = Engine()  # legacy breadth window: 30s
    e.on_trade("AAA", 100.0, 10.0, "buy", 0)            # baseline
    e.on_trade("AAA", 100.0, 900.0, "sell", 80 * S)     # sell bucket @80s
    e.on_trade("AAA", 100.0, 5.0, "buy", 100 * S)       # global clock -> 100s
    e.on_trade("BBB", 50.0, 15.0, "sell", 60 * S)       # regressed: clamps to 100s
    e.on_trade("AAA", 100.0, 5.0, "buy", 115 * S)       # 80s sell expires; BBB's must not
    # In-window at 115s (30s window): BBB sell 15 (clamped @100s) + AAA buys
    # 5 (@100s) and 5 (@115s) -> sell_pct = 15 / 25.
    assert abs(e.breadth.sell_pct() - 60.0) < 1e-9      # bug: 0.0 (sell evicted wholesale)
    # RateMeter contract: the tick meter must have seen non-decreasing stamps.
    stamps = list(e.breadth._tick_meter.timestamps)
    assert stamps == sorted(stamps)


# --- non-finite guard: NaN/inf records are dropped at the engine boundary.

def test_nan_price_is_rejected_and_does_not_corrupt_extremes():
    # NaN fails both comparisons in MonotonicExtreme, gets appended, and can
    # never be displaced — masking the true extreme and producing phantom
    # events with a NaN prev_extreme. The engine must drop it at the boundary.
    e = Engine()
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)
    assert e.on_trade("AAA", float("nan"), 1.0, "buy", 1 * S) == []
    assert e.rejected_ticks == 1
    evs = e.on_trade("AAA", 101.0, 1.0, "buy", 2 * S)
    nh_w0 = next(x for x in evs if isinstance(x, NewHigh) and x.window.value == "w0")
    assert nh_w0.prev_extreme == 100.0                  # not NaN, not masked
    evs = e.on_trade("AAA", 99.0, 1.0, "sell", 3 * S)
    nl_w0 = next(x for x in evs if isinstance(x, NewLow) and x.window.value == "w0")
    assert nl_w0.prev_extreme == 100.0


def test_non_finite_amount_and_inf_price_rejected_even_as_first_tick():
    e = Engine()
    assert e.on_trade("AAA", float("inf"), 1.0, "buy", 0) == []
    assert e.on_trade("AAA", 100.0, float("nan"), "buy", 0) == []
    assert e.rejected_ticks == 2
    assert e.quote("AAA") is None                       # no tape was created
    e.on_trade("AAA", 100.0, 1.0, "buy", 1 * S)         # first REAL tick = baseline
    assert e.quote("AAA") == (100.0, 0.0)


def test_per_window_counts_and_ticker():
    e = Engine()
    e.on_trade("AAA", 100.0, 1.0, "buy", 0)          # baseline
    e.on_trade("AAA", 101.0, 1.0, "buy", S)          # new highs across all windows
    e.on_trade("AAA", 102.0, 1.0, "buy", 2 * S)      # more new highs
    snap = e.snapshot()
    # every rolling window registered new-high activity for AAA
    assert snap.breadth.nh_counts["30s"] >= 2
    assert set(snap.breadth.nh_counts) == {"30s", "1m", "5m"}
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
