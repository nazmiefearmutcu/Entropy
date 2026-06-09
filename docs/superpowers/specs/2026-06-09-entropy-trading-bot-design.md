# Entropy Trading Bot — Design Spec

**Date:** 2026-06-09
**Status:** Approved (design), pending implementation
**Author:** brainstorming session
**Supersedes/extends:** `2026-06-08-entropy-design.md` (the TUI market scanner this bot plugs into)

---

## 1. Purpose

Add a fully terminal-based **automatic trading bot** to the existing Entropy project. The bot must be **high-speed** — able to make trade decisions on every tick (sub-millisecond), even on very short timeframes — by running its decision path on the existing synchronous per-tick pipeline rather than the 10fps UI loop.

The bot trades on **paper** (simulated fills against real prices) by default, with a clean, **disabled-by-default** live-execution adapter scaffold. All live/financial-risk warnings are in **English**.

## 2. Context: what already exists

Entropy (`src/entropy/`) is a Python 3.12 / `uv` / Textual TUI market scanner:

- **Data:** live Binance crypto (14 pairs via Crypcodile WS) + deterministic simulated US equities (123 symbols, GBM). Both produce Crypcodile `Trade` records into a bounded `QueueSink` (`feeds/bus.py`).
- **Engine** (`engine/engine.py`): pure-sync `Engine.on_trade(symbol, price, amount, side, ts_ns) -> list[Event]`. Emits `NewHigh/NewLow/Spike/SnapDrop/UpMove/DownMove`. Runs 1–10 kHz. `snapshot()` returns an immutable `EngineSnapshot` read by the UI at 10fps.
- **Strategy** (`strategy/engine.py`): an EMA-crossover `Strategy` that emits `StrategyEvent` (OPEN_LONG/CLOSE_LONG/OPEN_SHORT/CLOSE_SHORT) — **but these events are only logged to the console; there is no order execution, portfolio, or risk layer.**
- **Data flow:** `QueueSink → drain worker (per-tick, sync) → Engine.on_trade() + Strategy.on_price() → snapshot → UI (10fps, read-only)`.
- **Conventions:** frozen `msgspec.Struct` for events/config/snapshots; `@dataclass(slots=True)` for mutable state; nanosecond timestamps everywhere (`ts_ns`); full type hints; `ruff` + `mypy` clean; `pytest` with `asyncio_mode=auto`, hermetic + seeded.

**The bot supplies the missing "hands":** order execution, portfolio/PnL, risk management, and signal→order routing — while reusing the existing feed, engine, and (wrapped) EMA strategy.

## 3. Architecture

Mirror the existing engine/UI separation: a fast, pure, testable **bot engine** plus a fully decoupled **dashboard**.

```
                         ┌──────────────────────────────────────────────┐
   Feed (live crypto /   │  BotRunner  (per-tick, SYNC, sub-ms)          │
   sim equity)           │                                                │
      │  Trade           │   Engine.on_trade() ──► events[]               │
      ▼                  │        │                                       │
  QueueSink ──drain────► │   Strategy(s).on_tick(tick, events, snapshot)  │
   (existing, 1–10kHz)   │        │  └► Signal[]                          │
                         │   RiskManager.evaluate(signal, portfolio)      │
                         │        │  └► Order | REJECT                     │
                         │   Executor.submit(order) ──► Fill              │
                         │        │   (Paper: instant price±slippage+fee) │
                         │   Portfolio.apply(fill)  (cash/pos/PnL/equity) │
                         │   StopTP monitor (each tick: check open        │
                         │        positions → exit Order)                  │
                         │   Ledger.record(order, fill, equity)           │
                         └───────────────┬──────────────────────────────┘
                                         │ snapshot (immutable)
                                         ▼ (optional, 10fps read)
                                  TUI Dashboard (Textual)
```

**Critical design decision:** the decision + paper-fill path runs in the **synchronous per-tick drain loop**, not gated by the UI's 10fps. This satisfies the high-speed / very-short-timeframe requirement: a sub-ms decision on every tick (1–10 kHz). The dashboard only **reads** an immutable snapshot and never blocks the engine. The only I/O point is the executor; in paper mode it is synchronous and deterministic, so **backtest and live-paper run through the identical code path**.

