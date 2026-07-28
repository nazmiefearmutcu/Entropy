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
        # exact ticker > ticker prefix > a name STARTING with the token >
        # the token buried mid-ticker. "QX Mining Ltd" leading with the query is
        # a stronger signal than "AQXZ" merely containing it.
        ("QX", ["QX", "QXA", "ZZZ", "AQXZ"]),
        # case-insensitive on every tier
        ("qx", ["QX", "QXA", "ZZZ", "AQXZ"]),
        ("aapl", ["AAPL"]),
        # name-contains only
        ("microso", ["MSFT"]),
        # surrounding whitespace is stripped
        ("  QX  ", ["QX", "QXA", "ZZZ", "AQXZ"]),
    ],
)
def test_search_ranking(tmp_path: Path, query: str, expected_head: list[str]) -> None:
    write_cache(tmp_path / "cache", NOW, RANKING_TICKERS)
    svc = service(tmp_path)
    got = symbols(svc.search(query))
    assert got[: len(expected_head)] == expected_head


def test_exact_base_asset_beats_a_ticker_that_merely_starts_with_it(tmp_path: Path) -> None:
    """Typing "BTC" means Bitcoin, not "BTC Mining Corp".

    The old ranking put every equity whose ticker merely STARTED with the query
    ahead of the coin the query names, because it only ever matched the ticker
    string. Matching the base asset exactly now outranks a ticker prefix.
    """
    write_cache(tmp_path / "cache", NOW, [{"symbol": "BTCM", "name": "BTC Mining Corp"}])
    svc = service(tmp_path)
    got = symbols(svc.search("BTC"))
    assert got[:2] == ["binance-spot:BTCUSDT", "coinbase:BTC-USD"]
    assert "BTCM" in got


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
    # The venue lives in its own field now instead of being spliced into the
    # name, so the name can just say what the instrument is.
    assert bn.name == "Bitcoin / TetherUS"
    assert cb.name == "Bitcoin / US Dollar"
    assert (bn.ticker, bn.exchange, bn.base, bn.quote) == ("BTCUSDT", "BINANCE", "BTC", "USDT")
    assert (cb.ticker, cb.exchange, cb.base, cb.quote) == ("BTC-USD", "COINBASE", "BTC", "USD")


def test_crypto_name_search(tmp_path: Path) -> None:
    svc = service(tmp_path)
    got = symbols(svc.search("bitcoin"))
    assert "binance-spot:BTCUSDT" in got
    assert "coinbase:BTC-USD" in got
    # "Bitcoin Cash" matches by name too, but strictly AFTER Bitcoin itself —
    # the old alphabetical tiebreak inside the name tier put BCH first.
    assert any("BCH" in s for s in got)
    assert got.index("binance-spot:BTCUSDT") < min(
        i for i, s in enumerate(got) if "BCH" in s
    )


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


# --- terminal-style display + multi-token search -------------------------------


def test_row_splits_into_ticker_instrument_exchange(tmp_path: Path) -> None:
    """A row must read like a trading terminal's.

    Before, the symbol column carried the whole canonical id — nine identical
    leading characters that clipped to "binance-s…" — and the name repeated the
    venue a second time.
    """
    svc = service(tmp_path)
    bn = next(i for i in svc.search("BTCUSDT") if i.symbol == "binance-spot:BTCUSDT")
    assert bn.ticker == "BTCUSDT"          # what the exchange calls it
    assert bn.name == "Bitcoin / TetherUS"  # what it actually is
    assert bn.exchange == "BINANCE"        # a badge, not part of either column
    assert bn.symbol == "binance-spot:BTCUSDT"  # canonical id still intact


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("BTC", "binance-spot:BTCUSDT"),          # abbreviation
        ("bitcoin", "binance-spot:BTCUSDT"),      # spelled out
        ("Bitcoin Cash", "binance-spot:BCHUSDT"), # multi-word long name
        ("bitcoincash", "binance-spot:BCHUSDT"),  # glued long name
        ("doge", "binance-spot:DOGEUSDT"),
        ("dogecoin", "binance-spot:DOGEUSDT"),
        ("stellar", "binance-spot:XLMUSDT"),      # name shares nothing with XLM
        ("chainlink", "binance-spot:LINKUSDT"),
    ],
)
def test_coin_found_by_abbreviation_and_by_full_name(
    tmp_path: Path, query: str, expected: str
) -> None:
    assert symbols(service(tmp_path).search(query))[0] == expected


