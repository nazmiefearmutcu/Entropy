import csv
import json
from pathlib import Path

from entropy.bot.ledger import Ledger
from entropy.bot.orders import Fill, OrderIntent, OrderSide
from entropy.bot.portfolio import Portfolio, PositionSide


def test_record_fill_writes_csv_and_jsonl(tmp_path: Path):
    led = Ledger(str(tmp_path))
    f = Fill(order_id="o1", symbol="SPY", side=OrderSide.BUY, qty=10.0,
             price=100.0, fee=0.1, slippage=0.05, ts_ns=1)
    led.record_fill(f, OrderIntent.OPEN)
    rows = list(csv.DictReader((tmp_path / "fills.csv").open()))
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["intent"] == "open"
    lines = (tmp_path / "events.jsonl").read_text().strip().splitlines()
    assert json.loads(lines[0])["kind"] == "fill"


def test_record_equity_appends_row(tmp_path: Path):
    led = Ledger(str(tmp_path))
    p = Portfolio(1000.0)
    p.open("A", PositionSide.LONG, 1.0, 10.0, 9.0, 11.0, 1, 0.0)
    p.mark("A", 12.0)
    led.record_equity(p.snapshot(ts_ns=5))
    rows = list(csv.DictReader((tmp_path / "equity.csv").open()))
    assert float(rows[0]["equity"]) == 1002.0


def test_record_risk_change_and_reject(tmp_path: Path):
    led = Ledger(str(tmp_path))
    led.record_risk_change("Balanced", "Aggressive")
    led.record_reject("SPY", "cooldown active")
    kinds = [json.loads(x)["kind"]
             for x in (tmp_path / "events.jsonl").read_text().strip().splitlines()]
    assert "risk_profile_changed" in kinds
    assert "reject" in kinds