## 4. Package layout — `src/entropy/bot/`

| File | Responsibility |
|---|---|
| `runner.py` | Headless engine; wires feed→engine→strategies→risk→executor→portfolio→ledger. Entry: `python -m entropy.bot` |
| `config.py` | `BotConfig` (frozen msgspec): mode (paper/live), active risk profile, enabled strategies, symbols, starting capital |
| `signals.py` | `Signal` (frozen): symbol, side, strength, reason, ts_ns |
| `orders.py` | `Order`, `Fill`, `OrderSide`, `OrderKind`, `OrderStatus` (frozen structs) |
| `strategies/base.py` | `Strategy` protocol: `on_tick(symbol, price, ts_ns, events, snapshot) -> list[Signal]` + `warmup(bars)` |
| `strategies/momentum_scalper.py` | Scalper driven by Engine's `Spike/UpMove/SnapDrop/DownMove/NewHigh/NewLow` events; short window, fast, cooldown'd |
| `strategies/ema_cross.py` | Adapter wrapping the existing `entropy.strategy.Strategy` (EMA) — wraps, does not delete existing code |
| `risk/profiles.py` | `RiskProfile` definitions: **Conservative / Balanced / Aggressive / Custom** — each with explicit numbers + human-readable risk description |
| `risk/manager.py` | `RiskManager`: position sizing, stop-loss/take-profit, per-symbol & total exposure caps, max-daily-loss kill-switch, cooldown. Signal→Order or REJECT |
| `execution/base.py` | `ExecutionAdapter` protocol: `submit(order) -> Fill` |
| `execution/paper.py` | `PaperExecutor`: instant fill at current price with slippage + fee model |
| `execution/live.py` | `LiveExecutor` scaffold — **disabled by default**, guarded with English warnings (see §7) |
| `portfolio.py` | `Portfolio`: cash, multi-symbol positions, realized/unrealized PnL, equity curve |
| `ledger.py` | Trade journal: order/fill/equity/risk-change records → CSV + JSON-lines under `runs/<timestamp>/` |
| `ui/app.py` + `ui/widgets/` | Optional Textual dashboard — positions table, PnL, equity sparkline, trade log, **colored risk banner** |

## 5. Data model (frozen msgspec structs)

- `Signal`: `symbol: str`, `side: Side` (BUY/SELL/EXIT), `strength: float` (0–1), `reason: str`, `ts_ns: int`.
- `Order`: `id: str`, `symbol`, `side`, `kind` (MARKET/EXIT), `qty: float`, `intent` (OPEN/CLOSE/STOP/TP), `ts_ns`, `strategy: str`.
- `Fill`: `order_id`, `symbol`, `side`, `qty`, `price`, `fee`, `slippage`, `ts_ns`.
- `PositionState` (`@dataclass(slots=True)`, mutable): `symbol`, `side`, `qty`, `entry_px`, `stop_px`, `tp_px`, `entry_ts_ns`, `realized_pnl`.
- `PortfolioSnapshot` (frozen): `cash`, `equity`, `unrealized_pnl`, `realized_pnl`, `positions: tuple[...]`, `daily_pnl`, `open_count`, `ts_ns`.

## 6. Risk profiles

Defined in `risk/profiles.py`; 3 presets + Custom. Each field states how much risk explicitly, and each profile carries a human-readable description string surfaced in config and dashboard.

| Profile | Color | Per-trade | Max concurrent | Stop-loss | Take-profit | Total exposure | Max daily loss (kill) | Cooldown |
|---|---|---|---|---|---|---|---|---|
| **Conservative** | green | 1% of equity | 2 | 0.5% | 1.0% | 5% | 2% | 30 s |
| **Balanced** | yellow | 2.5% | 4 | 1.0% | 2.0% | 15% | 5% | 10 s |
| **Aggressive** | red | 5% | 8 | 2.0% | 4.0% | 40% | 10% | 2 s |
| **Custom** | cyan | from config | from config | … | … | … | … | … |