@pytest.mark.parametrize("query", ["btc usd", "BTC/USD", "btc-usd", "  btc   usd  "])
def test_multi_token_query_picks_the_right_pair(tmp_path: Path, query: str) -> None:
    """Every token must match, so the quote currency actually narrows the result.

    A single-token matcher returned NOTHING for "btc usd" — the most natural way
    to ask for that pair.
    """
    got = symbols(service(tmp_path).search(query))
    assert got[0] == "coinbase:BTC-USD"
    assert "binance-spot:BTCUSDT" not in got[:1]


def test_quote_currency_selects_between_venues(tmp_path: Path) -> None:
    assert symbols(service(tmp_path).search("eth usdt"))[0] == "binance-spot:ETHUSDT"
    assert symbols(service(tmp_path).search("eth usd"))[0] == "coinbase:ETH-USD"


def test_exchange_name_does_not_flood_short_queries(tmp_path: Path) -> None:
    """"COIN" is the Coinbase Global listing, not an instruction to list every
    Coinbase pair.

    The old matcher hit the venue inside the canonical symbol, so Aave, Cardano
    and Avalanche — which have nothing whatever to do with "coin" — ranked above
    most real matches. Pairs whose NAME contains "coin" (Bitcoin, Litecoin) are
    still legitimate hits; pairs that only share the venue are not.
    """
    write_cache(tmp_path / "cache", NOW, [{"symbol": "COIN", "name": "Coinbase Global, Inc."}])
    got = symbols(service(tmp_path).search("COIN"))
    assert got[0] == "COIN"
    venue_only = {"coinbase:AAVE-USD", "coinbase:ADA-USD", "coinbase:AVAX-USD",
                  "coinbase:XLM-USD", "coinbase:LINK-USD"}
    assert venue_only.isdisjoint(got)
    assert "coinbase:BTC-USD" in got  # matched on "Bitcoin", not on the venue


def test_exchange_is_searchable_when_spelled_out(tmp_path: Path) -> None:
    """The venue is still reachable — it just needs a token long enough to mean it."""
    got = symbols(service(tmp_path).search("coinbase"))
    # Every Coinbase pair is reachable, including the ones with no "coin" in
    # their name — that is the difference a 5+ character token makes.
    assert "coinbase:AAVE-USD" in got
    assert "coinbase:BTC-USD" in got
    assert symbols(service(tmp_path).search("binance"))[0].startswith("binance-spot:")


def test_short_token_does_not_match_mid_word_in_a_name(tmp_path: Path) -> None:
    """"sol" is Solana and Motorola SOLutions, but not conSOLidated."""
    write_cache(tmp_path / "cache", NOW, [
        {"symbol": "ED", "name": "CONSOLIDATED EDISON INC"},
        {"symbol": "IR", "name": "Ingersoll Rand Inc."},
        {"symbol": "MSI", "name": "Motorola Solutions, Inc."},
    ])
    got = symbols(service(tmp_path).search("sol"))
    assert got[0] == "binance-spot:SOLUSDT"
    assert "MSI" in got            # a name WORD starting with the token still counts
    assert "ED" not in got
    assert "IR" not in got


def test_every_token_must_match(tmp_path: Path) -> None:
    assert service(tmp_path).search("bitcoin ethereum") == []


