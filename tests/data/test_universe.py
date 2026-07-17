# tests/data/test_universe.py
"""UniverseService: layered equity loading (cache -> bundled) + fuzzy search."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from entropy.data.universe import SymbolInfo, UniverseService

NOW = 1_700_000_000.0
TTL = 24 * 3600.0


def write_cache(cache_dir: Path, fetched_at: float, tickers: list[dict[str, str]]) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "tickers.json"
    path.write_text(json.dumps({"fetched_at": fetched_at, "tickers": tickers}))
    return path


def service(tmp_path: Path, *, now: float = NOW) -> UniverseService:
    return UniverseService(cache_dir=tmp_path / "cache", clock=lambda: now)


def symbols(infos: list[SymbolInfo]) -> list[str]:
    return [i.symbol for i in infos]


# --- search ranking -----------------------------------------------------------

# "QX" stem chosen so no crypto symbol/name lands in the same tiers.
RANKING_TICKERS = [
    {"symbol": "QX", "name": "Qx Corp"},           # exact for "QX"
    {"symbol": "QXA", "name": "Qxa Inc."},         # symbol prefix
    {"symbol": "AQXZ", "name": "Aqxz Ltd"},        # symbol substring
    {"symbol": "ZZZ", "name": "QX Mining Ltd"},    # name substring
    {"symbol": "AAPL", "name": "Apple Inc."},
    {"symbol": "MSFT", "name": "Microsoft Corp"},
]


@pytest.mark.parametrize(
    ("query", "expected_head"),
    [
        # exact > prefix > substring > name-contains
        ("QX", ["QX", "QXA", "AQXZ", "ZZZ"]),
        # case-insensitive on every tier
        ("qx", ["QX", "QXA", "AQXZ", "ZZZ"]),
        ("aapl", ["AAPL"]),
        # name-contains only
        ("microso", ["MSFT"]),
        # surrounding whitespace is stripped
        ("  QX  ", ["QX", "QXA", "AQXZ", "ZZZ"]),
    ],
)
def test_search_ranking(tmp_path: Path, query: str, expected_head: list[str]) -> None:
    write_cache(tmp_path / "cache", NOW, RANKING_TICKERS)
    svc = service(tmp_path)
    got = symbols(svc.search(query))
    assert got[: len(expected_head)] == expected_head


def test_search_equities_before_crypto_within_tier(tmp_path: Path) -> None:
    write_cache(tmp_path / "cache", NOW, [{"symbol": "BTCM", "name": "BTC Mining Corp"}])
    svc = service(tmp_path)
    got = symbols(svc.search("BTC"))
    # prefix tier: equity first (alphabetical), then crypto (alphabetical by canonical)
    assert got[:3] == ["BTCM", "binance-spot:BTCUSDT", "coinbase:BTC-USD"]


def test_search_limit(tmp_path: Path) -> None:
    tickers = [{"symbol": f"QQ{i}", "name": f"Q Corp {i}"} for i in range(30)]
    write_cache(tmp_path / "cache", NOW, tickers)
    svc = service(tmp_path)
    assert len(svc.search("QQ", limit=5)) == 5
    assert len(svc.search("QQ")) == 20  # default limit
    assert svc.search("QQ", limit=0) == []


def test_search_no_match(tmp_path: Path) -> None:
    write_cache(tmp_path / "cache", NOW, RANKING_TICKERS)
    assert service(tmp_path).search("XYZZY123") == []


@pytest.mark.parametrize("query", ["", "   ", "\t"])
def test_empty_query_returns_curated_defaults(tmp_path: Path, query: str) -> None:
    write_cache(tmp_path / "cache", NOW, RANKING_TICKERS)
    svc = service(tmp_path)
    got = svc.search(query)
    head = symbols(got)
    assert head[:3] == ["SPY", "QQQ", "IWM"]
    assert "binance-spot:BTCUSDT" in head
    assert "binance-spot:ETHUSDT" in head
    assert len(got) <= 20
    # defaults are synthesized even when absent from the loaded universe
    spy = got[0]
    assert spy.asset_class == "equity"
    assert spy.name  # human-readable, non-empty
    # limit applies to defaults too
    assert symbols(svc.search("", limit=2)) == ["SPY", "QQQ"]


# --- crypto universe ----------------------------------------------------------

def test_crypto_entries_present_with_canonicals(tmp_path: Path) -> None:
    svc = service(tmp_path)
    got = {i.symbol: i for i in svc.search("BTC")}
    bn = got["binance-spot:BTCUSDT"]
    cb = got["coinbase:BTC-USD"]
    assert bn.asset_class == cb.asset_class == "crypto"
    assert bn.venue == "binance-spot"
    assert cb.venue == "coinbase"
    assert "Bitcoin" in bn.name and "Binance" in bn.name
    assert "Bitcoin" in cb.name and "Coinbase" in cb.name


def test_crypto_name_search(tmp_path: Path) -> None:
    svc = service(tmp_path)
    got = symbols(svc.search("bitcoin"))
    assert "binance-spot:BTCUSDT" in got
    assert "coinbase:BTC-USD" in got
    # "Bitcoin Cash" matches by name too, ranked after plain prefix hits
    assert any("BCH" in s for s in got)


def test_crypto_majors_all_mapped(tmp_path: Path) -> None:
    svc = service(tmp_path)
    for query, name in [("SOL", "Solana"), ("DOGE", "Dogecoin"), ("XLM", "Stellar")]:
        infos = svc.search(query)
        assert any(name in i.name for i in infos), (query, symbols(infos))


# --- cache layering -----------------------------------------------------------

def test_valid_cache_is_used(tmp_path: Path) -> None:
    write_cache(tmp_path / "cache", NOW - TTL + 60, [{"symbol": "ZZZZTEST", "name": "Zzzz Test"}])
    svc = service(tmp_path)
    assert symbols(svc.search("ZZZZTEST")) == ["ZZZZTEST"]
    # cache replaced the bundled snapshot entirely
    assert svc.search("AAPL") == []


def test_expired_cache_falls_back_to_bundled(tmp_path: Path) -> None:
    write_cache(tmp_path / "cache", NOW - TTL - 1, [{"symbol": "ZZZZTEST", "name": "Zzzz Test"}])
    svc = service(tmp_path)
    assert svc.search("ZZZZTEST") == []
    assert symbols(svc.search("AAPL"))[0] == "AAPL"  # bundled snapshot has megacaps


def test_corrupt_cache_falls_back_to_bundled(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "tickers.json").write_text("{not json!!")
    svc = service(tmp_path)
    assert symbols(svc.search("AAPL"))[0] == "AAPL"


def test_missing_cache_falls_back_to_bundled(tmp_path: Path) -> None:
    svc = service(tmp_path)
    assert symbols(svc.search("AAPL"))[0] == "AAPL"


# --- refresh ------------------------------------------------------------------

def _patch_fetch(monkeypatch: pytest.MonkeyPatch, rows: list[tuple[str, str]]) -> list[int]:
    calls: list[int] = []

    async def fake(self: UniverseService) -> list[tuple[str, str]]:
        calls.append(1)
        return rows

    monkeypatch.setattr(UniverseService, "_fetch_edgar_tickers", fake)
    return calls


async def test_refresh_writes_cache_and_updates_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_fetch(monkeypatch, [("FRESH", "Fresh Corp")])
    svc = service(tmp_path)
    await svc.refresh()
    assert symbols(svc.search("FRESH")) == ["FRESH"]
    # cache file written with the fake clock's timestamp
    doc = json.loads((tmp_path / "cache" / "tickers.json").read_text())
    assert doc["fetched_at"] == NOW
    assert doc["tickers"] == [{"symbol": "FRESH", "name": "Fresh Corp"}]
    # a brand-new service sees the refreshed cache (valid TTL)
    assert symbols(service(tmp_path).search("FRESH")) == ["FRESH"]


async def test_refresh_atomic_failure_keeps_old_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = write_cache(tmp_path / "cache", NOW - 60, [{"symbol": "OLD", "name": "Old Corp"}])
    before = old.read_text()
    _patch_fetch(monkeypatch, [("NEW", "New Corp")])
    svc = service(tmp_path)

    def boom(src: object, dst: object) -> None:
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr("entropy.data.universe.os.replace", boom)
    with pytest.raises(OSError, match="simulated"):
        await svc.refresh()
    assert old.read_text() == before                      # old cache intact
    assert symbols(svc.search("OLD")) == ["OLD"]          # memory not poisoned
    assert svc.search("NEW") == []


async def test_construction_never_fetches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_fetch(monkeypatch, [("X", "X Corp")])
    svc = service(tmp_path)
    svc.search("AAPL")
    svc.search("")
    assert calls == []  # only explicit refresh() may fetch
    await svc.refresh()
    assert calls == [1]
