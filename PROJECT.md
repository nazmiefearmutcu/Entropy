# Project: Entropy TUI Frontend, Trade Bot Calibration & Accuracy Verification

## Architecture
Entropy is a market terminal with a live TUI, trading simulator, speed benchmarks, and strategy calibration tool.
- **TUI Frontend (`src/entropy/ui/`)**: Built on Textual, displays real-time price tick feeds, candlestick/line plots, trade signals, depth indicators, and logs. It uses custom styling and supports dynamic runtime theme changes across 7 distinct themes.
- **Trading Bot Engine (`src/entropy/bot/`)**: Simulates back/forward trading on synthetic tick datasets generated dynamically. Calibrates parameters (EMA periods, momentum threshold, risk rules) via grid search optimization.
- **Data Feeds (`src/entropy/feeds/`)**: Generates synthetic or pulls live market ticks.

## Code Layout
- `src/entropy/__main__.py`: CLI entry point (subcommands: `ui`, `bot`, `calibrate`, `benchmark`).
- `src/entropy/bot/calibration.py`: Trade bot calibration search and backtesting module.
- `src/entropy/ui/app.py`: Main Textual App initialization and coordinate wiring.
- `src/entropy/ui/widgets/`: Individual widget components (Header, PriceChart, VolumeChart, Gauges, StatusBar, Settings modal, etc.).
- `src/entropy/ui/theme.py`: Palette variables for the 7 themes.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Calibration Logic Fixes | Correct LONG trade PnL calculations and grid search KeyError robustness in `src/entropy/bot/calibration.py` | None | DONE |
| 2 | TUI Frontend Audit & Polish | Verify layout stability, dynamic theme applying, settings reactive changes (Chart style toggle, volume show/hide) | M1 | DONE |
| 3 | E2E CLI & Integration Verification | Validate `entropy calibrate` Rich tables and integration outputs | M2 | DONE |
| 4 | Final Verification & Hardening | Run challenger tests, coverage verification, and Forensic Audit check | M3 | DONE |
| 5 | Volatility & Safety Guards | Volatility dynamic thresholds, fat-finger guard, slippage guard, emergency circuit breaker, confirm risk screen dialog | M4 | DONE |
| 6 | Accuracy & Ship-Readiness | Consensus strategy default, cost-gate amortization, new accuracy knobs, real BTC 15m accuracy test vs Binance fees | M5 | DONE |

## Interface Contracts
### `entropy.bot.calibration` ↔ `entropy.__main__`
- `calibrate_and_test(n_ticks_back, n_ticks_forward, seed) -> dict`: Returns execution details, optimal parameter configuration, and performance metrics (final_equity, win_rate, total_trades, profit_factor, sharpe) for back and forward test windows.
- In case of unsuccessful grid search (no configuration meets minimal trade constraint), must not raise KeyError and should fallback gracefully or relax the constraint.

## Accuracy & Ship-Readiness (2026-08-03)

### Root cause fixed: cost gate was not amortized
`ConsensusStrategy` gated entries on raw `minimum_move` / `sigma_gate` per 15m bar
(≈52 bps/bar). With Binance spot costs (10 bps fee + 3 bps slippage per side, 26 bps
round trip) the gate was effectively unreachable, producing 502 trades with 8.6% win
rate and −17.09% in 30 days. The gate is now amortized over the regime window
(`minimum_move / regime_window`, `sigma_gate / regime_window`, ≈2.6 bps/bar), so it
fires on genuine structure instead of being a permanent veto. See
`src/entropy/bot/strategies/consensus.py` and `src/entropy/bot/costs.py`.

### New accuracy knobs (ConsensusConfig + CLI)
- `direction_bars` (default 0) / `direction_min_slope`: higher-timeframe EMA slope
  filters entries against `Regime.trend_dir`.