def test_split_pair_handles_both_conventions() -> None:
    from entropy.data.universe import split_pair

    assert split_pair("BTCUSDT") == ("BTC", "USDT")
    assert split_pair("BTC-USD") == ("BTC", "USD")
    assert split_pair("BTC/USD") == ("BTC", "USD")
    assert split_pair("ETHBTC") == ("ETH", "BTC")
    assert split_pair("WEIRD") == ("WEIRD", "")   # degrades, never raises
    assert split_pair("USDT") == ("USDT", "")     # a bare quote is not a pair


def test_make_symbol_info_fills_the_display_fields() -> None:
    from entropy.data.universe import make_symbol_info

    unknown = make_symbol_info("kraken:FOO-EUR")
    assert unknown.ticker == "FOO-EUR"
    assert unknown.exchange == "KRAKEN"   # venue we have no label for still reads sanely
    assert unknown.base == "FOO"
    assert unknown.name == "FOO / Euro"
    equity = make_symbol_info("AAPL", "Apple Inc.")
    assert (equity.ticker, equity.exchange, equity.base) == ("AAPL", "US", "")


def test_display_ticker_strips_the_venue() -> None:
    from entropy.data.universe import display_ticker

    assert display_ticker("binance-spot:BTCUSDT") == "BTCUSDT"
    assert display_ticker("AAPL") == "AAPL"


# --- the whole-market symbol shapes crocodile's ~109 venues actually publish ---
#
# Entropy used to see two venues' spot pairs and nothing else, so the parser only
# ever met "BTC-USD" and "BTCUSDT". Audited against 40 991 live instruments from
# 20 venues, 45% of them parsed wrong the moment ccxt's unified spelling arrived:
# "BTC/USDT:USDT" partitioned to a quote of "USDT:USDT". These pin the shapes.

@pytest.mark.parametrize("raw,expected", [
    ("BTC/USDT", ("BTC", "USDT")),                    # ccxt spot
    ("BTC/USDT:USDT", ("BTC", "USDT")),               # ccxt perpetual: settle tail
    ("ETH/USD:ETH", ("ETH", "USD")),                  # inverse perp: settles in base
    ("BTC/USD:USD-260731", ("BTC", "USD")),           # dated future
    ("BTC/USD:USD-260731-100000-C", ("BTC", "USD")),  # option: strike + right
    ("BTC-USD", ("BTC", "USD")),                      # punctuated spot
    ("BTC_USD", ("BTC", "USD")),
    ("BTCUSDT", ("BTC", "USDT")),                     # glued
    ("1000SHIBUSDT", ("1000SHIB", "USDT")),           # leading digits are the base
    ("BTC-PERPETUAL", ("BTC", "")),                   # deribit: a kind, not a quote
    ("BTC-25DEC26-100000-C", ("BTC", "")),            # deribit option: expiry, not quote
    ("USDT", ("USDT", "")),                           # a bare quote is not a pair
])
def test_split_pair_handles_every_venue_shape(raw: str, expected: tuple[str, str]) -> None:
    from entropy.data.universe import split_pair

    assert split_pair(raw) == expected


class _FakeInstrument:
    """Shaped like crocodile's Instrument — only the fields the mapper reads."""

    def __init__(self, canonical, exchange, symbol_raw, base, quote, kind):
        self.canonical = canonical
        self.exchange = exchange
        self.symbol_raw = symbol_raw
        self.base = base
        self.quote = quote
        self.kind = kind


def test_instrument_path_is_told_not_guessed() -> None:
    """The shape that defeats string parsing is trivial when the venue reports it."""
    from entropy.data.universe import symbol_info_from_instrument

    inst = _FakeInstrument("deribit:BTC-25DEC26-100000-C", "deribit",
                           "BTC-25DEC26-100000-C", "BTC", "USD", "option")
    info = symbol_info_from_instrument(inst)
    assert (info.base, info.quote) == ("BTC", "USD")   # the id alone cannot yield this
    assert info.symbol == "deribit:BTC-25DEC26-100000-C"
    assert info.venue == "deribit"
    assert info.asset_class == "crypto"


