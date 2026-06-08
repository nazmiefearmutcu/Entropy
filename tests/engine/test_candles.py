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
    o, h, l, c = bars[0].o, bars[0].h, bars[0].l, bars[0].c
    assert (o, h, l, c) == (100.0, 105.0, 98.0, 98.0)
    assert bars[0].vol == 4.0
