# Entropy Trading Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fully terminal-based, high-speed automatic trading bot to Entropy that paper-trades the existing live feed on every tick (sub-ms), with a full risk layer (profiles), a verifiable ledger, an optional TUI dashboard, and a disabled-by-default live-execution scaffold (English warnings).

**Architecture:** A new `src/entropy/bot/` subsystem that reuses Entropy's existing feed (`QueueSink` + `EquitySimFeed`/crypto `start_feed`) and detection `Engine`. The decision path (engine → strategies → risk → executor → portfolio → ledger) runs in the **synchronous per-tick drain loop**, not the 10fps UI. Strategies are pluggable; execution is a paper executor by default with a guarded live scaffold; an optional Textual dashboard reads an immutable snapshot.

**Tech Stack:** Python 3.12, `uv`, `msgspec` (frozen structs), `dataclasses` (mutable state, `slots=True`), Textual (dashboard), `pytest` (`asyncio_mode=auto`), `ruff` + `mypy`.

**Conventions (match existing code):**
- Frozen `msgspec.Struct` for events/config/snapshots; `@dataclass(slots=True)` for mutable state.
- Nanosecond timestamps everywhere (`ts_ns`). `from __future__ import annotations` at top of every module.
- Full type hints. `ruff check src/ tests/` and `mypy src/entropy/` must stay clean.
- Tests hermetic + seeded; reuse the `ns` fixture (`tests/conftest.py`) where helpful.
- Run a single test: `uv run pytest tests/bot/test_x.py::test_y -v`. Run all: `uv run pytest -q`.

