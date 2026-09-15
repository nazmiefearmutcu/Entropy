# commits
f2a90be docs(bot): warn that RiskOverrides.active() is lossy for barrier fields
e07670d fix(bot): sigma-mode entry cost gate judges the actual sigma barriers

# full diff (fix round 1 only)
diff --git a/src/entropy/bot/config.py b/src/entropy/bot/config.py
index 6b7b317..e85dad0 100644
--- a/src/entropy/bot/config.py
+++ b/src/entropy/bot/config.py
@@ -138,6 +138,9 @@ class MarketCostConfig(msgspec.Struct, frozen=True):
 
 #: RiskOverrides fields consumed by the runner directly, never handed to
 #: make_custom() via active() (they are not RiskProfile overrides).
+#: WARNING: active() is LOSSY for these fields — they are never returned even
+#: when non-default, so callers that need the barrier mode must read
+#: ``risk_overrides.stop_mode`` (etc.) directly, as the runner does.
 _RISK_BARRIER_FIELDS = frozenset({"stop_mode", "stop_sigma_mult", "tp_sigma_mult"})
 
 
@@ -149,7 +152,8 @@ class RiskOverrides(msgspec.Struct, frozen=True):
 
     Barrier-mode fields (``stop_mode`` and the sigma multipliers) are NOT risk
     preset overrides — they are consumed by :class:`~entropy.bot.runner.BotRunner`
