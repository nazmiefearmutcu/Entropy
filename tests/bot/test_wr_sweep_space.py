"""wave3 sweep space + selection guards of scripts/entropy_wr_sweep.py.

Pins the pure enumeration/selection logic (no simulation, no network):

* ``combos("wave3")`` = 2*3*2*2*2*2*2*4*2 = 1536 combos, every swept knob
  expanded, barriers exploded into stop_mode/stop_sigma_mult/tp_sigma_mult
  with the fixed percents kept at 1.5/1.2;
* sharding (``i % shard_total == shard``) is deterministic, disjoint and
  complete;
* the wave3 key encodes every swept knob in short form and is unique;
* ``build_cfg`` wires long_only/max_hold_bars into ConsensusConfig and
  stop_mode/sigma multipliers into RiskOverrides (legacy spaces keep their
  old defaults);
* the selection guard + eligible-first ranking (win_rate, then return_pct).

The script lives in scripts/ (not a package) and is imported via importlib
(the same pattern tests/bot/test_accuracy_harness.py uses).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "entropy_wr_sweep.py"
_spec = importlib.util.spec_from_file_location("entropy_wr_sweep", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)


# ---- space sizes ----------------------------------------------------------------


def test_wave3_space_has_1536_combos():
    assert len(mod.combos("wave3")) == 2 * 3 * 2 * 2 * 2 * 2 * 2 * 4 * 2 == 1536


def test_legacy_space_sizes_unchanged():
    assert len(mod.combos("full")) == 1152 + 192 == 1344
    assert len(mod.combos("wave2")) == 384


def test_wave3_every_swept_knob_expanded():
    combos = mod.combos("wave3")
    assert {c["long_only"] for c in combos} == {True, False}
    assert {c["trail_pct"] for c in combos} == {0.2, 0.3, 0.5}
    assert {c["threshold"] for c in combos} == {0.5, 0.65}
    assert {c["confirm_bars"] for c in combos} == {1, 2}
    assert {c["direction_bars"] for c in combos} == {0, 20}
    assert {c["min_hold_bars"] for c in combos} == {3, 5}
    assert {c["cooldown_bars"] for c in combos} == {2, 4}
    assert {c["max_hold_bars"] for c in combos} == {0, 96}
    assert all(c["exit_mode"] == "trail" for c in combos)
    assert all(c["vote_mode"] == "adaptive" for c in combos)  # FIXED applied


def test_wave3_barriers_exploded_percent_entry_keeps_fixed_percents():
    combos = mod.combos("wave3")
    shapes = {(c["stop_mode"], c["stop_sigma_mult"], c["tp_sigma_mult"])
              for c in combos}
    assert shapes == {
        ("percent", 1.5, 1.2),
        ("sigma", 4.0, 3.2),
        ("sigma", 5.0, 4.0),
        ("sigma", 6.0, 4.8),
    }
    for c in combos:
        assert c["sl"] == 1.5 and c["tp"] == 1.2


def test_legacy_combos_carry_no_wave3_fields():
    # backward compat: old consumers never see the new keys
    for space in ("full", "wave2"):
        for c in mod.combos(space):
            assert "long_only" not in c
            assert "stop_mode" not in c
            assert "max_hold_bars" not in c
            assert "barriers" not in c


# ---- sharding: deterministic, disjoint, complete --------------------------------


def _shard(combos: list[dict], shard: int, total: int) -> list[str]:
    return [mod.combo_key(c) for c in combos[shard::total]]


def test_wave3_shard_partition_is_deterministic_disjoint_and_complete():
    combos = mod.combos("wave3")
    total = 7
    parts = [_shard(combos, s, total) for s in range(total)]
    # deterministic: same enumeration -> same partition
    assert parts == [_shard(mod.combos("wave3"), s, total) for s in range(total)]
    # disjoint
    all_keys = [k for part in parts for k in part]
    assert len(all_keys) == len(set(all_keys))
    # complete: union covers every combo, sizes differ by at most one
    assert set(all_keys) == {mod.combo_key(c) for c in combos}
    assert max(len(p) for p in parts) - min(len(p) for p in parts) <= 1


def test_wave3_shard_slice_matches_i_mod_total():
    combos = mod.combos("wave3")
    for i, c in enumerate(combos):
        if i % 8 == 3:
            assert c in combos[3::8]


# ---- combo key -------------------------------------------------------------------


def test_wave3_keys_are_unique_across_the_whole_space():
    keys = [mod.combo_key(c) for c in mod.combos("wave3")]
    assert len(keys) == len(set(keys)) == 1536


def test_wave3_key_short_forms_encode_every_swept_knob():
    c = next(c for c in mod.combos("wave3")
             if c["long_only"] and c["stop_mode"] == "sigma"
             and c["stop_sigma_mult"] == 4.0 and c["max_hold_bars"] == 96
             and c["trail_pct"] == 0.2 and c["threshold"] == 0.5
             and c["confirm_bars"] == 1 and c["direction_bars"] == 0
             and c["min_hold_bars"] == 3 and c["cooldown_bars"] == 2)
    key = mod.combo_key(c)
    assert key == "lo=T/tr0.2/th0.5/cb1/db0/mh3/co2/sl=s4.0x3.2/hold96"


def test_wave3_percent_barrier_key_uses_p_form():
    c = next(c for c in mod.combos("wave3") if c["stop_mode"] == "percent")
    assert "sl=p1.5x1.2" in mod.combo_key(c)
    assert "hold0" in mod.combo_key(c) or "hold96" in mod.combo_key(c)


def test_legacy_key_format_unchanged():
    c = mod.combos("wave2")[0]
    key = mod.combo_key(c)
    for part in ("exit_mode=", "trail_pct=", "threshold=", "confirm_bars=",
                 "direction_bars=", "min_hold_bars=", "cooldown_bars=",
                 "sl=", "tp="):
        assert part in key
    assert "lo=T" not in key and "lo=F" not in key
    assert "hold0" not in key and "hold96" not in key


def test_combo_key_is_stable():
    combos = mod.combos("wave3")
    assert all(mod.combo_key(c) == mod.combo_key(c) for c in combos)


# ---- build_cfg wiring ------------------------------------------------------------


def test_build_cfg_wires_wave3_knobs():
    c = next(c for c in mod.combos("wave3")
             if c["long_only"] and c["max_hold_bars"] == 96
             and c["stop_mode"] == "sigma" and c["stop_sigma_mult"] == 5.0
             and c["tp_sigma_mult"] == 4.0)
    cfg = mod.build_cfg(c)
    assert cfg.consensus.long_only is True
    assert cfg.consensus.max_hold_bars == 96
    assert cfg.risk_overrides.stop_mode == "sigma"
    assert cfg.risk_overrides.stop_sigma_mult == 5.0
    assert cfg.risk_overrides.tp_sigma_mult == 4.0
    # fixed percents remain the fallback barriers
    assert cfg.risk_overrides.stop_loss_pct == 1.5
    assert cfg.risk_overrides.take_profit_pct == 1.2


def test_build_cfg_wave3_long_only_false_and_hold_zero():
    c = next(c for c in mod.combos("wave3")
             if not c["long_only"] and c["max_hold_bars"] == 0
             and c["stop_mode"] == "percent")
    cfg = mod.build_cfg(c)
    assert cfg.consensus.long_only is False
    assert cfg.consensus.max_hold_bars == 0
    assert cfg.risk_overrides.stop_mode == "percent"


def test_build_cfg_legacy_spaces_keep_old_defaults():
    for space in ("full", "wave2"):
        cfg = mod.build_cfg(mod.combos(space)[0])
        assert cfg.consensus.long_only is False
        assert cfg.consensus.max_hold_bars == 0
        assert cfg.risk_overrides.stop_mode == "percent"
        assert cfg.risk_overrides.stop_sigma_mult == 1.5
        assert cfg.risk_overrides.tp_sigma_mult == 1.2


# ---- CSV row shape ---------------------------------------------------------------


def _fake_sim() -> dict:
    return {"metrics": {"total_return_pct": 1.5, "total_trades": 25,
                        "win_rate": 0.6, "profit_factor": 1.2,
                        "max_drawdown_pct": 2.0, "costs_paid": 0.5},
            "avg_hold_bars": 4.0, "exit_breakdown": {"take_profit": 10}}


def test_wave3_row_carries_the_new_columns():
    c = next(c for c in mod.combos("wave3") if c["long_only"])
    row = mod.build_row(c, _fake_sim())
    assert row["long_only"] is True
    assert row["stop_mode"] == c["stop_mode"]
    assert row["max_hold_bars"] == c["max_hold_bars"]
    # `eligible` is stamped later by rank_rows
    assert set(row) | {"eligible"} == set(mod.WAVE3_CSV_FIELDS)


def test_legacy_row_has_no_new_columns():
    row = mod.build_row(mod.combos("full")[0], _fake_sim())
    assert set(mod.CSV_FIELDS) == set(row)
    assert "eligible" not in row


# ---- selection guard + eligible-first ranking ------------------------------------


def _row(trades: int, wr: float, ret: float, pf: float = 1.5,
         dd: float = 1.0, key: str = "k") -> dict:
    return {"key": key, "return_pct": ret, "trades": trades, "win_rate": wr,
            "pf": pf, "max_dd_pct": dd, "costs": 0.0, "avg_hold_bars": 0.0,
            "exits": {}}


def test_wave3_guard_requires_all_four_conditions():
    guard = dict(min_trades=20, min_pf=1.0, min_return=0.0, max_dd=5.0)
    assert mod.is_eligible(_row(20, 0.6, 1.0), **guard)
    assert not mod.is_eligible(_row(19, 0.6, 1.0), **guard)   # too few trades
    assert not mod.is_eligible(_row(20, 0.6, 1.0, pf=0.99), **guard)  # pf
    assert not mod.is_eligible(_row(20, 0.6, -0.01), **guard)  # return
    assert not mod.is_eligible(_row(20, 0.6, 1.0, dd=5.01), **guard)  # dd
    # boundaries are inclusive
    assert mod.is_eligible(_row(20, 0.6, 0.0, pf=1.0, dd=5.0), **guard)


def test_wave3_ranking_eligible_first_by_wr_then_return():
    rows = [
        _row(30, 0.70, 0.5, key="elig_high_wr_low_ret"),
        _row(30, 0.70, 2.0, key="elig_high_wr_high_ret"),
        _row(30, 0.90, -9.0, pf=0.5, key="inelig_wr90"),      # fails pf+ret
        _row(5, 0.99, 9.0, key="inelig_too_few_trades"),
    ]
    ranked = mod.rank_rows(rows, "wave3", min_trades=20, min_pf=1.0,
                           min_return=0.0, max_dd=5.0)
    assert [r["key"] for r in ranked] == [
        "elig_high_wr_high_ret",   # tie on WR 0.70 -> higher return first
        "elig_high_wr_low_ret",
        "inelig_too_few_trades",   # ineligible block ordered by WR (0.99)...
        "inelig_wr90",             # ...then 0.90 — never beats eligible
    ]
    assert [r["eligible"] for r in ranked] == [True, True, False, False]


def test_legacy_ranking_keeps_min_trades_only_behavior():
    rows = [_row(5, 0.99, 9.0, key="few_wr99"),
            _row(30, 0.70, 0.5, pf=0.2, dd=50.0, key="many_wr70"),
            _row(30, 0.80, -9.0, pf=0.1, key="many_wr80")]
    ranked = mod.rank_rows(rows, "full", min_trades=20, min_pf=1.0,
                           min_return=0.0, max_dd=5.0)
    # legacy: PF/return/DD guards ignored, pure win_rate order, eligible first
    assert [r["key"] for r in ranked] == ["many_wr80", "many_wr70", "few_wr99"]
    assert all("eligible" not in r for r in ranked)
