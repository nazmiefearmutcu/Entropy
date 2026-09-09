"""2026-09-09 mekanizma kampanyası testleri (offline, mock'lu).

Kapsam: M1 yön-küme koruması, M2 funding giriş kapısı, M3 stop-streak
kesici + state devri, B'nin BE ratchet çekirdeği (cancel_stop_by_id,
yerle-önce-iptal-sonra disiplini, fire-once) ve funding parser.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import kaos_testnet_exec as kx  # noqa: E402
from kaos_testnet_exec import TestnetExecutor  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_f4_replay_accounting import _mk_paper  # noqa: E402

import entropy_live_paper as lp_mod  # noqa: E402
from entropy.bot.signals import Signal, SignalAction  # noqa: E402


def _ex(resp_map=None):
    ex = TestnetExecutor("k", "s")
    cap: list = []
    resp_map = resp_map or {}

    def fake_request(method, path, params, tries=2, _resynced=False):
        cap.append((method, path, dict(params)))
        v = resp_map.get((method, path), resp_map.get("default", {}))
        if isinstance(v, Exception):
            raise v
        return v

    ex._request = fake_request  # type: ignore[assignment]
    ex.captured = cap  # type: ignore[attr-defined]
    return ex, cap


def _sig(sym: str, action: SignalAction):
    return Signal(symbol="binance-spot:" + sym, action=action, strength=0.9,
                  reason="test", ts_ns=int(time.time() * 1e9),
                  strategy="consensus", sigma=0.004)


def _gate(lp, sym: str, action: SignalAction, px: float = 100.0):
    """Runner'ın _gated_evaluate sarmalayıcısını doğrudan çağır."""
    ts = int(time.time() * 1e9)
    sig = _sig(sym, action)
    return lp.runner.risk.evaluate(sig, lp.runner.portfolio, px, ts)


# ---- executor: cancel_stop_by_id + funding ---------------------------------

def test_cancel_stop_by_id_deletes_algo_order():
    ex, cap = _ex()
    assert ex.cancel_stop_by_id("ETHUSDT", "3000002180929115") is True
    m, p, params = cap[-1]
    assert (m, p) == ("DELETE", "/fapi/v1/algoOrder")
    assert params["algoId"] == 3000002180929115


def test_cancel_stop_by_id_treats_2011_as_success():
    ex, _ = _ex({("DELETE", "/fapi/v1/algoOrder"):
                 kx.ExecutorError(-2011, "Unknown order sent")})
    assert ex.cancel_stop_by_id("ETHUSDT", "1") is True


def test_get_funding_rate_parses_premium_index():
    ex, cap = _ex({("GET", "/fapi/v1/premiumIndex"):
                   {"lastFundingRate": "-0.00080000", "markPrice": "1.5",
                    "nextFundingTime": 123}})
    r = ex.get_funding_rate("ENAUSDT")
    assert r == {"last_funding_rate": -0.0008, "mark_price": 1.5,
                 "next_funding_ms": 123}
    assert cap[-1][1] == "/fapi/v1/premiumIndex"


# ---- M1: yön-küme koruması --------------------------------------------------

def _seed_shorts(lp, n: int, sym0: str = "ENAUSDT") -> None:
    for j in range(n):
        sym = f"binance-spot:{sym0}{j}X" if j else f"binance-spot:{sym0}"
        lp.runner.portfolio.open(sym, lp_mod.PositionSide.SHORT, 1.0, 2.0,
                                 1.8, 2.2, int(time.time() * 1e9), fee=0.0)


def test_direction_cap_blocks_fourth_same_side():
    lp = _mk_paper(Path(f"C:\\Users\\KULLAN~1\\Entropy\\reports\\live-paper"
                        f"\\_test-tmp-{int(time.time()*1000)%100000}"), 100.0)
    _seed_shorts(lp, 3)
    dec = _gate(lp, "BTCUSDT", SignalAction.ENTER_SHORT)
    assert dec.approved is False
    assert "direction cap" in str(dec.reason)
    assert lp.entry_telemetry["rejected"]["direction cap"] == 1


def test_direction_cap_opposite_side_passes_gate():
    lp = _mk_paper(Path(f"C:\\Users\\KULLAN~1\\Entropy\\reports\\live-paper"
                        f"\\_test-tmp-{int(time.time()*1000)%100000+1}"), 100.0)
    _seed_shorts(lp, 3)
    dec = _gate(lp, "BTCUSDT", SignalAction.ENTER_LONG)
    assert "direction cap" not in str(dec.reason)   # LONG'a kapı dokunmaz


