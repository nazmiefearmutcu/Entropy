from __future__ import annotations

import math
import random
import time
from typing import Any

from entropy.bot.config import BotConfig
from entropy.bot.orders import Fill, OrderIntent, OrderSide
from entropy.bot.portfolio import PositionSide
from entropy.bot.risk.profiles import make_custom
from entropy.bot.runner import BotRunner
from entropy.feeds.crypto import BINANCE_MAJORS
from entropy.feeds.equities.sim import EquitySimulator, SymRuntime
from entropy.feeds.equities.universe import UNIVERSE, SymParams


class DummyLedger:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.fills: list[tuple[Any, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.rejects: list[tuple[str, str]] = []

    def record_event(self, kind: str, payload: dict[str, Any]) -> None:
        self.events.append((kind, payload))

    def record_fill(self, fill: Any, intent: Any) -> None:
        self.fills.append((fill, intent))

    def record_equity(self, snap: Any) -> None:
        pass

    def record_reject(self, symbol: str, reason: str) -> None:
        self.rejects.append((symbol, reason))

    def record_risk_change(self, old: str, new: str) -> None:
        pass

    def record_trade_open(self, symbol: str, side: str, price: float) -> None:
        pass

    def record_trade_close(self, symbol: str, side: str, price: float) -> None:
        pass


def generate_ticks(
    symbols: list[str],
    n_ticks: int,
    seed: int = 42
) -> list[dict[str, Any]]:
    """Generate price ticks using GBM simulator with both equities and crypto parameters."""
    rng = random.Random(seed)
    sim = EquitySimulator(rng, lambda: 0)
    
    # Populate params for any crypto symbols
    for sym in symbols:
        if sym not in sim.params:
            # High volatility for crypto
            is_btc = "BTC" in sym or "ETH" in sym
            s0 = rng.uniform(30000.0, 70000.0) if is_btc else rng.uniform(10.0, 500.0)
            sim.params[sym] = SymParams(
                s0=s0,
                sigma_bps=rng.uniform(6.0, 18.0),
                drift_bps=rng.uniform(-0.1, 0.1),
                mr_kappa=rng.uniform(0.005, 0.02),
                base_size=rng.uniform(1.0, 10.0),
                sector="crypto"
            )
            sim.rt[sym] = SymRuntime(px=s0, anchor=s0, sess_high=s0, sess_low=s0)

    ticks = []
    base_ts = 1_700_000_000_000_000_000 # dummy timestamp in ns
    
    for i in range(n_ticks):
        # Inject occasional events
        sim.maybe_inject_events()
        sym = rng.choice(symbols)
        s, px, size, side = sim.step_symbol(sym)
        ticks.append({
            "symbol": s,
            "price": px,
            "amount": float(size),
            "side": side,
            "ts_ns": base_ts + i * 1_000_000_000 # 1 second increments
        })
    return ticks


def run_backtest(
    ticks: list[dict[str, Any]],
    symbols: list[str],
    fast: int,
    slow: int,
    min_pct: float,
    stop_loss_pct: float,
    take_profit_pct: float
) -> dict[str, Any]:
    """Runs a fast in-memory backtest with specified configuration."""
    cfg = BotConfig(
        mode="paper",
        risk_profile="medium",
        strategies=("momentum_scalper", "ema_cross"),
        symbols=tuple(symbols),
        ema_symbol=symbols[0], # use the first symbol for EMA cross
        ema_fast=fast,
        ema_slow=slow,
        momentum_min_pct=min_pct,
        starting_cash=100_000.0,
        fee_bps=1.0,
        slippage_bps=1.0,
        enable_crypto=False,
        enable_equities=False
    )
    
    runner = BotRunner(cfg)
    # Patch ledger to use DummyLedger (no disk writes)
    dummy_ledger = DummyLedger()
    runner.ledger = dummy_ledger  # type: ignore[assignment]
    
    # Configure custom risk profile
    custom_profile = make_custom(
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct
    )
    runner.risk.set_profile(custom_profile)

    # Feed ticks
    for tick in ticks:
        runner.on_trade(tick["symbol"], tick["price"], tick["amount"], tick["side"], tick["ts_ns"])

    # Close all remaining open positions at final prices. The close bypasses the
    # executor, so record an equivalent synthetic CLOSE fill into the dummy ledger:
    # otherwise these liquidations are counted in final_equity but invisible to
    # win_rate/profit_factor/sharpe/total_trades (their OPEN fills never pair up).
    final_ts = ticks[-1]["ts_ns"] if ticks else 0
    for symbol in list(runner.portfolio.positions):
        pos = runner.portfolio.positions[symbol]
        mark_px = runner.portfolio.mark_of(symbol)
        close_side = OrderSide.SELL if pos.side is PositionSide.LONG else OrderSide.BUY
        dummy_ledger.record_fill(
            Fill(order_id=f"liq-{symbol}", symbol=symbol, side=close_side, qty=pos.qty,
                 price=mark_px, fee=0.0, slippage=0.0, ts_ns=final_ts),
            OrderIntent.CLOSE,
        )
        runner.portfolio.close(symbol, mark_px, final_ts, fee=0.0)

    # Calculate metrics
    snap = runner.portfolio.snapshot(final_ts)
    total_trades = len(dummy_ledger.fills) // 2  # open and close fill pairs
    wins = 0
    losses = 0
    total_profit = 0.0
    total_loss = 0.0
    
    # Process fills from dummy ledger to compute win/loss statistics
    closed_pnls = []
    # Match entries and exits
    positions_history = {}
    for fill, intent in dummy_ledger.fills:
        symbol = fill.symbol
        if intent.value == "open":
            positions_history[symbol] = fill
        else:
            if symbol in positions_history:
                entry_fill = positions_history.pop(symbol)
                qty = fill.qty
                entry_px = entry_fill.price
                exit_px = fill.price
                entry_fee = entry_fill.fee
                exit_fee = fill.fee
                if entry_fill.side.value == "buy":  # LONG
                    pnl = (exit_px - entry_px) * qty - entry_fee - exit_fee
                else:  # SHORT
                    pnl = (entry_px - exit_px) * qty - entry_fee - exit_fee
                closed_pnls.append(pnl)
                if pnl > 0:
                    wins += 1
                    total_profit += pnl
                else:
                    losses += 1
                    total_loss += abs(pnl)


    win_rate = wins / total_trades if total_trades > 0 else 0.0
    if total_loss > 0:
        profit_factor = total_profit / total_loss
    else:
        profit_factor = total_profit if total_profit > 0 else 1.0
    
    # Calculate Sharpe ratio on closed trade PnLs
    if len(closed_pnls) > 1:
        mean_pnl = sum(closed_pnls) / len(closed_pnls)
        variance = sum((x - mean_pnl) ** 2 for x in closed_pnls) / (len(closed_pnls) - 1)
        std_pnl = math.sqrt(variance)
        sharpe = (mean_pnl / std_pnl) * math.sqrt(252) if std_pnl > 0 else 0.0
    else:
        sharpe = 0.0

    return {
        "final_equity": snap.equity,
        "total_return": (snap.equity / 100_000.0) - 1.0,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "sharpe": sharpe,
        "closed_pnls": closed_pnls
    }


def calibrate_and_test(
    n_ticks_back: int = 15000,
    n_ticks_forward: int = 15000,
    seed: int = 42
) -> dict[str, Any]:
    """Calibrates trade bot parameters and tests on back/forward data using random symbols."""
    # Choose random symbols from each market
    # 3 random from Equities (excluding indices to make it diverse)
    equities_pool = [s for s in UNIVERSE if s not in ("SPY", "QQQ", "IWM")]
    random.seed(seed)
    selected_equities = random.sample(equities_pool, 3)
    # 3 random from Crypto
    selected_crypto = random.sample(list(BINANCE_MAJORS), 3)
    
    symbols = selected_equities + selected_crypto
    print(f"Selected Equities: {selected_equities}")
    print(f"Selected Crypto: {selected_crypto}")
    
    # Generate back and forward datasets
    print("Generating simulation datasets...")
    back_ticks = generate_ticks(symbols, n_ticks_back, seed=seed)
    forward_ticks = generate_ticks(symbols, n_ticks_forward, seed=seed + 100)

    # Grid search parameter space
    fast_grid = [5, 9, 12]
    slow_grid = [15, 21, 30]
    min_pct_grid = [0.10, 0.15, 0.25]
    sl_grid = [0.5, 1.0]
    tp_grid = [1.0, 2.0]

    best_score = -math.inf
    best_params: dict[str, Any] = {}
    
    n_combos = len(fast_grid) * len(slow_grid) * len(min_pct_grid) * len(sl_grid) * len(tp_grid)
    print(f"Running grid search over {n_combos} combinations...")
    t0 = time.perf_counter()
    
    for fast in fast_grid:
        for slow in slow_grid:
            if slow <= fast:
                continue
            for min_pct in min_pct_grid:
                for sl in sl_grid:
                    for tp in tp_grid:
                        res = run_backtest(back_ticks, symbols, fast, slow, min_pct, sl, tp)
                        # We optimize for Sharpe + Total Return
                        score = (
                            res["total_return"] * 10.0
                            + res["sharpe"]
                            - (res["total_trades"] * 0.02)
                        )
                        if score > best_score and res["total_trades"] >= 2:
                            best_score = score
                            best_params = {
                                "fast": fast,
                                "slow": slow,
                                "min_pct": min_pct,
                                "stop_loss_pct": sl,
                                "take_profit_pct": tp
                            }
    
    t_search = time.perf_counter() - t0
    print(f"Grid search completed in {t_search:.2f} seconds.")
    if not best_params:
        best_params = {
            "fast": 9,
            "slow": 21,
            "min_pct": 0.15,
            "stop_loss_pct": 1.0,
            "take_profit_pct": 2.0
        }
    print(f"Best parameters: {best_params}")

    # Evaluate best parameters on Backtest dataset (In-Sample)
    back_results = run_backtest(
        back_ticks, symbols,
        best_params["fast"], best_params["slow"], best_params["min_pct"],
        best_params["stop_loss_pct"], best_params["take_profit_pct"]
    )
    
    # Evaluate best parameters on Forward Test dataset (Out-of-Sample)
    forward_results = run_backtest(
        forward_ticks, symbols,
        best_params["fast"], best_params["slow"], best_params["min_pct"],
        best_params["stop_loss_pct"], best_params["take_profit_pct"]
    )

    return {
        "symbols": {
            "equities": selected_equities,
            "crypto": selected_crypto
        },
        "best_params": best_params,
        "back_results": back_results,
        "forward_results": forward_results
    }
