"""T8 Deliverable B: Round-2 (wave4) sweep space, guards, K, warmup + symbol.

Pins the pure enumeration/selection logic of scripts/entropy_wr_sweep.py (no
simulation, no network):

* ``combos("wave4")`` = 3*16*3*4*2 = 1152 combos: direction_bars {0,10,20} x
  sigma barriers {3..6}x{4..12} x max_hold_bars {48,96,192} x
  entry_grace_bars {0,1,2,3} x risk_trail_pct {0.0,0.5}, long_only pinned
  True, non-swept knobs at the shipped "H" defaults;
* ``vote_mode`` {adaptive, trend} is a per-run flag — the sweep stays
  single-mode per run (per-symbol mapping is a T9 ship concern);
* wave4 combo keys encode every swept knob incl. the new levers (vm/eg/rt);
* ``build_cfg`` wires the combo's risk_trail_pct (combo wins over the legacy
  --risk-trail-pct passthrough) and the --symbol passthrough;
* ``rank_wave4`` applies the T4 hard guards; when ZERO rows pass it relaxes
  to the top-K-by-WR UNION top-K-by-PF families and stamps K (the candidate
  count handed to OOS) on every row;
* end-to-end ``main()`` wiring (patched simulate): --space wave4 --vote-mode
  --warmup-bars --symbol --top-k reach the simulate call and the CSV carries
  the K column.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "entropy_wr_sweep.py"
_spec = importlib.util.spec_from_file_location("entropy_wr_sweep", _SCRIPT)
assert _spec is not None and _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)


# ---- wave4 space ----------------------------------------------------------------


def test_wave4_space_has_1152_combos():
    assert len(mod.combos("wave4")) == 3 * 16 * 3 * 4 * 2 == 1152


def test_wave4_new_knobs_present():
    combos = mod.combos("wave4")
    assert {c["entry_grace_bars"] for c in combos} == {0, 1, 2, 3}
    assert {c["risk_trail_pct"] for c in combos} == {0.0, 0.5}
    assert {c["stop_sigma_mult"] for c in combos} == {3, 4, 5, 6}
    assert {c["tp_sigma_mult"] for c in combos} == {4, 6, 8, 12}
    assert {c["max_hold_bars"] for c in combos} == {48, 96, 192}
    assert {c["direction_bars"] for c in combos} == {0, 10, 20}


def test_wave4_pins_the_round2_deployable_subset():
    combos = mod.combos("wave4")
    assert all(c["long_only"] for c in combos)          # ETH spot subset
    assert all(c["exit_mode"] == "trail" for c in combos)
    assert all(c["stop_mode"] == "sigma" for c in combos)  # sigma-only wave
    # non-swept knobs sit at the shipped "H" defaults
    assert {c["threshold"] for c in combos} == {0.5}
    assert {c["confirm_bars"] for c in combos} == {2}
    assert {c["min_hold_bars"] for c in combos} == {5}
    assert {c["cooldown_bars"] for c in combos} == {4}
    assert {c["trail_pct"] for c in combos} == {0.3}


def test_wave4_barrier_shapes_are_sigma_3_6_x_4_12():
    shapes = {(c["stop_mode"], c["stop_sigma_mult"], c["tp_sigma_mult"])
              for c in mod.combos("wave4")}
    assert shapes == {
        ("sigma", s, t) for s in (3, 4, 5, 6) for t in (4, 6, 8, 12)
    }


def test_vote_mode_is_a_per_run_flag_not_a_dimension():
    assert all(c["vote_mode"] == "adaptive" for c in mod.combos("wave4"))
    assert all(c["vote_mode"] == "trend" for c in mod.combos("wave4", vote_mode="trend"))
    # legacy spaces take the flag too
    assert all(c["vote_mode"] == "trend" for c in mod.combos("wave3", vote_mode="trend"))


def test_wave4_keys_unique_and_encode_the_new_levers():
    combos = mod.combos("wave4")
    keys = [mod.combo_key(c) for c in combos]
    assert len(keys) == len(set(keys)) == 1152
    c = next(c for c in combos if c["entry_grace_bars"] == 2
             and c["risk_trail_pct"] == 0.5 and c["vote_mode"] == "adaptive"
             and c["stop_sigma_mult"] == 3.0 and c["tp_sigma_mult"] == 12.0
             and c["max_hold_bars"] == 48 and c["direction_bars"] == 10)
    key = mod.combo_key(c)
    assert "eg2" in key and "rt0.5" in key and "vmadaptive" in key
    assert "sl=s3x12" in key and "hold48" in key and "db10" in key


def test_wave3_keys_unchanged_by_the_wave4_additions():
    c = next(c for c in mod.combos("wave3")
             if c["long_only"] and c["stop_mode"] == "sigma"
             and c["stop_sigma_mult"] == 4.0 and c["max_hold_bars"] == 96
             and c["trail_pct"] == 0.2 and c["threshold"] == 0.5
             and c["confirm_bars"] == 1 and c["direction_bars"] == 0
             and c["min_hold_bars"] == 3 and c["cooldown_bars"] == 2)
    assert mod.combo_key(c) == "lo=T/tr0.2/th0.5/cb1/db0/mh3/co2/sl=s4.0x3.2/hold96"


def test_legacy_spaces_carry_no_wave4_fields():
    for space in ("full", "wave2", "wave3"):
        for c in mod.combos(space):
            assert "entry_grace_bars" not in c
            assert "risk_trail_pct" not in c


# ---- build_cfg wiring ------------------------------------------------------------


def test_build_cfg_wave4_combo_risk_trail_wins_over_passthrough():
    c = next(c for c in mod.combos("wave4") if c["risk_trail_pct"] == 0.5
             and c["entry_grace_bars"] == 1 and c["stop_mode"] == "sigma"
             and c["stop_sigma_mult"] == 5.0 and c["tp_sigma_mult"] == 4.0
             and c["max_hold_bars"] == 96 and c["long_only"])
    cfg = mod.build_cfg(c, risk_trail_pct=0.0)   # passthrough must NOT win
    assert cfg.risk_overrides.risk_trail_pct == 0.5
    assert cfg.consensus.long_only is True
    assert cfg.consensus.max_hold_bars == 96
    assert cfg.risk_overrides.stop_mode == "sigma"
    assert cfg.risk_overrides.stop_sigma_mult == 5.0
    assert cfg.risk_overrides.tp_sigma_mult == 4.0


def test_build_cfg_legacy_spaces_use_the_passthrough():
    c = mod.combos("wave3")[0]
    cfg = mod.build_cfg(c, risk_trail_pct=0.5)
    assert cfg.risk_overrides.risk_trail_pct == 0.5
    assert mod.build_cfg(c).risk_overrides.risk_trail_pct == 0.0


def test_build_cfg_symbol_passthrough():
    c = mod.combos("wave4")[0]
    cfg = mod.build_cfg(c, symbol="binance-spot:ETHUSDT")
    assert cfg.symbols == ("binance-spot:ETHUSDT",)
    assert cfg.ema_symbol == "binance-spot:ETHUSDT"
    assert mod.build_cfg(c).symbols == (mod.SYMBOL,)   # default unchanged


# ---- CSV row shape ---------------------------------------------------------------


def _fake_sim(wr: float = 0.6, trades: int = 25, ret: float = 1.5,
              pf: float = 1.2) -> dict:
    return {"metrics": {"total_return_pct": ret, "total_trades": trades,
                        "win_rate": wr, "profit_factor": pf,
                        "max_drawdown_pct": 2.0, "costs_paid": 0.5},
            "avg_hold_bars": 4.0, "exit_breakdown": {"take_profit": 10}}


def test_wave4_row_carries_the_new_columns():
    c = next(c for c in mod.combos("wave4") if c["entry_grace_bars"] == 2
             and c["risk_trail_pct"] == 0.5)
    row = mod.build_row(c, _fake_sim())
    assert row["entry_grace_bars"] == 2
    assert row["risk_trail_pct"] == 0.5
    assert row["vote_mode"] == c["vote_mode"]
    assert row["long_only"] is True
    assert set(row) | {"eligible", "K"} == set(mod.WAVE4_CSV_FIELDS)


def test_wave3_row_has_no_wave4_columns():
    row = mod.build_row(mod.combos("wave3")[0], _fake_sim())
    assert set(row) | {"eligible"} == set(mod.WAVE3_CSV_FIELDS)
    assert "K" not in row and "entry_grace_bars" not in row


# ---- rank_wave4: guards, relax, K -------------------------------------------------


def _row(trades: int, wr: float, ret: float, pf: float = 1.5,
         dd: float = 1.0, key: str = "k") -> dict:
    return {"key": key, "return_pct": ret, "trades": trades, "win_rate": wr,
            "pf": pf, "max_dd_pct": dd, "costs": 0.0, "avg_hold_bars": 0.0,
            "exits": {}}


def test_wave4_hard_guards_rank_eligible_first_and_record_k():
    rows = [
        _row(30, 0.70, 0.5, key="elig1"),
        _row(30, 0.69, 2.0, key="elig2"),
        _row(5, 0.99, 9.0, pf=0.5, key="few"),
        _row(30, 0.90, -9.0, pf=0.5, key="inelig_wr90"),
    ]
    ranked = mod.rank_wave4(rows, min_trades=20, min_pf=1.0,
                            min_return=0.0, max_dd=5.0)
    assert [r["key"] for r in ranked[:2]] == ["elig1", "elig2"]
    assert [r["eligible"] for r in ranked] == [True, True, False, False]
    assert all(r["K"] == 2 for r in ranked)   # K = eligible candidate count


def test_wave4_relaxes_to_top_k_by_wr_union_top_k_by_pf_when_none_eligible():
    rows = [
        _row(5, 0.90, -9.0, pf=0.2, key="wr90_pf02"),
        _row(5, 0.80, -9.0, pf=0.1, key="wr80_pf01"),
        _row(5, 0.75, -9.0, pf=0.3, key="wr75_pf03"),
        _row(5, 0.60, -9.0, pf=1.8, key="wr60_pf18"),
        _row(5, 0.50, -9.0, pf=1.5, key="wr50_pf15"),
        _row(5, 0.40, -9.0, pf=0.8, key="wr40_pf08"),
    ]
    ranked = mod.rank_wave4(rows, min_trades=20, min_pf=1.0,
                            min_return=0.0, max_dd=5.0, top_k=2)
    # top-2 by WR: wr90_pf02, wr80_pf01; top-2 by PF: wr60_pf18, wr50_pf15
    assert {r["key"] for r in ranked[:4]} == {"wr90_pf02", "wr80_pf01",
                                              "wr60_pf18", "wr50_pf15"}
    assert all(r["eligible"] is False for r in ranked)
    assert all(r["K"] == 4 for r in ranked)   # K = union size of the families


def test_wave4_relax_with_top_k_larger_than_rows_keeps_all():
    rows = [_row(5, 0.9, -1.0, pf=0.2, key="a"),
            _row(5, 0.8, -1.0, pf=0.1, key="b")]
    ranked = mod.rank_wave4(rows, min_trades=20, min_pf=1.0,
                            min_return=0.0, max_dd=5.0, top_k=5)
    assert len(ranked) == 2
    assert all(r["K"] == 2 for r in ranked)
    # candidates rank by WR then return
    assert [r["key"] for r in ranked] == ["a", "b"]


def test_wave4_non_candidates_rank_after_candidates_by_wr():
    rows = [
        _row(30, 0.65, 1.0, key="elig"),
        _row(5, 0.99, -9.0, pf=0.1, key="few_wr99"),
        _row(5, 0.80, -9.0, pf=0.1, key="few_wr80"),
    ]
    ranked = mod.rank_wave4(rows, min_trades=20, min_pf=1.0,
                            min_return=0.0, max_dd=5.0)
    assert [r["key"] for r in ranked] == ["elig", "few_wr99", "few_wr80"]
    assert all(r["K"] == 1 for r in ranked)


# ---- end-to-end main() wiring (patched simulate) ---------------------------------


def _patch_simulate(monkeypatch):
    calls: list[dict] = []
    state = {"i": 0}

    def fake_simulate(klines, cfg, **kw):
        # vary the metrics per combo so the top-K-by-WR and top-K-by-PF
        # families are distinguishable rows
        i = state["i"]
        state["i"] += 1
        calls.append({"cfg": cfg, **kw})
        if i == 0:
            return _fake_sim(wr=0.9, trades=5, ret=-1.0, pf=0.2)
        return _fake_sim(wr=0.8, trades=5, ret=-1.0, pf=1.5)

    monkeypatch.setattr(mod, "simulate", fake_simulate)
    return calls


def test_main_wave4_passes_vote_mode_warmup_symbol_and_writes_k(
    tmp_path, monkeypatch, capsys
):
    klines_path = tmp_path / "klines.json"
    klines_path.write_text(json.dumps({
        "meta": {"bars": 100},
        "klines": [[1000 + i * 900000, "100", "101", "99", "100.5"] for i in range(120)],
    }))
    out = tmp_path / "wave4.csv"
    calls = _patch_simulate(monkeypatch)
    monkeypatch.setattr(
        sys, "argv",
        ["entropy_wr_sweep.py", "--klines", str(klines_path), "--out", str(out),
         "--space", "wave4", "--limit", "3", "--vote-mode", "trend",
         "--warmup-bars", "100", "--symbol", "ETHUSDT"],
    )
    mod.main()
    assert len(calls) == 3
    for call in calls:
        assert call["warmup_bars"] == 100                 # warmup parity flag
        assert call["symbol"] == "binance-spot:ETHUSDT"   # --symbol passthrough
        assert call["cfg"].consensus.vote_mode == "trend"  # per-run mode
        assert call["cfg"].ema_symbol == "binance-spot:ETHUSDT"
        assert call["entry_grace_bars"] in {0, 1, 2, 3}    # combo-driven grace
    with out.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert "K" in reader.fieldnames
    assert len(rows) == 3
    assert all(row["K"] == "3" for row in rows)   # all 3 fake rows are ineligible
    assert all(row["vote_mode"] == "trend" for row in rows)
    captured = capsys.readouterr().out
    assert "K=3 candidates" in captured


def test_main_wave4_relax_uses_top_k_flag(tmp_path, monkeypatch):
    klines_path = tmp_path / "klines.json"
    klines_path.write_text(json.dumps({
        "meta": {"bars": 100},
        "klines": [[1000 + i * 900000, "100", "101", "99", "100.5"] for i in range(120)],
    }))
    out = tmp_path / "wave4.csv"
    calls = _patch_simulate(monkeypatch)
    monkeypatch.setattr(
        sys, "argv",
        ["entropy_wr_sweep.py", "--klines", str(klines_path), "--out", str(out),
         "--space", "wave4", "--limit", "2", "--top-k", "1"],
    )
    mod.main()
    assert len(calls) == 2
    with out.open(newline="") as f:
        rows = list(csv.DictReader(f))
    # relaxed with top_k=1: 1 by WR + 1 by PF (disjoint rows) -> K = 2
    assert all(row["K"] == "2" for row in rows)


def test_main_wave3_keeps_legacy_csv_shape(tmp_path, monkeypatch):
    """wave3 main() still writes the wave3 fields (no K, no wave4 columns)
    and still passes warmup/symbol through."""
    klines_path = tmp_path / "klines.json"
    klines_path.write_text(json.dumps({
        "meta": {"bars": 100},
        "klines": [[1000 + i * 900000, "100", "101", "99", "100.5"] for i in range(120)],
    }))
    out = tmp_path / "wave3.csv"
    calls = _patch_simulate(monkeypatch)
    monkeypatch.setattr(
        sys, "argv",
        ["entropy_wr_sweep.py", "--klines", str(klines_path), "--out", str(out),
         "--space", "wave3", "--limit", "2", "--warmup-bars", "100"],
    )
    mod.main()
    assert len(calls) == 2
    with out.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert set(reader.fieldnames) == set(mod.WAVE3_CSV_FIELDS)
    assert all(call["warmup_bars"] == 100 for call in calls)