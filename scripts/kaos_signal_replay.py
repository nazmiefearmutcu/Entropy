#!/usr/bin/env python3
"""kaos_signal_replay.py — CANLI sinyal tekrarı (salt-okur tanı aracı).

Soru: "bot hâlâ neden işlem yapmadı?" — cevabı veriye döker. Canlı runner'ın
build_cfg'siyle BİREBİR aynı configi (yalnız long_only canlıdaki gibi False)
son N 15m barı üzerinde sembol başına çalıştırır; kaç giriş olurdu, ne zaman
olurdu, hangi yönde olurdu yazar. Canlı sürece/state'e DOKUNMAZ (throwaway
run_dir). 2026-09-08 "hâlâ işlem yok" soruşturması için yazıldı.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

_HARNESS = REPO / "scripts" / "entropy_accuracy_btc15m.py"
_spec = importlib.util.spec_from_file_location("entropy_accuracy_harness", _HARNESS)
ha = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ha)

from entropy.bot.config import (BotConfig, ConsensusConfig,  # noqa: E402
                                MarketCostConfig, RiskOverrides)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
           "ADAUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT", "NEARUSDT",
           "ENAUSDT", "LTCUSDT", "DOTUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
           "ATOMUSDT", "FILUSDT", "SEIUSDT"]
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 48.0
BARS = int(HOURS * 4) + 320          # 15m bar + warmup payı


def s20_cfg(symbols: tuple) -> BotConfig:
    """LivePaper.build_cfg BİREBİR (yalnız long_only=False = canlı gerçek)."""
    return BotConfig(
        mode="paper",
        starting_cash=100.0,
        strategies=("consensus",),
        symbols=symbols,
        ema_symbol=symbols[0],
        ema_fast=9,
        ema_slow=21,
        momentum_min_pct=0.15,
        timeframe="15m",
        bar_s=900.0,
        warmup=False,
        market_costs=MarketCostConfig(),
        cost_aware=True,
        cost_edge_mult=1.0,
        consensus=ConsensusConfig(
            threshold=0.5, exit_mode="trail", min_hold_bars=5,
            cooldown_bars=4, move_floor=0.0003, vote_mode="trend",
            normalize="total", min_participation=0.5, direction_bars=20,
            confirm_bars=2, trail_pct=0.3, max_hold_bars=192,
            long_only=False),
        risk_overrides=RiskOverrides(
            per_trade_pct=10.0, max_concurrent=4, stop_loss_pct=1.5,
            take_profit_pct=1.2, max_total_exposure_pct=40.0,
            max_daily_loss_pct=40.0, cooldown_s=180.0,
            min_volatility_pct=0.05, vol_window_s=900.0, stop_mode="sigma",
            stop_sigma_mult=20.0, tp_sigma_mult=4.0, risk_trail_pct=0.0,
            entry_grace_bars=3),
        console_log_path="", trade_csv_path="",
    )


def main() -> None:
    now_ms = int(time.time() * 1000)
    total_new = 0
    for sym in SYMBOLS:
        ks = ha.fetch_klines(BARS, now_ms, raw=sym)
        # son HOURS saati = değerlendirme penceresi, kalanı warmup
        window = int(HOURS * 4)
        with tempfile.TemporaryDirectory() as td:
            m = ha.simulate(ks, s20_cfg((sym,)), run_dir=str(Path(td) / "l"),
                            trade_csv=str(Path(td) / "t.csv"), symbol=sym,
                            warmup_bars=max(0, len(ks) - window))
        trades = m.get("trades") or []
        rows = []
        for t in trades:
            rows.append({"symbol": sym,
                         "side": t.get("side"),
                         "entry_utc": t.get("entry_ts"),
                         "exit_utc": t.get("exit_ts"),
                         "exit_reason": t.get("exit_intent"),
                         "pnl_pct": t.get("pnl_pct")})
        n_open = int(m.get("open_positions") or 0)
        total_new += len(rows)
        print(f"== {sym}: pencere({HOURS:.0f}h) içinde {len(rows)} giriş "
              f"(+{n_open} açık kaldı) ==", flush=True)
        for r in rows[-8:]:
            print("   ", json.dumps(r, ensure_ascii=False), flush=True)
    print(f"\nTOPLAM: {HOURS:.0f} saatlik pencerede {total_new} giriş sinyali "
          f"(5 sembol, long+short)", flush=True)


if __name__ == "__main__":
    main()
