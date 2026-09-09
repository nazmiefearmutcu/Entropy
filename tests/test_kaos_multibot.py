"""Offline tests for the KAOS MULTIBOT supervisor (scripts/kaos_multibot.py).

Zero network, zero live launches: the parent is exercised through injectable
spawn seams and stub children; the NASDAQ lane runs against fake clocks,
fake bar fetchers and fake adapters; the crypto lane's CONFIG contract is
pinned against the standalone live runner without ever constructing the real
LivePaper (a recording fake is injected into the importlib-loaded module).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import kaos_multibot as kb  # noqa: E402

BAR_MS = 15 * 60 * 1000
HIST_START = (1_700_000_000_000 // BAR_MS) * BAR_MS


def make_klines(start_open_ms: int, n: int, base: float = 100.0) -> list:
    """Ascending BINE-shaped kline rows (the adapter's output contract)."""
    rows = []
    for i in range(n):
        o = start_open_ms + i * BAR_MS
        c = base + 0.1 * i
        rows.append([o, c - 0.5, c + 0.5, c - 1.0, c, 1000.0,
                     o + BAR_MS - 1, c * 1000.0, 0, 0.0, 0.0, "0.0"])
    return rows


class FakeFetcher:
    """Bars source honoring end_ms (closed bars only, ascending)."""

    def __init__(self, hist: list) -> None:
        self.hist = hist
        self.calls: list[tuple] = []

    def __call__(self, raw: str, timeframe: str, limit: int,
                 end_ms: int | None) -> list:
        self.calls.append((raw, timeframe, int(limit), end_ms))
        rows = [k for k in self.hist
                if not end_ms or int(k[0]) + BAR_MS <= int(end_ms)]
        return [list(k) for k in rows[-int(limit):]]


class FakeClock:
    """USEquitiesClock stand-in with a fixed answer (never touches the
    calendar)."""

    def __init__(self, open_now: bool = True, label: str = "RTH") -> None:
        self.open_now = open_now
        self.label = label

    def is_open(self, now_ms: int) -> bool:
        return self.open_now

    def session_label(self, now_ms: int) -> str:
        return self.label


class FakeAdapter:
    """AlpacaEquitiesAdapter stand-in: records calls, canned answers."""

    venue_id = "alpaca-nasdaq"

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.host = "https://paper-api.alpaca.markets"
        self.is_live = False
        self.connect_note = "paper"
        self.pdt_counter = None

    @property
    def available(self) -> bool:
        return True

    def place_market_order(self, symbol: str, side: str, qty: float,
                           reduce_only: bool = False) -> dict:
        self.calls.append(("market", symbol, side, qty, reduce_only))
        return {"ok": True, "order_id": "oid1", "qty_used": float(qty)}

    def place_venue_stop(self, sym: str, pos_side: str, px: float) -> str:
        self.calls.append(("stop", sym, pos_side, px))
        return "stop1"

    def place_venue_tp(self, sym: str, pos_side: str, qty: float,
                       px: float) -> str:
        self.calls.append(("tp", sym, pos_side, qty, px))
        return "tp1"

    def cancel_venue_stop(self, sym: str) -> bool:
        self.calls.append(("cancel_stop", sym))
        return True

    def cancel_venue_tp(self, sym: str) -> bool:
        self.calls.append(("cancel_tp", sym))
        return True

    def adopt_open_stop(self, sym: str, pos_side: str = "long") -> str | None:
        return None

    def adopt_open_tp(self, sym: str, pos_side: str) -> str | None:
        return None

    def algo_order_alive(self, sym: str, algoid: str) -> bool:
        return False

    def order_alive(self, sym: str, order_id: str) -> bool:
        return False

    def get_open_positions(self) -> list:
        self.calls.append(("positions",))
        return []


class StubProc:
    """Popen stand-in for spawn-injection tests."""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        pass

    def kill(self):
        pass


class Transport:
    """urllib transport stand-in for build_equity_executor tests."""

    def __init__(self, status: int = 0, payload: dict | None = None) -> None:
        self.status = status
        self.payload = payload or {}
        self.calls: list[str] = []

    def request(self, method, url, headers=None, body=None):
        self.calls.append(f"{method} {url}")
        return self.status, dict(self.payload)


class WeekdayCalendar:
    """Minimal crocodile USMarketCalendar stand-in (PdtCounter only needs
    is_trading_day)."""

    def is_trading_day(self, d) -> bool:
        return d.weekday() < 5


def make_venue(tmp_path: Path, clock: FakeClock, fetcher: FakeFetcher,
               **kwargs) -> kb.EquityVenue:
    kwargs.setdefault("universe", ("AAPL", "MSFT"))
    kwargs.setdefault("cash", 25000.0)
    kwargs.setdefault("out_dir", tmp_path)
    kwargs.setdefault("state_path", tmp_path / "state-nasdaq.json")
    return kb.EquityVenue(clock=clock, fetch_bars=fetcher, **kwargs)


# ============================================================================
# Parent: env / backoff / pause / aggregate
# ============================================================================

def test_build_child_env_carries_venue_and_full_kaos_envs():
    base = {"KAOS_EXCHANGE_NETWORK": "mainnet", "KAOS_EXCHANGE_LEVERAGE": "10",
            "KAOS_EXCHANGE_SIZING_PCT": "30", "KAOS_EXCHANGE_SLOTS": "3",
            "BINANCE_API_KEY": "k", "BINANCE_API_SECRET": "s",
            "PATH": "whatever"}
    env = kb.build_child_env(base, "crypto")
    # the run-kaos-mainnet env contract flows through UNCHANGED
    for k, v in base.items():
        assert env[k] == v
    assert env["KAOS_VENUE"] == "crypto"
    assert kb.build_child_env({}, "nasdaq")["KAOS_VENUE"] == "nasdaq"
    # base dict not mutated
    assert "KAOS_VENUE" not in base


def test_restart_backoff_math_and_crash_loop_escalation():
    # 2 -> 4 -> 8 ... capped at 600
    assert kb.next_restart_delay_s(0) == 2.0
    assert kb.next_restart_delay_s(1) == 4.0
    assert kb.next_restart_delay_s(2) == 8.0
    assert kb.next_restart_delay_s(8) == 512.0
    assert kb.next_restart_delay_s(9) == kb.RESTART_BACKOFF_MAX_S   # 1024 > cap
    assert kb.next_restart_delay_s(16) == kb.RESTART_BACKOFF_MAX_S
    assert kb.next_restart_delay_s(99) == kb.RESTART_BACKOFF_MAX_S
    # a child dying younger than CRASH_STABLE_S escalates; a stable run resets
    assert kb.update_crash_streak(0, 10.0) == 1
    assert kb.update_crash_streak(3, 299.9) == 4
    assert kb.update_crash_streak(5, kb.CRASH_STABLE_S) == 0


def test_spawn_env_reaches_stub_child(tmp_path):
    """The parent's spawn env must carry KAOS_VENUE into the real child
    process (verified by the stub's exit code)."""
    stub = ("import os,sys;"
            "sys.exit(0 if os.environ.get('KAOS_VENUE')=='crypto' else 3)")
    sup = kb.Supervisor(venues=("crypto",), out_dir=tmp_path, ops_dir=tmp_path,
                        agg_path=tmp_path / "agg.json",
                        child_cmd=lambda v: [sys.executable, "-c", stub])
    sup.run_cycle(now_mono=1000.0)
    proc = sup.meta["crypto"]["proc"]
    assert proc is not None
    assert proc.wait(timeout=30) is not None
    sup.run_cycle(now_mono=1001.0)
    assert sup.meta["crypto"]["exit_code"] == 0   # env verified by the stub


def test_crash_restart_with_escalating_backoff(tmp_path):
    stub = "import sys;sys.exit(3)"
    sup = kb.Supervisor(venues=("crypto",), out_dir=tmp_path, ops_dir=tmp_path,
                        agg_path=tmp_path / "agg.json",
                        child_cmd=lambda v: [sys.executable, "-c", stub])
    sup.run_cycle(now_mono=1000.0)                      # spawn #1
    sup.meta["crypto"]["proc"].wait(timeout=30)
    agg = sup.run_cycle(now_mono=1001.0)                # reap: crash #1
    m = sup.meta["crypto"]
    assert m["exit_code"] == 3 and m["crashes"] == 1 and m["proc"] is None
    assert agg["venues"]["crypto"]["backoff_remaining_s"] == pytest.approx(4.0)
    # hold backoff artificially expired to drive the loop deterministically
    for i, now in enumerate((1001.0, 1002.0)):
        m["backoff_until_mono"] = now
        sup.run_cycle(now_mono=now)                     # respawn #i+2
        m["proc"].wait(timeout=30)
        sup.run_cycle(now_mono=now + 1.0)               # reap
    assert m["crashes"] == 3
    delay = sup.run_cycle(now_mono=1003.0)["venues"]["crypto"][
        "backoff_remaining_s"]
    assert delay == pytest.approx(kb.next_restart_delay_s(3))   # 16s: escalated


def test_pause_file_suppresses_spawn(tmp_path):
    spawned: list[tuple] = []

    def fake_spawn(cmd, cwd=None, env=None):
        spawned.append((cmd, env))
        return StubProc()

    sup = kb.Supervisor(venues=("crypto", "nasdaq"), out_dir=tmp_path,
                        ops_dir=tmp_path, agg_path=tmp_path / "agg.json",
                        child_cmd=lambda v: ["stub", v], spawn=fake_spawn)
    # BOTH venue pause files exist -> nothing spawns, modes say paused
    for v in ("crypto", "nasdaq"):
        kb.pause_file_path(tmp_path, v).write_text("maintenance",encoding="utf-8")
    agg = sup.run_cycle(now_mono=1.0)
    assert spawned == []
    assert all(m["paused"] for m in sup.meta.values())
    assert agg["venues"]["crypto"]["mode"] == "paused"
    # release the crypto venue -> it spawns immediately with the venue env
    kb.pause_file_path(tmp_path, "crypto").unlink()
    sup.run_cycle(now_mono=2.0)
    assert len(spawned) == 1
    cmd, env = spawned[0]
    assert cmd == ["stub", "crypto"]
    assert env["KAOS_VENUE"] == "crypto"
    assert sup.meta["crypto"]["paused"] is False
    assert sup.meta["nasdaq"]["paused"] is True


def test_aggregate_schema_and_atomic_write(tmp_path):
    now_iso = datetime.now(timezone.utc).isoformat()
    (tmp_path / "state-crypto.json").write_text(json.dumps(
        {"ts_utc": now_iso, "mode": "PAPER_LIVE", "equity_usd": 101.5,
         "positions": [{"symbol": "BTCUSDT"}]}), encoding="utf-8")
    spawned: list[tuple] = []

    def fake_spawn(cmd, cwd=None, env=None):
        spawned.append(cmd)
        return StubProc(pid=1111)

    agg_path = tmp_path / "state-multibot.json"
    sup = kb.Supervisor(venues=("crypto", "nasdaq"), out_dir=tmp_path,
                        ops_dir=tmp_path, agg_path=agg_path,
                        child_cmd=lambda v: ["stub", v], spawn=fake_spawn)
    agg = sup.run_cycle(now_mono=1.0)
    assert agg["ts_utc"] and isinstance(agg["supervisor_pid"], int)
    crypto = agg["venues"]["crypto"]
    for key in ("pid", "running", "state_age_s", "equity", "open_positions",
                "mode", "paused", "crashes", "restarts", "exit_code",
                "backoff_remaining_s"):
        assert key in crypto
    assert crypto["equity"] == 101.5
    assert crypto["open_positions"] == 1
    assert crypto["mode"] == "PAPER_LIVE"
    assert crypto["state_age_s"] is not None and crypto["state_age_s"] >= 0
    # nasdaq has no state file: None-safe entry, honest mode
    nasdaq = agg["venues"]["nasdaq"]
    assert nasdaq["equity"] is None and nasdaq["mode"] in ("starting", "down")
    # on-disk file matches + atomic writer leaves no tmp droppings
    on_disk = json.loads(agg_path.read_text(encoding="utf-8"))
    assert on_disk["venues"]["crypto"]["equity"] == 101.5
    assert not list(tmp_path.glob("state-multibot.json.*.tmp"))
    # corrupt child state must never break the aggregate
    (tmp_path / "state-crypto.json").write_text("{broken", encoding="utf-8")
    assert kb.read_child_summary(tmp_path / "state-crypto.json") is None
    agg2 = sup.run_cycle(now_mono=2.0)
    assert agg2["venues"]["crypto"]["equity"] is None


# ============================================================================
# Crypto child: byte-identical standalone-config contract (no live launch)
# ============================================================================

def test_crypto_launch_resolution_matches_standalone(tmp_path):
    mod = kb._load_live_paper()
    launch = kb.resolve_crypto_launch(mod, tmp_path)
    expected_raws = [s.strip().upper()
                     for s in str(mod.DEFAULT_SYMBOLS).split(",") if s.strip()]
    assert launch["raws"] == expected_raws
    assert len(launch["raws"]) == 20
    assert launch["base_cash"] == 100.0
    assert launch["prev"] is None and launch["seeded_from"] is None
    # first boot seeds READ-ONLY from the LIVE state file
    (tmp_path / "state.json").write_text(json.dumps(
        {"equity_usd": 123.45, "positions": []}), encoding="utf-8")
    launch2 = kb.resolve_crypto_launch(mod, tmp_path)
    assert launch2["base_cash"] == 123.45
    assert launch2["prev"]["equity_usd"] == 123.45
    assert launch2["seeded_from"].endswith("state.json")
    # once state-crypto.json exists, it wins (live file no longer consulted)
    (tmp_path / "state-crypto.json").write_text(json.dumps(
        {"equity_usd": 55.0}), encoding="utf-8")
    launch3 = kb.resolve_crypto_launch(mod, tmp_path)
    assert launch3["base_cash"] == 55.0 and launch3["seeded_from"] is None


def test_crypto_child_config_object_equals_standalone_build_cfg(tmp_path):
    """Pin the standalone live config knobs — the child passes
    (raws, cash, out_dir) VERBATIM into LivePaper, so pinning build_cfg pins
    the child's crypto behavior (any drift in the live runner's knobs must
    surface here)."""
    mod = kb._load_live_paper()
    raws = [s.strip().upper()
            for s in str(mod.DEFAULT_SYMBOLS).split(",") if s.strip()]
    cfg = mod.build_cfg(raws, 100.0, tmp_path)
    assert cfg.symbols == tuple(f"binance-spot:{r}" for r in raws)
    assert cfg.timeframe == "15m" and cfg.bar_s == 900.0
    assert cfg.consensus.threshold == 0.5
    assert cfg.consensus.long_only is False          # LONG+SHORT (user order)
    assert cfg.consensus.confirm_bars == 2
    assert cfg.consensus.max_hold_bars == 192
    ro = cfg.risk_overrides
    assert ro.per_trade_pct == 10.0 and ro.max_concurrent == 4
    assert ro.max_total_exposure_pct == 40.0
    assert ro.max_daily_loss_pct == 15.0
    assert ro.stop_mode == "sigma"
    assert ro.stop_sigma_mult == 20.0 and ro.tp_sigma_mult == 4.0
    assert ro.entry_grace_bars == 3


def test_crypto_child_uses_crypto_state_file_and_standalone_args(
        tmp_path, monkeypatch):
    mod = kb._load_live_paper()
    recorded: dict = {}

    class FakeLP:
        def __init__(self, raws, cash, out_dir):
            recorded["raws"] = list(raws)
            recorded["cash"] = cash
            recorded["out_dir"] = Path(out_dir)
            self.state_path = Path(out_dir) / "state.json"   # child overrides
            self.closed_all: list = []
            self.runner = SimpleNamespace(portfolio=SimpleNamespace(
                snapshot=lambda ms: SimpleNamespace(equity=100.0,
                                                    positions=[])))

        def restore_from(self, st):
            recorded["restore"] = st

        def start(self):
            recorded["started"] = True
            return {}

        def write_state(self):
            recorded["state_path"] = self.state_path

    monkeypatch.setattr(mod, "LivePaper", FakeLP)
    monkeypatch.setenv("KAOS_VENUE", "parent-would-set-this")
    (tmp_path / "state.json").write_text(json.dumps(
        {"equity_usd": 77.5}), encoding="utf-8")
    lp = kb.run_crypto_child(out_dir=tmp_path, once=True)
    assert recorded["raws"] == [s.strip().upper()
                                for s in str(mod.DEFAULT_SYMBOLS).split(",")
                                if s.strip()]
    assert recorded["cash"] == 77.5          # equity seeded from prev state
    assert recorded["started"] is True
    assert recorded["state_path"] == tmp_path / "state-crypto.json"
    assert recorded["restore"]["equity_usd"] == 77.5
    assert os.environ["KAOS_VENUE"] == "crypto"   # discriminator forced
    assert lp.state_path == tmp_path / "state-crypto.json"


# ============================================================================
# NASDAQ child: clock gating, paper-only honesty, PDT, heartbeat
# ============================================================================

def test_nasdaq_idle_outside_rth_no_bars_no_venue_calls(tmp_path):
    fetcher = FakeFetcher(make_klines(HIST_START, 60))
    venue = make_venue(tmp_path, FakeClock(open_now=False, label="CLOSED"),
                       fetcher, backfill_bars=60, idle_heartbeat_s=60.0)
    result = venue.run_once(HIST_START + 60 * BAR_MS)
    assert result == "idle"
    assert fetcher.calls == []            # IDLE: no bar polling at all
    state = json.loads((tmp_path / "state-nasdaq.json").read_text("utf-8"))
    assert state["mode"] == "IDLE" and state["session"] == "CLOSED"
    assert state["ts_utc"] and state["venue"] == "nasdaq"


def test_nasdaq_heartbeat_during_idle(tmp_path):
    fetcher = FakeFetcher(make_klines(HIST_START, 60))
    venue = make_venue(tmp_path, FakeClock(open_now=False, label="CLOSED"),
                       fetcher, backfill_bars=60, idle_heartbeat_s=3600.0)
    t = HIST_START + 60 * BAR_MS
    venue.run_once(t)                     # first idle pass: immediate write
    assert venue._state_writes == 1
    venue.run_once(t)                     # inside the heartbeat window: no write
    assert venue._state_writes == 1
    venue.run_once(t, force_write=True)   # forced pass: heartbeat fires
    assert venue._state_writes == 2
    assert fetcher.calls == []            # liveness without any market data I/O


def test_nasdaq_rth_open_backfills_then_polls(tmp_path):
    fetcher = FakeFetcher(make_klines(HIST_START, 400))
    venue = make_venue(tmp_path, FakeClock(open_now=True, label="RTH"),
                       fetcher, backfill_bars=60, idle_heartbeat_s=60.0)
    now = HIST_START + 339 * BAR_MS
    assert venue.run_once(now) == "open"
    # backfill: one fetch per universe symbol with the full backfill window
    assert len(fetcher.calls) == 2
    assert all(c[2] == 60 for c in fetcher.calls)
    state = json.loads((tmp_path / "state-nasdaq.json").read_text("utf-8"))
    # v2.4 (L-3 fix): paper-only construction honestly labels PAPER
    assert state["mode"] == "PAPER" and state["session"] == "RTH"
    assert state["bars_fed"] >= 1
    assert isinstance(state["equity_usd"], float)
    assert state["universe"] == ["AAPL", "MSFT"]
    assert state["closed_trades"] == []
    assert state["leverage"] == 1.0
    # next 15m bar: poll fetches exactly the gap (last fed open = bar 338,
    # target = bar 340 -> need == 2)
    n_calls = len(fetcher.calls)
    venue.run_once(HIST_START + 340 * BAR_MS + 20_000)
    new_calls = fetcher.calls[n_calls:]
    assert len(new_calls) == 2
    assert all(c[2] == 2 for c in new_calls)      # need == 2 bars


def test_nasdaq_restart_replay_neutralized(tmp_path):
    fetcher = FakeFetcher(make_klines(HIST_START, 400))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prev = {"ts_utc": f"{today}T12:00:00+00:00", "equity_usd": 555.0,
            "day_pnl_usd": 5.0, "trades_today": 0, "closed_trades": [],
            "equity_history": []}
    venue = make_venue(tmp_path, FakeClock(open_now=True, label="RTH"),
                       fetcher, backfill_bars=60)
    venue.restore_from(prev)
    venue.run_once(HIST_START + 339 * BAR_MS)
    assert venue._warmed is True
    # replay accounting rebased to the restored base: equity == 555.0 exactly
    assert venue.runner.portfolio.equity() == pytest.approx(555.0, abs=1e-9)


def test_nasdaq_paper_only_when_keys_absent(tmp_path):
    transport = Transport()      # would record ANY network attempt
    adapter, banner, mirror_ok = kb.build_equity_executor(
        env={}, pdt_path=tmp_path / "pdt-fills.json", transport=transport)
    assert banner == kb.NASDAQ_BANNER_NOKEYS      # frozen honesty string
    assert mirror_ok is False
    assert adapter.available is False
    assert transport.calls == []                  # zero network attempts
    assert adapter.pdt_counter is not None        # PDT still wired
    # and the mirror layer is inert around an unavailable adapter
    mirror = kb.EquityMirror(adapter, enabled=mirror_ok)
    assert mirror.available is False
    mirror.mirror_entry("AAPL", "BUY", 5.0, 99.0, 101.0)
    assert mirror.positions == {}                 # nothing tracked, nothing sent


def test_nasdaq_executor_connect_test_gates_mirror(tmp_path):
    # keys present but broker unreachable -> stays paper with an honest banner
    transport = Transport(status=0)
    adapter, banner, mirror_ok = kb.build_equity_executor(
        env={"APCA_API_KEY_ID": "key", "APCA_API_SECRET_KEY": "sec"},
        pdt_path=tmp_path / "pdt-fills.json", transport=transport)
    assert mirror_ok is False
    assert "PAPER" in banner and "FAILED" in banner
    assert adapter.leverage == 1.0                # RegT: forced, never argued
    assert adapter.get_funding_rate("AAPL") is None   # M2 funding gate no-op
    # keys present + broker ok -> mirror enabled on the PAPER host
    transport_ok = Transport(status=200, payload={"equity": "50000"})
    adapter2, banner2, mirror_ok2 = kb.build_equity_executor(
        env={"APCA_API_KEY_ID": "key", "APCA_API_SECRET_KEY": "sec"},
        pdt_path=tmp_path / "pdt-fills.json", transport=transport_ok)
    assert mirror_ok2 is True
    assert "ok" in banner2
    assert "paper-api.alpaca.markets" in adapter2.host   # live needs KAOS_ALPACA_LIVE


def test_nasdaq_pdt_counter_wired_and_persistent(tmp_path):
    transport = Transport(status=200, payload={"equity": "20000"})
    adapter, _banner, _ok = kb.build_equity_executor(
        env={"APCA_API_KEY_ID": "key", "APCA_API_SECRET_KEY": "sec"},
        pdt_path=tmp_path / "pdt-fills.json", calendar=WeekdayCalendar(),
        transport=transport)
    pc = adapter.pdt_counter
    assert pc is not None and pc.path == tmp_path / "pdt-fills.json"
    now_ms = HIST_START
    assert pc.can_open_trade(now_ms) is True
    pc.record_fill(now_ms)
    pc.record_fill(now_ms + 60_000)
    assert (tmp_path / "pdt-fills.json").exists()      # persisted in state dir
    assert pc.count_in_window(now_ms + 120_000) == 2
    pc.record_fill(now_ms + 180_000)
    pc.record_fill(now_ms + 240_000)
    assert pc.can_open_trade(now_ms + 300_000) is False   # 3/day-trade cap


def test_nasdaq_mirror_entry_exit_and_rearm(tmp_path):
    adapter = FakeAdapter()
    mirror = kb.EquityMirror(adapter)
    mirror.mirror_entry("AAPL", "BUY", 5.0, 99.0, 101.0)
    assert ("market", "AAPL", "BUY", 5.0, False) in adapter.calls
    assert ("stop", "AAPL", "long", 99.0) in adapter.calls
    assert ("tp", "AAPL", "long", 5.0, 101.0) in adapter.calls
    assert mirror.positions["AAPL"]["side"] == "long"
    assert mirror.positions["AAPL"]["stop_id"] == "stop1"
    mirror.mirror_exit("AAPL", True, 5.0)
    assert ("market", "AAPL", "SELL", 5.0, True) in adapter.calls
    assert "AAPL" not in mirror.positions
    # rearm: tracked position gone on venue -> bracket cancelled, track dropped
    mirror.positions["MSFT"] = {"qty": 2.0, "side": "short", "stop_id": "s",
                                "tp_id": "t"}
    mirror.rearm("test", {"MSFT": ("short", 101.0, 99.0)})
    assert ("cancel_stop", "MSFT") in adapter.calls
    assert ("cancel_tp", "MSFT") in adapter.calls
    assert "MSFT" not in mirror.positions


def test_nasdaq_rearm_pass_runs_at_session_open(tmp_path):
    fetcher = FakeFetcher(make_klines(HIST_START, 60))
    adapter = FakeAdapter()
    mirror = kb.EquityMirror(adapter)
    mirror.positions["AAPL"] = {"qty": 5.0, "side": "long", "stop_id": "s",
                                "tp_id": "t", "sl_px": 99.0, "tp_px": 101.0}
    venue = make_venue(tmp_path, FakeClock(open_now=True, label="RTH"),
                       fetcher, backfill_bars=60, adapter=adapter, mirror=mirror)
    venue.run_once(HIST_START + 59 * BAR_MS)
    assert ("positions",) in adapter.calls            # adopt/sweep pass ran
    assert "AAPL" not in mirror.positions             # gone on venue -> dropped


def test_nasdaq_risk_profile_and_universe_contract(tmp_path):
    cfg = kb.build_equity_cfg(("AAPL", "MSFT"), 25000.0, tmp_path)
    # same sigma barriers / daily-loss as the crypto lane (contract)
    assert cfg.risk_overrides.stop_sigma_mult == 20.0
    assert cfg.risk_overrides.tp_sigma_mult == 4.0
    assert cfg.risk_overrides.max_daily_loss_pct == 15.0
    assert cfg.risk_overrides.max_concurrent == 4
    assert cfg.risk_overrides.max_total_exposure_pct == 40.0
    assert cfg.consensus.threshold == 0.5 and cfg.consensus.long_only is False
    assert cfg.symbols == ("AAPL", "MSFT") and cfg.ema_symbol == "AAPL"
    assert cfg.bar_s == 900.0 and cfg.warmup is False
    universe = kb._load_equity_universe()
    from entropy.feeds.equities.universe import LIVE_UNIVERSE
    assert universe == tuple(LIVE_UNIVERSE[:20]) and len(universe) == 20