-    directly and are therefore excluded from :meth:`active`.
+    directly and are therefore excluded from :meth:`active`. That makes
+    ``active()`` lossy for them: read them off the struct itself when needed.
     """
 
     per_trade_pct: float | None = None
diff --git a/src/entropy/bot/risk/manager.py b/src/entropy/bot/risk/manager.py
index 9d9f32b..d47f661 100644
--- a/src/entropy/bot/risk/manager.py
+++ b/src/entropy/bot/risk/manager.py
@@ -37,10 +37,20 @@ class RiskManager:
         profile: RiskProfile,
         cost_model: CostModel | None = None,
         max_cost_to_stop: float = 0.5,
+        stop_mode: str = "percent",
+        stop_sigma_mult: float = 1.5,
+        tp_sigma_mult: float = 1.2,
     ) -> None:
         self.profile = profile
         self.cost_model = cost_model
         self.max_cost_to_stop = max_cost_to_stop
+        # Barrier anchoring mode (mirrors RiskOverrides.stop_mode): "sigma" makes
+        # BOTH the anchored barriers (open path) and this gate's cost checks use
+        # sigma-scaled distances, so the gate judges the barriers the position
+        # will actually get.
+        self.stop_mode = stop_mode
+        self.stop_sigma_mult = stop_sigma_mult
+        self.tp_sigma_mult = tp_sigma_mult
         self.halted = False
         self.circuit_tripped = False
         self._cooldown_until: dict[str, int] = {}
@@ -50,6 +60,30 @@ class RiskManager:
     def set_profile(self, profile: RiskProfile) -> None:
         self.profile = profile
 
+    def set_barrier_mode(self, stop_mode: str, stop_sigma_mult: float,
+                         tp_sigma_mult: float) -> None:
+        """Swap the sigma barrier mode in place (hot-apply path)."""
+        self.stop_mode = stop_mode
+        self.stop_sigma_mult = stop_sigma_mult
+        self.tp_sigma_mult = tp_sigma_mult
+
+    def barrier_pcts(
+        self, sigma: float | None
+    ) -> tuple[float, float] | None:
+        """Sigma-scaled (stop_pct, tp_pct) for an entry signal's sigma, or None.
+
+        None means "no sigma override": percent mode, or the signal carries no
+        usable sigma — callers fall back to the profile's percent barriers.
+        Shared by the entry cost gate (evaluate) and the runner's open path so
+        a position is gated and anchored on the SAME barrier distances.
+        """
+        if self.stop_mode == "sigma" and sigma is not None and sigma > 0.0:
+            return (
+                self.stop_sigma_mult * sigma * 100.0,
+                self.tp_sigma_mult * sigma * 100.0,
+            )
+        return None
+
     def update_cost_model(
         self, cost_model: CostModel | None, max_cost_to_stop: float
     ) -> None:
@@ -243,7 +277,16 @@ class RiskManager:
                 if signal.action is SignalAction.ENTER_LONG
                 else PositionSide.SHORT
             )
-            stop_px, tp_px = self.stop_tp_prices(side, mark_px, signal.symbol)
+            # Judge the barriers the position will actually get: in sigma mode
+            # the sigma-scaled distances, else the profile's percents (the same
+            # choice the runner's open path makes when anchoring).
+            pcts = self.barrier_pcts(signal.sigma)
+            if pcts is not None:
+                stop_px, tp_px = self.stop_tp_prices(
+                    side, mark_px, signal.symbol, stop_pct=pcts[0], tp_pct=pcts[1]
+                )
+            else:
+                stop_px, tp_px = self.stop_tp_prices(side, mark_px, signal.symbol)
             stop_bps = abs(mark_px - stop_px) / mark_px * 10_000.0
             tp_bps = abs(tp_px - mark_px) / mark_px * 10_000.0
             round_trip_bps = self.cost_model.round_trip_bps(signal.symbol)
diff --git a/src/entropy/bot/runner.py b/src/entropy/bot/runner.py
index 96c9e31..b420629 100644
--- a/src/entropy/bot/runner.py
+++ b/src/entropy/bot/runner.py
@@ -96,6 +96,9 @@ class BotRunner:
             config.profile(),
             cost_model=config.cost_model(),
             max_cost_to_stop=config.max_cost_to_stop,
+            stop_mode=config.risk_overrides.stop_mode,
+            stop_sigma_mult=config.risk_overrides.stop_sigma_mult,
+            tp_sigma_mult=config.risk_overrides.tp_sigma_mult,
         )
         self.executor = _make_executor(config)
         self.strategies = build_strategies(config)
@@ -109,13 +112,9 @@ class BotRunner:
         self.paused = False
         self._recent_signals: deque[str] = deque(maxlen=_RECENT_MAX)
         self._recent_rejects: deque[str] = deque(maxlen=_RECENT_MAX)
-        # Sigma-scaled barrier mode (RiskOverrides.stop_mode): when "sigma", an
-        # entry signal carrying sigma > 0 anchors the stop/TP at
-        # mult * sigma instead of the profile's percents. Barriers are anchored
-        # ONCE at open below — never re-anchored (harness ledger invariant).
-        self._stop_mode = config.risk_overrides.stop_mode
-        self._stop_sigma_mult = config.risk_overrides.stop_sigma_mult
-        self._tp_sigma_mult = config.risk_overrides.tp_sigma_mult
+        # Sigma barrier mode lives on the risk layer (it gates entries on the
+        # same barriers it anchors); the runner only carries the entry signal's
+        # sigma from on_trade to the open path.
         #: symbol -> entry-bar sigma of the entry signal being processed (set
         #: in on_trade, consumed-or-dropped in _execute / on rejection).
         self._entry_sigma: dict[str, float] = {}
@@ -166,6 +165,9 @@ class BotRunner:
             # Live execution is guarded / intentionally unimplemented. Record the block
             # HONESTLY and do NOT fabricate a fill or mutate the portfolio — a blocked
             # order must never look like a real one.
+            if order.intent is OrderIntent.OPEN:
+                # the blocked entry never opened, so its stashed sigma is stale
+                self._entry_sigma.pop(order.symbol, None)
             self.ledger.record_event("live_blocked", {
                 "symbol": order.symbol, "intent": order.intent.value,
                 "reason": str(exc).splitlines()[0],
@@ -174,19 +176,15 @@ class BotRunner:
         if order.intent is OrderIntent.OPEN:
             pos_side = PositionSide.LONG if order.side is OrderSide.BUY else PositionSide.SHORT
             sigma = self._entry_sigma.pop(order.symbol, None)
-            if (
-                self._stop_mode == "sigma"
-                and sigma is not None and sigma > 0.0
-            ):
+            pcts = self.risk.barrier_pcts(sigma)
+            if pcts is not None:
                 # Sigma-scaled barriers: convert the sigma multipliers into the
                 # percent units the barrier computation already consumes.
                 # stop_tp_prices skips its tick-window volatility scale here —
                 # sigma IS the entry bar's volatility (no double-counting).
-                stop_pct = self._stop_sigma_mult * sigma * 100.0
-                tp_pct = self._tp_sigma_mult * sigma * 100.0
                 stop_px, tp_px = self.risk.stop_tp_prices(
                     pos_side, fill.price, order.symbol,
-                    stop_pct=stop_pct, tp_pct=tp_pct,
+                    stop_pct=pcts[0], tp_pct=pcts[1],
                 )
             else:
                 # percent mode, or sigma missing/zero on the entry signal:
@@ -284,10 +282,13 @@ class BotRunner:
         old_profile = self.risk.profile.name
 
         self.config = cfg
-        # Barrier mode rides on risk_overrides, read fresh per open below.
-        self._stop_mode = cfg.risk_overrides.stop_mode
-        self._stop_sigma_mult = cfg.risk_overrides.stop_sigma_mult
-        self._tp_sigma_mult = cfg.risk_overrides.tp_sigma_mult
+        # Barrier mode rides on risk_overrides; the risk layer gates entries on
+        # the same sigma-scaled distances it anchors at open.
+        self.risk.set_barrier_mode(
+            cfg.risk_overrides.stop_mode,
+            cfg.risk_overrides.stop_sigma_mult,
+            cfg.risk_overrides.tp_sigma_mult,
+        )
         profile = cfg.profile()
         if profile != self.risk.profile:
             self.risk.set_profile(profile)
diff --git a/tests/bot/test_sigma_barriers.py b/tests/bot/test_sigma_barriers.py
index 49decac..109079e 100644
--- a/tests/bot/test_sigma_barriers.py
+++ b/tests/bot/test_sigma_barriers.py
@@ -7,7 +7,7 @@ from __future__ import annotations
 import pytest
 
 from entropy.bot.config import BotConfig, ConsensusConfig, RiskOverrides, validate
-from entropy.bot.portfolio import PositionSide
+from entropy.bot.portfolio import Portfolio, PositionSide
 from entropy.bot.risk.manager import RiskManager
 from entropy.bot.risk.profiles import MEDIUM
 from entropy.bot.runner import BotRunner
@@ -122,10 +122,12 @@ _FILL = 100.0 * (1 + 2.0 / 10_000.0)
 
 
 def test_runner_sigma_mode_anchors_barriers_from_entry_sigma(tmp_path):
-    runner, pos = _run_one_entry(tmp_path, "sigma", sigma=0.001)
-    # stop = 1.5 * 0.001 = 0.15% below the fill, tp = 1.2 * 0.001 = 0.12% above
-    assert pos.stop_px == pytest.approx(_FILL * (1 - 0.0015))
-    assert pos.tp_px == pytest.approx(_FILL * (1 + 0.0012))
+    # sigma = 0.002 clears the sigma-mode cost gate (stop 0.3% -> cost-to-stop
+    # 0.08/0.3 = 0.27 <= 0.5, tp 24 bps > 8 bps RT) and anchors at the multipliers
+    runner, pos = _run_one_entry(tmp_path, "sigma", sigma=0.002)
+    # stop = 1.5 * 0.002 = 0.3% below the fill, tp = 1.2 * 0.002 = 0.24% above
+    assert pos.stop_px == pytest.approx(_FILL * (1 - 0.003))
+    assert pos.tp_px == pytest.approx(_FILL * (1 + 0.0024))
     # the stash is consumed: nothing left behind for a later unrelated entry
     assert runner._entry_sigma == {}
 
@@ -154,3 +156,89 @@ def test_runner_percent_mode_ignores_entry_sigma(tmp_path):
     runner, pos = _run_one_entry(tmp_path, "percent", sigma=0.001)
     assert pos.stop_px == pytest.approx(_FILL * (1 - MEDIUM.stop_loss_pct / 100))
     assert pos.tp_px == pytest.approx(_FILL * (1 + MEDIUM.take_profit_pct / 100))
+
+
+# ---- round 1: the entry cost gate must judge the sigma barriers -------------
+
+
+def _gate_manager(stop_mode: str) -> RiskManager:
+    from entropy.bot.costs import CostModel
+
+    return RiskManager(
+        MEDIUM,
+        cost_model=CostModel(flat_fee_bps=10.0, flat_slippage_bps=3.0),  # 26 bps RT
+        stop_mode=stop_mode,
+    )
+
+
+def test_barrier_pcts_helper():
+    rm = _gate_manager("sigma")
+    assert rm.barrier_pcts(0.0011) == pytest.approx((0.165, 0.132))
+    assert rm.barrier_pcts(None) is None
+    assert rm.barrier_pcts(0.0) is None
+    pct_rm = _gate_manager("percent")
+    assert pct_rm.barrier_pcts(0.0011) is None
+
+
+def test_sigma_mode_cost_gate_rejects_cost_dead_entries():
+    """sigma ~ 0.0011 (BTC-15m-like) anchors a ~13 bps TP — below the 26 bps
+    round trip. Under percent-barrier judging (2% TP = 200 bps) this entry was
+    approved despite being guaranteed cost-dead; the gate must now judge the
+    sigma barriers and reject."""
+    rm = _gate_manager("sigma")
+    p = Portfolio(100_000.0)
+    sig = Signal(symbol="SPY", action=SignalAction.ENTER_LONG, strength=1.0,
+                 reason="t", ts_ns=1, strategy="s", sigma=0.0011)
+    d = rm.evaluate(sig, p, mark_px=100.0, ts_ns=1)
+    assert not d.approved
+    assert d.reason == "take-profit below round-trip cost"
+
+
+def test_sigma_mode_cost_gate_still_allows_viable_sigma():
+    """sigma = 0.005 -> TP = 0.6% = 60 bps > 26 bps RT and cost-to-stop
+    26/75 = 0.35 <= 0.5: the sigma-mode gate must approve."""
+    rm = _gate_manager("sigma")
+    p = Portfolio(100_000.0)
+    sig = Signal(symbol="SPY", action=SignalAction.ENTER_LONG, strength=1.0,
+                 reason="t", ts_ns=1, strategy="s", sigma=0.005)
+    d = rm.evaluate(sig, p, mark_px=100.0, ts_ns=1)
+    assert d.approved
+
+
+def test_percent_mode_cost_gate_ignores_sigma():
+    """Percent mode (the default) gates on the profile's percents exactly as
+    before, whatever sigma the signal carries."""
+    rm = _gate_manager("percent")
+    p = Portfolio(100_000.0)
+    sig = Signal(symbol="SPY", action=SignalAction.ENTER_LONG, strength=1.0,
+                 reason="t", ts_ns=1, strategy="s", sigma=0.0011)
+    assert rm.evaluate(sig, p, mark_px=100.0, ts_ns=1).approved
+
+
+def test_sigma_mode_missing_sigma_gates_on_percent_fallback():
+    """No usable sigma on the signal: the gate (like the open path) falls back
+    to the profile's percent barriers."""
+    rm = _gate_manager("sigma")
+    p = Portfolio(100_000.0)
+    sig = Signal(symbol="SPY", action=SignalAction.ENTER_LONG, strength=1.0,
+                 reason="t", ts_ns=1, strategy="s", sigma=None)
+    assert rm.evaluate(sig, p, mark_px=100.0, ts_ns=1).approved
+
+
+def test_runner_plumbs_barrier_mode_into_risk_and_hot_apply(tmp_path):
+    cfg = BotConfig(
+        enable_crypto=False, enable_equities=False,
+        risk_overrides=RiskOverrides(stop_mode="sigma", stop_sigma_mult=2.0,
+                                     tp_sigma_mult=1.0),
+    )
+    runner = BotRunner(cfg, run_dir=str(tmp_path))
+    assert runner.risk.stop_mode == "sigma"
+    assert (runner.risk.stop_sigma_mult, runner.risk.tp_sigma_mult) == (2.0, 1.0)
+    # hot-apply a percent config: the risk layer's gate must follow
+    cfg2 = BotConfig(
+        enable_crypto=False, enable_equities=False,
+        risk_overrides=RiskOverrides(),
+    )
+    assert runner.apply_config(cfg2) == []
+    assert runner.risk.stop_mode == "percent"
+    assert runner.risk.barrier_pcts(0.0011) is None
