from __future__ import annotations

import random
from dataclasses import dataclass

INDICES = ("SPY", "QQQ", "IWM")

MEGACAP = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRKB", "LLY")
SEMIS = (
    "AMD", "INTC", "MU", "QCOM", "TXN", "ASML", "AMAT", "LRCX", "KLAC", "ARM",
    "SMCI", "MRVL", "ON", "NXPI", "MPWR", "WOLF", "SWKS", "QRVO", "STM", "ENTG",
)
SOFTWARE = (
    "CRM", "ORCL", "ADBE", "NOW", "SNOW", "PLTR", "DDOG", "NET", "CRWD", "PANW",
    "ZS", "MDB", "TEAM", "SHOP", "SPOT", "UBER", "ABNB", "COIN", "HOOD", "SQ", "PYPL",
)
FINANCE = (
    "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "AXP", "V",
    "MA", "COF", "USB", "TFC", "PNC", "FITB", "KEY", "CFG", "MTB", "RF",
)
HEALTH = (
    "UNH", "JNJ", "MRK", "ABBV", "PFE", "TMO", "DHR", "ABT", "BMY", "AMGN",
    "GILD", "VRTX", "REGN", "ISRG", "MRNA", "BIIB", "ILMN", "IQV", "CI", "HUM",
)
INDUSTRIAL = (
    "GE", "CAT", "DE", "BA", "HON", "UNP", "GWW", "ETN", "EMR", "PH", "RTX", "LMT", "NOC", "GD",
)
ENERGY = ("XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "OXY", "FANG", "DVN")
CONSUMER = (
    "WMT", "COST", "HD", "LOW", "NKE", "MCD", "SBUX", "TGT", "PG", "KO", "PEP", "DIS", "NFLX",
)
VOLATILE = (
    "GME", "AMC", "MSTR", "RIVN", "LCID", "CVNA", "AFRM", "SOFI", "DKNG", "RBLX",
    "U", "AI", "IONQ", "PLUG", "SOUN", "BBAI", "MARA", "RIOT", "HUT", "CLSK",
)

SECTORS: dict[str, tuple[str, ...]] = {
    "megacap": MEGACAP, "semis": SEMIS, "software": SOFTWARE, "finance": FINANCE,
    "health": HEALTH, "industrial": INDUSTRIAL, "energy": ENERGY,
    "consumer": CONSUMER, "volatile": VOLATILE,
}
_all_stocks: tuple[str, ...] = tuple(dict.fromkeys(sum(SECTORS.values(), ())))
UNIVERSE: tuple[str, ...] = INDICES + _all_stocks

# Curated subset for the LIVE stockodile feed (~28 tickers): keyless providers
# poll per-symbol, and keyed providers cap subscriptions, so keep this small.
LIVE_UNIVERSE: tuple[str, ...] = INDICES + MEGACAP + SEMIS[:8] + FINANCE[:7]


@dataclass(slots=True)
class SymParams:
    s0: float          # opening price
    sigma_bps: float   # per-tick vol in bps of price
    drift_bps: float   # tiny per-tick drift
    mr_kappa: float    # mean-reversion strength toward intraday anchor
    base_size: float   # typical share size
    sector: str


def build_params(rng: random.Random) -> dict[str, SymParams]:
    out: dict[str, SymParams] = {}
    for sym in INDICES:
        out[sym] = SymParams(rng.uniform(180, 520), rng.uniform(0.3, 0.8), 0.0, 0.02,
                             rng.uniform(200, 800), "index")
    vol_mult = {"volatile": 3.5, "semis": 2.0, "software": 1.8}
    for sec, syms in SECTORS.items():
        m = vol_mult.get(sec, 1.0)
        for sym in syms:
            out[sym] = SymParams(rng.uniform(15, 900), rng.uniform(1.0, 3.0) * m,
                                 rng.uniform(-0.2, 0.2), rng.uniform(0.005, 0.03),
                                 rng.uniform(50, 400), sec)
    return out