Requirements:
- Each profile in config is annotated with a plain-English risk description (e.g. `# Aggressive: up to 40% of equity exposed; halts all trading after a 10% daily loss`).
- **Always-on colored risk banner** in the dashboard main view: `RISK PROFILE: BALANCED` rendered in the profile's color, visible every frame.
- **Change verification:** when the risk profile changes (config or dashboard), an explicit confirmation step is required, and the change is visibly confirmed (banner updates immediately) and recorded in the ledger as a `RISK_PROFILE_CHANGED` event. This guarantees the "you can be sure it changed" requirement both visually and via the audit trail.

## 7. Live execution — English warnings & guards

`execution/live.py` and live-mode config are **disabled by default**. Attempting to enable live mode surfaces non-dismissible English warnings:

```
⚠️  LIVE TRADING IS DISABLED BY DEFAULT.
    Enabling live mode places REAL orders with REAL money on a real exchange.
    You are solely responsible for all financial risk, losses, and exchange fees.
    This software is provided "as is", with NO warranty. Past simulated
    performance does NOT guarantee future results.
    To enable, you must explicitly set live.enabled = true AND pass
    --i-understand-the-risk. The bot will never enable live trading on its own.
```

- In paper mode no real-money path executes at all.
- The live adapter raises an English error if `submit()` is called without **both** credentials and two explicit opt-ins (config flag + CLI flag).
- Per project hard limits: the infrastructure is built, but the bot **never auto-triggers a live order** — the trigger remains entirely in the user's hands.

## 8. Strategies (pluggable)

- **MomentumScalper:** Engine `Spike`/`UpMove` → long signal; `SnapDrop`/`DownMove` → short/exit; `NewHigh`/`NewLow` as momentum confirmation. Short window (30s/1m), cooldown'd. Primary engine for the "fast, short-timeframe" requirement.
- **EmaCross:** wraps the existing `entropy.strategy.Strategy`; fast>slow → long, fast<slow → short. Existing tests remain green.
- Adding a strategy = one file implementing the `Strategy` protocol, registered in the runner.

## 9. Error handling

- Engine/strategy/risk path stays pure-sync; invalid inputs validated upstream, no exceptions on the hot path.
- `RiskManager` returns explicit `REJECT(reason)` rather than raising — rejections are logged to the ledger.
- Daily-loss kill-switch halts new entries and (configurably) flattens open positions; halt state is visible in the dashboard and recorded.
- Feed disconnects (crypto) reuse Entropy's existing reconnect path; the bot continues on reconnect.
- Live adapter failures surface as English errors and never silently fall back to a different behavior.

## 10. Testing strategy

New `tests/bot/` following existing conventions (`asyncio_mode=auto`, hermetic, seeded, frozen-struct assertions):

- Paper executor fill, fee & slippage math.
- Portfolio PnL: realized, unrealized, equity, daily PnL reset.
- RiskManager: sizing (per profile), stop/TP trigger, daily-loss kill-switch, cooldown, per-symbol & total exposure caps, REJECT reasons.
- Both strategies: signal generation from events / EMA crossover.
- End-to-end integration: seeded sim feed → bot → ledger, deterministic and repeatable.
- Risk-profile change confirmation + ledger record.
- Live executor raises the English guard error when disabled / under-authorized.
- Existing 62 tests stay green; `ruff` + `mypy` clean.

## 11. Success criteria (measurable)

1. `python -m entropy.bot` runs headless and paper-trades the live crypto feed end-to-end.
2. Decision→paper-fill path is tick-synchronous and sub-ms (independent of the UI).
3. Full risk layer with 3 presets + Custom; dashboard shows an always-on colored risk banner; profile changes are confirmed + recorded.
4. Produces a verifiable trade ledger + equity curve (CSV/JSON).
5. Live-trading scaffold exists but is disabled, English-warned, and double-opt-in guarded.
6. All new tests + existing 62 tests green; `ruff` + `mypy` clean.

## 12. Out of scope (YAGNI)

- Real live-money execution wiring beyond the disabled scaffold (user enables manually later).
- Multi-leg / options / derivatives positions.
- Limit/stop order book simulation beyond market + synthetic stop/TP (paper fills at current price ± slippage).
- Distributed/multi-process execution.
- Web UI (terminal only).