- `confirm_bars` (default 2): require consecutive confirmations before entry.
- `threshold` (default 0.5): consensus signal entry threshold.
- `exit_mode` (default `trail`): `score | trend_flip | either | hold | trail`.
- `trail_pct` (default 0.3): trailing exit distance (used with `exit_mode="trail"`).
- `min_hold_bars` (default 5) and `cooldown_bars` (default 4): churn control.
- `normalize` (default `total`), `move_floor` (default 0.0003), `cost_edge_mult` (default 1.0).
- Default strategy set is now `("consensus",)`; `ema_cross` no longer ships enabled.
- Accuracy script defaults: stop-loss 1.5%, take-profit 1.2%.

### Real-data results (Binance spot BTCUSDT, 15m bars, $100, 26 bps round trip)
| Run | Window | Return | Trades | Win rate | PF | Max DD |
|-----|--------|--------|--------|----------|----|--------|
| 30d before fix | 2026-07-04 → 08-03 | −17.09% | 502 | 8.6% | — | — |
| 7d before fix | 2026-07-27 → 08-03 | −3.82% | — | — | — | — |
| 30d final (ship default) | 2026-07-04 → 08-03 (2879 bars) | −0.12% ($99.88) | 29 | 62.1% | 0.94 | 0.58% |
| 7d final | 2026-07-27 → 08-03 | −0.03% ($99.97) | 11 | 45.5% | 0.92 | 0.26% |
| 1d final | 2026-08-01 | −0.03% ($99.97) | 1 | 0% | 0.0 | 0.10% |

Context: BTC HODL over the same 30d window returned +0.01%; the bot finished
≈0.97pp behind on a flat market. Daily kill switch never triggered; daily range
was +0.13% / −0.16%; max concurrent exposure observed 1 (theoretical 4);
commission paid $1.09 over 30d (8.35x notional turnover).

### Honest verdict
The churn/meltdown regime is gone: no 500-trade cost bleed, realistic fees, sane
drawdowns, and results are now noise-band around flat. That is shippable as a
paper/demo-quality simulator, **not** as a profit generator: no window or market
shows consistent net profit (positive OOS edges from the WR sweep are noise-band,
PF ≈ 0.9-1.0), and the 512-combo grid was uniformly negative. "+10%/day" is
not achievable with this signal family.

### Walk-forward & cross-market validation (2026-08-03, continued)
Walk-forward on 120d BTCUSDT (4 folds: train 45d / test 15d, 32-combo sweep,
rank by train return, min 20 trades) — `scripts/entropy_walk_forward.py`:

| Fold | Test window (15d) | Best-on-train OOS | Ship default OOS | HODL |
|------|-------------------|-------------------|------------------|------|
| 0 | 2026-05-20 → 06-04 | +0.84% (27t) | +0.90% (25t) | −17.24% |
| 1 | 2026-06-04 → 06-19 | −1.35% (24t) | −1.07% (22t) | −1.11% |
| 2 | 2026-06-19 → 07-04 | −1.83% (28t) | −1.83% (28t) | −0.80% |
| 3 | 2026-07-04 → 07-19 | −0.63% (22t) | −0.64% (22t) | +2.94% |
| **Chained 60d OOS** | | **−2.96%** | **−2.63%** | **−16.43%** |

Selection stability: the best-on-train config was the same family as the ship
default in every fold (pre-sweep ship default: trend_flip / confirm=2, hold 5-8)
— no overfitting whiplash, but also no differentiated edge. Full window matrix
(pre-sweep ship default):

| Market | Window | Bot | HODL | Bot vs HODL |
|--------|--------|-----|------|-------------|
| BTCUSDT | 30d (07-04 → 08-03) | −0.96% | +0.01% | −0.98pp |
| BTCUSDT | 60d (06-04 → 08-03) | −3.87% | −1.61% | −2.25pp |
| BTCUSDT | 90d (05-05 → 08-03) | −3.73% | −22.90% | **+19.17pp** |
| BTCUSDT | 120d (04-05 → 08-03) | −5.01% | −6.08% | **+1.07pp** |
| ETHUSDT | 30d (07-04 → 08-03) | −0.95% | +4.56% | −5.51pp |
| ETHUSDT | 60d (06-04 → 08-03) | −2.95% | +3.68% | −6.62pp |

