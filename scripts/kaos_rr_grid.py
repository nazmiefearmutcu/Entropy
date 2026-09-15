#!/usr/bin/env python3
"""kaos_rr_grid.py — KAOS agent A: stop/TP sigma-multiplier grid evidence.

READ-ONLY on repo code: this script only RUNS compute via the harness
(scripts/entropy_accuracy_btc15m.py fetch_klines + simulate) and writes a
results JSONL + an incremental markdown report. It never edits source files.

REDUCED MISSION (A2 relaunch, 2026-09-09): 10 symbols x 3 sequential ~30d
windows x 9 (stop_sigma_mult, tp_sigma_mult) variants (baseline + 8).
Baseline = shipped live config (scripts/entropy_live_paper.py build_cfg,
stop=20.0*sigma, tp=4.0*sigma, max_daily_loss_pct=15.0 as of 2026-09-09).

Method (pre-registered, mechanical):
  * config = build_cfg VERBATIM (long_only=False, threshold 0.5, min_hold 5,
    cooldown 4, direction_bars 20, confirm 2, trail 0.3, max_hold 192,
    per_trade 10%, max_concurrent 4, exposure 40%, daily-loss halt 15%,
    cooldown_s 180, min_vol 0.05, entry_grace 3) — ONLY the two sigma
    multipliers change per variant.
  * data: ~90d of 15m bars per symbol (8960 = 320 warmup + 3x2880), cached
    under reports/rr-grid-cache/ (end_ms reused from cache if present).
  * windows: w0 (oldest), w1, w2 (newest), each 2880 evaluated bars with 320
    bars of preceding context as warmup.
  * metrics per (variant, window) pooled over symbols: n, win%, compounded
    (prod(1+pnl_pct/100)-1), stop%/tp%/time-stop%.
  * DECISION RULE (pre-registered): variant replaces baseline iff pooled
    compounded >= baseline's AND worst-window compounded >= baseline's worst
    window AND n >= 60% of baseline n. Among passers pick highest
    worst-window. If none pass -> BASELINE STAYS.
  * report written INCREMENTALLY to
    C:/botmonitor/reports/kaos-upgrade-20260909/A-rr-evidence.md after every
    cell lands; hard compute stop at 02:45 local (partial = honest k/3 note).

Usage:
  python scripts/kaos_rr_grid.py --timing-test   # 2 sanity cells + elapsed
  python scripts/kaos_rr_grid.py                 # full pre-registered grid
  python scripts/kaos_rr_grid.py --windows 2     # fallback: latest 60d only
  python scripts/kaos_rr_grid.py --resume        # skip cells already in JSONL
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import pickle
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
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
           "ADAUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT"]

# Pre-registered grid; (20, 4) is the shipped baseline. First = baseline.
VARIANTS: list[tuple[float, float]] = [
    (20.0, 4.0),   # baseline (shipped s20/tp4)
    (8.0, 4.0),
    (10.0, 4.0),
    (12.0, 4.0),
    (8.0, 6.0),
    (10.0, 6.0),
    (6.0, 6.0),
    (10.0, 8.0),
    (20.0, 8.0),
]

WARMUP = 320          # warmup bars before each evaluated window
W = 2880              # evaluated bars per window (30 days of 15m)
TOTAL_BARS = WARMUP + 3 * W   # 8960 (~93.3 days)
CACHE_DIR = REPO / "reports" / "rr-grid-cache"
RESULTS_JSONL = CACHE_DIR / "results.jsonl"
REPORT_PATH = Path(r"C:\botmonitor\reports\kaos-upgrade-20260909\A-rr-evidence.md")
MAX_HOLD_BARS = 192
BAR_S = 900.0
COMPUTE_DEADLINE_HM = (2, 45)   # hard compute stop, local time


def deadline_epoch() -> float:
    t = datetime.now()
    d = t.replace(hour=COMPUTE_DEADLINE_HM[0], minute=COMPUTE_DEADLINE_HM[1],
                  second=0, microsecond=0)
    return d.timestamp()


def cached_end_ms() -> int:
    """Reuse the dead-agent fetch: end_ms from an existing cache meta if any."""
    for s in SYMBOLS:
        p = CACHE_DIR / f"{s}.json"
        if p.exists():
            meta = json.loads(p.read_text()).get("meta", {})
            if meta.get("bars") == TOTAL_BARS and meta.get("raw") == s:
                return int(meta["end_ms"])
    return int(time.time() * 1000)


def run_cfg(symbol: str, stop_mult: float, tp_mult: float) -> BotConfig:
    """entropy_live_paper.py build_cfg VERBATIM for a single symbol, with ONLY
    stop_sigma_mult / tp_sigma_mult changed (paths emptied as in the
    kaos_signal_replay throwaway-run precedent). max_daily_loss_pct=15.0
    matches the live build_cfg as of 2026-09-09."""
    symbols = (ha.resolve_symbol(symbol),)
    return BotConfig(
        mode="paper",
        starting_cash=100.0,
        strategies=("consensus",),
        symbols=symbols,
        ema_symbol=symbols[0],
        ema_fast=9,
        momentum_min_pct=0.15,
        ema_slow=21,
        timeframe="15m",
        bar_s=900.0,
        warmup=False,
        market_costs=MarketCostConfig(),
        cost_aware=True,
        cost_edge_mult=1.0,
        consensus=ConsensusConfig(
            threshold=0.5,
            exit_mode="trail",
            min_hold_bars=5,
            cooldown_bars=4,
            move_floor=0.0003,
            vote_mode="trend",
            normalize="total",
            min_participation=0.5,
            direction_bars=20,
            confirm_bars=2,
            trail_pct=0.3,
            max_hold_bars=192,
            long_only=False,
        ),
        risk_overrides=RiskOverrides(
            per_trade_pct=10.0,
            max_concurrent=4,
            stop_loss_pct=1.5,
            take_profit_pct=1.2,
            max_total_exposure_pct=40.0,
            max_daily_loss_pct=15.0,
            cooldown_s=180.0,
            min_volatility_pct=0.05,
            vol_window_s=900.0,
            stop_mode="sigma",
            stop_sigma_mult=stop_mult,
            tp_sigma_mult=tp_mult,
            risk_trail_pct=0.0,
            entry_grace_bars=3,
        ),
        console_log_path="",
        trade_csv_path="",
    )


def get_klines(raw: str, end_ms: int) -> list[list]:
    """Fetch TOTAL_BARS 15m bars (cached per symbol in reports/rr-grid-cache)."""
    return ha.fetch_klines(TOTAL_BARS, end_ms, cache=CACHE_DIR / f"{raw}.json",
                           raw=raw)


def trade_metrics(trades: list[dict]) -> dict:
    """Pooled metrics over a list of harness trade dicts."""
    n = len(trades)
    if n == 0:
        return {"n": 0, "win_rate": None, "mean_pnl_pct": None,
                "comp_pct": None, "worst_pnl_pct": None,
                "stop_rate": None, "tp_rate": None, "time_stop_rate": None}
    pnls = [float(t["pnl_pct"]) for t in trades]
    wins = sum(1 for p in pnls if p > 0)          # harness: pnl > 0 is a win
    comp = 1.0
    for p in pnls:
        comp *= (1.0 + p / 100.0)
    stops = sum(1 for t in trades if t["exit_intent"] == "stop")
    tps = sum(1 for t in trades if t["exit_intent"] == "take_profit")
    time_stops = 0
    for t in trades:
        if t["exit_intent"] == "close":
            held_s = (datetime.fromisoformat(t["exit_ts"])
                      - datetime.fromisoformat(t["entry_ts"])).total_seconds()
            if held_s >= MAX_HOLD_BARS * BAR_S:
                time_stops += 1
    return {"n": n,
            "win_rate": wins / n,
            "mean_pnl_pct": sum(pnls) / n,
            "comp_pct": (comp - 1.0) * 100.0,
            "worst_pnl_pct": min(pnls),
            "stop_rate": stops / n,
            "tp_rate": tps / n,
            "time_stop_rate": time_stops / n}


def run_cell(ks: list[list], sym: str, wi: int, cfg, tmp: str) -> dict:
    """One (variant, symbol, window) simulate run. Window wi in 0..2
    (0 = oldest month); slice keeps WARMUP bars of context before the window."""
    lo = wi * W
    hi = min(lo + WARMUP + W, len(ks))
    sl = ks[max(0, lo):hi]
    wb = min(WARMUP, len(sl) - 1)
    sym_resolved = ha.resolve_symbol(sym)  # tick label must match cfg.symbols
    with tempfile.TemporaryDirectory(prefix="kaosg_", dir=tmp,
                                     ignore_cleanup_errors=True) as td:
        with contextlib.redirect_stdout(io.StringIO()):
            rep = ha.simulate(sl, cfg,
                              run_dir=str(Path(td) / "l"),
                              trade_csv=str(Path(td) / "t.csv"),
                              symbol=sym_resolved, warmup_bars=wb,
                              entry_grace_bars=3)  # live config entry grace
    return {"symbol": sym, "variant": [cfg.risk_overrides.stop_sigma_mult,
                                       cfg.risk_overrides.tp_sigma_mult],
            "window": wi,
            "bars_eval": len(sl) - wb, "warmup_bars": wb,
            "halted": bool(rep["metrics"]["halted"]),
            "trades": rep["trades"]}


def worker(sym: str, end_ms: int, stop_m: float, tp_m: float, windows: int,
           root: str, cfg_blob: bytes) -> list[dict]:
    """All windows for ONE (symbol, variant). cfg arrives pickled, built ONCE
    per variant in the parent (mission perf advice)."""
    ks = get_klines(sym, end_ms)
    cfg = pickle.loads(cfg_blob)
    rows = []
    for wi in range(windows):
        lo = wi * W
        if len(ks) < lo + WARMUP + 2:   # not enough history for this window
            rows.append({"symbol": sym, "variant": [stop_m, tp_m],
                         "window": wi, "bars_eval": 0, "warmup_bars": 0,
                         "halted": False, "trades": [],
                         "skipped": "short_history"})
            continue
        rows.append(run_cell(ks, sym, wi, cfg, root))
    return rows


# ---------------- incremental report ----------------

def aggregate(rows: list[dict], variants: list, windows: int) -> dict:
    agg: dict = {}
    for r in rows:
        if r.get("skipped"):
            continue
        agg.setdefault((tuple(r["variant"]), r["window"]), []).append(r)
    out = {}
    for v in variants:
        per_win = {}
        for wi in range(windows):
            rs = agg.get((tuple(v), wi), [])
            trades = [t for r in rs for t in r["trades"]]
            m = trade_metrics(trades)
            m["runs"] = len(rs)
            m["halted_runs"] = sum(1 for r in rs if r["halted"])
            per_win[wi] = m
        all_trades = [t for wi in per_win for t in
                      [t for r in agg.get((tuple(v), wi), []) for t in r["trades"]]]
        pooled = trade_metrics(all_trades)
        pooled["halted_runs"] = sum(per_win[wi]["halted_runs"] for wi in range(windows))
        comps = [per_win[wi]["comp_pct"] for wi in range(windows)
                 if per_win[wi]["comp_pct"] is not None]
        pooled["worst_window_comp"] = min(comps) if comps else None
        pooled["windows_done"] = len(comps)
        pooled["cells_done"] = sum(per_win[wi]["runs"] for wi in range(windows))
        pooled["cells_total"] = len(SYMBOLS) * windows
        out[tuple(v)] = {"per_win": per_win, "pooled": pooled}
    return out


def verdict(agg: dict, variants: list) -> tuple[str, list]:
    """Mechanical pre-registered decision rule. Returns (verdict, lines)."""
    base = tuple(variants[0])
    if base not in agg:
        return "PENDING", ["baseline not finished yet"]
    ba = agg[base]["pooled"]
    if ba["n"] == 0:
        return "PENDING", ["baseline has 0 trades"]
    if ba["cells_done"] < ba["cells_total"]:
        return "PENDING", [f"baseline incomplete ({ba['cells_done']}/"
                           f"{ba['cells_total']} cells)"]
    bw = ba["worst_window_comp"]
    lines = []
    passers = []
    for v in variants[1:]:
        if v not in agg:
            continue
        a = agg[v]["pooled"]
        if a["cells_done"] < a["cells_total"] or ba["cells_done"] < ba["cells_total"]:
            lines.append(f"- ({v[0]},{v[1]}): INCOMPLETE "
                         f"({a['cells_done']}/{a['cells_total']} cells) — not evaluated")
            continue
        ok_a = a["comp_pct"] >= ba["comp_pct"]
        ok_b = (a["worst_window_comp"] or -1e9) >= (bw or -1e9)
        ok_c = a["n"] >= 0.6 * ba["n"]
        lines.append(f"- ({v[0]},{v[1]}): pooled_comp={a['comp_pct']:.2f}% "
                     f"({'PASS' if ok_a else 'FAIL'}), worst_w={a['worst_window_comp']:.2f}% "
                     f"({'PASS' if ok_b else 'FAIL'}), n={a['n']} vs 60%*{ba['n']}="
                     f"{0.6 * ba['n']:.0f} ({'PASS' if ok_c else 'FAIL'})")
        if ok_a and ok_b and ok_c:
            passers.append(((a["worst_window_comp"] or 0), a["comp_pct"], a["n"], v))
    if passers:
        passers.sort(reverse=True)
        wwt, comp, ntr, winv = passers[0]
        return (f"WINNER stop_sigma_mult={winv[0]} tp_sigma_mult={winv[1]}", lines)
    if all(tuple(v) in agg and agg[tuple(v)]["pooled"]["cells_done"] >=
           agg[tuple(v)]["pooled"]["cells_total"] for v in variants[1:]):
        return "BASELINE-STAYS (no variant passed all three gates)", lines
    return "RUNNING", lines


def write_report(agg: dict, variants: list, windows: int, status: str,
                 notes: list[str]) -> str:
    L = []
    L.append("# KAOS A — stop/TP sigma-multiplier evidence grid")
    L.append("")
    L.append(f"*Status: **{status}** — last updated "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} local*")
    L.append("")
    L.append("- Baseline = live KAOS config (scripts/entropy_live_paper.py build_cfg): "
             "stop=20.0σ, tp=4.0σ, max_daily_loss_pct=15.0, long_only=False, "
             "entry_grace=3, harness=scripts/entropy_accuracy_btc15m.py simulate().")
    L.append(f"- Data: {len(SYMBOLS)} Binance-spot symbols x ~90d of 15m bars, "
             "3 sequential ~30d windows (w0 oldest → w2 newest), 320-bar warmup "
             "preceding each window; pessimistic harness (4 ticks/bar, L/H ordered "
             "by open direction, costs on).")
    L.append("- Pre-registered rule: variant replaces baseline iff pooled compounded "
             "≥ baseline's AND worst-window compounded ≥ baseline's worst AND "
             "n ≥ 60% of baseline n; tie-break = highest worst-window. "
             "No passer → BASELINE-STAYS.")
    L.append("")
    hdr = (f"| variant | cells | n | win% | pooled comp% | worst win comp% | "
           f"w0 comp% | w1 comp% | w2 comp% | stop% | tp% | time-stop% | halted runs |")
    L.append(hdr)
    L.append("|" + "---|" * (hdr.count("|") - 1))
    for v in variants:
        a = agg.get(tuple(v))
        if not a:
            L.append(f"| ({v[0]},{v[1]}) | pending | - | - | - | - | - | - | - | - | - | - | - |")
            continue
        p, pw = a["pooled"], a["per_win"]

        def fmt(x, pct=True):
            return "-" if x is None else (f"{x:.2f}" if pct else f"{x:.0f}")
        wcs = [fmt(pw[wi]["comp_pct"]) if wi in pw else "-" for wi in range(windows)]

        def rate(x):
            return "-" if x is None else f"{x * 100:.1f}"
        L.append(f"| ({v[0]},{v[1]}) | {p['cells_done']}/{p['cells_total']} "
                 f"| {fmt(p['n'], pct=False)} | {rate(p['win_rate'])} "
                 f"| {fmt(p['comp_pct'])} | {fmt(p['worst_window_comp'])} "
                 f"| {wcs[0] if len(wcs) > 0 else '-'} | {wcs[1] if len(wcs) > 1 else '-'} "
                 f"| {wcs[2] if len(wcs) > 2 else '-'} "
                 f"| {rate(p['stop_rate'])} | {rate(p['tp_rate'])} "
                 f"| {rate(p['time_stop_rate'])} | {p['halted_runs']} |")
    L.append("")
    L.append("## Decision gates vs baseline")
    L.append("")
    vrd, lines = verdict(agg, variants)
    L.extend(lines)
    if notes:
        L.append("")
        L.append("## Notes")
        L.extend(f"- {n}" for n in notes)
    L.append("")
    L.append(f"## VERDICT (mechanical, pre-registered): **{vrd}**")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(L) + "\n", encoding="utf-8")
    return vrd


def timing_test(end_ms: int) -> None:
    """Sanity: 2 cells (BTCUSDT, SUIUSDT @ w2) baseline — trades>0, entry_ts
    inside the window, exit-intent histogram, elapsed per run."""
    tmp = tempfile.mkdtemp(prefix="kaosg_sanity_")
    for sym in ("BTCUSDT", "SUIUSDT"):
        ks = get_klines(sym, end_ms)
        cfg = run_cfg(sym, 20.0, 4.0)
        t0 = time.time()
        r = run_cell(ks, sym, 2, cfg, tmp)
        dt = time.time() - t0
        lo = 2 * W
        sl = ks[lo:lo + WARMUP + W]
        win_start_ms = sl[WARMUP][0]
        win_end_ms = sl[-1][6]
        trs = r["trades"]
        ets = [datetime.fromisoformat(t["entry_ts"]) for t in trs]
        in_win = all(win_start_ms / 1000 <= e.timestamp() <= win_end_ms / 1000
                     for e in ets)
        hist: dict = {}
        for t in trs:
            hist[t["exit_intent"]] = hist.get(t["exit_intent"], 0) + 1
        print(f"[sanity] {sym}: bars={len(ks)} bars_eval={r['bars_eval']} "
              f"trades={len(trs)} elapsed={dt:.1f}s halted={r['halted']}")
        print(f"[sanity]   window=[{datetime.fromtimestamp(win_start_ms / 1000, tz=timezone.utc).isoformat()} "
              f".. {datetime.fromtimestamp(win_end_ms / 1000, tz=timezone.utc).isoformat()}] "
              f"entries_inside={in_win}")
        print(f"[sanity]   intents={hist}")
        if trs:
            m = trade_metrics(trs)
            print(f"[sanity]   win%={m['win_rate'] * 100:.1f} comp%={m['comp_pct']:.2f} "
                  f"mean%={m['mean_pnl_pct']:.3f}")
    print("[sanity] OK" )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timing-test", action="store_true")
    ap.add_argument("--windows", type=int, default=3, choices=(2, 3))
    ap.add_argument("--resume", action="store_true",
                    help="skip (symbol,variant) cells already in results.jsonl")
    args = ap.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    end_ms = cached_end_ms()
    print(f"[kaos-rr-grid] end_ms={end_ms} "
          f"({datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).isoformat()}) "
          f"windows={args.windows} cache={CACHE_DIR}", flush=True)

    if args.timing_test:
        timing_test(end_ms)
        return

    t0 = time.time()
    deadline = deadline_epoch()
    done_cells: set[tuple] = set()
    rows: list[dict] = []
    if args.resume and RESULTS_JSONL.exists():
        for line in RESULTS_JSONL.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            rows.append(r)
            done_cells.add((r["symbol"], tuple(r["variant"]), r["window"]))
        print(f"[kaos-rr-grid] resume: {len(rows)} cells already done", flush=True)

    # cfg must embed the SYMBOL (cfg.symbols/ema_symbol) — build per
    # (symbol, variant), once per worker. A per-variant BTC cfg would zero
    # every other symbol (measured bug, 2026-09-09 01:1x).
    cfgs = {(s, v): pickle.dumps(run_cfg(s, v[0], v[1]))
            for s in SYMBOLS for v in VARIANTS}
    tasks = []
    for s in SYMBOLS:
        for v in VARIANTS:
            if any((s, tuple(v), wi) not in done_cells for wi in range(args.windows)):
                tasks.append((s, v))

    print(f"[kaos-rr-grid] {len(tasks)} (symbol,variant) workers to run "
          f"({len(SYMBOLS) * len(VARIANTS) * args.windows - len(done_cells)} cells)",
          flush=True)
    notes = []
    status = "RUNNING"
    timed_out = False
    with tempfile.TemporaryDirectory(prefix="kaos_rr_grid_",
                                     ignore_cleanup_errors=True) as root:
        with ProcessPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(worker, s, end_ms, v[0], v[1], args.windows,
                              root, cfgs[(s, v)]): (s, v) for s, v in tasks}
            errors: list[str] = []
            try:
                for fut in as_completed(futs, timeout=max(60.0, deadline - time.time())):
                    s, v = futs[fut]
                    try:
                        new_rows = fut.result()
                    except Exception as exc:  # one bad cell must not kill the fleet
                        errors.append(f"{s} ({v[0]},{v[1]}): {type(exc).__name__}: {exc}")
                        print(f"[grid] ERROR {s} ({v[0]},{v[1]}): {exc}", flush=True)
                        continue
                    with RESULTS_JSONL.open("a", encoding="utf-8") as fh:
                        for r in new_rows:
                            fh.write(json.dumps(r) + "\n")
                    rows.extend(new_rows)
                    # incremental report after every worker (mission requirement)
                    agg = aggregate(rows, VARIANTS, args.windows)
                    write_report(agg, VARIANTS, args.windows, "RUNNING", [])
                    print(f"[grid] {s} ({v[0]},{v[1]}) done "
                          f"[{len(rows)} cells, {time.time() - t0:.0f}s]",
                          flush=True)
            except TimeoutError:
                timed_out = True
            finally:
                if timed_out:
                    for f in futs:
                        f.cancel()
                    ex.shutdown(wait=False, cancel_futures=True)
            if errors:
                notes.append(f"{len(errors)} worker errors: "
                             + "; ".join(errors[:8]))
    if timed_out:
        notes.append(f"COMPUTE HARD-STOP at {COMPUTE_DEADLINE_HM} local hit "
                     f"— incomplete grid, cells below are what finished")
        status = "PARTIAL (runtime guard)"

    agg = aggregate(rows, VARIANTS, args.windows)
    done_cells_final = {(tuple(r["variant"]), r["window"]) for r in rows
                        if not r.get("skipped")}
    n_expected = len(VARIANTS) * args.windows
    if len(done_cells_final) < n_expected:
        missing = [f"({v[0]},{v[1]}) w{wi}" for v in VARIANTS
                   for wi in range(args.windows)
                   if (tuple(v), wi) not in done_cells_final]
        notes.append(f"windows completed: {len(done_cells_final)}/{n_expected} "
                     f"(variant,window) cells; missing: {', '.join(missing[:12])}"
                     f"{' …' if len(missing) > 12 else ''}")
    if len(done_cells_final) == n_expected and not timed_out:
        status = "FINAL"
    vrd = write_report(agg, VARIANTS, args.windows, status, notes)
    print(f"[kaos-rr-grid] raw results -> {RESULTS_JSONL} "
          f"({time.time() - t0:.0f}s total)", flush=True)
    print(f"[VERDICT] {vrd}", flush=True)


if __name__ == "__main__":
    main()
