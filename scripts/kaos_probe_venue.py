#!/usr/bin/env python3
"""One-off signed READ-ONLY probe: (1) is ALGO stop 3000002180929115 alive?
(2) does a list-open-algo-orders endpoint exist? (3) open normal orders?
Keys from botmonitor secrets.json (never printed)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from kaos_testnet_exec import TestnetExecutor, HOST_MAINNET, ExecutorError  # noqa: E402

sec = json.loads(Path(r"C:\botmonitor\gateway\secrets.json").read_text(encoding="utf-8"))
ex = TestnetExecutor(str(sec["mainnet_api_key"]), str(sec["mainnet_api_secret"]),
                     host=HOST_MAINNET, leverage=1)

print("== single algo order 3000002180929115 ==")
try:
    r = ex._request("GET", "/fapi/v1/algoOrder",
                    {"symbol": "ETHUSDT", "algoid": 3000002180929115})
    print(json.dumps({k: r.get(k) for k in
                      ("orderId", "algoId", "algoStatus", "status", "side",
                       "type", "triggerPrice", "stopPrice", "closePosition",
                       "algoType", "reduceOnly", "origQty", "quantity")},
                     indent=1))
except ExecutorError as e:
    print("FAIL", e)

for path in ("/fapi/v1/algoOpenOrders", "/fapi/v1/openAlgoOrders",
             "/fapi/v1/algoOrders"):
    print(f"== list probe {path} ==")
    try:
        r = ex._request("GET", path, {"symbol": "ETHUSDT"})
        s = json.dumps(r)[:400]
        print("OK", s)
    except ExecutorError as e:
        print("FAIL", str(e)[:160])

print("== open normal orders ETHUSDT ==")
try:
    r = ex._request("GET", "/fapi/v1/openOrders", {"symbol": "ETHUSDT"})
    print(json.dumps(r)[:500])
except ExecutorError as e:
    print("FAIL", e)

print("== balance/positions sanity ==")
try:
    print(ex.get_balance())
    for p in ex.get_open_positions():
        print(p["symbol"], p["qty"], p["side"], p["notional"])
except ExecutorError as e:
    print("FAIL", e)
