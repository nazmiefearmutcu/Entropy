#!/usr/bin/env python3
"""Regenerate src/entropy/data/us_tickers_snapshot.json (dev-run-once; REAL network).

Fetches SEC EDGAR's company_tickers.json (ordered by market cap) via stockodile's
rate-limited client and keeps the first ~500 entries. If EDGAR is unreachable, falls
back to the sim-feed universe (entropy.feeds.equities.universe.UNIVERSE) with a
built-in name map so the committed artifact always exists.

Never runs under pytest — invoke manually: `uv run python scripts/gen_ticker_snapshot.py`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "entropy" / "data"
SNAPSHOT = _DATA_DIR / "us_tickers_snapshot.json"
LIMIT = 500

# Names for the ~150 sim-universe tickers (fallback only; EDGAR titles win when reachable).
FALLBACK_NAMES: dict[str, str] = {
    # indices
    "SPY": "SPDR S&P 500 ETF Trust", "QQQ": "Invesco QQQ Trust",
    "IWM": "iShares Russell 2000 ETF",
    # megacap
    "AAPL": "Apple Inc.", "MSFT": "Microsoft Corp.", "NVDA": "NVIDIA Corp.",
    "AMZN": "Amazon.com Inc.", "GOOGL": "Alphabet Inc.", "META": "Meta Platforms Inc.",
    "AVGO": "Broadcom Inc.", "TSLA": "Tesla Inc.", "BRKB": "Berkshire Hathaway Inc.",
    "LLY": "Eli Lilly & Co.",
    # semis
    "AMD": "Advanced Micro Devices Inc.", "INTC": "Intel Corp.", "MU": "Micron Technology Inc.",
    "QCOM": "Qualcomm Inc.", "TXN": "Texas Instruments Inc.", "ASML": "ASML Holding NV",
    "AMAT": "Applied Materials Inc.", "LRCX": "Lam Research Corp.", "KLAC": "KLA Corp.",
    "ARM": "Arm Holdings plc", "SMCI": "Super Micro Computer Inc.",
    "MRVL": "Marvell Technology Inc.", "ON": "ON Semiconductor Corp.",
    "NXPI": "NXP Semiconductors NV", "MPWR": "Monolithic Power Systems Inc.",
    "WOLF": "Wolfspeed Inc.", "SWKS": "Skyworks Solutions Inc.", "QRVO": "Qorvo Inc.",
    "STM": "STMicroelectronics NV", "ENTG": "Entegris Inc.",
    # software / internet
    "CRM": "Salesforce Inc.", "ORCL": "Oracle Corp.", "ADBE": "Adobe Inc.",
    "NOW": "ServiceNow Inc.", "SNOW": "Snowflake Inc.", "PLTR": "Palantir Technologies Inc.",
    "DDOG": "Datadog Inc.", "NET": "Cloudflare Inc.", "CRWD": "CrowdStrike Holdings Inc.",
    "PANW": "Palo Alto Networks Inc.", "ZS": "Zscaler Inc.", "MDB": "MongoDB Inc.",
    "TEAM": "Atlassian Corp.", "SHOP": "Shopify Inc.", "SPOT": "Spotify Technology SA",
    "UBER": "Uber Technologies Inc.", "ABNB": "Airbnb Inc.", "COIN": "Coinbase Global Inc.",
    "HOOD": "Robinhood Markets Inc.", "SQ": "Block Inc.", "PYPL": "PayPal Holdings Inc.",
    # finance
    "JPM": "JPMorgan Chase & Co.", "BAC": "Bank of America Corp.", "WFC": "Wells Fargo & Co.",
    "GS": "Goldman Sachs Group Inc.", "MS": "Morgan Stanley", "C": "Citigroup Inc.",
    "SCHW": "Charles Schwab Corp.", "BLK": "BlackRock Inc.", "AXP": "American Express Co.",
    "V": "Visa Inc.", "MA": "Mastercard Inc.", "COF": "Capital One Financial Corp.",
    "USB": "U.S. Bancorp", "TFC": "Truist Financial Corp.",
    "PNC": "PNC Financial Services Group Inc.",
    "FITB": "Fifth Third Bancorp", "KEY": "KeyCorp", "CFG": "Citizens Financial Group Inc.",
    "MTB": "M&T Bank Corp.", "RF": "Regions Financial Corp.",
    # health
    "UNH": "UnitedHealth Group Inc.", "JNJ": "Johnson & Johnson", "MRK": "Merck & Co. Inc.",
    "ABBV": "AbbVie Inc.", "PFE": "Pfizer Inc.", "TMO": "Thermo Fisher Scientific Inc.",
    "DHR": "Danaher Corp.", "ABT": "Abbott Laboratories", "BMY": "Bristol-Myers Squibb Co.",
    "AMGN": "Amgen Inc.", "GILD": "Gilead Sciences Inc.", "VRTX": "Vertex Pharmaceuticals Inc.",
    "REGN": "Regeneron Pharmaceuticals Inc.", "ISRG": "Intuitive Surgical Inc.",
    "MRNA": "Moderna Inc.", "BIIB": "Biogen Inc.", "ILMN": "Illumina Inc.",
    "IQV": "IQVIA Holdings Inc.", "CI": "Cigna Group", "HUM": "Humana Inc.",
    # industrial / defense
    "GE": "GE Aerospace", "CAT": "Caterpillar Inc.", "DE": "Deere & Co.",
    "BA": "Boeing Co.", "HON": "Honeywell International Inc.", "UNP": "Union Pacific Corp.",
    "GWW": "W.W. Grainger Inc.", "ETN": "Eaton Corp. plc", "EMR": "Emerson Electric Co.",
    "PH": "Parker-Hannifin Corp.", "RTX": "RTX Corp.", "LMT": "Lockheed Martin Corp.",
    "NOC": "Northrop Grumman Corp.", "GD": "General Dynamics Corp.",
    # energy
    "XOM": "Exxon Mobil Corp.", "CVX": "Chevron Corp.", "COP": "ConocoPhillips",
    "SLB": "SLB (Schlumberger)", "EOG": "EOG Resources Inc.", "MPC": "Marathon Petroleum Corp.",
    "PSX": "Phillips 66", "OXY": "Occidental Petroleum Corp.", "FANG": "Diamondback Energy Inc.",
    "DVN": "Devon Energy Corp.",
    # consumer
    "WMT": "Walmart Inc.", "COST": "Costco Wholesale Corp.", "HD": "Home Depot Inc.",
    "LOW": "Lowe's Companies Inc.", "NKE": "Nike Inc.", "MCD": "McDonald's Corp.",
    "SBUX": "Starbucks Corp.", "TGT": "Target Corp.", "PG": "Procter & Gamble Co.",
    "KO": "Coca-Cola Co.", "PEP": "PepsiCo Inc.", "DIS": "Walt Disney Co.",
    "NFLX": "Netflix Inc.",
    # volatile
    "GME": "GameStop Corp.", "AMC": "AMC Entertainment Holdings Inc.",
    "MSTR": "MicroStrategy Inc.", "RIVN": "Rivian Automotive Inc.", "LCID": "Lucid Group Inc.",
    "CVNA": "Carvana Co.", "AFRM": "Affirm Holdings Inc.", "SOFI": "SoFi Technologies Inc.",
    "DKNG": "DraftKings Inc.", "RBLX": "Roblox Corp.", "U": "Unity Software Inc.",
    "AI": "C3.ai Inc.", "IONQ": "IonQ Inc.", "PLUG": "Plug Power Inc.",
    "SOUN": "SoundHound AI Inc.", "BBAI": "BigBear.ai Holdings Inc.",
    "MARA": "MARA Holdings Inc.", "RIOT": "Riot Platforms Inc.",
    "HUT": "Hut 8 Corp.", "CLSK": "CleanSpark Inc.",
}


async def fetch_edgar() -> list[dict[str, str]]:
    from stockodile.providers.sec_edgar.client import SecEdgarClient

    from entropy.data.universe import EDGAR_TICKERS_URL, parse_edgar_payload

    client = SecEdgarClient()
    try:
        raw = await client._request_json(EDGAR_TICKERS_URL)
    finally:
        await client.close()
    pairs = parse_edgar_payload(raw)
    return [{"symbol": s, "name": n} for s, n in pairs[:LIMIT]]


def build_fallback() -> list[dict[str, str]]:
    from entropy.feeds.equities.universe import UNIVERSE

    return [{"symbol": s, "name": FALLBACK_NAMES.get(s, s)} for s in UNIVERSE]


def main() -> int:
    try:
        rows = asyncio.run(fetch_edgar())
        provenance = "EDGAR"
    except Exception as exc:  # network is best-effort; the artifact must always exist
        print(f"EDGAR fetch failed ({exc!r}); using sim-universe fallback", file=sys.stderr)
        rows = build_fallback()
        provenance = "sim-universe fallback"
    SNAPSHOT.write_text(json.dumps(rows, indent=1) + "\n")
    print(f"wrote {len(rows)} tickers ({provenance}) -> {SNAPSHOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
