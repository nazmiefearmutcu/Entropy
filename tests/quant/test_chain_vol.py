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
