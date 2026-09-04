"""Native RiskManager entry-grace (grace-to-bot port, review H1).

The shipped s20 config documents entry_grace_bars=3, but grace lived only in
the accuracy harness (which monkeypatches check_exits). These tests pin the
native risk-layer implementation: identical arithmetic to the harness
`_grace_exempt` (entry bar = bar 0, TP never suppressed, stop-first precedence
preserved even while suppressed), grace=0 byte-compat, and runner plumbing.
"""
from __future__ import annotations

from msgspec import structs as ms_structs

from entropy.bot.config import BotConfig
from entropy.bot.orders import OrderIntent
from entropy.bot.portfolio import Portfolio, PositionSide
from entropy.bot.risk.manager import RiskManager
from entropy.bot.risk.profiles import get_profile
from entropy.bot.runner import BotRunner

_BAR_S = 900
_BAR_NS = _BAR_S * 1_000_000_000


def _manager(grace: int) -> RiskManager:
    return RiskManager(
        get_profile("medium"),
        entry_grace_bars=grace,
        bar_s=_BAR_S,
    )


def _open_long(portfolio: Portfolio, ts_ns: int = 0) -> None:
    portfolio.open("BTCUSDT", PositionSide.LONG, qty=1.0, entry_px=100.0,
                   stop_px=99.0, tp_px=101.0, ts_ns=ts_ns, fee=0.0)


def test_stop_suppressed_within_grace_then_fires_after():
    rm = _manager(grace=3)
    p = Portfolio(1_000.0)
    _open_long(p, ts_ns=0)
    p.mark("BTCUSDT", 98.0)  # far through the 99.0 stop

    # entry bar (bar 0) and the next two bars: suppressed
    for bar in range(3):
        assert rm.check_exits(p, bar * _BAR_NS + 1) == []
    # bar 3: grace expired, the stop fires at the tripping mark
    orders = rm.check_exits(p, 3 * _BAR_NS)
    assert len(orders) == 1 and orders[0].intent is OrderIntent.STOP


def test_entry_bar_counts_as_bar_zero():
    rm = _manager(grace=1)
    p = Portfolio(1_000.0)
    _open_long(p, ts_ns=0)
    p.mark("BTCUSDT", 98.0)
    assert rm.check_exits(p, 0) == []                                 # bar 0
    assert rm.check_exits(p, _BAR_NS)[0].intent is OrderIntent.STOP   # bar 1


def test_tp_never_suppressed_during_grace():
    rm = _manager(grace=3)
    p = Portfolio(1_000.0)
    _open_long(p, ts_ns=0)
    p.mark("BTCUSDT", 101.5)  # through the TP on the entry bar
    orders = rm.check_exits(p, 0)
    assert len(orders) == 1 and orders[0].intent is OrderIntent.TAKE_PROFIT


def test_grace_zero_is_off():
    rm = _manager(grace=0)
    p = Portfolio(1_000.0)
    _open_long(p, ts_ns=0)
    p.mark("BTCUSDT", 98.0)
    assert rm.check_exits(p, 0)[0].intent is OrderIntent.STOP


def test_missing_bar_s_disables_grace():
    rm = RiskManager(get_profile("medium"), entry_grace_bars=3, bar_s=0.0)
    p = Portfolio(1_000.0)
    _open_long(p, ts_ns=0)
    p.mark("BTCUSDT", 98.0)
    assert rm.check_exits(p, 0)[0].intent is OrderIntent.STOP


def test_short_side_mirror():
    rm = _manager(grace=2)
    p = Portfolio(1_000.0)
    p.open("ETHUSDT", PositionSide.SHORT, qty=1.0, entry_px=100.0,
           stop_px=101.0, tp_px=99.0, ts_ns=0, fee=0.0)
    p.mark("ETHUSDT", 102.0)  # through the short stop
    assert rm.check_exits(p, _BAR_NS) == []              # bar 1 < grace 2
    assert rm.check_exits(p, 2 * _BAR_NS)[0].intent is OrderIntent.STOP


def test_runner_plumbs_shipped_grace_and_hot_apply_updates_it(tmp_path):
    bot = BotRunner(BotConfig(), run_dir=str(tmp_path))
    assert bot.risk.entry_grace_bars == 3                # shipped default
    assert bot.risk._bar_ns == int(bot.config.bar_seconds() * 1_000_000_000)

    cfg2 = ms_structs.replace(
        bot.config,
        risk_overrides=ms_structs.replace(
            bot.config.risk_overrides, entry_grace_bars=0),
    )
    assert bot.apply_config(cfg2) == []
    assert bot.risk.entry_grace_bars == 0