def test_direction_cap_uses_mirror_union():
    """Kâğıt FLAT + mirror izinde 3 SHORT -> yine redd (restart-gün kapsama)."""
    lp = _mk_paper(Path(f"C:\\Users\\KULLAN~1\\Entropy\\reports\\live-paper"
                        f"\\_test-tmp-{int(time.time()*1000)%100000+2}"), 100.0)
    lp.mirror_positions = {f"SYM{i}USDT": {"side": "short"} for i in range(3)}
    dec = _gate(lp, "BTCUSDT", SignalAction.ENTER_SHORT)
    assert dec.approved is False and "direction cap" in str(dec.reason)


# ---- M2: funding kapısı -----------------------------------------------------

def test_funding_gate_blocks_paying_side_and_fails_open():
    lp = _mk_paper(Path(f"C:\\Users\\KULLAN~1\\Entropy\\reports\\live-paper"
                        f"\\_test-tmp-{int(time.time()*1000)%100000+3}"), 100.0)
    calls = {"n": 0}

    class FakeExec:
        def get_funding_rate(self, sym):
            calls["n"] += 1
            return {"last_funding_rate": -0.0008}

    lp.exec_ = FakeExec()
    lp._mirror_live = True
    dec = _gate(lp, "ENAUSDT", SignalAction.ENTER_SHORT)
    assert dec.approved is False and "funding_gate" in str(dec.reason)
    assert lp.entry_telemetry["rejected"]["funding_gate"] == 1
    # TTL cache: ikinci çağrı fetch yapmaz
    _gate(lp, "ENAUSDT", SignalAction.ENTER_SHORT)
    assert calls["n"] == 1
    # LONG aynı koşulda serbest (short öder, long kazanır)
    dec2 = _gate(lp, "ENAUSDT", SignalAction.ENTER_LONG)
    assert "funding_gate" not in str(dec2.reason)


def test_funding_gate_fail_open_and_replay_inert():
    class BoomExec:
        def get_funding_rate(self, sym):
            raise RuntimeError("network down")

    lp = _mk_paper(Path(f"C:\\Users\\KULLAN~1\\Entropy\\reports\\live-paper"
                        f"\\_test-tmp-{int(time.time()*1000)%100000+4}"), 100.0)
    lp.exec_ = BoomExec()
    lp._mirror_live = True
    dec = _gate(lp, "ENAUSDT", SignalAction.ENTER_SHORT)
    assert "funding_gate" not in str(dec.reason)   # fail-open: izin
    # replay determinizmi: mirror kapalıyken kapı tamamen sessiz
    lp2 = _mk_paper(Path(f"C:\\Users\\KULLAN~1\\Entropy\\reports\\live-paper"
                         f"\\_test-tmp-{int(time.time()*1000)%100000+5}"), 100.0)
    lp2.exec_ = BoomExec()
    lp2._mirror_live = False
    dec2 = _gate(lp2, "ENAUSDT", SignalAction.ENTER_SHORT)
    assert "funding_gate" not in str(dec2.reason)


# ---- M3: stop-streak kesici -------------------------------------------------

def test_breaker_halts_after_three_stop_fills():
    lp = _mk_paper(Path(f"C:\\Users\\KULLAN~1\\Entropy\\reports\\live-paper"
                        f"\\_test-tmp-{int(time.time()*1000)%100000+6}"), 100.0)
    lp._note_venue_stop_fill("ENAUSDT")
    lp._note_venue_stop_fill("FILUSDT")
    assert lp.breaker_until == 0.0
    lp._note_venue_stop_fill("OPUSDT")
    assert lp.breaker_until > time.time()
    dec = _gate(lp, "BTCUSDT", SignalAction.ENTER_LONG)
    assert dec.approved is False and "breaker" in str(dec.reason)
    assert lp.entry_telemetry["rejected"]["breaker"] == 1


def test_breaker_prunes_24h_window():
    lp = _mk_paper(Path(f"C:\\Users\\KULLAN~1\\Entropy\\reports\\live-paper"
                        f"\\_test-tmp-{int(time.time()*1000)%100000+7}"), 100.0)
    lp.breaker_stops = [time.time() - 90000.0, time.time() - 90000.0]
    lp._note_venue_stop_fill("ENAUSDT")
    assert lp.breaker_until == 0.0   # 2 eski dolgu pencere dışı -> tek fresh


