"""The score and the risk translation.

The score answers: how much more likely than a carry-neutral world does the
tape's drift make a one-sigma up move, net of how much more likely it makes a
one-sigma down move? Zero when the tape says nothing the carry did not already
say — which is the first test here, and the reason the whole construction is
falsifiable.
"""

from __future__ import annotations

import math

import pytest

from entropy.quant.distribution import (
    MAX_STOP_TP_FRAC,
    barrier,
    divergence_score,
    risk_hints,
    shrink_drift,
)


def test_barrier_is_symmetric_in_log_space():
    up = barrier(100.0, 0.5, 0.01, 1.0, up=True)
    dn = barrier(100.0, 0.5, 0.01, 1.0, up=False)
    assert up * dn == pytest.approx(100.0 * 100.0, rel=1e-12)
    assert up > 100.0 > dn


def test_score_is_zero_when_drift_equals_carry():
    assert divergence_score(
        100.0, 0.6, 0.01, carry=0.05, drift=0.05, barrier_k=1.0
    ) == pytest.approx(0.0, abs=1e-12)


def test_score_is_positive_when_drift_exceeds_carry():
    assert divergence_score(100.0, 0.6, 0.01, carry=0.0, drift=2.0, barrier_k=1.0) > 0.0


def test_score_is_negative_when_drift_trails_carry():
    assert divergence_score(100.0, 0.6, 0.01, carry=0.0, drift=-2.0, barrier_k=1.0) < 0.0


def test_score_is_antisymmetric_about_the_carry():
    # The carry is sigma**2/2 (0.6**2/2 = 0.18) and must stay there. That is the ONLY carry at
    # which antisymmetry is exact: it zeroes the offset y = (carry - sigma**2/2)*sqrt(T)/sigma
    # that sits inside d2. Away from it the residual is real arithmetic and not float noise
    # (~2e-6 at carry=0.10), so rounding this back to a tidier number reintroduces a failure.
    # It is also the only assertion here that catches a divergence_score returning edge_up
    # alone, which is not antisymmetric even at y = 0.
    up = divergence_score(100.0, 0.6, 0.01, carry=0.18, drift=0.18 + 1.5, barrier_k=1.0)
    dn = divergence_score(100.0, 0.6, 0.01, carry=0.18, drift=0.18 - 1.5, barrier_k=1.0)
    assert up == pytest.approx(-dn, abs=1e-12)


def test_score_stays_inside_the_unit_interval():
    for drift in (-500.0, -5.0, 0.0, 5.0, 500.0):
        s = divergence_score(100.0, 0.6, 0.01, carry=0.0, drift=drift, barrier_k=1.0)
        assert -1.0 <= s <= 1.0


def test_score_grows_with_the_drift_gap():
    small = divergence_score(100.0, 0.6, 0.01, carry=0.0, drift=0.5, barrier_k=1.0)
    large = divergence_score(100.0, 0.6, 0.01, carry=0.0, drift=2.0, barrier_k=1.0)
    assert large > small


def test_shrinkage_of_zero_collapses_drift_onto_carry():
    assert shrink_drift(
        9.0, 0.04, shrinkage=0.0, sigma=0.6, t_years=0.01, cap_sigmas=3.0
    ) == pytest.approx(0.04)


def test_shrinkage_of_one_keeps_the_raw_drift_when_uncapped():
    raw = 0.05
    assert shrink_drift(
        raw, 0.04, shrinkage=1.0, sigma=0.6, t_years=0.01, cap_sigmas=3.0
    ) == pytest.approx(raw)


def test_cap_bounds_a_violent_drift():
    sigma, t, cap = 0.6, 0.01, 3.0
    limit = cap * sigma / math.sqrt(t)
    got = shrink_drift(1e6, 0.0, shrinkage=1.0, sigma=sigma, t_years=t, cap_sigmas=cap)
    assert got == pytest.approx(limit)
    got_dn = shrink_drift(-1e6, 0.0, shrinkage=1.0, sigma=sigma, t_years=t, cap_sigmas=cap)
    assert got_dn == pytest.approx(-limit)
    # Kills a cap applied to mu instead of to mu - carry. The asserts above use carry=0.0, where
    # the two are arithmetically identical; at a carry of 0.5 the bound sits at 0.5 + 18.0 = 18.5,
    # where capping mu directly would say 18.0.
    assert shrink_drift(
        1e6, 0.5, shrinkage=1.0, sigma=sigma, t_years=t, cap_sigmas=cap
    ) == pytest.approx(0.5 + limit)