Pattern: the strategy is defensively long-biased — it loses far less than HODL in
drawdown windows (BTC 90d: −3.73% vs −22.90%; walk-forward fold 0: +0.84% vs
−17.24%) but gives up rallies (ETH 30d: −0.95% vs +4.56%). Win rate 23-36%, PF
< 1 across all those windows (pre-sweep default): there is no exploitable
directional edge yet, but the
risk-controlled behavior is consistent across markets and windows — the honest
ship story is "risk control", not "returns".

### Win-rate sweep: WR > 50% on OOS (2026-08-03, agent waves)
Goal from the 2026-08-03 spec (`docs/superpowers/specs/2026-08-03-winrate-over-50-design.md`):
win rate above 50% with >= 20 trades on BOTH the recent 30d window and the
chained walk-forward OOS, without a hollow win (PF/return reported).

- Wave 1: 8 parallel agents swept 1,344 knob combos (exit_mode, trail_pct,
  confirm/direction bars, min_hold/cooldown, SL/TP) on a 60d BTC train
  (`scripts/entropy_wr_sweep.py`, sharded). Train WR peaked at 71.6% but with
  negative returns (PF 0.79) — the payoff-asymmetry trap.
- Wave 2: 6 parallel agents re-swept a refined space (384 combos; trail + trend_flip,
  TP 0.5/0.8, SL 1.5/2.0) on a clean head-60d train. Train WR reached 84.2%
  (TP=0.5/SL=2.0) but chained OOS was NEGATIVE for all of them (−0.96% to
  −2.60%): high win rate was hollow, paid for by 4:1 loss asymmetry + fees.
- Honest winners: the trail family with TP=1.2/SL=1.5 (moderate asymmetry)
  verified positive on OOS:

| Candidate (trail 0.3 / confirm 2 / hold 5 / cooldown 4 / SL 1.5 / TP 1.2) | 30d WR | 30d ret | Chained OOS |
|------|--------|---------|-------------|
| direction_bars=0 (SHIPPED default) | **60.7%** (28t) | −0.16% | **+0.62%** |
| direction_bars=20 (alternative) | **55.6%** (27t) | −0.57% | **+1.00%** |

Verification (`scripts/entropy_wr_verify.py`, 30d window + 4 walk-forward folds,
chained OOS): both candidates clear WR > 50% on the 30d window and end positive
on chained OOS. The shipped default (`trail / trail_pct=0.3 / direction_bars=0 /
min_hold_bars=5 / SL 1.5 / TP 1.2`) is now baked into `ConsensusConfig` and the
accuracy script; the final 30d run reports WR 62.1% (29 trades), PF 0.94,
max DD 0.58%, −0.12% return on $100 with Binance spot costs.

Honest caveat: WR > 50% is achieved OOS, but expectancy is still noise-band
around flat (PF ≈ 0.9-1.0). The strategy is not a profit generator; it now
qualifies on the accuracy metric without destroying capital.

### Reproduce
```bash
.venv/bin/python scripts/entropy_accuracy_btc15m.py --bars 2880 --out /tmp/entropy_accuracy/repro_30d
.venv/bin/python scripts/entropy_accuracy_btc15m.py --bars 672 --out /tmp/entropy_accuracy/repro_7d
.venv/bin/python scripts/entropy_accuracy_btc15m.py --bars 96 --end-date 2026-08-01T23:59:59.999000+00:00 --out /tmp/entropy_accuracy/repro_1d
.venv/bin/python scripts/entropy_grid_search.py   # 512-combo grid (train on first 2000 bars)
.venv/bin/python scripts/entropy_walk_forward.py --klines /tmp/entropy_accuracy/120d_btc/klines.json --fold 0 --out /tmp/entropy_accuracy/wf/fold0
.venv/bin/python scripts/entropy_accuracy_btc15m.py --symbol ETHUSDT --bars 2880 --out /tmp/entropy_accuracy/repro_eth_30d
```
`entropy_accuracy_btc15m.py --symbol` accepts any Binance spot pair (e.g.
ETHUSDT). Each run writes `report.json`, `trades.csv` and `console.log` into
`--out`; klines are cached. Full test suite (746 tests) passes.