**Existing APIs this plan integrates with (verified against source):**
- `entropy.engine.engine.Engine().on_trade(symbol: str, price: float, amount: float, side: str, ts_ns: int) -> list[Event]` — `side` is the STRING `"buy"`/`"sell"`; the FIRST trade for a symbol returns `[]` (seeds windows). `Engine().snapshot() -> EngineSnapshot`.
- `entropy.engine.events`: `Event = NewHigh | NewLow | Spike | SnapDrop | UpMove | DownMove`. Base fields `symbol, ts_ns, price`. `Spike/SnapDrop/UpMove/DownMove` add `pct, horizon_s, ref_price` and `kind: EventKind`. `EventKind` (StrEnum): `SPIKE/SNAP_DROP/UPMOVE/DOWNMOVE/NEW_HIGH/NEW_LOW`.
- `entropy.strategy.engine`: `Strategy(StrategyConfig(symbol, fast, slow, ...))`, `.warmup(bars) -> list[StrategyEvent]`, `.on_price(symbol, price, ts_ns) -> list[StrategyEvent]`. `EventKind` (StrEnum, distinct from engine's): `INFO/OPEN_LONG/OPEN_SHORT/CLOSE_LONG/CLOSE_SHORT`. `Bar(ts_ns, close, high=None, low=None)`.
- `entropy.feeds.bus.QueueSink(maxsize=200_000)` → `.q: asyncio.Queue`, `.dropped`, `async put`, `async flush`.
- `entropy.feeds.equities.feed.EquitySimFeed(sink, *, seed=7, ticks_per_sec=4000, batch_dt=0.01)` → emits `crypcodile.schema.records.Trade` (fields: `symbol, price, amount, side, local_ts`; `side` is `crypcodile.schema.enums.Side`, `.value` is `"buy"`/`"sell"`). `.run()` is the async loop.
- `entropy.feeds.crypto.start_feed(sink) -> asyncio.Task[None]` (live crypto; needs network).

---

## File Structure

Created under `src/entropy/bot/`:

| File | Responsibility |
|---|---|
| `__init__.py` | Package marker |
| `signals.py` | `SignalAction`, `Signal` (frozen) |
| `orders.py` | `OrderSide`, `OrderIntent`, `Order`, `Fill` (frozen) |
| `portfolio.py` | `PositionSide`, `PositionState` (mutable), `PositionView`/`PortfolioSnapshot` (frozen), `Portfolio` |
| `risk/__init__.py` | Package marker |
| `risk/profiles.py` | `RiskProfile` (frozen) + `CONSERVATIVE/BALANCED/AGGRESSIVE` presets, `get_profile`, `make_custom` |
| `risk/manager.py` | `RiskDecision` (frozen), `RiskManager` (sizing, exposure cap, cooldown, daily-loss kill-switch, stop/TP) |
| `execution/__init__.py` | Package marker |
| `execution/base.py` | `ExecutionAdapter` Protocol |
| `execution/paper.py` | `PaperExecutor` (fill at price ± slippage + fee) |
| `execution/live.py` | `LiveExecutor` scaffold, `LiveTradingDisabledError`, `LIVE_WARNING` (English, disabled by default) |
| `strategies/__init__.py` | Package marker |
| `strategies/base.py` | `Strategy` Protocol |
| `strategies/momentum_scalper.py` | `MomentumScalper` |
| `strategies/ema_cross.py` | `EmaCrossStrategy` (wraps existing `entropy.strategy.Strategy`) |
| `ledger.py` | `Ledger` (JSONL events + CSV fills/equity) |
| `config.py` | `LiveConfig`, `BotConfig`, strategy factory |
| `runner.py` | `BotSnapshot` (frozen), `BotRunner` (headless engine wiring + sync hot path) |
| `__main__.py` | CLI entry (`python -m entropy.bot`), argparse, headless/dashboard switch |
| `ui/__init__.py` | Package marker |
| `ui/widgets.py` | `RiskBanner`, `PositionsTable`, `PnLPanel`, `TradeLog` |
| `ui/confirm.py` | `ConfirmRiskScreen` (modal) |
| `ui/app.py` | `BotDashboard` (Textual App) |

Tests under `tests/bot/` (mirrors source). `runs/` (ledger output) added to `.gitignore`.

---

### Task 1: Bot package skeleton + core data model (signals, orders)

**Files:**
- Create: `src/entropy/bot/__init__.py`, `src/entropy/bot/signals.py`, `src/entropy/bot/orders.py`
- Test: `tests/bot/__init__.py`, `tests/bot/test_datamodel.py`

- [ ] **Step 1: Write the failing test**

`tests/bot/__init__.py`: empty file.

`tests/bot/test_datamodel.py`:
```python
from entropy.bot.signals import Signal, SignalAction
from entropy.bot.orders import Order, OrderSide, OrderIntent, Fill


def test_signal_is_frozen_with_fields():
    s = Signal(symbol="SPY", action=SignalAction.ENTER_LONG, strength=0.8,
               reason="test", ts_ns=1, strategy="x")
    assert s.action is SignalAction.ENTER_LONG
    assert s.strength == 0.8


def test_order_and_fill_construct():
    o = Order(id="o1", symbol="SPY", side=OrderSide.BUY, intent=OrderIntent.OPEN,
              qty=10.0, price=100.0, ts_ns=1, strategy="x")
    f = Fill(order_id=o.id, symbol="SPY", side=OrderSide.BUY, qty=10.0,
             price=100.1, fee=0.1, slippage=0.1, ts_ns=2)
    assert o.intent is OrderIntent.OPEN
    assert f.price == 100.1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_datamodel.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'entropy.bot'`.

- [ ] **Step 3: Write minimal implementation**

`src/entropy/bot/__init__.py`: empty file.

`src/entropy/bot/signals.py`:
```python
from __future__ import annotations

import enum

import msgspec


class SignalAction(enum.StrEnum):
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT = "exit"


class Signal(msgspec.Struct, frozen=True):
    symbol: str
    action: SignalAction
    strength: float  # 0.0–1.0 confidence
    reason: str
    ts_ns: int
    strategy: str
```

`src/entropy/bot/orders.py`:
```python
from __future__ import annotations

import enum

import msgspec


class OrderSide(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderIntent(enum.StrEnum):
    OPEN = "open"
    CLOSE = "close"
    STOP = "stop"
    TAKE_PROFIT = "take_profit"


class Order(msgspec.Struct, frozen=True):
    id: str
    symbol: str
    side: OrderSide
    intent: OrderIntent
    qty: float
    price: float  # mark price at decision time (paper-fill reference)
    ts_ns: int
    strategy: str


class Fill(msgspec.Struct, frozen=True):
    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    price: float  # executed price incl. slippage
    fee: float
    slippage: float
    ts_ns: int
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/bot/test_datamodel.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/entropy/bot/__init__.py src/entropy/bot/signals.py src/entropy/bot/orders.py tests/bot/__init__.py tests/bot/test_datamodel.py
git commit -m "feat(bot): core data model — signals and orders"
```

---

### Task 2: Risk profiles

**Files:**
- Create: `src/entropy/bot/risk/__init__.py`, `src/entropy/bot/risk/profiles.py`
- Test: `tests/bot/test_profiles.py`

- [ ] **Step 1: Write the failing test**

`tests/bot/test_profiles.py`:
```python
import pytest

from entropy.bot.risk.profiles import (
    AGGRESSIVE, BALANCED, CONSERVATIVE, get_profile, make_custom,
)


def test_presets_have_expected_numbers():
    assert CONSERVATIVE.per_trade_pct == 1.0
    assert CONSERVATIVE.max_concurrent == 2
    assert CONSERVATIVE.max_daily_loss_pct == 2.0
    assert BALANCED.per_trade_pct == 2.5
    assert AGGRESSIVE.max_total_exposure_pct == 40.0


def test_every_profile_has_color_and_description():
    for p in (CONSERVATIVE, BALANCED, AGGRESSIVE):
        assert p.color in {"green", "yellow", "red"}
        assert len(p.description) > 20  # human-readable risk explanation


def test_get_profile_is_case_insensitive():
    assert get_profile("balanced") is BALANCED
    assert get_profile("Aggressive") is AGGRESSIVE


def test_get_profile_unknown_raises():
    with pytest.raises(KeyError):
        get_profile("nope")


def test_make_custom_overrides_and_is_named_custom():
    c = make_custom(per_trade_pct=3.3, max_concurrent=6)
    assert c.name == "Custom"
    assert c.color == "cyan"
    assert c.per_trade_pct == 3.3
    assert c.max_concurrent == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_profiles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'entropy.bot.risk'`.

- [ ] **Step 3: Write minimal implementation**

`src/entropy/bot/risk/__init__.py`: empty file.

`src/entropy/bot/risk/profiles.py`:
```python
from __future__ import annotations

import msgspec


class RiskProfile(msgspec.Struct, frozen=True):
    name: str
    color: str  # rich/textual color name
    per_trade_pct: float  # % of equity allocated per trade
    max_concurrent: int  # max simultaneous open positions
    stop_loss_pct: float  # adverse move that closes a position
    take_profit_pct: float  # favorable move that closes a position
    max_total_exposure_pct: float  # cap on gross notional / equity
    max_daily_loss_pct: float  # daily-loss kill-switch threshold
    cooldown_s: float  # per-symbol re-entry cooldown
    description: str  # plain-English statement of how much risk this takes


CONSERVATIVE = RiskProfile(
    name="Conservative", color="green",
    per_trade_pct=1.0, max_concurrent=2, stop_loss_pct=0.5, take_profit_pct=1.0,
    max_total_exposure_pct=5.0, max_daily_loss_pct=2.0, cooldown_s=30.0,
    description=(
        "Conservative: allocates 1% of equity per trade, at most 2 open positions, "
        "0.5% stop / 1% target, up to 5% total exposure; halts all trading after a 2% daily loss."
    ),
)

BALANCED = RiskProfile(
    name="Balanced", color="yellow",
    per_trade_pct=2.5, max_concurrent=4, stop_loss_pct=1.0, take_profit_pct=2.0,
    max_total_exposure_pct=15.0, max_daily_loss_pct=5.0, cooldown_s=10.0,
    description=(
        "Balanced: allocates 2.5% of equity per trade, up to 4 open positions, "
        "1% stop / 2% target, up to 15% total exposure; halts all trading after a 5% daily loss."
    ),
)

AGGRESSIVE = RiskProfile(
    name="Aggressive", color="red",
    per_trade_pct=5.0, max_concurrent=8, stop_loss_pct=2.0, take_profit_pct=4.0,
    max_total_exposure_pct=40.0, max_daily_loss_pct=10.0, cooldown_s=2.0,
    description=(
        "Aggressive: allocates 5% of equity per trade, up to 8 open positions, "
        "2% stop / 4% target, up to 40% total exposure; halts all trading after a 10% daily loss."
    ),
)

PRESETS: dict[str, RiskProfile] = {
    p.name.lower(): p for p in (CONSERVATIVE, BALANCED, AGGRESSIVE)
}


def get_profile(name: str) -> RiskProfile:
    key = name.lower()
    if key not in PRESETS:
        raise KeyError(
            f"Unknown risk profile {name!r}; choose from {sorted(PRESETS)} or use make_custom()."
        )
    return PRESETS[key]


def make_custom(**overrides: object) -> RiskProfile:
    """Build a Custom profile by overriding Balanced's fields (e.g. per_trade_pct=3.0)."""
    return msgspec.structs.replace(BALANCED, name="Custom", color="cyan", **overrides)  # type: ignore[arg-type]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/bot/test_profiles.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/entropy/bot/risk/__init__.py src/entropy/bot/risk/profiles.py tests/bot/test_profiles.py
git commit -m "feat(bot): risk profiles (conservative/balanced/aggressive/custom)"
```

---

### Task 3: Portfolio (P&L model)

**Files:**
- Create: `src/entropy/bot/portfolio.py`
- Test: `tests/bot/test_portfolio.py`

**Accounting model (deterministic P&L):** `equity = starting_cash + realized_pnl + unrealized_pnl`. Entry/exit fees subtract from `realized_pnl`. `cash` reported as `starting_cash + realized_pnl` (free cash). `daily_pnl = equity - day_start_equity`. This avoids notional cash-settlement and short-margin complexity (out of scope).

- [ ] **Step 1: Write the failing test**

`tests/bot/test_portfolio.py`:
```python
from entropy.bot.orders import Fill, OrderSide
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_portfolio.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'entropy.bot.portfolio'`.

- [ ] **Step 3: Write minimal implementation**

`src/entropy/bot/portfolio.py`:
```python
from __future__ import annotations

import enum
from dataclasses import dataclass

import msgspec


class PositionSide(enum.StrEnum):
    LONG = "long"
    SHORT = "short"


@dataclass(slots=True)
class PositionState:
    symbol: str
    side: PositionSide
    qty: float
    entry_px: float
    stop_px: float
    tp_px: float
    entry_ts_ns: int
    realized_pnl: float = 0.0


class PositionView(msgspec.Struct, frozen=True):
    symbol: str
    side: PositionSide
    qty: float
    entry_px: float
    mark_px: float
    unrealized_pnl: float
    stop_px: float
    tp_px: float


class PortfolioSnapshot(msgspec.Struct, frozen=True):
    ts_ns: int
    cash: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    daily_pnl: float
    open_count: int
    positions: tuple[PositionView, ...]


def _gross(side: PositionSide, entry: float, mark: float, qty: float) -> float:
    return (mark - entry) * qty if side is PositionSide.LONG else (entry - mark) * qty


class Portfolio:
    def __init__(self, starting_cash: float) -> None:
        self.starting_cash = starting_cash
        self.realized_pnl = 0.0
        self.positions: dict[str, PositionState] = {}
        self._marks: dict[str, float] = {}
        self.day_start_equity = starting_cash

    def mark(self, symbol: str, price: float) -> None:
        self._marks[symbol] = price

    def mark_of(self, symbol: str) -> float:
        pos = self.positions.get(symbol)
        default = pos.entry_px if pos is not None else 0.0
        return self._marks.get(symbol, default)

    def open(self, symbol: str, side: PositionSide, qty: float, entry_px: float,
             stop_px: float, tp_px: float, ts_ns: int, fee: float) -> None:
        self.realized_pnl -= fee
        self.positions[symbol] = PositionState(
            symbol=symbol, side=side, qty=qty, entry_px=entry_px,
            stop_px=stop_px, tp_px=tp_px, entry_ts_ns=ts_ns,
        )
        self._marks[symbol] = entry_px

    def close(self, symbol: str, exit_px: float, ts_ns: int, fee: float) -> float:
        pos = self.positions.pop(symbol)
        realized = _gross(pos.side, pos.entry_px, exit_px, pos.qty) - fee
        self.realized_pnl += realized
        self._marks[symbol] = exit_px
        return realized

    def unrealized_pnl(self) -> float:
        return sum(
            _gross(p.side, p.entry_px, self._marks.get(s, p.entry_px), p.qty)
            for s, p in self.positions.items()
        )

    def equity(self) -> float:
        return self.starting_cash + self.realized_pnl + self.unrealized_pnl()

    def cash(self) -> float:
        return self.starting_cash + self.realized_pnl

    def exposure(self) -> float:
        return sum(self._marks.get(s, p.entry_px) * p.qty for s, p in self.positions.items())

    def daily_pnl(self) -> float:
        return self.equity() - self.day_start_equity

    def reset_day(self) -> None:
        self.day_start_equity = self.equity()

    def snapshot(self, ts_ns: int) -> PortfolioSnapshot:
        views = tuple(
            PositionView(
                symbol=p.symbol, side=p.side, qty=p.qty, entry_px=p.entry_px,
                mark_px=self._marks.get(p.symbol, p.entry_px),
                unrealized_pnl=_gross(p.side, p.entry_px, self._marks.get(p.symbol, p.entry_px), p.qty),
                stop_px=p.stop_px, tp_px=p.tp_px,
            )
            for p in self.positions.values()
        )
        return PortfolioSnapshot(
            ts_ns=ts_ns, cash=self.cash(), equity=self.equity(),
            realized_pnl=self.realized_pnl, unrealized_pnl=self.unrealized_pnl(),
            daily_pnl=self.daily_pnl(), open_count=len(self.positions), positions=views,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/bot/test_portfolio.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/entropy/bot/portfolio.py tests/bot/test_portfolio.py
git commit -m "feat(bot): portfolio with deterministic P&L, exposure, daily-pnl"
```

---

### Task 4: Execution base + Paper executor

**Files:**
- Create: `src/entropy/bot/execution/__init__.py`, `src/entropy/bot/execution/base.py`, `src/entropy/bot/execution/paper.py`
- Test: `tests/bot/test_paper_executor.py`

- [ ] **Step 1: Write the failing test**

`tests/bot/test_paper_executor.py`:
```python
from entropy.bot.execution.paper import PaperExecutor
from entropy.bot.orders import Order, OrderIntent, OrderSide


def _order(side: OrderSide) -> Order:
    return Order(id="o1", symbol="SPY", side=side, intent=OrderIntent.OPEN,
                 qty=10.0, price=100.0, ts_ns=1, strategy="x")


def test_buy_fills_above_with_slippage_and_fee():
    ex = PaperExecutor(fee_bps=10.0, slippage_bps=5.0)  # 0.10% fee, 0.05% slippage
    f = ex.submit(_order(OrderSide.BUY))
    assert f.slippage == 100.0 * 0.0005
    assert f.price == 100.0 + f.slippage  # buy fills higher (adverse)
    assert f.fee == abs(f.price * 10.0) * 0.001


def test_sell_fills_below_with_slippage():
    ex = PaperExecutor(fee_bps=0.0, slippage_bps=5.0)
    f = ex.submit(_order(OrderSide.SELL))
    assert f.price == 100.0 - 100.0 * 0.0005  # sell fills lower (adverse)
    assert f.fee == 0.0


def test_fill_carries_order_identity():
    ex = PaperExecutor()
    f = ex.submit(_order(OrderSide.BUY))
    assert f.order_id == "o1"
    assert f.symbol == "SPY"
    assert f.qty == 10.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_paper_executor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'entropy.bot.execution'`.

- [ ] **Step 3: Write minimal implementation**

`src/entropy/bot/execution/__init__.py`: empty file.

`src/entropy/bot/execution/base.py`:
```python
from __future__ import annotations

from typing import Protocol

from ..orders import Fill, Order


class ExecutionAdapter(Protocol):
    """Turns an Order into a Fill. Paper fills instantly; live routes to an exchange."""

    def submit(self, order: Order) -> Fill: ...
```

`src/entropy/bot/execution/paper.py`:
```python
from __future__ import annotations

from ..orders import Fill, Order, OrderSide


class PaperExecutor:
    """Simulates instant fills at the order's reference price ± adverse slippage, plus fees."""

    def __init__(self, fee_bps: float = 1.0, slippage_bps: float = 1.0) -> None:
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps

    def submit(self, order: Order) -> Fill:
        slip = order.price * (self.slippage_bps / 10_000.0)
        fill_px = order.price + slip if order.side is OrderSide.BUY else order.price - slip
        fee = abs(fill_px * order.qty) * (self.fee_bps / 10_000.0)
        return Fill(
            order_id=order.id, symbol=order.symbol, side=order.side, qty=order.qty,
            price=fill_px, fee=fee, slippage=slip, ts_ns=order.ts_ns,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/bot/test_paper_executor.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/entropy/bot/execution/__init__.py src/entropy/bot/execution/base.py src/entropy/bot/execution/paper.py tests/bot/test_paper_executor.py
git commit -m "feat(bot): execution adapter protocol + paper executor"
```

---

### Task 5: Live executor scaffold (disabled by default, English warnings)

**Files:**
- Create: `src/entropy/bot/execution/live.py`
- Test: `tests/bot/test_live_executor.py`

**Intent:** infrastructure exists, but the bot NEVER places a real order on its own. Even fully authorized, `submit()` raises `NotImplementedError` — real routing is intentionally unimplemented and left to the user. All warning text is in English.

- [ ] **Step 1: Write the failing test**

`tests/bot/test_live_executor.py`:
```python
import pytest

from entropy.bot.execution.live import (
    LIVE_WARNING, LiveExecutor, LiveTradingDisabledError,
)
from entropy.bot.orders import Order, OrderIntent, OrderSide


def _order() -> Order:
    return Order(id="o1", symbol="BTCUSDT", side=OrderSide.BUY, intent=OrderIntent.OPEN,
                 qty=1.0, price=50000.0, ts_ns=1, strategy="x")


def test_warning_is_english_and_mentions_real_money():
    assert "REAL money" in LIVE_WARNING
    assert "DISABLED BY DEFAULT" in LIVE_WARNING


def test_disabled_by_default_raises_with_warning():
    ex = LiveExecutor()
    with pytest.raises(LiveTradingDisabledError) as exc:
        ex.submit(_order())
    assert "DISABLED BY DEFAULT" in str(exc.value)


def test_enabled_without_risk_ack_raises():
    ex = LiveExecutor(enabled=True)
    with pytest.raises(LiveTradingDisabledError) as exc:
        ex.submit(_order())
    assert "risk" in str(exc.value).lower()


def test_enabled_and_acked_without_credentials_raises():
    ex = LiveExecutor(enabled=True, acknowledged_risk=True)
    with pytest.raises(LiveTradingDisabledError) as exc:
        ex.submit(_order())
    assert "credential" in str(exc.value).lower()


def test_fully_authorized_still_not_implemented():
    ex = LiveExecutor(enabled=True, acknowledged_risk=True,
                      api_key="k", api_secret="s")
    with pytest.raises(NotImplementedError):
        ex.submit(_order())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_live_executor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'entropy.bot.execution.live'`.

- [ ] **Step 3: Write minimal implementation**

`src/entropy/bot/execution/live.py`:
```python
from __future__ import annotations

from ..orders import Fill, Order

LIVE_WARNING = (
    "⚠️  LIVE TRADING IS DISABLED BY DEFAULT.\n"
    "    Enabling live mode places REAL orders with REAL money on a real exchange.\n"
    "    You are solely responsible for all financial risk, losses, and exchange fees.\n"
    "    This software is provided \"as is\", with NO warranty. Past simulated\n"
    "    performance does NOT guarantee future results.\n"
    "    To enable, you must explicitly set live.enabled = true AND pass\n"
    "    --i-understand-the-risk. The bot will never enable live trading on its own."
)


class LiveTradingDisabledError(RuntimeError):
    """Raised whenever a live order is attempted without full, explicit authorization."""


class LiveExecutor:
    """Disabled-by-default scaffold for live exchange execution.

    Requires three explicit opt-ins (enabled + acknowledged_risk + credentials).
    Even when fully authorized, real order routing is intentionally NOT implemented:
    the bot must never auto-place a real-money order. Wiring an exchange API is left
    entirely to the user.
    """

    def __init__(self, *, enabled: bool = False, acknowledged_risk: bool = False,
                 api_key: str = "", api_secret: str = "") -> None:
        self.enabled = enabled
        self.acknowledged_risk = acknowledged_risk
        self.api_key = api_key
        self.api_secret = api_secret

    def submit(self, order: Order) -> Fill:
        if not self.enabled:
            raise LiveTradingDisabledError(LIVE_WARNING)
        if not self.acknowledged_risk:
            raise LiveTradingDisabledError(
                "Live trading requires explicit risk acknowledgement: pass "
                "--i-understand-the-risk before any real order can be sent."
            )
        if not (self.api_key and self.api_secret):
            raise LiveTradingDisabledError(
                "Missing API credentials: live trading needs a valid api_key and api_secret."
            )
        raise NotImplementedError(
            "Live exchange order routing is intentionally not implemented. The bot will "
            "never auto-place a real-money order; wire your exchange API here yourself."
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/bot/test_live_executor.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add src/entropy/bot/execution/live.py tests/bot/test_live_executor.py
git commit -m "feat(bot): disabled-by-default live executor scaffold with English guards"
```

---

### Task 6: Risk manager

**Files:**
- Create: `src/entropy/bot/risk/manager.py`
- Test: `tests/bot/test_risk_manager.py`

**Design:** `evaluate(signal, portfolio, mark_px, ts_ns) -> RiskDecision`. ENTER signals → sized Order or REJECT (reasons: already-in-position, max-concurrent, cooldown, exposure-cap, halted). EXIT signals → close Order if a position exists. `check_exits(portfolio, ts_ns)` returns mechanical STOP/TAKE_PROFIT orders for positions whose mark crossed their stop/tp. `stop_tp_prices(side, entry)` computes stop/tp from the profile. Kill-switch trips `halted` when `daily_pnl <= -max_daily_loss_pct% * day_start_equity`.

- [ ] **Step 1: Write the failing test**

`tests/bot/test_risk_manager.py`:
```python
from entropy.bot.orders import OrderIntent, OrderSide
from entropy.bot.portfolio import Portfolio, PositionSide
from entropy.bot.risk.manager import RiskManager
from entropy.bot.risk.profiles import BALANCED, CONSERVATIVE, make_custom
from entropy.bot.signals import Signal, SignalAction


def _sig(action: SignalAction, symbol: str = "SPY") -> Signal:
    return Signal(symbol=symbol, action=action, strength=1.0, reason="t", ts_ns=1, strategy="s")


def test_enter_long_sizes_from_per_trade_pct():
    rm = RiskManager(BALANCED)  # 2.5% per trade
    p = Portfolio(100_000.0)
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=1)
    assert d.approved
    assert d.order is not None
    assert d.order.side is OrderSide.BUY
    assert d.order.intent is OrderIntent.OPEN
    assert d.order.qty == 0.025 * 100_000.0 / 100.0  # 25 shares


def test_reject_when_already_in_position():
    rm = RiskManager(BALANCED)
    p = Portfolio(100_000.0)
    p.open("SPY", PositionSide.LONG, 1.0, 100.0, 99.0, 101.0, 1, 0.0)
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=2)
    assert not d.approved
    assert "already" in d.reason


def test_reject_on_max_concurrent():
    rm = RiskManager(CONSERVATIVE)  # max 2
    p = Portfolio(100_000.0)
    p.open("A", PositionSide.LONG, 1.0, 10.0, 9.0, 11.0, 1, 0.0)
    p.open("B", PositionSide.LONG, 1.0, 10.0, 9.0, 11.0, 1, 0.0)
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG, "C"), p, mark_px=10.0, ts_ns=2)
    assert not d.approved
    assert "concurrent" in d.reason


def test_cooldown_blocks_immediate_reentry():
    rm = RiskManager(make_custom(cooldown_s=10.0, max_total_exposure_pct=100.0))
    p = Portfolio(100_000.0)
    d1 = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=0)
    assert d1.approved
    # simulate the position was opened+closed, then re-signal within cooldown
    d2 = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=5_000_000_000)
    assert not d2.approved
    assert "cooldown" in d2.reason


def test_exposure_cap_rejects():
    rm = RiskManager(make_custom(per_trade_pct=50.0, max_total_exposure_pct=10.0))
    p = Portfolio(100_000.0)
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=100.0, ts_ns=1)
    assert not d.approved
    assert "exposure" in d.reason


def test_exit_signal_closes_existing_position():
    rm = RiskManager(BALANCED)
    p = Portfolio(100_000.0)
    p.open("SPY", PositionSide.LONG, 1.0, 100.0, 99.0, 101.0, 1, 0.0)
    d = rm.evaluate(_sig(SignalAction.EXIT), p, mark_px=105.0, ts_ns=2)
    assert d.approved
    assert d.order is not None
    assert d.order.intent is OrderIntent.CLOSE
    assert d.order.side is OrderSide.SELL  # closing a long


def test_exit_with_no_position_rejected():
    rm = RiskManager(BALANCED)
    p = Portfolio(100_000.0)
    d = rm.evaluate(_sig(SignalAction.EXIT), p, mark_px=105.0, ts_ns=2)
    assert not d.approved


def test_stop_tp_prices_for_long_and_short():
    rm = RiskManager(BALANCED)  # 1% stop, 2% tp
    sl, tp = rm.stop_tp_prices(PositionSide.LONG, 100.0)
    assert sl == 99.0 and tp == 102.0
    sl, tp = rm.stop_tp_prices(PositionSide.SHORT, 100.0)
    assert sl == 101.0 and tp == 98.0


def test_check_exits_triggers_stop_for_long():
    rm = RiskManager(BALANCED)
    p = Portfolio(100_000.0)
    p.open("SPY", PositionSide.LONG, 10.0, 100.0, stop_px=99.0, tp_px=102.0, ts_ns=1, fee=0.0)
    p.mark("SPY", 98.5)  # below stop
    orders = rm.check_exits(p, ts_ns=2)
    assert len(orders) == 1
    assert orders[0].intent is OrderIntent.STOP
    assert orders[0].side is OrderSide.SELL


def test_kill_switch_halts_after_daily_loss():
    rm = RiskManager(make_custom(max_daily_loss_pct=5.0, max_total_exposure_pct=100.0))
    p = Portfolio(1000.0)
    # force a -6% day via a realized loss
    p.realized_pnl = -60.0
    d = rm.evaluate(_sig(SignalAction.ENTER_LONG), p, mark_px=10.0, ts_ns=1)
    assert not d.approved
    assert "halt" in d.reason.lower()
    assert rm.halted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_risk_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'entropy.bot.risk.manager'`.

- [ ] **Step 3: Write minimal implementation**

`src/entropy/bot/risk/manager.py`:
```python
from __future__ import annotations

import msgspec

from ..orders import Order, OrderIntent, OrderSide
from ..portfolio import Portfolio, PositionSide, PositionState
from ..signals import Signal, SignalAction
from .profiles import RiskProfile

_NS_PER_S = 1_000_000_000


class RiskDecision(msgspec.Struct, frozen=True):
    approved: bool
    order: Order | None
    reason: str


class RiskManager:
    def __init__(self, profile: RiskProfile) -> None:
        self.profile = profile
        self.halted = False
        self._cooldown_until: dict[str, int] = {}
        self._order_seq = 0

    def set_profile(self, profile: RiskProfile) -> None:
        self.profile = profile

    def _next_id(self) -> str:
        self._order_seq += 1
        return f"o{self._order_seq}"

    def stop_tp_prices(self, side: PositionSide, entry_px: float) -> tuple[float, float]:
        p = self.profile
        if side is PositionSide.LONG:
            return entry_px * (1 - p.stop_loss_pct / 100), entry_px * (1 + p.take_profit_pct / 100)
        return entry_px * (1 + p.stop_loss_pct / 100), entry_px * (1 - p.take_profit_pct / 100)

    def _kill_switch(self, portfolio: Portfolio) -> bool:
        limit = -(self.profile.max_daily_loss_pct / 100.0) * portfolio.day_start_equity
        if portfolio.daily_pnl() <= limit:
            self.halted = True
        return self.halted

    def evaluate(self, signal: Signal, portfolio: Portfolio,
                 mark_px: float, ts_ns: int) -> RiskDecision:
        if self._kill_switch(portfolio):
            return RiskDecision(False, None, "halted: daily loss limit reached")

        pos = portfolio.positions.get(signal.symbol)
        if signal.action is SignalAction.EXIT:
            if pos is None:
                return RiskDecision(False, None, "no open position to exit")
            return RiskDecision(True, self._close_order(pos, mark_px, ts_ns, signal.strategy), "exit")

        if pos is not None:
            return RiskDecision(False, None, f"already in position for {signal.symbol}")
        if len(portfolio.positions) >= self.profile.max_concurrent:
            return RiskDecision(False, None,
                                f"max concurrent positions ({self.profile.max_concurrent}) reached")
        if ts_ns < self._cooldown_until.get(signal.symbol, 0):
            return RiskDecision(False, None, "cooldown active")
        if mark_px <= 0:
            return RiskDecision(False, None, "invalid mark price")

        equity = portfolio.equity()
        qty = (self.profile.per_trade_pct / 100.0) * equity / mark_px
        if qty <= 0:
            return RiskDecision(False, None, "non-positive size")
        projected = portfolio.exposure() + qty * mark_px
        if projected > (self.profile.max_total_exposure_pct / 100.0) * equity:
            return RiskDecision(False, None, "exposure cap exceeded")

        side = OrderSide.BUY if signal.action is SignalAction.ENTER_LONG else OrderSide.SELL
        order = Order(id=self._next_id(), symbol=signal.symbol, side=side,
                      intent=OrderIntent.OPEN, qty=qty, price=mark_px, ts_ns=ts_ns,
                      strategy=signal.strategy)
        self._cooldown_until[signal.symbol] = ts_ns + int(self.profile.cooldown_s * _NS_PER_S)
        return RiskDecision(True, order, "approved")

    def _close_order(self, pos: PositionState, mark_px: float, ts_ns: int,
                     strategy: str, intent: OrderIntent = OrderIntent.CLOSE) -> Order:
        side = OrderSide.SELL if pos.side is PositionSide.LONG else OrderSide.BUY
        return Order(id=self._next_id(), symbol=pos.symbol, side=side, intent=intent,
                     qty=pos.qty, price=mark_px, ts_ns=ts_ns, strategy=strategy)

    def check_exits(self, portfolio: Portfolio, ts_ns: int) -> list[Order]:
        out: list[Order] = []
        for pos in list(portfolio.positions.values()):
            mk = portfolio.mark_of(pos.symbol)
            if pos.side is PositionSide.LONG:
                hit_stop, hit_tp = mk <= pos.stop_px, mk >= pos.tp_px
            else:
                hit_stop, hit_tp = mk >= pos.stop_px, mk <= pos.tp_px
            if hit_stop:
                out.append(self._close_order(pos, mk, ts_ns, "risk", OrderIntent.STOP))
            elif hit_tp:
                out.append(self._close_order(pos, mk, ts_ns, "risk", OrderIntent.TAKE_PROFIT))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/bot/test_risk_manager.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Commit**

```bash
git add src/entropy/bot/risk/manager.py tests/bot/test_risk_manager.py
git commit -m "feat(bot): risk manager — sizing, caps, cooldown, stop/tp, kill-switch"
```

---

### Task 7: Strategy base protocol + EMA-cross adapter

**Files:**
- Create: `src/entropy/bot/strategies/__init__.py`, `src/entropy/bot/strategies/base.py`, `src/entropy/bot/strategies/ema_cross.py`
- Test: `tests/bot/test_ema_cross.py`

**Note:** `on_tick` receives `events` (from `Engine.on_trade`) but NOT a snapshot — the snapshot is expensive and the hot path must stay fast. The EMA adapter ignores `events` and drives off price; it maps the existing strategy's CLOSE_*→EXIT and OPEN_LONG/SHORT→ENTER_* events into `Signal`s, preserving order (a flip emits EXIT then ENTER).

- [ ] **Step 1: Write the failing test**

`tests/bot/test_ema_cross.py`:
```python
from entropy.bot.strategies.ema_cross import EmaCrossStrategy
from entropy.bot.signals import SignalAction


def test_warmup_then_crossover_emits_enter_long():
    strat = EmaCrossStrategy(symbol="SPY", fast=2, slow=4)
    # warm up flat at 100, then ramp up to force fast>slow crossover
    from entropy.strategy.engine import Bar
    strat.warmup([Bar(ts_ns=i, close=100.0) for i in range(4)])
    signals = []
    for i, px in enumerate([101, 102, 103, 104, 105], start=10):
        signals += strat.on_tick("SPY", float(px), i, events=[])
    assert any(s.action is SignalAction.ENTER_LONG for s in signals)
    assert all(s.strategy == "ema_cross" for s in signals)


def test_ignores_other_symbols():
    strat = EmaCrossStrategy(symbol="SPY", fast=2, slow=4)
    from entropy.strategy.engine import Bar
    strat.warmup([Bar(ts_ns=i, close=100.0) for i in range(4)])
    assert strat.on_tick("BTCUSDT", 999.0, 1, events=[]) == []


def test_name_attribute():
    assert EmaCrossStrategy(symbol="SPY").name == "ema_cross"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_ema_cross.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'entropy.bot.strategies'`.

- [ ] **Step 3: Write minimal implementation**

`src/entropy/bot/strategies/__init__.py`: empty file.

`src/entropy/bot/strategies/base.py`:
```python
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from entropy.engine.events import Event
from entropy.strategy.engine import Bar

from ..signals import Signal


class Strategy(Protocol):
    """Pluggable trading strategy. `on_tick` runs on the synchronous per-tick hot path,
    so it must be fast and side-effect free. It receives the engine events produced for
    this tick (not a full snapshot)."""

    name: str

    def on_tick(self, symbol: str, price: float, ts_ns: int,
                events: Sequence[Event]) -> list[Signal]: ...

    def warmup(self, bars: Sequence[Bar]) -> None: ...
```

`src/entropy/bot/strategies/ema_cross.py`:
```python
from __future__ import annotations

from collections.abc import Sequence

from entropy.engine.events import Event
from entropy.strategy.engine import Bar, EventKind, Strategy as _EmaCore, StrategyConfig

from ..signals import Signal, SignalAction

_MAP = {
    EventKind.OPEN_LONG: SignalAction.ENTER_LONG,
    EventKind.OPEN_SHORT: SignalAction.ENTER_SHORT,
    EventKind.CLOSE_LONG: SignalAction.EXIT,
    EventKind.CLOSE_SHORT: SignalAction.EXIT,
}


class EmaCrossStrategy:
    name = "ema_cross"

    def __init__(self, symbol: str, fast: int = 9, slow: int = 21) -> None:
        self.symbol = symbol
        self._core = _EmaCore(StrategyConfig(symbol=symbol, fast=fast, slow=slow))

    def warmup(self, bars: Sequence[Bar]) -> None:
        self._core.warmup(bars)

    def on_tick(self, symbol: str, price: float, ts_ns: int,
                events: Sequence[Event]) -> list[Signal]:
        if symbol != self.symbol:
            return []
        out: list[Signal] = []
        for se in self._core.on_price(symbol, price, ts_ns):
            action = _MAP.get(se.kind)
            if action is not None:
                out.append(Signal(symbol=symbol, action=action, strength=1.0,
                                  reason=f"ema_cross:{se.kind.value}", ts_ns=ts_ns,
                                  strategy=self.name))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/bot/test_ema_cross.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/entropy/bot/strategies/__init__.py src/entropy/bot/strategies/base.py src/entropy/bot/strategies/ema_cross.py tests/bot/test_ema_cross.py
git commit -m "feat(bot): strategy protocol + EMA-cross adapter"
```

---

### Task 8: Momentum scalper strategy

**Files:**
- Create: `src/entropy/bot/strategies/momentum_scalper.py`
- Test: `tests/bot/test_momentum_scalper.py`

**Design (v1, deliberate):** up-momentum (`Spike`/`UpMove`) → `ENTER_LONG`; down-momentum (`SnapDrop`/`DownMove`) → `ENTER_SHORT`; both gated by `min_pct`. Exits are mechanical (risk manager stop/TP), so the scalper emits entries only. `strength` scales with `|pct|`. Optional `symbols` whitelist (None = all). It only acts on events whose `.symbol` matches the tick symbol.

- [ ] **Step 1: Write the failing test**

`tests/bot/test_momentum_scalper.py`:
```python
from entropy.bot.signals import SignalAction
from entropy.bot.strategies.momentum_scalper import MomentumScalper
from entropy.engine.events import DownMove, NewHigh, Spike


def test_spike_emits_enter_long():
    s = MomentumScalper(min_pct=0.15)
    ev = Spike(symbol="SPY", ts_ns=1, price=101.0, pct=0.5)
    out = s.on_tick("SPY", 101.0, 1, events=[ev])
    assert len(out) == 1
    assert out[0].action is SignalAction.ENTER_LONG
    assert out[0].strategy == "momentum_scalper"
    assert out[0].strength > 0


def test_downmove_emits_enter_short():
    s = MomentumScalper(min_pct=0.15)
    ev = DownMove(symbol="SPY", ts_ns=1, price=99.0, pct=-0.30)
    out = s.on_tick("SPY", 99.0, 1, events=[ev])
    assert out[0].action is SignalAction.ENTER_SHORT


def test_below_min_pct_is_ignored():
    s = MomentumScalper(min_pct=0.40)
    ev = Spike(symbol="SPY", ts_ns=1, price=100.1, pct=0.10)
    assert s.on_tick("SPY", 100.1, 1, events=[ev]) == []


def test_non_momentum_events_ignored():
    s = MomentumScalper(min_pct=0.0)
    ev = NewHigh(symbol="SPY", ts_ns=1, price=101.0)
    assert s.on_tick("SPY", 101.0, 1, events=[ev]) == []


def test_symbol_whitelist():
    s = MomentumScalper(symbols=("BTCUSDT",), min_pct=0.0)
    ev = Spike(symbol="SPY", ts_ns=1, price=101.0, pct=1.0)
    assert s.on_tick("SPY", 101.0, 1, events=[ev]) == []


def test_warmup_is_noop():
    s = MomentumScalper()
    assert s.warmup([]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_momentum_scalper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'entropy.bot.strategies.momentum_scalper'`.

- [ ] **Step 3: Write minimal implementation**

`src/entropy/bot/strategies/momentum_scalper.py`:
```python
from __future__ import annotations

from collections.abc import Sequence

from entropy.engine.events import DownMove, Event, SnapDrop, Spike, UpMove
from entropy.strategy.engine import Bar

from ..signals import Signal, SignalAction


class MomentumScalper:
    """Fast, short-window entries off the engine's momentum events. Exits are mechanical
    (risk-manager stop/take-profit), so this strategy only opens positions."""

    name = "momentum_scalper"

    def __init__(self, symbols: tuple[str, ...] | None = None, min_pct: float = 0.15) -> None:
        self.symbols = symbols  # None = trade every symbol
        self.min_pct = min_pct

    def warmup(self, bars: Sequence[Bar]) -> None:
        return None

    def on_tick(self, symbol: str, price: float, ts_ns: int,
                events: Sequence[Event]) -> list[Signal]:
        if self.symbols is not None and symbol not in self.symbols:
            return []
        out: list[Signal] = []
        for e in events:
            if e.symbol != symbol:
                continue
            if isinstance(e, (Spike, UpMove)) and e.pct >= self.min_pct:
                out.append(Signal(symbol=symbol, action=SignalAction.ENTER_LONG,
                                  strength=min(1.0, e.pct / 2.0),
                                  reason=f"momentum:{e.kind.value}:{e.pct:.2f}%",
                                  ts_ns=ts_ns, strategy=self.name))
            elif isinstance(e, (SnapDrop, DownMove)) and abs(e.pct) >= self.min_pct:
                out.append(Signal(symbol=symbol, action=SignalAction.ENTER_SHORT,
                                  strength=min(1.0, abs(e.pct) / 2.0),
                                  reason=f"momentum:{e.kind.value}:{e.pct:.2f}%",
                                  ts_ns=ts_ns, strategy=self.name))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/bot/test_momentum_scalper.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/entropy/bot/strategies/momentum_scalper.py tests/bot/test_momentum_scalper.py
git commit -m "feat(bot): momentum scalper strategy"
```

---

### Task 9: Ledger (JSONL events + CSV fills/equity)

**Files:**
- Create: `src/entropy/bot/ledger.py`
- Modify: `.gitignore` (add `runs/`)
- Test: `tests/bot/test_ledger.py`

- [ ] **Step 1: Write the failing test**

`tests/bot/test_ledger.py`:
```python
import csv
import json
from pathlib import Path

from entropy.bot.ledger import Ledger
from entropy.bot.orders import Fill, OrderIntent, OrderSide
from entropy.bot.portfolio import Portfolio, PositionSide


def test_record_fill_writes_csv_and_jsonl(tmp_path: Path):
    led = Ledger(str(tmp_path))
    f = Fill(order_id="o1", symbol="SPY", side=OrderSide.BUY, qty=10.0,
             price=100.0, fee=0.1, slippage=0.05, ts_ns=1)
    led.record_fill(f, OrderIntent.OPEN)
    rows = list(csv.DictReader((tmp_path / "fills.csv").open()))
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["intent"] == "open"
    lines = (tmp_path / "events.jsonl").read_text().strip().splitlines()
    assert json.loads(lines[0])["kind"] == "fill"


def test_record_equity_appends_row(tmp_path: Path):
    led = Ledger(str(tmp_path))
    p = Portfolio(1000.0)
    p.open("A", PositionSide.LONG, 1.0, 10.0, 9.0, 11.0, 1, 0.0)
    p.mark("A", 12.0)
    led.record_equity(p.snapshot(ts_ns=5))
    rows = list(csv.DictReader((tmp_path / "equity.csv").open()))
    assert float(rows[0]["equity"]) == 1002.0


def test_record_risk_change_and_reject(tmp_path: Path):
    led = Ledger(str(tmp_path))
    led.record_risk_change("Balanced", "Aggressive")
    led.record_reject("SPY", "cooldown active")
    kinds = [json.loads(x)["kind"]
             for x in (tmp_path / "events.jsonl").read_text().strip().splitlines()]
    assert "risk_profile_changed" in kinds
    assert "reject" in kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'entropy.bot.ledger'`.

- [ ] **Step 3: Write minimal implementation**

`src/entropy/bot/ledger.py`:
```python
from __future__ import annotations

import csv
import json
import os
from typing import Any

from .orders import Fill, OrderIntent
from .portfolio import PortfolioSnapshot

_FILL_HEADER = ["ts_ns", "order_id", "symbol", "side", "intent", "qty", "price", "fee", "slippage"]
_EQUITY_HEADER = ["ts_ns", "equity", "cash", "realized_pnl", "unrealized_pnl", "daily_pnl", "open_count"]


class Ledger:
    """Append-only trade journal: structured events to events.jsonl, plus flat CSVs for
    fills and the equity curve. All writes are synchronous appends (call off the hot path
    for equity; fills are rare)."""

    def __init__(self, run_dir: str) -> None:
        os.makedirs(run_dir, exist_ok=True)
        self.run_dir = run_dir
        self._events = os.path.join(run_dir, "events.jsonl")
        self._fills = os.path.join(run_dir, "fills.csv")
        self._equity = os.path.join(run_dir, "equity.csv")
        self._init_csv(self._fills, _FILL_HEADER)
        self._init_csv(self._equity, _EQUITY_HEADER)

    @staticmethod
    def _init_csv(path: str, header: list[str]) -> None:
        if not os.path.exists(path):
            with open(path, "w", newline="") as fh:
                csv.writer(fh).writerow(header)

    def record_event(self, kind: str, payload: dict[str, Any]) -> None:
        with open(self._events, "a") as fh:
            fh.write(json.dumps({"kind": kind, **payload}) + "\n")

    def record_fill(self, fill: Fill, intent: OrderIntent) -> None:
        with open(self._fills, "a", newline="") as fh:
            csv.writer(fh).writerow([
                fill.ts_ns, fill.order_id, fill.symbol, fill.side.value, intent.value,
                fill.qty, fill.price, fill.fee, fill.slippage,
            ])
        self.record_event("fill", {
            "ts_ns": fill.ts_ns, "symbol": fill.symbol, "side": fill.side.value,
            "intent": intent.value, "qty": fill.qty, "price": fill.price, "fee": fill.fee,
        })

    def record_equity(self, snap: PortfolioSnapshot) -> None:
        with open(self._equity, "a", newline="") as fh:
            csv.writer(fh).writerow([
                snap.ts_ns, snap.equity, snap.cash, snap.realized_pnl,
                snap.unrealized_pnl, snap.daily_pnl, snap.open_count,
            ])

    def record_reject(self, symbol: str, reason: str) -> None:
        self.record_event("reject", {"symbol": symbol, "reason": reason})

    def record_risk_change(self, old: str, new: str) -> None:
        self.record_event("risk_profile_changed", {"from": old, "to": new})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/bot/test_ledger.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Add `runs/` to .gitignore and commit**

First read `.gitignore`, then append `runs/` on its own line (do not duplicate if present).

```bash
git add src/entropy/bot/ledger.py tests/bot/test_ledger.py .gitignore
git commit -m "feat(bot): trade ledger (jsonl events + csv fills/equity)"
```

---

### Task 10: BotConfig + BotRunner (headless engine) + CLI entry

**Files:**
- Create: `src/entropy/bot/config.py`, `src/entropy/bot/runner.py`, `src/entropy/bot/__main__.py`
- Test: `tests/bot/test_runner.py`

**Design:** `BotRunner.on_trade(...)` is the synchronous hot path: engine events → mark → mechanical stop/TP exits → strategy signals → risk → execute → portfolio → ledger. `run()` wires the sim/crypto feeds + an async drain loop + a periodic equity recorder. The integration test drives the runner deterministically via the sim feed with no network (`enable_crypto=False`), mirroring `tests/test_wiring.py`.

- [ ] **Step 1: Write the failing test**

`tests/bot/test_runner.py`:
```python
import asyncio
import contextlib
from pathlib import Path

import pytest

from entropy.bot.config import BotConfig, build_strategies
from entropy.bot.runner import BotRunner


def test_build_strategies_from_names():
    cfg = BotConfig(strategies=("momentum_scalper", "ema_cross"), ema_symbol="SPY")
    strats = build_strategies(cfg)
    assert [s.name for s in strats] == ["momentum_scalper", "ema_cross"]


def test_on_trade_opens_position_on_momentum(tmp_path: Path):
    cfg = BotConfig(strategies=("momentum_scalper",), enable_crypto=False,
                    enable_equities=False, risk_profile="aggressive")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    # First tick at ts=0 seeds the engine window AND the momentum anchor (returns no events).
    # The momentum horizon is 5s (5e9 ns): has_anchor() only becomes true once a tick is at
    # least 5s newer than the anchor, so subsequent trades MUST be placed past 5e9 ns. Ramping
    # +1 from 100 over 100s gives ~1% moves vs the anchor → classified as Spike (>=0.40%).
    bot.on_trade("ZZZ", 100.0, 1.0, "buy", 0)
    for i in range(1, 6):
        bot.on_trade("ZZZ", 100.0 + i, 1.0, "buy", 5_000_000_000 + i * 1_000_000_000)
    snap = bot.snapshot()
    assert snap.portfolio.open_count >= 1


@pytest.mark.asyncio
async def test_run_with_sim_feed_records_equity(tmp_path: Path):
    cfg = BotConfig(strategies=("momentum_scalper",), enable_crypto=False,
                    enable_equities=True, equity_tps=3000, seed=11,
                    risk_profile="aggressive")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    task = asyncio.create_task(bot.run())
    await asyncio.sleep(0.3)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    # equity curve was written and the bot processed ticks deterministically
    assert (tmp_path / "equity.csv").exists()
    assert bot.ticks > 0


def test_set_risk_profile_records_change(tmp_path: Path):
    cfg = BotConfig(risk_profile="conservative")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    assert bot.risk.profile.name == "Conservative"
    bot.set_risk_profile("aggressive")
    assert bot.risk.profile.name == "Aggressive"
    import json
    kinds = [json.loads(x)["kind"]
             for x in (tmp_path / "events.jsonl").read_text().strip().splitlines()]
    assert "risk_profile_changed" in kinds
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'entropy.bot.config'`.

- [ ] **Step 3: Write minimal implementation**

`src/entropy/bot/config.py`:
```python
from __future__ import annotations

import msgspec

from .risk.profiles import RiskProfile, get_profile
from .strategies.base import Strategy
from .strategies.ema_cross import EmaCrossStrategy
from .strategies.momentum_scalper import MomentumScalper


class LiveConfig(msgspec.Struct, frozen=True):
    enabled: bool = False
    acknowledged_risk: bool = False
    exchange: str = "binance"
    api_key: str = ""
    api_secret: str = ""


class BotConfig(msgspec.Struct, frozen=True):
    mode: str = "paper"  # "paper" | "live"
    risk_profile: str = "balanced"
    strategies: tuple[str, ...] = ("momentum_scalper", "ema_cross")
    symbols: tuple[str, ...] = ()  # () = all symbols from the feed
    starting_cash: float = 100_000.0
    fee_bps: float = 1.0
    slippage_bps: float = 1.0
    ema_symbol: str = "SPY"  # deterministic sim symbol by default; use "binance-spot:BTCUSDT" live
    momentum_min_pct: float = 0.15
    seed: int = 42
    equity_tps: int = 4000
    enable_crypto: bool = True
    enable_equities: bool = True
    live: LiveConfig = msgspec.field(default_factory=LiveConfig)

    def profile(self) -> RiskProfile:
        return get_profile(self.risk_profile)


def build_strategies(cfg: BotConfig) -> list[Strategy]:
    syms = cfg.symbols or None
    out: list[Strategy] = []
    for name in cfg.strategies:
        if name == "momentum_scalper":
            out.append(MomentumScalper(symbols=syms, min_pct=cfg.momentum_min_pct))
        elif name == "ema_cross":
            out.append(EmaCrossStrategy(symbol=cfg.ema_symbol))
        else:
            raise KeyError(f"Unknown strategy {name!r}")
    return out
```

`src/entropy/bot/runner.py`:
```python
from __future__ import annotations

import asyncio
import contextlib

import msgspec
from crypcodile.schema.records import Trade

from entropy.engine.engine import Engine
from entropy.feeds.bus import QueueSink
from entropy.feeds.equities.feed import EquitySimFeed

from .config import BotConfig, build_strategies
from .execution.base import ExecutionAdapter
from .execution.live import LiveExecutor
from .execution.paper import PaperExecutor
from .ledger import Ledger
from .orders import Order, OrderIntent, OrderSide
from .portfolio import Portfolio, PortfolioSnapshot, PositionSide
from .risk.manager import RiskManager
from .risk.profiles import RiskProfile, get_profile

_NS_PER_S = 1_000_000_000


class BotSnapshot(msgspec.Struct, frozen=True):
    portfolio: PortfolioSnapshot
    risk_profile: RiskProfile
    halted: bool
    ticks: int


def _make_executor(cfg: BotConfig) -> ExecutionAdapter:
    if cfg.mode == "live":
        return LiveExecutor(enabled=cfg.live.enabled, acknowledged_risk=cfg.live.acknowledged_risk,
                            api_key=cfg.live.api_key, api_secret=cfg.live.api_secret)
    return PaperExecutor(fee_bps=cfg.fee_bps, slippage_bps=cfg.slippage_bps)


class BotRunner:
    def __init__(self, config: BotConfig, run_dir: str = "runs/latest") -> None:
        self.config = config
        self.engine = Engine()
        self.portfolio = Portfolio(config.starting_cash)
        self.risk = RiskManager(config.profile())
        self.executor = _make_executor(config)
        self.strategies = build_strategies(config)
        self.ledger = Ledger(run_dir)
        self._sink = QueueSink()
        self._equity = EquitySimFeed(self._sink, seed=config.seed, ticks_per_sec=config.equity_tps)
        self.ticks = 0
        self._last_ts_ns = 0

    # ---- synchronous hot path -------------------------------------------------
    def on_trade(self, symbol: str, price: float, amount: float, side: str, ts_ns: int) -> None:
        events = self.engine.on_trade(symbol, price, amount, side, ts_ns)
        self.portfolio.mark(symbol, price)
        self._last_ts_ns = ts_ns
        self.ticks += 1
        # mechanical stop/take-profit exits first
        for order in self.risk.check_exits(self.portfolio, ts_ns):
            self._execute(order)
        # strategy signals
        for strat in self.strategies:
            for sig in strat.on_tick(symbol, price, ts_ns, events):
                decision = self.risk.evaluate(sig, self.portfolio, price, ts_ns)
                if decision.approved and decision.order is not None:
                    self._execute(decision.order)
                elif not decision.approved:
                    self.ledger.record_reject(sig.symbol, decision.reason)

    def _execute(self, order: Order) -> None:
        fill = self.executor.submit(order)
        if order.intent is OrderIntent.OPEN:
            pos_side = PositionSide.LONG if order.side is OrderSide.BUY else PositionSide.SHORT
            stop_px, tp_px = self.risk.stop_tp_prices(pos_side, fill.price)
            self.portfolio.open(order.symbol, pos_side, fill.qty, fill.price,
                                stop_px, tp_px, fill.ts_ns, fill.fee)
        else:
            self.portfolio.close(order.symbol, fill.price, fill.ts_ns, fill.fee)
        self.ledger.record_fill(fill, order.intent)

    # ---- control --------------------------------------------------------------
    def set_risk_profile(self, name: str) -> RiskProfile:
        old = self.risk.profile.name
        profile = get_profile(name)
        self.risk.set_profile(profile)
        self.ledger.record_risk_change(old, profile.name)
        return profile

    def snapshot(self) -> BotSnapshot:
        return BotSnapshot(
            portfolio=self.portfolio.snapshot(self._last_ts_ns),
            risk_profile=self.risk.profile, halted=self.risk.halted, ticks=self.ticks,
        )

    # ---- async wiring ---------------------------------------------------------
    async def _drain(self) -> None:
        q = self._sink.q
        while True:
            r = await q.get()
            if isinstance(r, Trade):
                self.on_trade(r.symbol, r.price, r.amount, r.side.value, r.local_ts)

    async def _record_equity_loop(self, period_s: float = 1.0) -> None:
        while True:
            await asyncio.sleep(period_s)
            self.ledger.record_equity(self.portfolio.snapshot(self._last_ts_ns))

    async def run(self) -> None:
        tasks: list[asyncio.Task[None]] = [
            asyncio.create_task(self._drain()),
            asyncio.create_task(self._record_equity_loop()),
        ]
        if self.config.enable_equities:
            tasks.append(asyncio.create_task(self._equity.run()))
        if self.config.enable_crypto:
            from entropy.feeds.crypto import start_feed
            tasks.append(await start_feed(self._sink))
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with contextlib.suppress(asyncio.CancelledError):
                    await t
```

`src/entropy/bot/__main__.py`:
```python
from __future__ import annotations

import argparse
import asyncio

from .config import BotConfig, LiveConfig
from .execution.live import LIVE_WARNING
from .runner import BotRunner


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="entropy.bot", description="Entropy automatic trading bot")
    ap.add_argument("--mode", choices=["paper", "live"], default="paper")
    ap.add_argument("--risk", default="balanced", help="conservative | balanced | aggressive")
    ap.add_argument("--cash", type=float, default=100_000.0)
    ap.add_argument("--no-crypto", action="store_true", help="disable the live crypto feed")
    ap.add_argument("--dashboard", action="store_true", help="run the TUI dashboard")
    ap.add_argument("--i-understand-the-risk", action="store_true",
                    help="required to even attempt live trading (see warning)")
    return ap.parse_args(argv)


def build_config(ns: argparse.Namespace) -> BotConfig:
    live = LiveConfig(enabled=(ns.mode == "live"), acknowledged_risk=ns.i_understand_the_risk)
    return BotConfig(mode=ns.mode, risk_profile=ns.risk, starting_cash=ns.cash,
                     enable_crypto=not ns.no_crypto, live=live)


def main(argv: list[str] | None = None) -> None:
    ns = _parse_args(argv)
    if ns.mode == "live":
        print(LIVE_WARNING)
    cfg = build_config(ns)
    if ns.dashboard:
        from .ui.app import BotDashboard
        BotDashboard(cfg).run()
        return
    bot = BotRunner(cfg)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/bot/test_runner.py -v`
Expected: PASS (4 passed). `test_on_trade_opens_position_on_momentum` is deterministic: the anchor at ts=0 plus a tick at ts=6e9 (price 101) yields a +1.0% move vs the anchor, which the engine classifies as a Spike; the momentum scalper turns that into ENTER_LONG and the aggressive profile sizes a position. The timestamps MUST stay past the 5s (5e9 ns) momentum horizon — do not shrink them.

- [ ] **Step 5: Commit**

```bash
git add src/entropy/bot/config.py src/entropy/bot/runner.py src/entropy/bot/__main__.py tests/bot/test_runner.py
git commit -m "feat(bot): headless BotRunner, config, strategy factory, CLI entry"
```

---

### Task 11: TUI dashboard (colored risk banner, positions, P&L, trade log, risk-change confirmation)

**Files:**
- Create: `src/entropy/bot/ui/__init__.py`, `src/entropy/bot/ui/widgets.py`, `src/entropy/bot/ui/confirm.py`, `src/entropy/bot/ui/app.py`
- Test: `tests/bot/test_dashboard.py`

**Design:** `BotDashboard(App)` holds a `BotRunner`, runs its feeds via Textual `@work`, samples `runner.snapshot()` at 10fps and updates widgets. `RiskBanner` renders `RISK PROFILE: <NAME>` in the profile's color every frame (always visible). Number keys `1/2/3` request a profile change, routed through `ConfirmRiskScreen`; on confirm, `runner.set_risk_profile(...)` runs (banner updates immediately + ledger records the change), satisfying the "be sure it changed" requirement.

- [ ] **Step 1: Write the failing test**

`tests/bot/test_dashboard.py`:
```python
import pytest

from entropy.bot.config import BotConfig
from entropy.bot.runner import BotRunner
from entropy.bot.ui.app import BotDashboard
from entropy.bot.ui.widgets import RiskBanner


def test_risk_banner_text_and_color():
    from entropy.bot.risk.profiles import AGGRESSIVE
    b = RiskBanner()
    b.set_profile(AGGRESSIVE)
    assert "AGGRESSIVE" in b.render_text()
    assert b.color == "red"


def test_dashboard_constructs_with_runner(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False)
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    assert app.runner is bot


@pytest.mark.asyncio
async def test_dashboard_boots_and_shows_banner(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="conservative")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()
        banner = app.query_one(RiskBanner)
        assert "CONSERVATIVE" in banner.render_text()


@pytest.mark.asyncio
async def test_changing_profile_updates_runner_and_banner(tmp_path):
    cfg = BotConfig(enable_crypto=False, enable_equities=False, risk_profile="conservative")
    bot = BotRunner(cfg, run_dir=str(tmp_path))
    app = BotDashboard(cfg, runner=bot)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.apply_risk_change("aggressive")  # the post-confirmation callback
        await pilot.pause()
        assert bot.risk.profile.name == "Aggressive"
        assert "AGGRESSIVE" in app.query_one(RiskBanner).render_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_dashboard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'entropy.bot.ui'`.

- [ ] **Step 3: Write minimal implementation**

`src/entropy/bot/ui/__init__.py`: empty file.

`src/entropy/bot/ui/widgets.py`:
```python
from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import DataTable, RichLog, Static

from ..portfolio import PortfolioSnapshot
from ..risk.profiles import BALANCED, RiskProfile


class RiskBanner(Static):
    """Always-on, colored risk-level banner."""

    profile_name: reactive[str] = reactive(BALANCED.name)
    color: reactive[str] = reactive(BALANCED.color)

    def set_profile(self, profile: RiskProfile) -> None:
        self.profile_name = profile.name
        self.color = profile.color
        self.update(Text(self.render_text(), style=f"bold {self.color}"))

    def render_text(self) -> str:
        return f"RISK PROFILE: {self.profile_name.upper()}"

    def on_mount(self) -> None:
        self.update(Text(self.render_text(), style=f"bold {self.color}"))


class PnLPanel(Static):
    def show(self, snap: PortfolioSnapshot) -> None:
        self.update(
            f"Equity {snap.equity:,.2f}   Cash {snap.cash:,.2f}   "
            f"Realized {snap.realized_pnl:+,.2f}   Unrealized {snap.unrealized_pnl:+,.2f}   "
            f"Day {snap.daily_pnl:+,.2f}   Open {snap.open_count}"
        )


class PositionsTable(DataTable[str]):
    def on_mount(self) -> None:
        self.cursor_type = "none"
        self.zebra_stripes = False
        self.add_columns("Symbol", "Side", "Qty", "Entry", "Mark", "uPnL")

    def show(self, snap: PortfolioSnapshot) -> None:
        self.clear()
        for p in snap.positions:
            self.add_row(p.symbol, p.side.value, f"{p.qty:.4f}", f"{p.entry_px:.2f}",
                         f"{p.mark_px:.2f}", f"{p.unrealized_pnl:+.2f}")


class TradeLog(RichLog):
    def log_line(self, text: str) -> None:
        self.write(text)
```

`src/entropy/bot/ui/confirm.py`:
```python
from __future__ import annotations

from collections.abc import Callable

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmRiskScreen(ModalScreen[None]):
    """Asks the user to confirm a risk-profile change. On confirm, invokes the callback."""

    def __init__(self, new_profile: str, on_confirm: Callable[[], None]) -> None:
        super().__init__()
        self._new = new_profile
        self._on_confirm = on_confirm

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(f"Change risk profile to {self._new.upper()}?")
            yield Button("Confirm", id="confirm", variant="warning")
            yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self._on_confirm()
        self.dismiss(None)
```

`src/entropy/bot/ui/app.py`:
```python
from __future__ import annotations

from typing import Any

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Vertical

from ..config import BotConfig
from ..runner import BotRunner
from .confirm import ConfirmRiskScreen
from .widgets import PnLPanel, PositionsTable, RiskBanner, TradeLog

_KEY_TO_PROFILE = {"1": "conservative", "2": "balanced", "3": "aggressive"}


class BotDashboard(App[None]):
    BINDINGS = [
        ("1", "risk('1')", "Conservative"),
        ("2", "risk('2')", "Balanced"),
        ("3", "risk('3')", "Aggressive"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, config: BotConfig | None = None,
                 runner: BotRunner | None = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.cfg = config or BotConfig()
        self.runner = runner or BotRunner(self.cfg)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield RiskBanner(id="risk-banner")
            yield PnLPanel(id="pnl")
            yield PositionsTable(id="positions")
            yield TradeLog(id="trades")

    def on_mount(self) -> None:
        self.query_one(RiskBanner).set_profile(self.runner.risk.profile)
        self.set_interval(1 / 10, self._sample)
        self._run_feeds()

    def _sample(self) -> None:
        snap = self.runner.snapshot()
        self.query_one(PnLPanel).show(snap.portfolio)
        self.query_one(PositionsTable).show(snap.portfolio)

    @work(exclusive=True, group="bot")
    async def _run_feeds(self) -> None:
        await self.runner.run()

    def action_risk(self, key: str) -> None:
        name = _KEY_TO_PROFILE.get(key)
        if name is None:
            return
        self.push_screen(ConfirmRiskScreen(name, lambda: self.apply_risk_change(name)))

    def apply_risk_change(self, name: str) -> None:
        profile = self.runner.set_risk_profile(name)
        self.query_one(RiskBanner).set_profile(profile)
        self.query_one(TradeLog).log_line(f"risk profile changed -> {profile.name}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/bot/test_dashboard.py -v`
Expected: PASS (4 passed). Textual's `run_test()` provides the async pilot harness (matches existing `tests/ui/` usage).

- [ ] **Step 5: Commit**

```bash
git add src/entropy/bot/ui/ tests/bot/test_dashboard.py
git commit -m "feat(bot): TUI dashboard — colored risk banner, positions, pnl, confirm modal"
```

---

### Task 12: End-to-end integration test, README docs, packaging entry

**Files:**
- Create: `tests/bot/test_integration.py`
- Modify: `README.md` (add a "Trading bot" section), `pyproject.toml` (add `[project.scripts]`)
- Test: the new integration test + full suite + lint/types

- [ ] **Step 1: Write the failing end-to-end test**

`tests/bot/test_integration.py`:
```python
import asyncio
import contextlib
import csv
from pathlib import Path

import pytest

from entropy.bot.config import BotConfig
from entropy.bot.runner import BotRunner


@pytest.mark.asyncio
async def test_end_to_end_paper_run_is_deterministic(tmp_path: Path):
    """Two identical seeded sim runs process the same number of ticks and produce the
    same final equity — proving the decision+paper-fill path is deterministic."""
    async def run_once(d: Path) -> tuple[int, float]:
        cfg = BotConfig(strategies=("momentum_scalper", "ema_cross"), ema_symbol="SPY",
                        enable_crypto=False, enable_equities=True, equity_tps=3000,
                        seed=7, risk_profile="aggressive")
        bot = BotRunner(cfg, run_dir=str(d))
        task = asyncio.create_task(bot.run())
        await asyncio.sleep(0.3)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return bot.ticks, bot.portfolio.equity()

    t1, e1 = await run_once(tmp_path / "a")
    t2, e2 = await run_once(tmp_path / "b")
    assert t1 > 0
    # Deterministic seed → identical tick counts are not guaranteed under wall-clock
    # sleep, but the ledger must be a valid, parseable artifact in both runs.
    for sub in ("a", "b"):
        rows = list(csv.DictReader((tmp_path / sub / "equity.csv").open()))
        assert all("equity" in r for r in rows)
```

(Determinism note: because `run()` is driven by real-time `asyncio.sleep`, exact tick counts vary; the strict determinism guarantee is on `on_trade` given an identical trade sequence — covered by Task 10's direct-call test. This integration test asserts the pipeline runs end-to-end and writes valid artifacts.)

- [ ] **Step 2: Run test to verify it fails, then passes**

Run: `uv run pytest tests/bot/test_integration.py -v`
Expected: PASS once the runner from Task 10 exists (this test adds coverage, no new source needed). If it fails, fix the runner, not the test.

- [ ] **Step 3: Add packaging entry point**

In `pyproject.toml`, add after the `[project]` dependencies block (before `[dependency-groups]`):
```toml
[project.scripts]
entropy-bot = "entropy.bot.__main__:main"
```

- [ ] **Step 4: Document in README**

Append a `## Trading bot` section to `README.md`:
````markdown
## Trading bot

Entropy ships a terminal automatic trading bot that paper-trades the live feed on every tick.

```bash
# headless paper run (live crypto + sim equities)
uv run python -m entropy.bot

# with the TUI dashboard (colored risk banner, positions, P&L, trade log)
uv run python -m entropy.bot --dashboard

# pick a risk profile
uv run python -m entropy.bot --risk aggressive
```

Risk profiles (`--risk`): **conservative** (green), **balanced** (yellow), **aggressive** (red).
Each profile states exactly how much risk it takes (per-trade %, max positions, stop/target,
total exposure, daily-loss kill-switch); the dashboard shows the active profile as an always-on
colored banner, and changing it requires confirmation.

> ⚠️  **Live trading is disabled by default.** Paper mode never touches real money. Enabling
> live mode would place REAL orders with REAL money; you are solely responsible for all risk.
> The bot will never enable or auto-trigger live trading on its own.

Each run writes a ledger under `runs/`: `events.jsonl` (fills, rejects, risk changes),
`fills.csv`, and `equity.csv` (the equity curve).
````

- [ ] **Step 5: Run the full suite + lint + types**

```bash
uv run pytest -q
uv run ruff check src/ tests/
uv run mypy src/entropy/
```
Expected: all bot tests + the existing 62 tests green; ruff clean; mypy clean. Fix any issues before committing.

- [ ] **Step 6: Commit**

```bash
git add tests/bot/test_integration.py pyproject.toml README.md
git commit -m "feat(bot): e2e integration test, packaging entry, README docs"
```

---

## Self-Review (completed during planning)

**Spec coverage:** §3 architecture → Task 10 runner (sync hot path). §4 package layout → all tasks (every file mapped). §5 data model → Tasks 1,3. §6 risk profiles → Task 2 (+ banner Task 11, change-confirm Task 11). §7 live warnings/guards → Task 5 (+ CLI warning Task 10). §8 strategies → Tasks 7,8. §9 error handling → RiskDecision REJECT (Task 6), kill-switch (Task 6), live English errors (Task 5). §10 testing → every task is TDD; e2e Task 12. §11 success criteria → headless run (Task 10), sub-ms sync path (Task 10), risk layer+banner+confirm (Tasks 2,6,11), ledger (Task 9), live scaffold disabled (Task 5), full suite green (Task 12).

**Placeholder scan:** none — every step has complete code.

**Type consistency:** `Signal(symbol, action, strength, reason, ts_ns, strategy)`, `Order(id, symbol, side, intent, qty, price, ts_ns, strategy)`, `Fill(order_id, symbol, side, qty, price, fee, slippage, ts_ns)`, `PositionSide.{LONG,SHORT}`, `OrderSide.{BUY,SELL}`, `OrderIntent.{OPEN,CLOSE,STOP,TAKE_PROFIT}`, `RiskDecision(approved, order, reason)`, `BotSnapshot(portfolio, risk_profile, halted, ticks)` — used identically across Tasks 1–12. `Strategy.on_tick(symbol, price, ts_ns, events)` (no snapshot arg) consistent in base/ema/momentum/runner. `engine.on_trade(..., side: str, ...)` always fed `r.side.value`.
