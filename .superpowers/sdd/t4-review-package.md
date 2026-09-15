# commits
e5064ae feat(scripts): wave3 sweep space with selection guards

# full diff
diff --git a/scripts/entropy_wr_sweep.py b/scripts/entropy_wr_sweep.py
index 21b62da..3e95e43 100644
--- a/scripts/entropy_wr_sweep.py
+++ b/scripts/entropy_wr_sweep.py
@@ -12,7 +12,9 @@ Usage:
       --bars 5760 --shard 0 --shard-total 8 --out /tmp/entropy_accuracy/wr/wave1_s0.csv
 
 The space: 1152 base combos (exit trend_flip|score|either) + 192 trail combos
-= 1344 total.
+= 1344 total. ``wave3`` adds a 1536-combo space (long_only x trail x sigma
+barriers x time stop) with selection guards (min trades/PF/return/max-DD):
+every row is reported with an ``eligible`` flag, eligible rows rank first.
 """
 
 from __future__ import annotations
@@ -67,6 +69,30 @@ WAVE2 = {
     "tp": (0.5, 0.8),
 }
 
+# wave3: long/short x trail tightness x consensus gates x sigma-barrier shape
+# x time stop. exit_mode is fixed "trail"; barriers carry
+# (stop_mode, stop_sigma_mult, tp_sigma_mult) — the "percent" entry keeps the
+# legacy 1.5%/1.2% fixed percents (which also stay as the fallback barriers a
+# sigma entry falls back to when a signal carries no usable sigma).
+WAVE3_BARRIERS = (
+    ("percent", 1.5, 1.2),
+    ("sigma", 4.0, 3.2),
+    ("sigma", 5.0, 4.0),
+    ("sigma", 6.0, 4.8),
+)
+WAVE3 = {
+    "exit_mode": ("trail",),
+    "long_only": (True, False),
+    "trail_pct": (0.2, 0.3, 0.5),
+    "threshold": (0.5, 0.65),
+    "confirm_bars": (1, 2),
+    "direction_bars": (0, 20),
+    "min_hold_bars": (3, 5),
+    "cooldown_bars": (2, 4),
+    "barriers": WAVE3_BARRIERS,
+    "max_hold_bars": (0, 96),
+}
+
 FIXED = {"vote_mode": "adaptive", "normalize": "total",
          "min_participation": 0.5, "move_floor": 0.0003,
          "cost_edge_mult": 1.0}
@@ -74,18 +100,40 @@ FIXED = {"vote_mode": "adaptive", "normalize": "total",
 
 def combos(space_name: str = "full") -> list[dict[str, Any]]:
     out: list[dict[str, Any]] = []
-    spaces = {"full": (BASE, TRAIL), "wave2": (WAVE2,)}[space_name]
+    spaces = {"full": (BASE, TRAIL), "wave2": (WAVE2,), "wave3": (WAVE3,)}[space_name]
     for space in spaces:
         for values in itertools.product(*(space[k] for k in space)):
             d = dict(zip(space.keys(), values, strict=True))
             if d["exit_mode"] != "trail":
                 d["trail_pct"] = 0.0
+            if "barriers" in d:
+                # wave3: expand the barrier tuple; the fixed percents stay at
+                # 1.5/1.2 for every entry (the sigma fallback anchors).
+                stop_mode, stop_mult, tp_mult = d.pop("barriers")
+                d["sl"], d["tp"] = 1.5, 1.2
+                d["stop_mode"] = stop_mode
+                d["stop_sigma_mult"] = stop_mult
+                d["tp_sigma_mult"] = tp_mult
             d.update(FIXED)
             out.append(d)
     return out
 
 
 def combo_key(c: dict[str, Any]) -> str:
+    if "long_only" in c:  # wave3: short-form key over every swept knob
+        bar = (f"p{c['sl']}x{c['tp']}" if c["stop_mode"] == "percent"
+               else f"s{c['stop_sigma_mult']}x{c['tp_sigma_mult']}")
+        return "/".join((
+            f"lo={'T' if c['long_only'] else 'F'}",
+            f"tr{c['trail_pct']}",
+            f"th{c['threshold']}",
+            f"cb{c['confirm_bars']}",
+            f"db{c['direction_bars']}",
+            f"mh{c['min_hold_bars']}",
+            f"co{c['cooldown_bars']}",
+            f"sl={bar}",
+            f"hold{c['max_hold_bars']}",
+        ))
     return "/".join(f"{k}={c[k]}" for k in
                     ("exit_mode", "trail_pct", "threshold", "confirm_bars",
                      "direction_bars", "min_hold_bars", "cooldown_bars",
@@ -117,6 +165,8 @@ def build_cfg(c: dict[str, Any]) -> BotConfig:
             direction_bars=c["direction_bars"],
             confirm_bars=c["confirm_bars"],
             trail_pct=c["trail_pct"],
+            max_hold_bars=c.get("max_hold_bars", 0),
+            long_only=c.get("long_only", False),
         ),
         risk_overrides=RiskOverrides(
             per_trade_pct=10.0,
@@ -128,12 +178,70 @@ def build_cfg(c: dict[str, Any]) -> BotConfig:
             cooldown_s=180.0,
             min_volatility_pct=0.05,
             vol_window_s=900.0,
+            stop_mode=c.get("stop_mode", "percent"),
+            stop_sigma_mult=c.get("stop_sigma_mult", 1.5),
+            tp_sigma_mult=c.get("tp_sigma_mult", 1.2),
         ),
         console_log_path=f"/tmp/entropy_accuracy/_wr/console-{os.getpid()}.log",
         trade_csv_path=f"/tmp/entropy_accuracy/_wr/trades-{os.getpid()}.csv",
     )
 
 
+CSV_FIELDS = ("key", "return_pct", "trades", "win_rate", "pf", "max_dd_pct",
+              "costs", "avg_hold_bars", "exits")
+WAVE3_CSV_FIELDS = CSV_FIELDS + ("eligible", "long_only", "stop_mode",
+                                 "max_hold_bars")
+
+
+def is_eligible(row: dict[str, Any], *, min_trades: int, min_pf: float,
+                min_return: float, max_dd: float) -> bool:
+    """wave3 selection guard: enough trades, profitable enough, and shallow
+    enough drawdown."""
+    return (int(row["trades"]) >= min_trades
+            and float(row["pf"]) >= min_pf
+            and float(row["return_pct"]) >= min_return
+            and float(row["max_dd_pct"]) <= max_dd)
+
+
+def rank_rows(rows: list[dict[str, Any]], space_name: str, *, min_trades: int,
+              min_pf: float, min_return: float, max_dd: float) -> list[dict[str, Any]]:
+    """Eligible-first ranking. wave3 applies the full selection guard and
+    ranks by win_rate then return_pct; the legacy spaces keep their
+    min-trades-only, win_rate-only behavior."""
+    if space_name != "wave3":
+        eligible = [r for r in rows if int(r["trades"]) >= min_trades]
+        rest = [r for r in rows if int(r["trades"]) < min_trades]
+        return (sorted(eligible, key=lambda x: -float(x["win_rate"]))
+                + sorted(rest, key=lambda x: -float(x["win_rate"])))
+    for r in rows:
+        r["eligible"] = is_eligible(r, min_trades=min_trades, min_pf=min_pf,
+                                    min_return=min_return, max_dd=max_dd)
+    return sorted(rows, key=lambda x: (not x["eligible"], -float(x["win_rate"]),
+                                       -float(x["return_pct"])))
+
+
+def build_row(c: dict[str, Any], r: dict[str, Any]) -> dict[str, Any]:
+    """One CSV row from a combo and its simulate() report (wave3 rows carry
+    the extra long_only/stop_mode/max_hold_bars columns)."""
+    m = r["metrics"]
+    row: dict[str, Any] = {
+        "key": combo_key(c),
+        "return_pct": m["total_return_pct"],
+        "trades": m["total_trades"],
+        "win_rate": m["win_rate"],
+        "pf": m["profit_factor"],
+        "max_dd_pct": m["max_drawdown_pct"],
+        "costs": m["costs_paid"],
+        "avg_hold_bars": r["avg_hold_bars"],
+        "exits": r["exit_breakdown"],
+    }
+    if "long_only" in c:
+        row["long_only"] = c["long_only"]
+        row["stop_mode"] = c["stop_mode"]
+        row["max_hold_bars"] = c["max_hold_bars"]
+    return row
+
+
 def main() -> None:
     ap = argparse.ArgumentParser()
     ap.add_argument("--klines", required=True)
@@ -142,8 +250,14 @@ def main() -> None:
     ap.add_argument("--shard-total", type=int, default=1)
     ap.add_argument("--out", required=True)
     ap.add_argument("--min-trades", type=int, default=20)
+    ap.add_argument("--min-pf", type=float, default=1.0,
+                    help="wave3 guard: minimum profit factor")
+    ap.add_argument("--min-return", type=float, default=0.0,
+                    help="wave3 guard: minimum total return %%")
+    ap.add_argument("--max-dd", type=float, default=5.0,
+                    help="wave3 guard: maximum max-drawdown %%")
     ap.add_argument("--limit", type=int, default=0, help="debug: only first N combos")
-    ap.add_argument("--space", choices=("full", "wave2"), default="full")
+    ap.add_argument("--space", choices=("full", "wave2", "wave3"), default="full")
     args = ap.parse_args()
 
     klines = json.loads(Path(args.klines).read_text())["klines"][-args.bars:]
@@ -158,27 +272,17 @@ def main() -> None:
     for i, c in enumerate(mine, 1):
         r = simulate(klines, build_cfg(c), run_dir=f"/tmp/entropy_accuracy/_wr/ledger-{os.getpid()}",
                      trade_csv=f"/tmp/entropy_accuracy/_wr/trades-{os.getpid()}.csv")
-        m = r["metrics"]
-        rows.append({
-            "key": combo_key(c),
-            "return_pct": m["total_return_pct"],
-            "trades": m["total_trades"],
-            "win_rate": m["win_rate"],
-            "pf": m["profit_factor"],
-            "max_dd_pct": m["max_drawdown_pct"],
-            "costs": m["costs_paid"],
-            "avg_hold_bars": r["avg_hold_bars"],
-            "exits": r["exit_breakdown"],
-        })
+        rows.append(build_row(c, r))
         if i % 25 == 0:
             print(f"[wr] {i}/{len(mine)} done, {time.perf_counter()-t0:.0f}s", flush=True)
 
-    eligible = [r for r in rows if int(r["trades"]) >= args.min_trades]
-    ranked = sorted(eligible, key=lambda x: -float(x["win_rate"])) + \
-        sorted((r for r in rows if r not in eligible), key=lambda x: -float(x["win_rate"]))
+    ranked = rank_rows(rows, args.space, min_trades=args.min_trades,
+                       min_pf=args.min_pf, min_return=args.min_return,
+                       max_dd=args.max_dd)
+    fields = WAVE3_CSV_FIELDS if args.space == "wave3" else CSV_FIELDS
     Path(args.out).parent.mkdir(parents=True, exist_ok=True)
     with Path(args.out).open("w", newline="") as f:
-        w = csv.DictWriter(f, fieldnames=list(ranked[0].keys()))
+        w = csv.DictWriter(f, fieldnames=list(fields))
         w.writeheader()
         w.writerows(ranked)
     print(f"[wr] done {time.perf_counter()-t0:.0f}s -> {args.out}", flush=True)
diff --git a/tests/bot/test_wr_sweep_space.py b/tests/bot/test_wr_sweep_space.py
new file mode 100644
index 0000000..4a6f53d
--- /dev/null
+++ b/tests/bot/test_wr_sweep_space.py
@@ -0,0 +1,265 @@
+"""wave3 sweep space + selection guards of scripts/entropy_wr_sweep.py.
+
+Pins the pure enumeration/selection logic (no simulation, no network):
+
+* ``combos("wave3")`` = 2*3*2*2*2*2*2*4*2 = 1536 combos, every swept knob
+  expanded, barriers exploded into stop_mode/stop_sigma_mult/tp_sigma_mult
+  with the fixed percents kept at 1.5/1.2;
+* sharding (``i % shard_total == shard``) is deterministic, disjoint and
+  complete;
+* the wave3 key encodes every swept knob in short form and is unique;
+* ``build_cfg`` wires long_only/max_hold_bars into ConsensusConfig and
+  stop_mode/sigma multipliers into RiskOverrides (legacy spaces keep their
+  old defaults);
+* the selection guard + eligible-first ranking (win_rate, then return_pct).
+
+The script lives in scripts/ (not a package) and is imported via importlib
+(the same pattern tests/bot/test_accuracy_harness.py uses).
+"""
+
+from __future__ import annotations
+
+import importlib.util
+import sys
+from pathlib import Path
+
+_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "entropy_wr_sweep.py"
+_spec = importlib.util.spec_from_file_location("entropy_wr_sweep", _SCRIPT)
+assert _spec is not None and _spec.loader is not None
+mod = importlib.util.module_from_spec(_spec)
+sys.modules[_spec.name] = mod
+_spec.loader.exec_module(mod)
+
+
+# ---- space sizes ----------------------------------------------------------------
+
+
+def test_wave3_space_has_1536_combos():
+    assert len(mod.combos("wave3")) == 2 * 3 * 2 * 2 * 2 * 2 * 2 * 4 * 2 == 1536
+
+
+def test_legacy_space_sizes_unchanged():
+    assert len(mod.combos("full")) == 1152 + 192 == 1344
+    assert len(mod.combos("wave2")) == 384
+
+
+def test_wave3_every_swept_knob_expanded():
+    combos = mod.combos("wave3")
+    assert {c["long_only"] for c in combos} == {True, False}
+    assert {c["trail_pct"] for c in combos} == {0.2, 0.3, 0.5}
+    assert {c["threshold"] for c in combos} == {0.5, 0.65}
+    assert {c["confirm_bars"] for c in combos} == {1, 2}
+    assert {c["direction_bars"] for c in combos} == {0, 20}
+    assert {c["min_hold_bars"] for c in combos} == {3, 5}
+    assert {c["cooldown_bars"] for c in combos} == {2, 4}
+    assert {c["max_hold_bars"] for c in combos} == {0, 96}
+    assert all(c["exit_mode"] == "trail" for c in combos)
+    assert all(c["vote_mode"] == "adaptive" for c in combos)  # FIXED applied
+
+
+def test_wave3_barriers_exploded_percent_entry_keeps_fixed_percents():
+    combos = mod.combos("wave3")
+    shapes = {(c["stop_mode"], c["stop_sigma_mult"], c["tp_sigma_mult"])
+              for c in combos}
+    assert shapes == {
+        ("percent", 1.5, 1.2),
+        ("sigma", 4.0, 3.2),
+        ("sigma", 5.0, 4.0),
+        ("sigma", 6.0, 4.8),
+    }
+    for c in combos:
+        assert c["sl"] == 1.5 and c["tp"] == 1.2
+
+
+def test_legacy_combos_carry_no_wave3_fields():
+    # backward compat: old consumers never see the new keys
+    for space in ("full", "wave2"):
+        for c in mod.combos(space):
+            assert "long_only" not in c
+            assert "stop_mode" not in c
+            assert "max_hold_bars" not in c
+            assert "barriers" not in c
+
+
+# ---- sharding: deterministic, disjoint, complete --------------------------------
+
+
+def _shard(combos: list[dict], shard: int, total: int) -> list[str]:
+    return [mod.combo_key(c) for c in combos[shard::total]]
+
+
+def test_wave3_shard_partition_is_deterministic_disjoint_and_complete():
+    combos = mod.combos("wave3")
+    total = 7
+    parts = [_shard(combos, s, total) for s in range(total)]
+    # deterministic: same enumeration -> same partition
+    assert parts == [_shard(mod.combos("wave3"), s, total) for s in range(total)]
+    # disjoint
+    all_keys = [k for part in parts for k in part]
+    assert len(all_keys) == len(set(all_keys))
+    # complete: union covers every combo, sizes differ by at most one
+    assert set(all_keys) == {mod.combo_key(c) for c in combos}
+    assert max(len(p) for p in parts) - min(len(p) for p in parts) <= 1
+
+
+def test_wave3_shard_slice_matches_i_mod_total():
+    combos = mod.combos("wave3")
+    for i, c in enumerate(combos):
+        if i % 8 == 3:
+            assert c in combos[3::8]
+
+
+# ---- combo key -------------------------------------------------------------------
+
+
+def test_wave3_keys_are_unique_across_the_whole_space():
+    keys = [mod.combo_key(c) for c in mod.combos("wave3")]
+    assert len(keys) == len(set(keys)) == 1536
+
+
+def test_wave3_key_short_forms_encode_every_swept_knob():
+    c = next(c for c in mod.combos("wave3")
+             if c["long_only"] and c["stop_mode"] == "sigma"
+             and c["stop_sigma_mult"] == 4.0 and c["max_hold_bars"] == 96
+             and c["trail_pct"] == 0.2 and c["threshold"] == 0.5
+             and c["confirm_bars"] == 1 and c["direction_bars"] == 0
+             and c["min_hold_bars"] == 3 and c["cooldown_bars"] == 2)
+    key = mod.combo_key(c)
+    assert key == "lo=T/tr0.2/th0.5/cb1/db0/mh3/co2/sl=s4.0x3.2/hold96"
+
+
+def test_wave3_percent_barrier_key_uses_p_form():
+    c = next(c for c in mod.combos("wave3") if c["stop_mode"] == "percent")
+    assert "sl=p1.5x1.2" in mod.combo_key(c)
+    assert "hold0" in mod.combo_key(c) or "hold96" in mod.combo_key(c)
+
+
+def test_legacy_key_format_unchanged():
+    c = mod.combos("wave2")[0]
+    key = mod.combo_key(c)
+    for part in ("exit_mode=", "trail_pct=", "threshold=", "confirm_bars=",
+                 "direction_bars=", "min_hold_bars=", "cooldown_bars=",
+                 "sl=", "tp="):
+        assert part in key
+    assert "lo=T" not in key and "lo=F" not in key
+    assert "hold0" not in key and "hold96" not in key
+
+
+def test_combo_key_is_stable():
+    combos = mod.combos("wave3")
+    assert all(mod.combo_key(c) == mod.combo_key(c) for c in combos)
+
+
+# ---- build_cfg wiring ------------------------------------------------------------
+
+
+def test_build_cfg_wires_wave3_knobs():
+    c = next(c for c in mod.combos("wave3")
+             if c["long_only"] and c["max_hold_bars"] == 96
+             and c["stop_mode"] == "sigma" and c["stop_sigma_mult"] == 5.0
+             and c["tp_sigma_mult"] == 4.0)
+    cfg = mod.build_cfg(c)
+    assert cfg.consensus.long_only is True
+    assert cfg.consensus.max_hold_bars == 96
+    assert cfg.risk_overrides.stop_mode == "sigma"
+    assert cfg.risk_overrides.stop_sigma_mult == 5.0
+    assert cfg.risk_overrides.tp_sigma_mult == 4.0
+    # fixed percents remain the fallback barriers
+    assert cfg.risk_overrides.stop_loss_pct == 1.5
+    assert cfg.risk_overrides.take_profit_pct == 1.2
+
+
+def test_build_cfg_wave3_long_only_false_and_hold_zero():
+    c = next(c for c in mod.combos("wave3")
+             if not c["long_only"] and c["max_hold_bars"] == 0
+             and c["stop_mode"] == "percent")
+    cfg = mod.build_cfg(c)
+    assert cfg.consensus.long_only is False
+    assert cfg.consensus.max_hold_bars == 0
+    assert cfg.risk_overrides.stop_mode == "percent"
+
+
+def test_build_cfg_legacy_spaces_keep_old_defaults():
+    for space in ("full", "wave2"):
+        cfg = mod.build_cfg(mod.combos(space)[0])
+        assert cfg.consensus.long_only is False
+        assert cfg.consensus.max_hold_bars == 0
+        assert cfg.risk_overrides.stop_mode == "percent"
+        assert cfg.risk_overrides.stop_sigma_mult == 1.5
+        assert cfg.risk_overrides.tp_sigma_mult == 1.2
+
+
+# ---- CSV row shape ---------------------------------------------------------------
+
+
+def _fake_sim() -> dict:
+    return {"metrics": {"total_return_pct": 1.5, "total_trades": 25,
+                        "win_rate": 0.6, "profit_factor": 1.2,
+                        "max_drawdown_pct": 2.0, "costs_paid": 0.5},
+            "avg_hold_bars": 4.0, "exit_breakdown": {"take_profit": 10}}
+
+
+def test_wave3_row_carries_the_new_columns():
+    c = next(c for c in mod.combos("wave3") if c["long_only"])
+    row = mod.build_row(c, _fake_sim())
+    assert row["long_only"] is True
+    assert row["stop_mode"] == c["stop_mode"]
+    assert row["max_hold_bars"] == c["max_hold_bars"]
+    # `eligible` is stamped later by rank_rows
+    assert set(row) | {"eligible"} == set(mod.WAVE3_CSV_FIELDS)
+
+
+def test_legacy_row_has_no_new_columns():
+    row = mod.build_row(mod.combos("full")[0], _fake_sim())
+    assert set(mod.CSV_FIELDS) == set(row)
+    assert "eligible" not in row
+
+
+# ---- selection guard + eligible-first ranking ------------------------------------
+
+
+def _row(trades: int, wr: float, ret: float, pf: float = 1.5,
+         dd: float = 1.0, key: str = "k") -> dict:
+    return {"key": key, "return_pct": ret, "trades": trades, "win_rate": wr,
+            "pf": pf, "max_dd_pct": dd, "costs": 0.0, "avg_hold_bars": 0.0,
+            "exits": {}}
+
+
+def test_wave3_guard_requires_all_four_conditions():
+    guard = dict(min_trades=20, min_pf=1.0, min_return=0.0, max_dd=5.0)
+    assert mod.is_eligible(_row(20, 0.6, 1.0), **guard)
+    assert not mod.is_eligible(_row(19, 0.6, 1.0), **guard)   # too few trades
+    assert not mod.is_eligible(_row(20, 0.6, 1.0, pf=0.99), **guard)  # pf
+    assert not mod.is_eligible(_row(20, 0.6, -0.01), **guard)  # return
+    assert not mod.is_eligible(_row(20, 0.6, 1.0, dd=5.01), **guard)  # dd
+    # boundaries are inclusive
+    assert mod.is_eligible(_row(20, 0.6, 0.0, pf=1.0, dd=5.0), **guard)
+
+
+def test_wave3_ranking_eligible_first_by_wr_then_return():
+    rows = [
+        _row(30, 0.70, 0.5, key="elig_high_wr_low_ret"),
+        _row(30, 0.70, 2.0, key="elig_high_wr_high_ret"),
+        _row(30, 0.90, -9.0, pf=0.5, key="inelig_wr90"),      # fails pf+ret
+        _row(5, 0.99, 9.0, key="inelig_too_few_trades"),
+    ]
+    ranked = mod.rank_rows(rows, "wave3", min_trades=20, min_pf=1.0,
+                           min_return=0.0, max_dd=5.0)
+    assert [r["key"] for r in ranked] == [
+        "elig_high_wr_high_ret",   # tie on WR 0.70 -> higher return first
+        "elig_high_wr_low_ret",
+        "inelig_too_few_trades",   # ineligible block ordered by WR (0.99)...
+        "inelig_wr90",             # ...then 0.90 — never beats eligible
+    ]
+    assert [r["eligible"] for r in ranked] == [True, True, False, False]
+
+
+def test_legacy_ranking_keeps_min_trades_only_behavior():
+    rows = [_row(5, 0.99, 9.0, key="few_wr99"),
+            _row(30, 0.70, 0.5, pf=0.2, dd=50.0, key="many_wr70"),
+            _row(30, 0.80, -9.0, pf=0.1, key="many_wr80")]
+    ranked = mod.rank_rows(rows, "full", min_trades=20, min_pf=1.0,
+                           min_return=0.0, max_dd=5.0)
+    # legacy: PF/return/DD guards ignored, pure win_rate order, eligible first
+    assert [r["key"] for r in ranked] == ["many_wr80", "many_wr70", "few_wr99"]
+    assert all("eligible" not in r for r in ranked)
