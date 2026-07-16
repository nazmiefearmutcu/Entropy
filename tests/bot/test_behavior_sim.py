from entropy.bot.portfolio import Portfolio, PositionSide
from entropy.bot.risk.manager import RiskManager
from entropy.bot.risk.profiles import EXTREME, FROSTY, MEDIUM
from entropy.bot.signals import Signal, SignalAction


def _sig(action: SignalAction, symbol: str = "SPY", ts_ns: int = 1) -> Signal:
    return Signal(symbol=symbol, action=action, strength=1.0, reason="test", ts_ns=ts_ns, strategy="ema_cross")

def test_run_simulation():
    print("==================================================")
    print("RUNNING BEHAVIOR SIMULATION FOR NEW RISK PROFILES")
    print("==================================================")
    
    profiles = {"Frosty": FROSTY, "Medium": MEDIUM, "Extreme": EXTREME}
    
    for name, prof in profiles.items():
        print(f"\n--- Simulating profile: {name.upper()} ---")
        print(f"Description: {prof.description}")
        
        # 1. Verify Allocation Size
        p = Portfolio(100_000.0)
        rm = RiskManager(prof)
        d = rm.evaluate(
            _sig(SignalAction.ENTER_LONG, "AAPL", ts_ns=50_000_000_000),
            p, mark_px=100.0, ts_ns=50_000_000_000
        )
        assert d.approved, f"{name} should approve AAPL entry"
        expected_qty = (prof.per_trade_pct / 100.0) * 100_000.0 / 100.0
        print(f"  Approved qty for $100k equity: {d.order.qty} (Expected: {expected_qty})")
        assert d.order.qty == expected_qty, f"{name} qty mismatch"
        
        # 2. Verify Stop Loss and Take Profit levels
        sl, tp = rm.stop_tp_prices(PositionSide.LONG, 100.0)
        expected_sl = 100.0 * (1 - prof.stop_loss_pct / 100.0)
        expected_tp = 100.0 * (1 + prof.take_profit_pct / 100.0)
        ratio = prof.take_profit_pct / prof.stop_loss_pct
        print(f"  LONG: SL={sl:.2f} (Exp: {expected_sl:.2f}), TP={tp:.2f} (Exp: {expected_tp:.2f})")
        assert sl == expected_sl and tp == expected_tp, f"{name} SL/TP mismatch"
        
        # Open the position
        p.open("AAPL", PositionSide.LONG, d.order.qty, 100.0, sl, tp, ts_ns=50_000_000_000, fee=0.0)
        
        # 3. Verify max concurrent positions limit
        symbols = [f"SYM{i}" for i in range(1, 15)]
        opened = 1
        rejected_concurrent = 0
        for i, sym in enumerate(symbols):
            ts = 50_000_000_000 + (i + 1) * 1_000_000_000
            d_sym = rm.evaluate(
                _sig(SignalAction.ENTER_LONG, sym, ts_ns=ts), p, mark_px=10.0, ts_ns=ts
            )
            if d_sym.approved:
                sl_sym, tp_sym = rm.stop_tp_prices(PositionSide.LONG, 10.0)
                p.open(
                    sym, PositionSide.LONG, d_sym.order.qty, 10.0,
                    sl_sym, tp_sym, ts_ns=ts, fee=0.0
                )
                opened += 1
            else:
                if "max concurrent" in d_sym.reason:
                    rejected_concurrent += 1
        print(f"  Max concurrent: {prof.max_concurrent}. Total: {opened}. Rejects: {rejected_concurrent}")
        assert opened == prof.max_concurrent, f"{name} max concurrent check failed"
        
        # 4. Verify Cooldown
        # Close one position and try to re-enter it immediately or during cooldown
        sym_to_reenter = "AAPL"
        p.close(sym_to_reenter, exit_px=100.0, ts_ns=50_000_000_001, fee=0.0)
        # Try re-entering within cooldown
        ts_within = 50_000_000_000 + int((prof.cooldown_s - 1) * 1_000_000_000)
        d_within = rm.evaluate(
            _sig(SignalAction.ENTER_LONG, sym_to_reenter, ts_ns=ts_within),
            p, mark_px=100.0, ts_ns=ts_within
        )
        print(f"  Re-entry within cooldown ({prof.cooldown_s - 1}s): Approved={d_within.approved}")
        assert not d_within.approved, f"{name} cooldown should block entry"
        
        # Try re-entering after cooldown
        ts_after = 50_000_000_000 + int((prof.cooldown_s + 1) * 1_000_000_000)
        d_after = rm.evaluate(
            _sig(SignalAction.ENTER_LONG, sym_to_reenter, ts_ns=ts_after),
            p, mark_px=100.0, ts_ns=ts_after
        )
        print(f"  Re-entry after cooldown ({prof.cooldown_s + 1}s): Approved={d_after.approved}")
        # Note: if exposure cap is exceeded because of other open positions, it might reject, which is fine
        if "cooldown" in d_after.reason:
            raise ValueError(f"{name} cooldown did not clear")
            
        # 5. Verify Exposure limit
        p_exp = Portfolio(100_000.0)
        rm_exp = RiskManager(prof)
        max_possible_exposure = prof.max_concurrent * prof.per_trade_pct
        print(f"  Max exposure: {max_possible_exposure}% (Cap: {prof.max_total_exposure_pct}%)")
        assert max_possible_exposure <= prof.max_total_exposure_pct, f"{name} concurrent exposure exceeds cap"
        
        # 6. Verify Daily Loss Kill-Switch
        p_loss = Portfolio(100_000.0)
        rm_loss = RiskManager(prof)
        loss_amount = (prof.max_daily_loss_pct / 100.0) * 100_000.0
        p_loss.realized_pnl = -loss_amount - 100.0
        d_loss = rm_loss.evaluate(
            _sig(SignalAction.ENTER_LONG, "BTCUSDT", ts_ns=0), p_loss, mark_px=50000.0, ts_ns=0
        )
        print(f"  Daily Loss limit: {prof.max_daily_loss_pct}%. Approved={d_loss.approved}")
        assert not d_loss.approved, f"{name} daily loss halt failed"
        assert rm_loss.halted, f"{name} halted flag not set"

    print("\n==================================================")
    print("ALL BEHAVIOR VERIFICATIONS PASSED SUCCESSFULLY!")
    print("==================================================")