def test_breaker_state_roundtrip(tmp_path):
    lp = _mk_paper(tmp_path, 100.0)
    lp._note_venue_stop_fill("ENAUSDT")
    lp._note_venue_stop_fill("FILUSDT")
    lp._note_venue_stop_fill("OPUSDT")
    st = lp.build_state()
    lp2 = _mk_paper(tmp_path / "b", 100.0)
    lp2.restore_from(st)
    assert len(lp2.breaker_stops) == 3
    assert lp2.breaker_until > time.time()   # halt restart'ı aşar


# ---- B: BE ratchet çekirdeği ------------------------------------------------

def _ratchet_lp(tmp_path, sub: str):
    lp = _mk_paper(tmp_path / sub, 100.0)
    lp.exec_ = SimpleNamespace(
        get_open_positions=lambda: [
            {"symbol": "binance-spot:ENAUSDT", "side": "short", "qty": 387.0,
             "entry_price": 0.16, "mark_price": 0.16}],
        place_venue_stop=lambda sym, side, px: "NEWID",
        cancel_stop_by_id=lambda sym, oid: True,
        sizing_pct=0.25, leverage=10)
    lp._mirror_live = True
    # kâğıt SHORT: entry 0.16, sl 0.18 (2σ üstü), tp 0.14 (4σ altı)
    lp.runner.portfolio.open("binance-spot:ENAUSDT",
                             lp_mod.PositionSide.SHORT, 387.0, 0.16,
                             0.18, 0.14, int(time.time() * 1e9), fee=0.0)
    mp = {"side": "short", "stop_id": "OLDID", "qty": 387.0}
    lp.mirror_positions = {"ENAUSDT": mp}
    lp.last_close = {"ENAUSDT": 0.15}   # +0.01 hareket = tam 0.5*tp_dist eşik
    return lp, mp


def test_ratchet_places_new_before_cancelling_old(tmp_path):
    lp, mp = _ratchet_lp(tmp_path, "r1")
    ops: list = []
    real_place = lp.exec_.place_venue_stop
    real_cancel = lp.exec_.cancel_stop_by_id

    lp.exec_.place_venue_stop = lambda s, side, px: (  # type: ignore[method-assign]
        ops.append("POST") or real_place(s, side, px))
    lp.exec_.cancel_stop_by_id = lambda s, oid: (  # type: ignore[method-assign]
        ops.append("DELETE") or real_cancel(s, oid))
    lp._venue_ratchet("test")
    # YENİ yerleşim eski iptalden ÖNCE; eski id'nin SİLİNMESİ bir sonraki
    # pasın 0. adımıdır (tasarım: sıcak yolda tek ağ işlemi, idempotent)
    assert "POST" in ops and "DELETE" not in ops
    lp._venue_ratchet("test")
    assert "DELETE" in ops and ops.index("POST") < ops.index("DELETE")
    assert mp["be_done"] is True and mp["stop_id"] == "NEWID"
    assert "old_stop_id" not in mp and mp["trail_px"] < 0.16


def test_ratchet_fires_once_without_trail(tmp_path):
    lp, mp = _ratchet_lp(tmp_path, "r2")
    lp._venue_ratchet("test")
    calls = {"n": 0}
    lp.exec_.place_venue_stop = lambda s, side, px: (  # type: ignore[method-assign]
        calls.__setitem__("n", calls["n"] + 1) or "NEWID2")
    lp._venue_ratchet("test")   # be_done=True, KAOS_TRAIL yok -> sessiz
    assert calls["n"] == 0


def test_ratchet_skips_paper_venue_side_mismatch(tmp_path):
    """Kâğıt LONG / gerçek SHORT uyuşmazlığında ratchet DOKUNMAZ — aksi halde
    gerçek short'un stopunu süpürüp yanlış yönde stop koyabilirdi."""
    lp, mp = _ratchet_lp(tmp_path, "r3")
    # kâğıt pozisyonu LONG'a çevir (replay türemesi simülasyonu)
    sym = "binance-spot:ENAUSDT"
    lp.runner.portfolio.close(sym, 0.15, int(time.time() * 1e9), fee=0.0)
    lp.runner.portfolio.open(sym, lp_mod.PositionSide.LONG, 387.0, 0.16,
                             0.14, 0.18, int(time.time() * 1e9), fee=0.0)
    lp.last_close = {"ENAUSDT": 0.19}   # kâğıt long için +2σ üzeri
    calls = {"n": 0}
    lp.exec_.place_venue_stop = lambda s, side, px: (  # type: ignore[method-assign]
        calls.__setitem__("n", calls["n"] + 1) or "X")
    lp._venue_ratchet("test")
    assert calls["n"] == 0
    assert mp.get("be_done") is None   # dokunulmadı
