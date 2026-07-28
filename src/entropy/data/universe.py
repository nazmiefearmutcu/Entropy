"""Unified symbol universe: EDGAR-backed US equities + curated crypto majors.

Equities are loaded in layers — a 24h-TTL disk cache written by :meth:`UniverseService.refresh`
(explicit; nothing here touches the network at construction), falling back to a bundled
snapshot committed alongside this module. Crypto is a static list derived from the feed
whitelists in :mod:`entropy.feeds.crypto`, using the same canonical symbols the live feed
publishes (``binance-spot:BTCUSDT`` / ``coinbase:BTC-USD``).
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import msgspec

from entropy.feeds.crypto import BINANCE_MAJORS, COINBASE_MAJORS

log = logging.getLogger(__name__)

EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_CACHE_TTL_S = 24 * 3600.0
_CACHE_FILENAME = "tickers.json"
_SNAPSHOT_PATH = Path(__file__).parent / "us_tickers_snapshot.json"
_EQUITY_VENUE = "us"
_DEFAULT_LIMIT = 20


class SymbolInfo(msgspec.Struct, frozen=True):
    """One searchable instrument, split the way a symbol picker wants to show it.

    ``symbol`` stays the canonical feed id because the engine, watchlist and
    charts key off it. Everything else exists so a row can read like a trading
    terminal's — the exchange's OWN ticker in the symbol column, the instrument
    spelled out beside it, and the venue as a separate badge:

        BTCUSDT    Bitcoin / TetherUS    BINANCE

    rather than cramming the venue into both columns (``binance-spot:BTCUSDT``
    /``Bitcoin · Binance spot``), which truncated to ``binance-s…`` in narrow
    panes and made every row start with the same nine characters.

    The derived fields default to empty so watchlist files written before they
    existed still decode; :func:`make_symbol_info` fills them in.
    """

    symbol: str        # canonical feed id: "AAPL", "binance-spot:BTCUSDT"
    name: str          # "Apple Inc.", "Bitcoin / TetherUS"
    asset_class: str   # "equity" | "crypto"
    venue: str         # "us" | "binance-spot" | "coinbase"
    ticker: str = ""   # the exchange's own symbol: "AAPL", "BTCUSDT", "BTC-USD"
    exchange: str = "" # badge text: "US", "BINANCE", "COINBASE"
    base: str = ""     # crypto only: "BTC"
    quote: str = ""    # crypto only: "USDT"


class _TickerEntry(msgspec.Struct):
    """On-disk shape shared by the cache and the bundled snapshot."""

    symbol: str
    name: str


class _CacheDoc(msgspec.Struct):
    fetched_at: float
    tickers: list[_TickerEntry]


# ~25 base coins covering the curated exchange whitelists.
_COIN_NAMES: dict[str, str] = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "XRP": "XRP",
    "DOGE": "Dogecoin",
    "ADA": "Cardano",
    "AVAX": "Avalanche",
    "LINK": "Chainlink",
    "LTC": "Litecoin",
    "BCH": "Bitcoin Cash",
    "DOT": "Polkadot",
    "UNI": "Uniswap",
    "AAVE": "Aave",
    "XLM": "Stellar",
    "MATIC": "Polygon",
    "ATOM": "Cosmos",
    "NEAR": "NEAR Protocol",
    "ARB": "Arbitrum",
    "OP": "Optimism",
    "FIL": "Filecoin",
    "ETC": "Ethereum Classic",
    "ALGO": "Algorand",
    "ICP": "Internet Computer",
    "SHIB": "Shiba Inu",
    "TRX": "TRON",
}


# Quote assets spelled out, so a pair reads "Bitcoin / TetherUS" instead of
# "BTCUSDT" twice over. Unknown quotes fall back to their own ticker.
_QUOTE_NAMES: dict[str, str] = {
    "USDT": "TetherUS",
    "USDC": "USD Coin",
    "BUSD": "Binance USD",
    "USD": "US Dollar",
    "EUR": "Euro",
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
}

# Venue id -> the short badge a trading terminal puts next to the ticker.
_EXCHANGE_LABELS: dict[str, str] = {
    "binance-spot": "BINANCE",
    "coinbase": "COINBASE",
    _EQUITY_VENUE: "US",
}

# Quote suffixes, longest first so "USDT" wins over "USD" on "BTCUSDT".
_QUOTE_SUFFIXES: tuple[str, ...] = ("USDT", "USDC", "BUSD", "USD", "EUR", "BTC", "ETH")

# Second fields that name a contract type rather than a settlement currency.
# Deribit spells its perpetual "BTC-PERPETUAL", which otherwise parses to a
# quote of "PERPETUAL" and feeds that nonsense into search scoring.
_CONTRACT_MARKERS: frozenset[str] = frozenset({"PERPETUAL", "PERP", "SWAP", "FS"})


def exchange_label(venue: str) -> str:
    return _EXCHANGE_LABELS.get(venue, venue.upper() or "—")


def display_ticker(symbol: str) -> str:
    """The exchange's own symbol, for tables that carry only a canonical id.

    Scanner boards key their rows on the canonical symbol, which meant every
    crypto row read ``binance-spot:BTCUSDT`` and clipped to ``binance-s…`` —
    nine identical leading characters before any information.
    """
    return symbol.split(":", 1)[1] if ":" in symbol else symbol


def split_pair(raw: str) -> tuple[str, str]:
    """(base, quote) for an exchange pair symbol — a FALLBACK guess.

    Prefer :func:`symbol_info_from_instrument` when a crocodile ``Instrument`` is
    at hand: it carries base/quote as the venue itself reports them, and no
    amount of string parsing beats being told. This function exists for the
    paths that only ever see a canonical id (watchlist files, a symbol the user
    typed, a row restored from disk).

    Handles the shapes crocodile's venues actually produce:

    * ccxt unified — ``BASE/QUOTE``, ``BASE/QUOTE:SETTLE`` (perp),
      ``BASE/QUOTE:SETTLE-YYMMDD`` (future), and the option form with strike and
      right appended. The quote is what sits between ``/`` and the first ``:``.
    * punctuated spot — ``BTC-USD``, ``BTC_USD``.
    * glued — ``BTCUSDT``, matched against known quote suffixes.

    Measured against 14 670 live instruments across binance/kraken/coinbase/
    bybit/okx before this handled the ``:SETTLE`` tail, 45% of them parsed
    wrong — every perpetual and every dated future — because ``BTC/USDT:USDT``
    partitioned to a quote of ``USDT:USDT``. A symbol we cannot read degrades to
    ``(raw, "")``: an honest blank beats a confident wrong answer, since the
    quote column feeds search scoring.
    """
    if "/" in raw:
        base, _, rest = raw.partition("/")
        # Strip the settlement tail (":USDT", ":USD-260731", option suffixes).
        quote = rest.partition(":")[0]
        return base, quote
    for sep in ("-", "_"):
        if sep in raw:
            parts = raw.split(sep)
            # Two parts is usually a spot pair ("BTC-USD"). More means a dated or
            # optioned contract ("BTC-25DEC26-100000-C") whose second field is
            # an expiry, not a quote — say we do not know rather than guess it.
            if len(parts) != 2 or parts[1] in _CONTRACT_MARKERS:
                return parts[0], ""
            return parts[0], parts[1]
    for suffix in _QUOTE_SUFFIXES:
        if raw.endswith(suffix) and len(raw) > len(suffix):
            return raw[: -len(suffix)], suffix
    return raw, ""


def make_symbol_info(
    symbol: str, name: str = "", asset_class: str = "", venue: str = ""
) -> SymbolInfo:
    """Build a fully-populated :class:`SymbolInfo` from a canonical symbol.

    Single place the derived display fields are computed, so the universe, the
    watchlist loader and the UI's unknown-symbol fallback cannot disagree about
    what a row should look like.
    """
    if ":" in symbol:
        derived_venue, raw = symbol.split(":", 1)
        venue = venue or derived_venue
        asset_class = asset_class or "crypto"
        base, quote = split_pair(raw.upper())
        if not name:
            base_name = _COIN_NAMES.get(base, base)
            quote_name = _QUOTE_NAMES.get(quote, quote)
            name = f"{base_name} / {quote_name}" if quote_name else base_name
        return SymbolInfo(
            symbol=symbol, name=name, asset_class=asset_class, venue=venue,
            ticker=raw.upper(), exchange=exchange_label(venue), base=base, quote=quote,
        )
    venue = venue or _EQUITY_VENUE
    return SymbolInfo(
        symbol=symbol, name=name or symbol, asset_class=asset_class or "equity",
        venue=venue, ticker=symbol.upper(), exchange=exchange_label(venue),
    )


# Contract kinds worth spelling out in a row's name. Spot is the unmarked case:
# every venue lists mostly spot, so a "SPOT" badge on 60% of rows is noise.
_KIND_SUFFIX: dict[str, str] = {
    "perpetual": "PERP",
    "future": "FUT",
    "option": "OPT",
}


def symbol_info_from_instrument(inst: object) -> SymbolInfo:
    """Build a :class:`SymbolInfo` from a crocodile ``Instrument``.

    The difference from :func:`make_symbol_info` is the whole point: an
    Instrument reports its own ``base``/``quote``/``kind`` as the venue defines
    them, so nothing is inferred from the spelling of the symbol. Use this
    wherever an instrument is available — discovery, universe refresh — and keep
    the string parser for the paths that genuinely only have an id.

    Contract kind is appended to the name because a venue lists ``BTC/USDT``
    and ``BTC/USDT:USDT`` side by side, and without it both rows read
    "Bitcoin / TetherUS" and the picker offers the same instrument twice.
    """
    base = (getattr(inst, "base", "") or "").upper()
    quote = (getattr(inst, "quote", "") or "").upper()
    venue = getattr(inst, "exchange", "") or ""
    raw = getattr(inst, "symbol_raw", "") or ""
    kind = getattr(inst, "kind", None)
    kind_value = getattr(kind, "value", kind)

    base_name = _COIN_NAMES.get(base, base)
    quote_name = _QUOTE_NAMES.get(quote, quote)
    name = f"{base_name} / {quote_name}" if quote_name else base_name
    suffix = _KIND_SUFFIX.get(str(kind_value))
    if suffix:
        name = f"{name} {suffix}"

    return SymbolInfo(
        symbol=getattr(inst, "canonical", raw), name=name, asset_class="crypto",
        venue=venue, ticker=raw.upper(), exchange=exchange_label(venue),
        base=base, quote=quote,
    )


# --- search scoring ---------------------------------------------------------
#
# One weight per kind of match, best-per-token wins. The ordering is the whole
# design: a ticker you typed exactly must always beat an instrument that merely
# mentions those letters, and an exchange name must never outrank either.
_MATCH_TICKER_EXACT = 100.0
_MATCH_BASE_EXACT = 95.0
_MATCH_ALIAS_EXACT = 92.0     # the coin spelled out: "bitcoin" -> BTC
_MATCH_TICKER_PREFIX = 80.0
_MATCH_BASE_PREFIX = 75.0
_MATCH_WORD_PREFIX = 60.0     # a word of the instrument name starts with the token
_MATCH_QUOTE_EXACT = 40.0     # "btc usd" -> the USD pair, not the USDT one
_MATCH_TICKER_PART = 30.0
_MATCH_NAME_PART = 10.0
_MATCH_EXCHANGE = 5.0

#: Tickers are short and dense, so matching inside one stays useful at two
#: characters ("QX" -> AQXZ). Instrument NAMES are prose, where the same reach
#: drags in "conSOLidated" for "sol" — hence the separate, higher floor.
_MIN_TICKER_PART_TOKEN = 2
_MIN_NAME_PART_TOKEN = 4
#: Below this length a token may not match an exchange at all: otherwise "COIN"
#: returns every Coinbase pair ahead of the actual COIN listing.
_MIN_EXCHANGE_TOKEN = 5

_UNRANKED = 10_000

_TOKEN_SPLIT = re.compile(r"[^A-Z0-9]+")


def _tokenize(query: str) -> list[str]:
    """Uppercase tokens from a free-text query; punctuation is a separator so
    "BTC/USD", "btc-usd" and "btc usd" all parse the same way."""
    return [t for t in _TOKEN_SPLIT.split(query.strip().upper()) if t]


class _IndexEntry(msgspec.Struct, frozen=True):
    """Precomputed match surfaces for one instrument."""

    info: SymbolInfo
    ticker: str
    canonical: str
    base: str
    quote: str
    exchange: str
    name_upper: str
    name_words: tuple[str, ...]
    aliases: frozenset[str]
    rank: int


def _index_entry(info: SymbolInfo, rank: int) -> _IndexEntry:
    name_upper = info.name.upper()
    words = tuple(w for w in _TOKEN_SPLIT.split(name_upper) if w)
    aliases = {info.ticker, info.base} if info.base else {info.ticker}
    if info.base:
        # The coin's long name, both spaced and glued, so "bitcoin cash" and
        # "bitcoincash" both land on BCH exactly rather than by word-prefix.
        long_name = _COIN_NAMES.get(info.base, "").upper()
        if long_name:
            aliases.add(long_name)
            aliases.add(long_name.replace(" ", ""))
    return _IndexEntry(
        info=info, ticker=info.ticker.upper(), canonical=info.symbol.upper(),
        base=info.base.upper(), quote=info.quote.upper(), exchange=info.exchange.upper(),
        name_upper=name_upper, name_words=words,
        aliases=frozenset(a for a in aliases if a), rank=rank,
    )


def _score_token(token: str, entry: _IndexEntry) -> float:
    """Best score this token can claim against one instrument; 0 = no match."""
    if token == entry.ticker or token == entry.canonical:
        return _MATCH_TICKER_EXACT
    if entry.base and token == entry.base:
        return _MATCH_BASE_EXACT
    if token in entry.aliases:
        return _MATCH_ALIAS_EXACT
    if entry.ticker.startswith(token):
        return _MATCH_TICKER_PREFIX
    if entry.base and entry.base.startswith(token):
        return _MATCH_BASE_PREFIX
    if any(w.startswith(token) for w in entry.name_words):
        return _MATCH_WORD_PREFIX
    if entry.quote and token == entry.quote:
        return _MATCH_QUOTE_EXACT
    if len(token) >= _MIN_TICKER_PART_TOKEN and token in entry.ticker:
        return _MATCH_TICKER_PART
    if len(token) >= _MIN_NAME_PART_TOKEN and token in entry.name_upper:
        return _MATCH_NAME_PART
    if len(token) >= _MIN_EXCHANGE_TOKEN and entry.exchange.startswith(token):
        return _MATCH_EXCHANGE
    return 0.0


def _crypto_universe() -> tuple[SymbolInfo, ...]:
    out: list[SymbolInfo] = []
    for raw in BINANCE_MAJORS:
        out.append(make_symbol_info(f"binance-spot:{raw}"))
    for raw in COINBASE_MAJORS:
        out.append(make_symbol_info(f"coinbase:{raw}"))
    out.sort(key=lambda s: s.symbol)
    return tuple(out)


def parse_edgar_payload(data: object) -> list[tuple[str, str]]:
    """(ticker, title) pairs from SEC's company_tickers.json, EDGAR order, deduped.

    The payload is ``{"0": {"cik_str": ..., "ticker": ..., "title": ...}, ...}``,
    ordered by market cap. Also used by scripts/gen_ticker_snapshot.py.
    """
    if not isinstance(data, dict):
        raise ValueError(f"unexpected EDGAR ticker payload: {type(data).__name__}")
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in data.values():
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker", "")).strip().upper()
        title = str(item.get("title", "")).strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        pairs.append((ticker, title))
    return pairs


# Curated empty-query defaults: index ETFs, BTC/ETH canonicals, then megacaps.
_CURATED_DEFAULTS: tuple[str, ...] = (
    "SPY", "QQQ", "IWM",
    "binance-spot:BTCUSDT", "coinbase:BTC-USD",
    "binance-spot:ETHUSDT", "coinbase:ETH-USD",
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
    "AVGO", "BRKB", "LLY", "JPM", "V", "XOM",
)

#: Prominence order used only to break score ties — the curated default list
#: doubles as "what a trader most likely meant" when two rows match equally well.
_PROMINENCE: dict[str, int] = {sym: i for i, sym in enumerate(_CURATED_DEFAULTS)}

# Names for defaults that may be missing from the loaded universe (ETF trusts are
# not in EDGAR's operating-company ticker file; sim tickers like BRKB differ from
# EDGAR's BRK-B). Synthesized entries keep the defaults list stable regardless.
_DEFAULT_NAMES: dict[str, str] = {
    "SPY": "SPDR S&P 500 ETF Trust",
    "QQQ": "Invesco QQQ Trust",
    "IWM": "iShares Russell 2000 ETF",
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corp.",
    "NVDA": "NVIDIA Corp.",
    "GOOGL": "Alphabet Inc.",
    "AMZN": "Amazon.com Inc.",
    "META": "Meta Platforms Inc.",
    "TSLA": "Tesla Inc.",
    "AVGO": "Broadcom Inc.",
    "BRKB": "Berkshire Hathaway Inc.",
    "LLY": "Eli Lilly & Co.",
    "JPM": "JPMorgan Chase & Co.",
    "V": "Visa Inc.",
    "XOM": "Exxon Mobil Corp.",
}


class UniverseService:
    """Symbol lookup across US equities (EDGAR) and crypto majors.

    Construction is I/O-free. The first :meth:`search` lazily loads equities from
    the freshest valid layer (TTL'd cache, else bundled snapshot). Only an explicit
    ``await refresh()`` ever touches the network.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        default_dir = Path.home() / ".entropy" / "cache"
        self._cache_dir = cache_dir if cache_dir is not None else default_dir
        self._clock = clock
        self._crypto = _crypto_universe()
        self._equities: list[SymbolInfo] = []
        self._loaded = False
        self._search_order: list[SymbolInfo] = []
        self._by_symbol: dict[str, SymbolInfo] = {}
        self._index: list[_IndexEntry] = []

    @property
    def cache_path(self) -> Path:
        return self._cache_dir / _CACHE_FILENAME

    # --- loading ---------------------------------------------------------

    async def refresh(self) -> None:
        """Fetch the live EDGAR ticker map, atomically persist it, then swap it in.

        Explicit by design; errors (network, disk) propagate to the caller and
        leave both the cache file and the in-memory universe untouched.
        """
        pairs = await self._fetch_edgar_tickers()
        self._write_cache(pairs)
        self._set_equities(pairs)

    async def _fetch_edgar_tickers(self) -> list[tuple[str, str]]:
        # Lazy import: crocodile's equity stack (aiohttp et al.) only loads on explicit refresh.
        # SecEdgarClient.fetch_ticker_map() discards company titles (it only keeps
        # ticker<->CIK), so we pull the same payload through the client's
        # rate-limited/retrying request path and parse titles ourselves.
        from crocodile.equity.providers.sec_edgar.client import SecEdgarClient

        client = SecEdgarClient()
        try:
            raw = await client._request_json(EDGAR_TICKERS_URL)
        finally:
            await client.close()
        return parse_edgar_payload(raw)

    def _write_cache(self, pairs: list[tuple[str, str]]) -> None:
        path = self.cache_path
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = _CacheDoc(
            fetched_at=self._clock(),
            tickers=[_TickerEntry(symbol=s, name=n) for s, n in pairs],
        )
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_bytes(msgspec.json.encode(doc))
            os.replace(tmp, path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        pairs = self._read_cache()
        if pairs is None:
            pairs = self._read_snapshot()
        self._set_equities(pairs)

    def _read_cache(self) -> list[tuple[str, str]] | None:
        path = self.cache_path
        try:
            raw = path.read_bytes()
        except OSError:
            return None
        try:
            doc = msgspec.json.decode(raw, type=_CacheDoc)
        except msgspec.DecodeError:
            log.debug("universe: corrupt ticker cache at %s; using bundled snapshot", path)
            return None
        if self._clock() - doc.fetched_at >= _CACHE_TTL_S:
            log.debug("universe: ticker cache at %s expired; using bundled snapshot", path)
            return None
        return [(e.symbol, e.name) for e in doc.tickers]

    def _read_snapshot(self) -> list[tuple[str, str]]:
        try:
            entries = msgspec.json.decode(_SNAPSHOT_PATH.read_bytes(), type=list[_TickerEntry])
        except (OSError, msgspec.DecodeError):
            log.debug("universe: bundled snapshot unreadable at %s", _SNAPSHOT_PATH)
            return []
        return [(e.symbol, e.name) for e in entries]

    def _set_equities(self, pairs: list[tuple[str, str]]) -> None:
        self._equities = sorted(
            (make_symbol_info(s, n, "equity", _EQUITY_VENUE) for s, n in pairs),
            key=lambda i: i.symbol,
        )
        self._rebuild()

    def _rebuild(self) -> None:
        """Re-derive the search order and index from the two universe halves.

        Split out from :meth:`_set_equities` so :meth:`add_venue` can grow the
        crypto half without re-reading the equity layer from disk.
        """
        order = list(self._equities) + list(self._crypto)
        self._search_order = order
        self._by_symbol = {i.symbol: i for i in order}
        self._index = [_index_entry(i, _PROMINENCE.get(i.symbol, _UNRANKED)) for i in order]
        self._loaded = True

    async def add_venue(
        self,
        exchange: str,
        *,
        quote: str | None = None,
        kinds: set[object] | None = None,
        limit: int | None = None,
    ) -> int:
        """Load a crypto venue's real instrument list into the searchable universe.

        The curated majors are a cold-start default, not a ceiling: this asks any
        of crocodile's ~109 venues what it actually lists and makes every one of
        those symbols searchable. Instruments are mapped through
        :func:`symbol_info_from_instrument`, so base/quote/kind come from the
        venue rather than from parsing the symbol's spelling.

        Explicit and network-bound, exactly like :meth:`refresh` — nothing here
        runs on the UI boot path. Returns the number of NEW symbols added;
        re-adding a venue is idempotent. Filter with ``quote``/``kinds`` and cap
        with ``limit``, because a single large venue lists thousands of pairs and
        an unfiltered add of several venues makes the picker unusable.

        ``limit`` keeps the most-liquid symbols, ranked by 24h quote volume —
        not the first N the venue happens to list. That ordering matters: taking
        Kraken's listing order dropped BTC/USD from a 400-symbol cap while
        keeping ``A/USD`` and ``G/USD``, which is the opposite of what someone
        capping a universe wants. If ranking is unavailable (the venue refuses
        the ticker call), it falls back to listing order rather than failing.
        """
        from crocodile.crypto.instruments.universe import (
            exchange_instruments,
            filter_instruments,
            top_symbols_by_volume,
        )

        insts = await exchange_instruments(exchange)
        if quote is not None or kinds is not None:
            insts = filter_instruments(insts, quote=quote, kinds=kinds)  # type: ignore[arg-type]
        if limit is not None and len(insts) > limit:
            insts = await self._rank_by_liquidity(exchange, insts, limit, quote, kinds,
                                                  top_symbols_by_volume)

        known = {i.symbol for i in self._crypto}
        fresh: list[SymbolInfo] = []
        for inst in insts:
            info = symbol_info_from_instrument(inst)
            if info.symbol in known:
                continue
            known.add(info.symbol)
            fresh.append(info)
        if not fresh:
            return 0
        self._crypto = tuple(sorted(self._crypto + tuple(fresh), key=lambda s: s.symbol))
        if self._loaded:
            self._rebuild()
        return len(fresh)

    @staticmethod
    async def _rank_by_liquidity(
        exchange: str,
        insts: list[Any],
        limit: int,
        quote: str | None,
        kinds: set[object] | None,
        top_symbols_by_volume: Callable[..., Any],
    ) -> list[Any]:
        """The ``limit`` most-liquid of *insts*, or listing order if unrankable."""
        try:
            ranked = await top_symbols_by_volume(
                exchange, limit, quote=quote, kinds=kinds,
            )
        except Exception:
            log.debug("universe: %s could not be ranked by volume; "
                      "falling back to listing order", exchange, exc_info=True)
            return insts[:limit]
        if not ranked:
            return insts[:limit]
        position = {sym: i for i, sym in enumerate(ranked)}
        picked = [i for i in insts if i.symbol_raw in position]
        picked.sort(key=lambda i: position[i.symbol_raw])
        # Ranking can return fewer names than asked for; top up from listing
        # order so a `limit` is a cap, never a silent shortfall.
        if len(picked) < limit:
            seen = {i.symbol_raw for i in picked}
            picked += [i for i in insts if i.symbol_raw not in seen][: limit - len(picked)]
        return picked[:limit]

    # --- search ----------------------------------------------------------

    def search(self, query: str, limit: int = _DEFAULT_LIMIT) -> list[SymbolInfo]:
        """Ranked lookup over tickers, instrument names and exchanges.

        The query is split on whitespace and pair punctuation, and EVERY token
        must match something — so "btc usd" finds the BTC/USD pair instead of
        the nothing a single-token matcher returned. Each token scores by how
        precisely it matched (see :data:`_MATCH_*`), the scores add up, and ties
        break on prominence then ticker length.

        Two deliberate exclusions keep the noise out:

        * an exchange only matches a token of 5+ characters, so searching "COIN"
          returns Coinbase Global the stock rather than every Coinbase pair;
        * loose substring matching needs a 4+ character token, so "sol" finds
          Solana and Motorola Solutions but not "conSOLidated" or "ingerSOLl".

        Case-insensitive. An empty query returns curated defaults.
        """
        if limit <= 0:
            return []
        tokens = _tokenize(query)
        if not tokens:
            return self._defaults(limit)
        self._ensure_loaded()
        scored: list[tuple[float, int, int, str, SymbolInfo]] = []
        for entry in self._index:
            total = 0.0
            for token in tokens:
                hit = _score_token(token, entry)
                if hit <= 0.0:
                    total = 0.0
                    break            # AND semantics: an unmatched token rejects the row
                total += hit
            if total > 0.0:
                scored.append((-total, entry.rank, len(entry.ticker),
                               entry.info.symbol, entry.info))
        scored.sort(key=lambda row: row[:4])
        return [row[4] for row in scored[:limit]]

    def _defaults(self, limit: int) -> list[SymbolInfo]:
        self._ensure_loaded()
        out: list[SymbolInfo] = []
        for sym in _CURATED_DEFAULTS[:limit]:
            info = self._by_symbol.get(sym)
            if info is None:  # equity absent from the loaded layer: synthesize
                info = make_symbol_info(sym, _DEFAULT_NAMES.get(sym, sym),
                                        "equity", _EQUITY_VENUE)
            out.append(info)
        return out
