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
import time
from collections.abc import Callable
from pathlib import Path

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
    """One searchable instrument."""

    symbol: str        # equities: bare ticker ("AAPL"); crypto: feed canonical
    name: str          # human-readable ("Apple Inc.", "Bitcoin · Binance spot")
    asset_class: str   # "equity" | "crypto"
    venue: str         # "us" | "binance-spot" | "coinbase"


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


def _crypto_universe() -> tuple[SymbolInfo, ...]:
    out: list[SymbolInfo] = []
    for raw in BINANCE_MAJORS:
        base = raw.removesuffix("USDT")
        out.append(SymbolInfo(
            symbol=f"binance-spot:{raw}",
            name=f"{_COIN_NAMES.get(base, base)} · Binance spot",
            asset_class="crypto",
            venue="binance-spot",
        ))
    for raw in COINBASE_MAJORS:
        base = raw.split("-", 1)[0]
        out.append(SymbolInfo(
            symbol=f"coinbase:{raw}",
            name=f"{_COIN_NAMES.get(base, base)} · Coinbase",
            asset_class="crypto",
            venue="coinbase",
        ))
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
        self._loaded = False
        self._search_order: list[SymbolInfo] = []
        self._by_symbol: dict[str, SymbolInfo] = {}

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
        # Lazy import: stockodile (aiohttp et al.) only loads on explicit refresh.
        # SecEdgarClient.fetch_ticker_map() discards company titles (it only keeps
        # ticker<->CIK), so we pull the same payload through the client's
        # rate-limited/retrying request path and parse titles ourselves.
        from stockodile.providers.sec_edgar.client import SecEdgarClient

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
        equities = [
            SymbolInfo(symbol=s, name=n, asset_class="equity", venue=_EQUITY_VENUE)
            for s, n in pairs
        ]
        # Search scan order doubles as the within-tier tiebreak: equities
        # (alphabetical) ahead of crypto (alphabetical by canonical).
        self._search_order = sorted(equities, key=lambda i: i.symbol) + list(self._crypto)
        self._by_symbol = {i.symbol: i for i in self._search_order}
        self._loaded = True

    # --- search ----------------------------------------------------------

    def search(self, query: str, limit: int = _DEFAULT_LIMIT) -> list[SymbolInfo]:
        """Ranked lookup: exact symbol > symbol prefix > symbol substring > name substring.

        Case-insensitive; stable within tiers (equities alphabetical, then crypto).
        An empty/whitespace query returns curated defaults.
        """
        if limit <= 0:
            return []
        q = query.strip().upper()
        if not q:
            return self._defaults(limit)
        self._ensure_loaded()
        exact: list[SymbolInfo] = []
        prefix: list[SymbolInfo] = []
        contains: list[SymbolInfo] = []
        by_name: list[SymbolInfo] = []
        for info in self._search_order:
            full = info.symbol.upper()
            key = full.rsplit(":", 1)[-1]  # crypto: match on the raw exchange symbol
            if q in (key, full):
                exact.append(info)
            elif key.startswith(q):
                prefix.append(info)
            elif q in full:
                contains.append(info)
            elif q in info.name.upper():
                by_name.append(info)
        ranked = [*exact, *prefix, *contains, *by_name]
        return ranked[:limit]

    def _defaults(self, limit: int) -> list[SymbolInfo]:
        self._ensure_loaded()
        out: list[SymbolInfo] = []
        for sym in _CURATED_DEFAULTS[:limit]:
            info = self._by_symbol.get(sym)
            if info is None:  # equity absent from the loaded layer: synthesize
                info = SymbolInfo(
                    symbol=sym,
                    name=_DEFAULT_NAMES.get(sym, sym),
                    asset_class="equity",
                    venue=_EQUITY_VENUE,
                )
            out.append(info)
        return out