def test_instrument_name_distinguishes_contract_kinds() -> None:
    """A venue lists BTC/USDT and BTC/USDT:USDT side by side; the picker must not
    show the same row twice."""
    from entropy.data.universe import symbol_info_from_instrument

    spot = symbol_info_from_instrument(
        _FakeInstrument("binance:BTC/USDT", "binance", "BTC/USDT", "BTC", "USDT", "spot"))
    perp = symbol_info_from_instrument(
        _FakeInstrument("binance:BTC/USDT:USDT", "binance", "BTC/USDT:USDT",
                        "BTC", "USDT", "perpetual"))
    assert spot.name == "Bitcoin / TetherUS"
    assert perp.name == "Bitcoin / TetherUS PERP"
    assert spot.name != perp.name


# --- growing the crypto half beyond the curated majors (no network) ----------

def _inst(canonical, exchange, raw, base, quote, kind="spot"):
    return _FakeInstrument(canonical, exchange, raw, base, quote, kind)


@pytest.fixture
def _fake_venue(monkeypatch):
    """Stub crocodile's venue enumeration; returns the call recorder."""
    listed = [
        _inst("kraken:A/USD", "kraken", "A/USD", "A", "USD"),
        _inst("kraken:G/USD", "kraken", "G/USD", "G", "USD"),
        _inst("kraken:BTC/USD", "kraken", "BTC/USD", "BTC", "USD"),
    ]

    async def fake_instruments(exchange, **kw):
        return list(listed)

    async def fake_ranked(exchange, n, **kw):
        return ["BTC/USD"]          # only BTC is liquid

    import crocodile.crypto.instruments.universe as cu
    monkeypatch.setattr(cu, "exchange_instruments", fake_instruments)
    monkeypatch.setattr(cu, "top_symbols_by_volume", fake_ranked)
    return listed


async def test_add_venue_makes_new_symbols_searchable(tmp_path: Path, _fake_venue) -> None:
    svc = UniverseService(cache_dir=tmp_path)
    before = {i.symbol for i in svc.search("btc", limit=10)}
    assert "kraken:BTC/USD" not in before      # curated majors only, to start

    assert await svc.add_venue("kraken") == 3
    after = {i.symbol for i in svc.search("btc", limit=10)}
    assert "kraken:BTC/USD" in after
    assert before < after                      # the majors are kept, not replaced


async def test_add_venue_is_idempotent(tmp_path: Path, _fake_venue) -> None:
    svc = UniverseService(cache_dir=tmp_path)
    assert await svc.add_venue("kraken") == 3
    assert await svc.add_venue("kraken") == 0      # nothing new the second time


async def test_add_venue_limit_keeps_the_liquid_names_not_the_first_listed(
    tmp_path: Path, _fake_venue
) -> None:
    """A cap must not drop BTC while keeping A/USD — the bug this ordering fixes."""
    svc = UniverseService(cache_dir=tmp_path)
    added = await svc.add_venue("kraken", limit=1)
    assert added == 1
    assert {i.symbol for i in svc.search("btc", limit=10)} >= {"kraken:BTC/USD"}


async def test_add_venue_falls_back_to_listing_order_when_ranking_fails(
    tmp_path: Path, monkeypatch, _fake_venue
) -> None:
    """An unrankable venue still contributes symbols rather than raising."""
    import crocodile.crypto.instruments.universe as cu

    async def boom(exchange, n, **kw):
        raise RuntimeError("ticker endpoint down")

    monkeypatch.setattr(cu, "top_symbols_by_volume", boom)
    svc = UniverseService(cache_dir=tmp_path)
    assert await svc.add_venue("kraken", limit=2) == 2
