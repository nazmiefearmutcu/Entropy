"""ChainVolSource refuses loudly rather than degrading quietly.

There are three ways it cannot answer and all three are the same rule: a run
configured for implied volatility must never silently trade on realized
volatility, because the ledger would then name a model the run did not use.
"""

from __future__ import annotations

import polars as pl

from entropy.quant.conventions import for_symbol
from entropy.quant.vol import ChainVolSource

CRYPTO = "binance-spot:BTCUSDT"
EQUITY = "SPY"


def closes(n: int = 100) -> list[float]:
    return [100.0 + i * 0.01 for i in range(n)]


def test_equities_are_refused_because_the_feed_is_a_simulator():
    src = ChainVolSource(catalog=object())
    assert src.sigma(EQUITY, closes(), for_symbol(EQUITY, 60.0)) is None
    assert "simulator" in src.last_reason


def test_no_catalog_means_no_answer():
    src = ChainVolSource(catalog=None)
    assert src.sigma(CRYPTO, closes(), for_symbol(CRYPTO, 60.0)) is None
    assert "catalog" in src.last_reason


def test_empty_chain_means_no_answer():
    class EmptyCatalog:
        pass

    src = ChainVolSource(catalog=EmptyCatalog(), now_ns=lambda: 1_700_000_000_000_000_000)
    src._term_structure = lambda *a, **k: pl.DataFrame()  # type: ignore[attr-defined]
    assert src.sigma(CRYPTO, closes(), for_symbol(CRYPTO, 60.0)) is None
    assert "no chain" in src.last_reason


def test_nearest_expiry_atm_iv_is_returned():
    class Catalog:
        pass

    frame = pl.DataFrame({
        "days_to_expiry": [30.0, 7.0, 90.0],
        "atm_iv": [0.55, 0.62, 0.50],
    })
    src = ChainVolSource(catalog=Catalog(), now_ns=lambda: 1_700_000_000_000_000_000)
    src._term_structure = lambda *a, **k: frame  # type: ignore[attr-defined]
    got = src.sigma(CRYPTO, closes(), for_symbol(CRYPTO, 60.0))
    assert got == 0.62
    assert src.last_reason == ""


def test_non_positive_iv_is_refused():
    class Catalog:
        pass

    frame = pl.DataFrame({"days_to_expiry": [7.0], "atm_iv": [0.0]})
    src = ChainVolSource(catalog=Catalog(), now_ns=lambda: 1_700_000_000_000_000_000)
    src._term_structure = lambda *a, **k: frame  # type: ignore[attr-defined]
    assert src.sigma(CRYPTO, closes(), for_symbol(CRYPTO, 60.0)) is None
    assert "iv" in src.last_reason


def test_a_nan_iv_is_refused_rather_than_returned_as_conviction():
    """`nan` is not a small number, it is a maximum-conviction long.

    A bare ``<= 0.0`` guard waves `nan` through, and downstream it does not stay
    `nan`: `divergence_score` ends in ``max(-1.0, min(1.0, x))`` and
    ``min(1.0, nan)`` is ``1.0``, so the score clamps to full conviction and the
    strategy enters. Refusing is the only honest answer.
    """

    class Catalog:
        pass

    frame = pl.DataFrame({"days_to_expiry": [7.0], "atm_iv": [float("nan")]})
    src = ChainVolSource(catalog=Catalog(), now_ns=lambda: 1_700_000_000_000_000_000)
    src._term_structure = lambda *a, **k: frame  # type: ignore[attr-defined]
    assert src.sigma(CRYPTO, closes(), for_symbol(CRYPTO, 60.0)) is None
    assert "iv" in src.last_reason


def test_a_passed_expiry_does_not_shadow_the_live_one():
    """Crocodile emits expired expiries with a negative day count and a null IV.

    An unfiltered sort takes the most expired row and the source refuses forever
    while a live quote sits one row below. "Nearest" means nearest ahead.
    """

    class Catalog:
        pass

    frame = pl.DataFrame(
        {"days_to_expiry": [-3.0, 7.0, 30.0], "atm_iv": [None, 0.62, 0.55]},
        schema={"days_to_expiry": pl.Float64, "atm_iv": pl.Float64},
    )
    src = ChainVolSource(catalog=Catalog(), now_ns=lambda: 1_700_000_000_000_000_000)
    src._term_structure = lambda *a, **k: frame  # type: ignore[attr-defined]
    assert src.sigma(CRYPTO, closes(), for_symbol(CRYPTO, 60.0)) == 0.62
    assert src.last_reason == ""


def test_a_wholly_expired_chain_says_so():
    class Catalog:
        pass

    frame = pl.DataFrame(
        {"days_to_expiry": [-3.0, -10.0], "atm_iv": [None, None]},
        schema={"days_to_expiry": pl.Float64, "atm_iv": pl.Float64},
    )
    src = ChainVolSource(catalog=Catalog(), now_ns=lambda: 1_700_000_000_000_000_000)
    src._term_structure = lambda *a, **k: frame  # type: ignore[attr-defined]
    assert src.sigma(CRYPTO, closes(), for_symbol(CRYPTO, 60.0)) is None
    assert "already passed" in src.last_reason


def test_a_malformed_frame_becomes_a_reason_not_an_exception():
    """The contract is "return None and say why", with no path that raises."""

    class Catalog:
        pass

    frame = pl.DataFrame({"atm_iv": [0.62]})  # no days_to_expiry column
    src = ChainVolSource(catalog=Catalog(), now_ns=lambda: 1_700_000_000_000_000_000)
    src._term_structure = lambda *a, **k: frame  # type: ignore[attr-defined]
    assert src.sigma(CRYPTO, closes(), for_symbol(CRYPTO, 60.0)) is None
    assert src.last_reason != ""


def test_a_raising_catalog_is_reported_not_swallowed():
    class Catalog:
        pass

    def boom(*a, **k):
        raise RuntimeError("duckdb is unhappy")

    src = ChainVolSource(catalog=Catalog(), now_ns=lambda: 1_700_000_000_000_000_000)
    src._term_structure = boom  # type: ignore[attr-defined]
    assert src.sigma(CRYPTO, closes(), for_symbol(CRYPTO, 60.0)) is None
    assert "duckdb is unhappy" in src.last_reason


def test_it_satisfies_the_vol_source_protocol():
    from entropy.quant.vol import VolSource

    src: VolSource = ChainVolSource()
    assert src.name == "chain"
