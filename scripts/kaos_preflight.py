"""KAOS mainnet preflight — Z-J F5 (read-only signed checks; NO orders).

Closes the two "first-order unknowns" from reports/analiz-2026-09-07/Z-J-sentez.md B4
without sending anything:
  1. positionSide/dual — dualSidePosition MUST be false (KAOS sends no positionSide param;
     a hedge-mode account rejects EVERY order with -4061).
  2. Key validity + futures reachability via signed reads (balance). NOTE: trade
     permission itself CANNOT be verified without an order — a read-only key passes here
     and may still -2015 on the first POST; disclosed, not hidden.

Keys come from the environment the same way launch_with_keys.py feeds the runner
(BINANCE_API_KEY / BINANCE_API_SECRET), so run it through the same launcher wrapper or
with the env set. Read-only: only GET requests. Prints PASS/FAIL per check; exit 0 iff
all PASS.

Run:  python scripts/kaos_preflight.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kaos_testnet_exec import TestnetExecutor  # noqa: E402


def main() -> int:
    key = os.environ.get("BINANCE_API_KEY", "").strip()
    sec = os.environ.get("BINANCE_API_SECRET", "").strip()
    if not key or not sec:
        print("FAIL: BINANCE_API_KEY/BINANCE_API_SECRET not set — run through "
              "launch_with_keys.py or export the mainnet env first.")
        return 2
    # mainnet host (Z-J: the live mirror runs against fapi.binance.com)
    ex = TestnetExecutor(key, sec, host="https://fapi.binance.com")

    ok = True

    # -- check 1: signed read reachable at all (key valid, futures endpoint accessible)
    try:
        bal = ex._request("GET", "/fapi/v2/balance", {})
        rows = [b for b in bal if float(b.get("balance", 0) or 0) != 0]
        print(f"PASS key-valid: signed read OK; {len(rows)} non-zero wallet rows")
    except Exception as exc:
        print(f"FAIL key-valid: {exc}")
        return 1

    # -- check 2: hedge mode must be OFF (else every order -> -4061)
    try:
        dual = ex._request("GET", "/fapi/v1/positionSide/dual", {})
        dual_side = bool(dual.get("dualSidePosition"))
        if dual_side:
            print("FAIL position-mode: dualSidePosition=true — KAOS orders would all be "
                  "rejected with -4061. Fix: account settings -> position mode -> "
                  "one-way, OR wire positionSide into the executor (code change).")
            ok = False
        else:
            print("PASS position-mode: one-way (dualSidePosition=false)")
    except Exception as exc:
        print(f"FAIL position-mode: {exc}")
        ok = False

    # -- check 3 (honest limitation): trade permission is NOT verifiable read-only
    print("NOTE trade-permission: cannot be verified without an order (read-only keys "
          "pass signed GETs and may still -2015 on the first POST). First live order is "
          "the true test — the executor's fail-open design keeps paper running if it fails.")

    print("PREFLIGHT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
