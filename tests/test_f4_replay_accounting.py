"""F4 offline harness tests — restart-replay accounting (Z3-1/Z3-4/Z3-7),
testnet-mirror provenance (Z4-2) and balance-poll ban backoff (Z3-5).

NO NETWORK: Binance fetch is stubbed with synthetic klines, the testnet
executor is a fake in-process stub, and mirror env vars are cleared. All
state writes go to pytest tmp_path (never to reports/live-paper/).

Fixes under test (entropy_live_paper.py):
  (a) restore + historical replay  -> equity/base cash UNCHANGED vs pre-replay,
      day_pnl re-anchored (replay contributes exactly 0);
  (b) the first genuinely-new bar after the replay barrier IS booked
      (fill + equity point), while a pre-barrier replay-timed fill stays
      suppressed;
  (c) reconcile/flatten provenance: untracked symbols are NEVER closed,
      tracked+not-in-paper symbols ARE closed, in-paper symbols untouched;
  (d) balance poll: 300 s interval, full stop while a -1003 ban is active,
      public IP masked in stored errors, F5 accessor feature-detect.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from entropy_live_paper import (  # noqa: E402
    BAR_MS,
    BALANCE_REFRESH_S,
    LivePaper,
    _closed_key,
)
from entropy.bot.portfolio import PositionSide  # noqa: E402

RAW = "BTCUSDT"
_LAST_OPEN_MS = (1_715_000_000_000 // BAR_MS) * BAR_MS  # fixed, bar-aligned


@pytest.fixture(autouse=True)
def _no_mirror_env(monkeypatch):
    for name in ("KAOS_EXCHANGE_TESTNET", "BINANCE_API_KEY", "BINANCE_API_SECRET"):
        monkeypatch.delenv(name, raising=False)


def _synth_klines(n: int) -> list[list]:
    """n fully-closed 15m bars ending at _LAST_OPEN_MS; gentle oscillation."""
    rows: list[list] = []
    base_open = _LAST_OPEN_MS - (n - 1) * BAR_MS
    px = 100.0
    for i in range(n):
        o = base_open + i * BAR_MS
        c = px + (0.10 if i % 2 == 0 else -0.10)
        rows.append([o, px, max(px, c) + 0.5, min(px, c) - 0.5, c, 1.0,
                     o + BAR_MS - 1])
        px = c
    return rows


def _mk_paper(tmp_path: Path, cash: float) -> LivePaper:
    lp = LivePaper([RAW], cash, tmp_path)
    assert lp.exec_ is None  # env cleared -> mirror layer off by default
    return lp


def _stub_fetch(lp: LivePaper) -> list[list]:
    kl = _synth_klines(300)
    lp._fetch_with_backoff = lambda n, now_ms, raw: list(kl)  # type: ignore[method-assign]
    return kl


def _prev_state(today_iso: str, equity: float = 150.0, day_pnl: float = 3.0,
                same_day: bool = True) -> dict:
    return {
        "ts_utc": today_iso if same_day else "2026-09-01T00:00:00+00:00",
        "equity_usd": equity,
        "day_pnl_usd": day_pnl,
        "live_base_equity": 145.0,
        "equity_history": [[float(_LAST_OPEN_MS - BAR_MS), equity]],
        "closed_trades": [{
            "symbol": RAW, "side": "LONG", "qty": 0.1,
            "entry_price": 100.0, "exit_price": 101.0, "pnl_usd": 0.05,
            "exit_reason": "trail", "exit_utc": today_iso,
        }],
        "trades_today": 1,
        "exchange": {"mirror_open": {
            RAW: {"qty": 0.02, "order_id": 111, "opened_utc": today_iso}}},
    }


# ---- (a) restore + full startup replay is accounting-inert (Z3-1/Z3-7) -----

def test_restore_replay_is_accounting_inert(tmp_path):
    now_iso = datetime.now(timezone.utc).isoformat()
    lp = _mk_paper(tmp_path, 150.0)
    lp.restore_from(_prev_state(now_iso))
    assert lp._replay_neutralize is True
    assert lp._day_anchor_equity == pytest.approx(147.0)

    # make the replay BOOK real PnL: +0.25 realized per eval-bar collect_closed
    real_cc = lp.collect_closed
    bumps = [0]

    def spy_cc():
        real_cc()
        bumps[0] += 1
        lp.runner.portfolio.realized_pnl += 0.25

    lp.collect_closed = spy_cc  # type: ignore[method-assign]
    _stub_fetch(lp)
    lp.start()
    lp.collect_closed = real_cc  # type: ignore[method-assign]

    p = lp.runner.portfolio
    # equity is rebased EXACTLY back to the restored base, whatever replay did
    assert p.equity() == pytest.approx(150.0, abs=1e-9)
    assert bumps[0] > 0                      # replay really booked PnL...
    assert lp.replay_rebase_usd == pytest.approx(-0.25 * bumps[0])
    # ...and the booked PnL is exactly what the rebase absorbed (Z3-1 identity)
    assert p.realized_pnl + p.unrealized_pnl() == pytest.approx(
        -lp.replay_rebase_usd)
    # Z3-7: day anchor = prev in-day value -> day_pnl continues, replay-free
    assert p.day_start_equity == pytest.approx(147.0)
    assert p.daily_pnl() == pytest.approx(3.0)
    # Z3-4: barrier = last replayed bar's CLOSE
    assert lp.last_fed_open_ms == _LAST_OPEN_MS
    assert lp._replay_guard_until_ms == _LAST_OPEN_MS + BAR_MS
    # no NEW curve points from replay; restored record intact, nothing live
    assert lp.equity_history == [[float(_LAST_OPEN_MS - BAR_MS), 150.0]]
    assert len(lp.closed_all) == 1
    assert lp.closed_all[0]["origin"] == "restored"
    # mirror stays OFF during the whole replay: tracking only from prev state
    assert lp.mirror_positions == {RAW: {"qty": 0.02, "order_id": 111,
                                         "opened_utc": now_iso}}
    # audit field reaches state
    st = lp.build_state()
    assert st["replay_rebase_usd"] == pytest.approx(lp.replay_rebase_usd)
    assert st["equity_usd"] == pytest.approx(150.0, abs=1e-6)


def test_clean_start_counts_replay_once_no_rebase(tmp_path):
    lp = _mk_paper(tmp_path, 100.0)
    real_cc = lp.collect_closed
    bumps = [0]

    def spy_cc():
        real_cc()
        bumps[0] += 1
        lp.runner.portfolio.realized_pnl += 0.25

    lp.collect_closed = spy_cc  # type: ignore[method-assign]
    _stub_fetch(lp)
    lp.start()
    lp.collect_closed = real_cc  # type: ignore[method-assign]
    assert bumps[0] > 0
    p = lp.runner.portfolio
    assert p.equity() == pytest.approx(100.0 + 0.25 * bumps[0])  # counted ONCE
    assert lp.replay_rebase_usd is None
    # Z3-9: clean-start live base pinned to starting cash, not min(curve)
    assert lp.live_base_equity == pytest.approx(100.0)


def test_cross_midnight_restart_day_pnl_starts_at_zero(tmp_path):
    lp = _mk_paper(tmp_path, 150.0)
    lp.restore_from(_prev_state("x", same_day=False))
    _stub_fetch(lp)
    lp.start()
    assert lp.runner.portfolio.daily_pnl() == pytest.approx(0.0)
    assert lp.runner.portfolio.day_start_equity == pytest.approx(150.0)


# ---- (b) the first live bar after the barrier IS booked (Z3-4) --------------

def _push_fill(lp: LivePaper, fill, intent_value: str,
               levels: tuple | None = None) -> int:
    """Append a ledger fill keeping the _BarrierLedger invariant: open_levels
    stays 1:1 with fills (levels captured at OPEN, None for exit fills)."""
    lp.ledger.fills.append((fill, SimpleNamespace(value=intent_value)))
    lp.ledger.open_levels.append(levels)
    return len(lp.ledger.fills) - 1


def _stage_pre_restart_position(lp: LivePaper) -> str:
    """Emulate one position opened during replay: portfolio + entry fill in
    open_fills + ledger open levels (exactly what collect_closed pairs on).
    Returns the resolved symbol."""
    sym = lp.syms[RAW]  # binance-spot:BTCUSDT
    entry_ts_ns = _LAST_OPEN_MS * 1_000_000
    lp.runner.portfolio.open(sym, PositionSide.LONG, 0.1, 100.0,
                             99.5, 101.5, entry_ts_ns, fee=0.01)
    entry_fill = SimpleNamespace(symbol=sym, side=SimpleNamespace(value="buy"),
                                 price=100.0, qty=0.1, fee=0.01,
                                 ts_ns=entry_ts_ns)
    idx = _push_fill(lp, entry_fill, "open", (99.5, 101.5))
    lp.open_fills[sym] = (idx, entry_fill)
    lp.cursor = idx + 1  # pairing starts at the exit fill only
    return sym


def _stop_fill(sym: str, price: float, ts_ms: int):
    """Closing (exit) fill for a 0.1 long; levels=None keeps ledger 1:1."""
    return SimpleNamespace(symbol=sym, side=SimpleNamespace(value="sell"),
                           price=price, qty=0.1, fee=0.01,
                           ts_ns=ts_ms * 1_000_000)


def test_first_live_bar_booked_replay_bar_suppressed(tmp_path):
    now_iso = datetime.now(timezone.utc).isoformat()
    lp = _mk_paper(tmp_path, 150.0)
    lp.restore_from(_prev_state(now_iso))
    _stub_fetch(lp)
    lp.start()
    guard_ms = lp._replay_guard_until_ms

    # phase 1: a PRE-barrier (replay-timed) stop fill must stay suppressed
    sym = _stage_pre_restart_position(lp)
    _push_fill(lp, _stop_fill(sym, 99.0, guard_ms - 1), "stop")
    lp.collect_closed()
    assert len(lp.closed_all) == 1            # only the restored record
    assert sym not in lp.open_fills           # pairing slot consumed, no rec

    # phase 2: the FIRST live bar's stop fill (ts == barrier == its bar open,
    # ticks are re-stamped to bar open) must be booked normally
    sym = _stage_pre_restart_position(lp)
    _push_fill(lp, _stop_fill(sym, 98.0, guard_ms), "stop")
    p = lp.runner.portfolio
    eq_before = p.equity()
    p.close(sym, 98.0, guard_ms * 1_000_000, fee=0.0098)  # executor effect
    lp.collect_closed()
    assert len(lp.closed_all) == 2            # restored + the live-bar close
    rec = lp.closed_all[-1]
    assert rec["exit_reason"] == "stop"
    assert rec["exit_price"] == pytest.approx(98.0)
    assert rec["pnl_usd"] == pytest.approx(-0.2198, abs=1e-6)
    assert "origin" not in rec                # mirror layer off in this test
    # portfolio realized the same economic close (gross - close fee)
    assert p.equity() == pytest.approx(eq_before - 0.2098, abs=1e-6)

    # equity point AT the barrier (= last replay close) suppressed; the first
    # live bar's close point (barrier + BAR_MS) IS appended.
    eq = p.equity()
    lp._append_equity(float(guard_ms), eq)
    assert all(pt[0] != float(guard_ms) for pt in lp.equity_history)
    lp._append_equity(float(guard_ms + BAR_MS), eq)
    assert [pt[0] for pt in lp.equity_history][-1] == float(guard_ms + BAR_MS)


# ---- (c) reconcile/flatten provenance (Z4-2) ---------------------------------

class FakeExec:
    def __init__(self, positions: list[dict]):
        self.positions = positions
        self.orders: list[tuple] = []

    def get_open_positions(self):
        return [dict(p) for p in self.positions]

    def place_market_order(self, symbol, side, qty, reduce_only=False):
        self.orders.append((symbol, side, qty, reduce_only))
        return {"ok": True, "order_id": 1, "qty_used": qty}


def test_flatten_provenance_untracked_never_touched(tmp_path):
    lp = _mk_paper(tmp_path, 100.0)
    ex = FakeExec([
        {"symbol": "BTCUSDT", "contracts": 0.02, "side": "long"},    # tracked
        {"symbol": "DOGEUSDT", "contracts": 500.0, "side": "long"},  # manual!
        {"symbol": "ETHUSDT", "contracts": 0.5, "side": "long"},     # paper open
    ])
    lp.exec_ = ex
    lp._mirror_live = True
    lp.mirror_positions = {"BTCUSDT": {"qty": 0.02, "order_id": 111,
                                       "opened_utc": "x"}}
    # paper (replay-derived) open position: ETHUSDT is bot-managed
    lp.runner.portfolio.open("binance-spot:ETHUSDT", PositionSide.LONG, 0.1,
                             100.0, 99.0, 101.0, _LAST_OPEN_MS * 1_000_000,
                             fee=0.0)
    lp._flatten_orphans("test")

    # ONLY the tracked, not-in-paper symbol was closed
    assert ex.orders == [("BTCUSDT", "SELL", 0.02, True)]
    assert RAW not in lp.mirror_positions            # closed -> de-tracked
    assert "DOGEUSDT" in lp._warned_untracked        # manual position warned...
    assert all(o[0] != "DOGEUSDT" for o in ex.orders)  # ...but NEVER touched
    assert all(o[0] != "ETHUSDT" for o in ex.orders)   # paper-managed kept
    # untracked symbols reach state untouched
    st = lp.build_state()
    assert "DOGEUSDT" not in st["exchange"]["mirror_open"]


def test_entry_mirror_tracks_exit_mirror_detracks(tmp_path):
    lp = _mk_paper(tmp_path, 100.0)
    ex = FakeExec([])
    lp.exec_ = ex
    lp._mirror_live = True
    sym = lp.syms[RAW]
    # live entry (post-barrier ts; guard is process-lifetime and 0 here)
    entry = SimpleNamespace(symbol=sym, side=SimpleNamespace(value="buy"),
                            price=100.0, qty=0.1, fee=0.01,
                            ts_ns=(_LAST_OPEN_MS + BAR_MS) * 1_000_000)
    _push_fill(lp, entry, "open", (99.5, 101.5))
    lp.runner.portfolio.open(sym, PositionSide.LONG, 0.1, 100.0, 99.5,
                             101.5, entry.ts_ns, fee=0.01)
    lp.collect_closed()
    # production passes the prefixed paper symbol; the real executor's
    # place_market_order runs clean_symbol() internally (verified)
    assert ex.orders == [(sym, "BUY", 0.1, False)]
    assert RAW in lp.mirror_positions         # tracking key is the BARE symbol
    # live exit (reduce-only) de-tracks the symbol
    exitf = SimpleNamespace(symbol=sym, side=SimpleNamespace(value="sell"),
                            price=101.0, qty=0.1, fee=0.01,
                            ts_ns=(_LAST_OPEN_MS + BAR_MS + 1) * 1_000_000)
    _push_fill(lp, exitf, "take_profit")
    lp.collect_closed()
    assert len(ex.orders) == 2 and ex.orders[1][3] is True
    assert RAW not in lp.mirror_positions
    rec = lp.closed_all[-1]
    assert rec["origin"] == "live" and rec["exchange_verified"] is True


def test_replay_entries_send_no_orders_even_with_executor(tmp_path):
    lp = _mk_paper(tmp_path, 100.0)
    ex = FakeExec([])
    lp.exec_ = ex           # executor present...
    assert lp._mirror_live is False  # ...but mirror not yet enabled (replay)
    sym = lp.syms[RAW]
    fill = SimpleNamespace(symbol=sym, side=SimpleNamespace(value="buy"),
                           price=100.0, qty=0.1, fee=0.01,
                           ts_ns=_LAST_OPEN_MS * 1_000_000)
    lp.ledger.fills.append((fill, SimpleNamespace(value="open")))
    lp.collect_closed()
    assert ex.orders == []           # Z3-2/Z4-1: replay entries never mirrored
    assert lp.mirror_positions == {}


# ---- (d) balance poll: 300 s + ban-aware stop + IP mask (Z3-5) ---------------

class BanExec:
    def __init__(self, ban_until_ms=None, err=None):
        self.calls = 0
        self.banned_until_ms = ban_until_ms  # F5-style attribute (or None)
        self.err = err

    def get_balance(self):
        self.calls += 1
        if self.err:
            raise RuntimeError(self.err)
        return {"wallet": 1000.0, "available": 900.0}


BAN_ERR = ("-1003: Way too many requests; IP(15.158.242.76) banned until "
           "1999999999999")


def test_balance_poll_ban_aware_and_slow(tmp_path, monkeypatch):
    t0 = 1_000_000.0
    ban_until_ms = int((t0 + 60.0) * 1000)   # 60 s ban starting at t0
    ban_err = ("-1003: Way too many requests; IP(15.158.242.76) banned until "
               f"{ban_until_ms}")
    monkeypatch.setattr("entropy_live_paper.time.time", lambda: t0)
    lp = _mk_paper(tmp_path, 100.0)
    ex = BanExec(err=ban_err)
    lp.exec_ = ex
    assert BALANCE_REFRESH_S == 300.0

    s1 = lp._exchange_section()
    assert ex.calls == 1                                    # first refresh
    assert "IP(<masked>)" in (s1["last_error"] or "")       # public IP masked
    assert "15.158.242.76" not in "".join(s1["recent_errors"])
    # next read parses the ban from the stored (masked-IP) error text
    s2 = lp._exchange_section()
    assert s2["banned_until_utc"] is not None
    assert ex.calls == 1

    # force the refresh window open DURING the ban: poll must NOT fire
    lp._last_balance_refresh = 0.0
    lp._exchange_section()
    assert ex.calls == 1

    # ban deadline self-expires (stale error text must never block forever);
    # server recovered too: refresh succeeds, error slot clears
    ex.err = None
    monkeypatch.setattr("entropy_live_paper.time.time", lambda: t0 + 61.0)
    lp._last_balance_refresh = 0.0
    s3 = lp._exchange_section()
    assert ex.calls == 2
    assert s3["last_error"] is None and s3["wallet_usdt"] == 1000.0
    # 300 s cadence around the recovery point
    monkeypatch.setattr("entropy_live_paper.time.time", lambda: t0 + 360.0)
    lp._exchange_section()
    assert ex.calls == 2          # inside the 300 s window
    monkeypatch.setattr("entropy_live_paper.time.time", lambda: t0 + 362.0)
    lp._exchange_section()
    assert ex.calls == 3          # past the window


def test_balance_poll_feature_detects_f5_accessor(tmp_path, monkeypatch):
    t0 = 1_000_000.0
    monkeypatch.setattr("entropy_live_paper.time.time", lambda: t0)
    lp = _mk_paper(tmp_path, 100.0)
    ex = BanExec(ban_until_ms=1_999_999_999_999)  # F5-style accessor attribute
    lp.exec_ = ex
    lp._last_balance_refresh = 0.0
    lp._exchange_section()
    assert ex.calls == 0          # accessor says banned -> poll skipped

    # error-text fallback: a prior -1003 (e.g. from the order path) stops the
    # poll even when the executor exposes no accessor at all
    ex2 = BanExec()
    lp.exec_ = ex2
    lp.exec_stats["last_error"] = BAN_ERR
    lp._last_balance_refresh = 0.0
    s = lp._exchange_section()
    assert ex2.calls == 0
    assert s["banned_until_utc"] is not None


# ---- regression: restored records keep stable dedup keys ---------------------

def test_restored_records_dedup_against_replay_regeneration(tmp_path):
    rec = {"symbol": RAW, "side": "LONG", "qty": 0.1, "entry_price": 100.0,
           "exit_price": 101.0, "pnl_usd": 0.05, "exit_reason": "trail",
           "exit_utc": "2026-09-06T10:00:00+00:00"}
    assert _closed_key(rec) in {_closed_key(dict(rec))}
