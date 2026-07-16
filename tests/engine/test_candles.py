from entropy.engine.candles import CandleAggregator

S = 1_000_000_000

def test_aggregates_trades_into_bars():
    agg = CandleAggregator(interval_ns=60 * S, maxlen=10)
    agg.add(0, 100.0, 1.0)
    agg.add(10 * S, 105.0, 2.0)
    agg.add(30 * S, 98.0, 1.0)
    agg.add(65 * S, 101.0, 1.0)         # new bucket
    bars = agg.bars()
    assert len(bars) == 2
    o, h, low, c = bars[0].o, bars[0].h, bars[0].l, bars[0].c
    assert (o, h, low, c) == (100.0, 105.0, 98.0, 98.0)
    assert bars[0].vol == 4.0


def test_timestamp_regression_clamps_into_current_bar():
    # A regressed tick (bucket sequence 10, 11, 10, 11) must NOT open duplicate
    # backwards bars: the aggregator only rolls forward; late ticks fold into
    # the current bar's h/l/c/vol.
    agg = CandleAggregator(interval_ns=60 * S, maxlen=10)
    agg.add(600 * S, 100.0, 1.0)   # bucket 10
    agg.add(660 * S, 105.0, 1.0)   # bucket 11 -> new bar
    agg.add(610 * S, 90.0, 2.0)    # bucket 10 again: REGRESSION -> clamp into bar 11
    agg.add(665 * S, 110.0, 1.0)   # bucket 11 again: still the same bar
    bars = agg.bars()
    assert len(bars) == 2          # bug: 4 bars (duplicate/backwards)
    assert [b.t for b in bars] == [600 * S, 660 * S]
    b = bars[1]
    assert (b.o, b.h, b.l, b.c) == (105.0, 110.0, 90.0, 110.0)
    assert b.vol == 4.0