def test_capped_drift_cannot_saturate_the_score():
    sigma, t = 0.6, 0.01
    mu = shrink_drift(1e9, 0.0, shrinkage=1.0, sigma=sigma, t_years=t, cap_sigmas=3.0)
    # Kills a shrink_drift that stops capping. This compares against the uncapped baseline
    # rather than a constant: the old `< 0.999` could not fail, because the score is
    # structurally bounded near +/-1/2 (0.5073 at these parameters), so capped and uncapped
    # both sat far below the threshold and the cap could be deleted outright unnoticed.
    assert divergence_score(100.0, sigma, t, carry=0.0, drift=mu, barrier_k=1.0) < divergence_score(
        100.0, sigma, t, carry=0.0, drift=1e9, barrier_k=1.0
    )  # 0.4950 capped vs 0.5073 uncapped


def test_hints_hold_the_stop_to_target_ratio():
    h = risk_hints(0.60, 0.01, z_stop=1.0, z_tp=2.0, stop_floor_pct=0.05,
                   risk_budget_pct=1.0, max_size_pct=100.0)
    assert h.tp_pct == pytest.approx(2.0 * h.stop_pct)


def test_hints_clamp_at_the_risk_managers_ceiling():
    h = risk_hints(9.0, 1.0, z_stop=1.0, z_tp=2.0, stop_floor_pct=0.05,
                   risk_budget_pct=1.0, max_size_pct=100.0)
    assert h.stop_pct == pytest.approx(100.0 * MAX_STOP_TP_FRAC)
    assert h.tp_pct == pytest.approx(100.0 * MAX_STOP_TP_FRAC)
    # Kills a size_pct computed from the unclamped z_stop*root. This is the only case in the
    # file where the stop actually clamps, so it is the only place the two can disagree:
    # 1.0/0.50 = 2.0 against the unclamped stop of 9.0 saying 1.0/9.0 = 0.111.
    assert h.size_pct == pytest.approx(1.0 / MAX_STOP_TP_FRAC)


def test_hints_respect_the_floor_on_a_dead_tape():
    h = risk_hints(1e-9, 1e-9, z_stop=1.0, z_tp=2.0, stop_floor_pct=0.05,
                   risk_budget_pct=1.0, max_size_pct=100.0)
    assert h.stop_pct == pytest.approx(0.05)
    assert h.tp_pct == pytest.approx(0.05)


def test_size_never_exceeds_the_profile_allowance():
    h = risk_hints(0.05, 1e-6, z_stop=1.0, z_tp=2.0, stop_floor_pct=0.05,
                   risk_budget_pct=5.0, max_size_pct=2.5)
    assert h.size_pct == pytest.approx(2.5)


def test_size_spends_exactly_the_budget_at_the_stop():
    # Budget 1% of equity, stop 2% away -> 50% notional loses 1% at the stop.
    h = risk_hints(0.02, 1.0, z_stop=1.0, z_tp=2.0, stop_floor_pct=0.001,
                   risk_budget_pct=1.0, max_size_pct=100.0)
    assert h.stop_pct == pytest.approx(2.0)
    assert h.size_pct == pytest.approx(50.0)
    assert h.size_pct / 100.0 * h.stop_pct / 100.0 == pytest.approx(0.01)


def test_wider_stops_buy_smaller_size():
    tight = risk_hints(0.20, 0.01, z_stop=1.0, z_tp=2.0, stop_floor_pct=0.05,
                       risk_budget_pct=1.0, max_size_pct=100.0)
    wide = risk_hints(0.80, 0.01, z_stop=1.0, z_tp=2.0, stop_floor_pct=0.05,
                      risk_budget_pct=1.0, max_size_pct=100.0)
    assert wide.stop_pct > tight.stop_pct
    assert wide.size_pct < tight.size_pct
