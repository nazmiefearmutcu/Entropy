# commits
b0606b2 feat(scripts): --long-only/--max-hold-bars/--stop-mode/--*-sigma-mult flags in accuracy harness
3fd4053 feat(bot): sigma-scaled stop/TP barriers via RiskOverrides stop_mode + runner plumbing
276ad9e feat(bot): long_only flag, max_hold_bars time stop, entry-bar sigma on consensus signals

# stat
 scripts/entropy_accuracy_btc15m.py      |  31 +++++++
 src/entropy/bot/config.py               |  49 +++++++++-
 src/entropy/bot/risk/manager.py         |  55 +++++++----
 src/entropy/bot/runner.py               |  40 +++++++-
 src/entropy/bot/signals.py              |   4 +
 src/entropy/bot/strategies/consensus.py |  50 +++++++++-
 tests/bot/test_consensus.py             | 116 ++++++++++++++++++++++++
 tests/bot/test_sigma_barriers.py        | 156 ++++++++++++++++++++++++++++++++
 8 files changed, 480 insertions(+), 21 deletions(-)

# full diff
diff --git a/scripts/entropy_accuracy_btc15m.py b/scripts/entropy_accuracy_btc15m.py
index 2afcbe5..72e3815 100644
--- a/scripts/entropy_accuracy_btc15m.py
+++ b/scripts/entropy_accuracy_btc15m.py
@@ -39,6 +39,11 @@ rate versus the original harness — that is the point):
     evaluated tick. ``--warmup-bars 0`` restores the old cold-start behavior.
   * Long-only metrics (``win_rate_long_only``) report the spot-deployable
     subset alongside the total.
+  * Accuracy levers, all default-off/percent (behavior preserved):
+    ``--long-only`` makes the strategy never emit ENTER_SHORT (spot shorts are
+    not executable live), ``--max-hold-bars N`` adds a time stop, and
+    ``--stop-mode sigma`` anchors stop/TP at ``--stop-sigma-mult``/``--tp-sigma-mult``
+    times the entry bar's per-bar return RMS instead of the fixed percents.
 """
 
 from __future__ import annotations
@@ -506,6 +511,18 @@ def main() -> None:
     ap.add_argument("--direction-bars", type=int, default=0)
     ap.add_argument("--confirm-bars", type=int, default=2)
     ap.add_argument("--trail-pct", type=float, default=0.3)
+    ap.add_argument("--long-only", action="store_true",
+                    help="never emit ENTER_SHORT (spot-deployable subset only)")
+    ap.add_argument("--max-hold-bars", type=int, default=0,
+                    help="time stop: exit after N completed bars in a trade "
+                         "(0 = off; must be 0 or >= --min-hold-bars)")
+    ap.add_argument("--stop-mode", choices=("percent", "sigma"), default="percent",
+                    help="barrier anchoring: fixed percents (default) or "
+                         "sigma-scaled from the entry bar's return RMS")
+    ap.add_argument("--stop-sigma-mult", type=float, default=1.5,
+                    help="stop distance = mult * sigma (sigma mode only)")
+    ap.add_argument("--tp-sigma-mult", type=float, default=1.2,
+                    help="take-profit distance = mult * sigma (sigma mode only)")
     ap.add_argument("--strategy", action="append", default=None,
                     help="strategy name (repeatable); default consensus")
     args = ap.parse_args()
@@ -556,6 +573,8 @@ def main() -> None:
             direction_bars=args.direction_bars,
             confirm_bars=args.confirm_bars,
             trail_pct=args.trail_pct,
+            max_hold_bars=args.max_hold_bars,
+            long_only=args.long_only,
         ),
         risk_overrides=RiskOverrides(
             per_trade_pct=args.per_trade_pct,
@@ -567,6 +586,9 @@ def main() -> None:
             cooldown_s=args.cooldown_s,
             min_volatility_pct=args.min_volatility_pct,
             vol_window_s=args.vol_window_s,
+            stop_mode=args.stop_mode,
+            stop_sigma_mult=args.stop_sigma_mult,
+            tp_sigma_mult=args.tp_sigma_mult,
         ),
         console_log_path=str(out_dir / "console.log"),
         trade_csv_path=str(out_dir / "trades.csv"),
@@ -590,7 +612,12 @@ def main() -> None:
             "max_total_exposure_pct": args.max_total_exposure_pct,
             "max_daily_loss_pct": args.max_daily_loss_pct,
             "cooldown_s": args.cooldown_s,
+            "stop_mode": args.stop_mode,
+            "stop_sigma_mult": args.stop_sigma_mult,
+            "tp_sigma_mult": args.tp_sigma_mult,
         },
+        "long_only": args.long_only,
+        "max_hold_bars": args.max_hold_bars,
     }
     (out_dir / "report.json").write_text(json.dumps(report, indent=2))
 
@@ -603,6 +630,10 @@ def main() -> None:
     print(f"costs        : Binance spot {report['config']['market_costs']}")
     print(f"risk         : {args.per_trade_pct}%/trade, max {args.max_concurrent} open, "
           f"exposure <= {args.max_total_exposure_pct}%, daily loss <= {args.max_daily_loss_pct}%")
+    print(f"levers       : long_only={args.long_only}, max_hold_bars={args.max_hold_bars}, "
+          f"stop_mode={args.stop_mode}"
+          + (f" (stop {args.stop_sigma_mult}x / tp {args.tp_sigma_mult}x sigma)"
+             if args.stop_mode == "sigma" else ""))
     print(f"final equity : ${m['final_equity']:.2f}  (return {m['total_return_pct']:+.2f}%)")
     print(f"accuracy     : win rate {m['win_rate']*100:.1f}%  ({m['total_trades']} closed trades, "
           f"win = pnl > 0)")
diff --git a/src/entropy/bot/config.py b/src/entropy/bot/config.py
index 1eea400..6b7b317 100644
--- a/src/entropy/bot/config.py
+++ b/src/entropy/bot/config.py
@@ -75,6 +75,12 @@ class ConsensusConfig(msgspec.Struct, frozen=True):
     min_hold_bars: int = 5
     cooldown_bars: int = 4
     exit_mode: str = "trail"             # score | trend_flip | either | hold | trail
+    #: Time stop: exit after this many completed bars in the trade regardless
+    #: of score/trend/trail (0 = off; must be 0 or >= min_hold_bars).
+    max_hold_bars: int = 0
+    #: Never emit ENTER_SHORT (spot shorts are not executable live, so the
+    #: short leg's measured accuracy is not deployable).
+    long_only: bool = False
 
     def weights(self) -> dict[str, float]:
         return {
@@ -130,11 +136,20 @@ class MarketCostConfig(msgspec.Struct, frozen=True):
         return out
 
 
+#: RiskOverrides fields consumed by the runner directly, never handed to
+#: make_custom() via active() (they are not RiskProfile overrides).
+_RISK_BARRIER_FIELDS = frozenset({"stop_mode", "stop_sigma_mult", "tp_sigma_mult"})
+
+
 class RiskOverrides(msgspec.Struct, frozen=True):
     """Optional per-field overrides applied on top of the named risk preset.
 
     ``None`` means "inherit the preset". This is how the settings UI offers a
     Custom profile without needing a whole extra preset vocabulary.
+
+    Barrier-mode fields (``stop_mode`` and the sigma multipliers) are NOT risk
+    preset overrides — they are consumed by :class:`~entropy.bot.runner.BotRunner`
+    directly and are therefore excluded from :meth:`active`.
     """
 
     per_trade_pct: float | None = None
@@ -146,12 +161,31 @@ class RiskOverrides(msgspec.Struct, frozen=True):
     cooldown_s: float | None = None
     min_volatility_pct: float | None = None
     vol_window_s: float | None = None
+    #: Stop/TP barrier anchoring: "percent" anchors at the profile's fixed
+    #: percents (legacy), "sigma" anchors at sigma-mult x the entry bar's
+    #: per-bar return RMS carried by the entry signal (falls back to percent
+    #: when the signal carries no usable sigma). Barriers are anchored ONCE at
+    #: open and never re-anchored — see the harness `_BarrierLedger` invariant.
+    stop_mode: str = "percent"
+    #: sigma multipliers: stop distance = stop_sigma_mult * sigma, TP distance
+    #: = tp_sigma_mult * sigma (as fractions of entry; defaults mirror the
+    #: shipped 1.5% / 1.2% shape at sigma ~ 0.001).
+    stop_sigma_mult: float = 1.5
+    tp_sigma_mult: float = 1.2
+
+    def __post_init__(self) -> None:
+        if self.stop_mode not in ("percent", "sigma"):
+            raise ValueError(
+                f"stop_mode must be 'percent' or 'sigma', got {self.stop_mode!r}"
+            )
+        if self.stop_sigma_mult <= 0.0 or self.tp_sigma_mult <= 0.0:
+            raise ValueError("sigma multipliers must be > 0")
 
     def active(self) -> dict[str, object]:
         return {
             name: value
             for name, value in ((n, getattr(self, n)) for n in self.__struct_fields__)
-            if value is not None
+            if value is not None and name not in _RISK_BARRIER_FIELDS
         }
 
 
@@ -310,6 +344,18 @@ def validate(cfg: BotConfig) -> list[str]:
         problems.append("consensus weights cannot all be zero")
     if not 0.0 <= c.min_participation <= 1.0:
         problems.append("consensus min participation must be a fraction in [0, 1]")
+    if c.max_hold_bars < 0:
+        problems.append("consensus max_hold_bars must be >= 0 (0 = off)")
+    elif c.max_hold_bars > 0 and c.max_hold_bars < max(0, c.min_hold_bars):
+        problems.append(
+            "consensus max_hold_bars must be 0 (off) or >= min_hold_bars, "
+            "otherwise the time stop can never fire"
+        )
+    ro = cfg.risk_overrides
+    if ro.stop_mode not in ("percent", "sigma"):
+        problems.append(f"risk stop_mode must be 'percent' or 'sigma', got {ro.stop_mode!r}")
+    if ro.stop_sigma_mult <= 0.0 or ro.tp_sigma_mult <= 0.0:
+        problems.append("risk sigma multipliers must be > 0")
     if cfg.mode == "live" and not cfg.live.acknowledged_risk:
         problems.append("live mode requires the risk acknowledgement")
     return problems
@@ -337,6 +383,7 @@ def build_strategies(cfg: BotConfig) -> list[Strategy]:
                 min_participation=c.min_participation,
                 min_hold_bars=c.min_hold_bars, cooldown_bars=c.cooldown_bars,
                 exit_mode=c.exit_mode, regime_window=c.regime_window,
+                max_hold_bars=c.max_hold_bars, long_only=c.long_only,
                 slope_lookback=c.slope_lookback, regime_tilt=c.regime_tilt,
                 direction_bars=c.direction_bars,
                 direction_min_slope=c.direction_min_slope,
diff --git a/src/entropy/bot/risk/manager.py b/src/entropy/bot/risk/manager.py
index cd3e92a..9d9f32b 100644
--- a/src/entropy/bot/risk/manager.py
+++ b/src/entropy/bot/risk/manager.py
@@ -107,25 +107,44 @@ class RiskManager:
         return f"o{self._order_seq}"
 
     def stop_tp_prices(
-        self, side: PositionSide, entry_px: float, symbol: str | None = None
+        self, side: PositionSide, entry_px: float, symbol: str | None = None,
+        *, stop_pct: float | None = None, tp_pct: float | None = None,
     ) -> tuple[float, float]:
-        p = self.profile
-        scale_factor = 1.0
-        if symbol is not None:
-            history = self.ticks_history.get(symbol)
-            if history:
-                # Same windowed sample the entry guards use (>= 5 in-window ticks);
-                # anything thinner falls back to the profile's base percentages.
-                prices = self._window_prices(symbol, history[-1][0])
-                if len(prices) >= _MIN_WINDOW_TICKS:
-                    mean = sum(prices) / len(prices)
-                    if mean > 0:
-                        variance = sum((x - mean) ** 2 for x in prices) / len(prices)
-                        std = variance ** 0.5
-                        scale_factor = 1.0 + std / mean
-
-        stop_loss_pct = min(p.stop_loss_pct * scale_factor, _MAX_STOP_TP_PCT)
-        take_profit_pct = min(p.take_profit_pct * scale_factor, _MAX_STOP_TP_PCT)
+        """Barrier prices for a fresh position.
+
+        Default (both ``stop_pct``/``tp_pct`` omitted): the profile's percents,
+        scaled by the tick window's volatility — the legacy behavior, unchanged.
+
+        With explicit ``stop_pct``/``tp_pct`` (percent-of-entry distances, e.g.
+        sigma-mult x sigma x 100 for sigma-scaled barriers), those are used as
+        the base distances instead of the profile's percents, and the
+        tick-window scale factor is SKIPPED: sigma already encodes the market's
+        volatility at the entry bar, so applying it again would double-count.
+        The 50% safety clamp still applies in both modes. Barriers are anchored
+        ONCE here at open and never re-anchored (see the harness
+        ``_BarrierLedger`` capture-at-open invariant).
+        """
+        if stop_pct is not None and tp_pct is not None and stop_pct > 0.0 and tp_pct > 0.0:
+            stop_loss_pct = min(stop_pct, _MAX_STOP_TP_PCT)
+            take_profit_pct = min(tp_pct, _MAX_STOP_TP_PCT)
+        else:
+            p = self.profile
+            scale_factor = 1.0
+            if symbol is not None:
+                history = self.ticks_history.get(symbol)
+                if history:
+                    # Same windowed sample the entry guards use (>= 5 in-window ticks);
+                    # anything thinner falls back to the profile's base percentages.
+                    prices = self._window_prices(symbol, history[-1][0])
+                    if len(prices) >= _MIN_WINDOW_TICKS:
+                        mean = sum(prices) / len(prices)
+                        if mean > 0:
+                            variance = sum((x - mean) ** 2 for x in prices) / len(prices)
+                            std = variance ** 0.5
+                            scale_factor = 1.0 + std / mean
+
+            stop_loss_pct = min(p.stop_loss_pct * scale_factor, _MAX_STOP_TP_PCT)
+            take_profit_pct = min(p.take_profit_pct * scale_factor, _MAX_STOP_TP_PCT)
 
         if side is PositionSide.LONG:
             return entry_px * (1 - stop_loss_pct / 100), entry_px * (1 + take_profit_pct / 100)
diff --git a/src/entropy/bot/runner.py b/src/entropy/bot/runner.py
index 3999d08..96c9e31 100644
--- a/src/entropy/bot/runner.py
+++ b/src/entropy/bot/runner.py
@@ -109,6 +109,16 @@ class BotRunner:
         self.paused = False
         self._recent_signals: deque[str] = deque(maxlen=_RECENT_MAX)
         self._recent_rejects: deque[str] = deque(maxlen=_RECENT_MAX)
+        # Sigma-scaled barrier mode (RiskOverrides.stop_mode): when "sigma", an
+        # entry signal carrying sigma > 0 anchors the stop/TP at
+        # mult * sigma instead of the profile's percents. Barriers are anchored
+        # ONCE at open below — never re-anchored (harness ledger invariant).
+        self._stop_mode = config.risk_overrides.stop_mode
+        self._stop_sigma_mult = config.risk_overrides.stop_sigma_mult
+        self._tp_sigma_mult = config.risk_overrides.tp_sigma_mult
+        #: symbol -> entry-bar sigma of the entry signal being processed (set
+        #: in on_trade, consumed-or-dropped in _execute / on rejection).
+        self._entry_sigma: dict[str, float] = {}
 
     # ---- synchronous hot path -------------------------------------------------
     def on_trade(self, symbol: str, price: float, amount: float, side: str, ts_ns: int) -> None:
@@ -132,6 +142,11 @@ class BotRunner:
                     self._recent_rejects.append(f"{sig.symbol}: paused")
                     self._notify_closed(sig.symbol, "paused")
                     continue
+                if sig.action in (SignalAction.ENTER_LONG, SignalAction.ENTER_SHORT):
+                    # Stash the entry bar's sigma (None when the strategy does
+                    # not measure it) so the open path below can anchor
+                    # sigma-scaled barriers. Removed on use or rejection.
+                    self._entry_sigma[sig.symbol] = sig.sigma
                 decision = self.risk.evaluate(sig, self.portfolio, price, ts_ns)
                 if decision.approved and decision.order is not None:
                     self._execute(decision.order)
@@ -142,6 +157,7 @@ class BotRunner:
                         # The entry never happened, so the strategy must not go on
                         # believing it holds the position it just asked for.
                         self._notify_closed(sig.symbol, f"rejected: {decision.reason}")
+                        self._entry_sigma.pop(sig.symbol, None)
 
     def _execute(self, order: Order) -> None:
         try:
@@ -157,7 +173,25 @@ class BotRunner:
             return
         if order.intent is OrderIntent.OPEN:
             pos_side = PositionSide.LONG if order.side is OrderSide.BUY else PositionSide.SHORT
-            stop_px, tp_px = self.risk.stop_tp_prices(pos_side, fill.price, order.symbol)
+            sigma = self._entry_sigma.pop(order.symbol, None)
+            if (
+                self._stop_mode == "sigma"
+                and sigma is not None and sigma > 0.0
+            ):
+                # Sigma-scaled barriers: convert the sigma multipliers into the
+                # percent units the barrier computation already consumes.
+                # stop_tp_prices skips its tick-window volatility scale here —
+                # sigma IS the entry bar's volatility (no double-counting).
+                stop_pct = self._stop_sigma_mult * sigma * 100.0
+                tp_pct = self._tp_sigma_mult * sigma * 100.0
+                stop_px, tp_px = self.risk.stop_tp_prices(
+                    pos_side, fill.price, order.symbol,
+                    stop_pct=stop_pct, tp_pct=tp_pct,
+                )
+            else:
+                # percent mode, or sigma missing/zero on the entry signal:
+                # exactly the legacy anchoring.
+                stop_px, tp_px = self.risk.stop_tp_prices(pos_side, fill.price, order.symbol)
             self.portfolio.open(order.symbol, pos_side, fill.qty, fill.price,
                                 stop_px, tp_px, fill.ts_ns, fill.fee)
             self.ledger.record_trade_open(
@@ -250,6 +284,10 @@ class BotRunner:
         old_profile = self.risk.profile.name
 
         self.config = cfg
+        # Barrier mode rides on risk_overrides, read fresh per open below.
+        self._stop_mode = cfg.risk_overrides.stop_mode
+        self._stop_sigma_mult = cfg.risk_overrides.stop_sigma_mult
+        self._tp_sigma_mult = cfg.risk_overrides.tp_sigma_mult
         profile = cfg.profile()
         if profile != self.risk.profile:
             self.risk.set_profile(profile)
diff --git a/src/entropy/bot/signals.py b/src/entropy/bot/signals.py
index 128148f..65cd3d9 100644
--- a/src/entropy/bot/signals.py
+++ b/src/entropy/bot/signals.py
@@ -18,3 +18,7 @@ class Signal(msgspec.Struct, frozen=True):
     reason: str
     ts_ns: int
     strategy: str
+    #: Per-bar RMS of returns (a fraction, e.g. 0.0011) at the entry bar, set by
+    #: strategies on ENTRY signals. Sigma-scaled stop/TP barriers consume it;
+    #: None (exits, strategies that do not measure it) falls back to percent.
+    sigma: float | None = None
diff --git a/src/entropy/bot/strategies/consensus.py b/src/entropy/bot/strategies/consensus.py
index aca6f3b..9b85fc0 100644
--- a/src/entropy/bot/strategies/consensus.py
+++ b/src/entropy/bot/strategies/consensus.py
@@ -223,6 +223,15 @@ class ConsensusStrategy:
     * ``confirm_bars``: require the entry condition to hold for that many
       consecutive completed bars before signalling, so a single noisy bar
       cannot fire an entry.
+
+    Two optional position-lifecycle levers:
+
+    * ``long_only``: never emit ENTER_SHORT (a short-qualifying score resets
+      the streak instead of entering). Matches spot-deployable reality — Binance
+      spot cannot open shorts live, so short "accuracy" is not executable.
+    * ``max_hold_bars``: time stop — after that many completed bars in the
+      trade the position is exited regardless of score/trend/trail (0 = off;
+      must be 0 or >= ``min_hold_bars`` or construction raises).
     """
 
     name = "consensus"
@@ -259,6 +268,8 @@ class ConsensusStrategy:
         min_hold_bars: int = 3,
         cooldown_bars: int = 2,
         exit_mode: str = "score",
+        max_hold_bars: int = 0,
+        long_only: bool = False,
         regime_window: int = 20,
         slope_lookback: int = 5,
         direction_bars: int = 0,
@@ -297,6 +308,12 @@ class ConsensusStrategy:
             raise ValueError("direction_min_slope must be >= 0")
         if trail_pct < 0.0:
             raise ValueError("trail_pct must be >= 0")
+        if max_hold_bars < 0:
+            raise ValueError("max_hold_bars must be >= 0 (0 = off)")
+        if max_hold_bars > 0 and max_hold_bars < max(0, min_hold_bars):
+            # A time stop that expires BEFORE the min-hold gate lifts can never
+            # fire — the config is contradictory, refuse it at construction.
+            raise ValueError("max_hold_bars must be 0 (off) or >= min_hold_bars")
 
         self.symbols = symbols  # None = trade every symbol
         self.bar_s = bar_s
@@ -322,6 +339,12 @@ class ConsensusStrategy:
         self.min_hold_bars = max(0, min_hold_bars)
         self.cooldown_bars = max(0, cooldown_bars)
         self.exit_mode = exit_mode
+        self.max_hold_bars = max_hold_bars
+        #: When True the strategy never emits ENTER_SHORT: a short-qualifying
+        #: score skips the entry entirely. Matches spot-deployable reality —
+        #: Binance spot cannot open short positions live, so the accuracy the
+        #: harness measures on shorts is not executable.
+        self.long_only = long_only
         self.regime_window = max(2, regime_window)
         self.slope_lookback = max(1, slope_lookback)
         self.direction_bars = direction_bars
@@ -538,6 +561,12 @@ class ConsensusStrategy:
                 st.streak_dir = 0
                 return []
             sgn = 1 if score > 0 else -1
+            if self.long_only and sgn < 0:
+                # long_only: a short-qualifying score is not an entry chance we
+                # sit out — the short is never tradable, so the streak resets.
+                st.streak = 0
+                st.streak_dir = 0
+                return []
             if st.streak_dir != sgn:
                 st.streak, st.streak_dir = 1, sgn
             else:
@@ -562,9 +591,16 @@ class ConsensusStrategy:
             # too early — often on the first bar after min_hold.
             st.peak = 0.0
             st.last_close = 0.0
+            # Sigma at the ENTRY bar, fresh for this signal (not the cost gate's
+            # cached read): the risk layer converts it into sigma-scaled stop/TP
+            # barriers. Same value the cost gate cached — set the cache too so
+            # both consumers agree.
+            sigma = self._bar_move_rms(closes)
+            self._last_move_rms[symbol] = sigma
             action = SignalAction.ENTER_LONG if sgn > 0 else SignalAction.ENTER_SHORT
             return [Signal(symbol=symbol, action=action, strength=abs(score),
-                           reason=reason, ts_ns=ts_ns, strategy=self.name)]
+                           reason=reason, ts_ns=ts_ns, strategy=self.name,
+                           sigma=sigma)]
 
         # Trail anchors accumulate from the FIRST completed bar of the trade:
         # this block deliberately runs above the min_hold early-return so the
@@ -577,6 +613,18 @@ class ConsensusStrategy:
             st.peak = max(st.peak, close) if st.peak > 0.0 else close
         else:
             st.peak = min(st.peak, close) if st.peak > 0.0 else close
+        if self.max_hold_bars > 0 and st.bars_in_trade >= self.max_hold_bars:
+            # Time stop: overrides the score/trend/trail checks entirely and is
+            # independent of min_hold (construction pins max_hold_bars == 0 or
+            # >= min_hold_bars, so it always fires the moment it is eligible).
+            held = st.bars_in_trade
+            st.direction = 0
+            st.bars_in_trade = 0
+            st.bars_since_exit = 0
+            return [Signal(symbol=symbol, action=SignalAction.EXIT, strength=1.0,
+                           reason=f"time stop after {held} bars "
+                                  f"(max_hold_bars={self.max_hold_bars})",
+                           ts_ns=ts_ns, strategy=self.name)]
         if st.bars_in_trade < self.min_hold_bars:
             return []  # a fresh position rides out its first few bars
         if self.costs is not None:
diff --git a/tests/bot/test_consensus.py b/tests/bot/test_consensus.py
index b98733b..4052d2a 100644
--- a/tests/bot/test_consensus.py
+++ b/tests/bot/test_consensus.py
@@ -767,3 +767,119 @@ def test_peak_accumulates_during_min_hold_and_anchors_first_trail_decision():
     assert exit_bar - entry_bar == 5  # the FIRST decision bar min_hold allows
     # the anchor was the pre-crash high accumulated during min_hold
     assert strat._states["SPY"].peak == pytest.approx(trade_high)
+
+
+# ---- T3 accuracy levers: long_only, time stop, entry sigma ------------------
+
+
+def feed_signals(strat: ConsensusStrategy, symbol: str,
+                 closes: list[float]) -> list[tuple[int, Signal]]:
+    """One tick per 5s bucket; returns (bar_index, Signal) pairs."""
+    out: list[tuple[int, Signal]] = []
+    for i, px in enumerate(closes):
+        ts = i * _BAR_NS + 1
+        for sig in strat.on_tick(symbol, px, ts, events=[]):
+            out.append((i, sig))
+    return out
+
+
+@pytest.mark.parametrize("seed", [1, 7, 42])
+def test_long_only_never_emits_enter_short(seed):
+    """A short-qualifying score is skipped entirely under long_only: the
+    downtrend that produces exactly one ENTER_SHORT normally must produce
+    NOTHING, and the strategy must not secretly track a short either."""
+    strat = ConsensusStrategy(symbols=("SPY",), long_only=True)
+    events = feed_signals(strat, "SPY", path_trend(seed, direction=-1))
+    assert events == []
+    assert strat._states["SPY"].direction == 0
+    # no short streak survives either: the reset leaves it at zero
+    assert strat._states["SPY"].streak == 0
+
+
+@pytest.mark.parametrize("seed", [1, 7, 42])
+def test_long_only_still_enters_long(seed):
+    strat = ConsensusStrategy(symbols=("SPY",), long_only=True)
+    events = feed_signals(strat, "SPY", path_trend(seed, direction=1))
+    assert [sig.action for _, sig in events] == [SignalAction.ENTER_LONG]
+
+
+def test_long_only_default_false_keeps_shorts():
+    """Default off: existing behavior bit-for-bit (the short still fires)."""
+    strat = ConsensusStrategy(symbols=("SPY",))
+    events = feed_signals(strat, "SPY", path_trend(7, direction=-1))
+    assert [sig.action for _, sig in events] == [SignalAction.ENTER_SHORT]
+
+
+def test_time_stop_fires_at_the_configured_bar():
+    """max_hold_bars=3 with exit_mode='hold' (which never exits on score):
+    the only possible EXIT is the time stop, exactly 3 completed bars in,
+    overriding the score/trend/trail checks by construction."""
+    closes = path_trend(7, direction=1, n=60)
+    strat = ConsensusStrategy(
+        symbols=("SPY",), exit_mode="hold", max_hold_bars=3, min_hold_bars=3,
+        confirm_bars=1, cooldown_bars=1 << 30,  # no re-entry: exactly one trade
+    )
+    events = feed_signals(strat, "SPY", closes)
+    assert [sig.action for _, sig in events] == [
+        SignalAction.ENTER_LONG, SignalAction.EXIT,
+    ]
+    entry_bar, exit_bar = (b for b, _ in events)
+    assert exit_bar - entry_bar == 3
+    assert "time stop" in events[1][1].reason
+    st = strat._states["SPY"]
+    assert st.direction == 0 and st.bars_in_trade == 0
+
+
+def test_time_stop_is_off_by_default():
+    """max_hold_bars defaults to 0: the same hold-mode path never exits."""
+    strat = ConsensusStrategy(symbols=("SPY",), exit_mode="hold")
+    events = feed_signals(strat, "SPY", path_trend(7, direction=1, n=120))
+    assert [sig.action for _, sig in events] == [SignalAction.ENTER_LONG]
+
+
+def test_time_stop_validation_requires_min_hold_compatibility():
+    with pytest.raises(ValueError):
+        ConsensusStrategy(symbols=("SPY",), min_hold_bars=5, max_hold_bars=3)
+    with pytest.raises(ValueError):
+        ConsensusStrategy(symbols=("SPY",), max_hold_bars=-1)
+    # 0 (off) and >= min_hold are both legal
+    ConsensusStrategy(symbols=("SPY",), min_hold_bars=5, max_hold_bars=0)
+    ConsensusStrategy(symbols=("SPY",), min_hold_bars=5, max_hold_bars=5)
+
+
+def test_entry_signals_carry_entry_bar_sigma():
+    """ENTRY signals carry the entry bar's RMS of returns (a fraction) and the
+    cost-gate cache is set to the same value; EXIT signals carry no sigma."""
+    closes = path_trend(3, direction=1, n=60) + [
+        px * 0.97 for px in path_trend(3, direction=1, n=60)[-60:]
+    ]
+    strat = ConsensusStrategy(
+        symbols=("SPY",), exit_mode="trail", trail_pct=0.005,
+        min_hold_bars=0, cooldown_bars=0, confirm_bars=1,
+        weights={"ema": 0.5, "rsi": 0.5},
+    )
+    events = feed_signals(strat, "SPY", closes)
+    actions = [sig.action for _, sig in events]
+    assert SignalAction.ENTER_LONG in actions
+    assert SignalAction.EXIT in actions
+    entry = [sig for _, sig in events if sig.action is SignalAction.ENTER_LONG][-1]
+    exit_sig = next(sig for _, sig in events if sig.action is SignalAction.EXIT)
+    assert entry.sigma is not None and entry.sigma > 0.0
+    assert entry.sigma < 1.0  # a fraction, not a percent
+    # the cache holds the LAST entry's sigma (no cost model -> no other writer)
+    assert strat._last_move_rms["SPY"] == pytest.approx(entry.sigma)
+    assert exit_sig.sigma is None
+
+
+def test_config_wiring_long_only_and_max_hold_bars():
+    from entropy.bot.config import ConsensusConfig
+
+    cfg = BotConfig(strategies=("consensus",), consensus=ConsensusConfig(
+        long_only=True, max_hold_bars=9,
+    ))
+    strat = build_strategies(cfg)[0]
+    assert strat.long_only is True
+    assert strat.max_hold_bars == 9
+    # defaults stay no-ops
+    dflt = build_strategies(BotConfig())[0]
+    assert dflt.long_only is False and dflt.max_hold_bars == 0
diff --git a/tests/bot/test_sigma_barriers.py b/tests/bot/test_sigma_barriers.py
new file mode 100644
index 0000000..49decac
--- /dev/null
+++ b/tests/bot/test_sigma_barriers.py
@@ -0,0 +1,156 @@
+"""Sigma-scaled stop/TP barriers (T3): RiskManager override path, runner
+plumbing from the entry signal's ``sigma`` to the anchored barriers, and the
+RiskOverrides validation. Percent mode must stay bit-for-bit legacy."""
+
+from __future__ import annotations
+
+import pytest
+
+from entropy.bot.config import BotConfig, ConsensusConfig, RiskOverrides, validate
+from entropy.bot.portfolio import PositionSide
+from entropy.bot.risk.manager import RiskManager
+from entropy.bot.risk.profiles import MEDIUM
+from entropy.bot.runner import BotRunner
+from entropy.bot.signals import Signal, SignalAction
+
+_NS = 1_000_000_000
+
+
+def _volatile_history(rm: RiskManager, symbol: str = "SPY") -> None:
+    """6 in-window ticks that WOULD scale the percent barriers by 22/21 in
+    legacy mode — the sigma override must skip that scale factor entirely."""
+    for i, px in enumerate((100.0, 110.0, 100.0, 110.0, 100.0, 110.0)):
+        rm.update_tick(symbol, px, 1000 + i)
+
+
+def test_stop_tp_prices_sigma_override_ignores_window_scale():
+    rm = RiskManager(MEDIUM)
+    _volatile_history(rm)
+    # sigma = 0.0011 with the default multipliers (1.5 / 1.2) -> 0.165% / 0.132%
+    sl, tp = rm.stop_tp_prices(PositionSide.LONG, 100.0, "SPY",
+                               stop_pct=0.165, tp_pct=0.132)
+    assert sl == pytest.approx(100.0 * (1 - 0.165 / 100))
+    assert tp == pytest.approx(100.0 * (1 + 0.132 / 100))
+    sl_s, tp_s = rm.stop_tp_prices(PositionSide.SHORT, 100.0, "SPY",
+                                   stop_pct=0.165, tp_pct=0.132)
+    assert sl_s == pytest.approx(100.0 * (1 + 0.165 / 100))
+    assert tp_s == pytest.approx(100.0 * (1 - 0.132 / 100))
+
+
+def test_stop_tp_prices_sigma_override_is_clamped_at_50pct():
+    rm = RiskManager(MEDIUM)
+    sl, tp = rm.stop_tp_prices(PositionSide.LONG, 100.0, "SPY",
+                               stop_pct=80.0, tp_pct=60.0)
+    assert sl == pytest.approx(100.0 * (1 - 50.0 / 100))
+    assert tp == pytest.approx(100.0 * (1 + 50.0 / 100))
+
+
+def test_stop_tp_prices_without_override_is_legacy_behavior():
+    """No overrides: the tick-window volatility scale factor applies exactly as
+    before (22/21 with the alternating history)."""
+    rm = RiskManager(MEDIUM)
+    _volatile_history(rm)
+    scale = 22.0 / 21.0
+    sl, tp = rm.stop_tp_prices(PositionSide.LONG, 100.0, "SPY")
+    assert sl == pytest.approx(100.0 * (1 - (MEDIUM.stop_loss_pct * scale) / 100))
+    assert tp == pytest.approx(100.0 * (1 + (MEDIUM.take_profit_pct * scale) / 100))
+
+
+def test_risk_overrides_barriers_validate_at_construction():
+    with pytest.raises(ValueError):
+        RiskOverrides(stop_mode="atr")
+    with pytest.raises(ValueError):
+        RiskOverrides(stop_sigma_mult=0.0)
+    with pytest.raises(ValueError):
+        RiskOverrides(tp_sigma_mult=-1.0)
+    # defaults are the no-op percent shape
+    ro = RiskOverrides()
+    assert ro.stop_mode == "percent"
+    assert (ro.stop_sigma_mult, ro.tp_sigma_mult) == (1.5, 1.2)
+    # barrier fields are never handed to make_custom (they are not profile
+    # fields) — cfg.profile() must keep working with sigma mode on
+    cfg = BotConfig(risk_overrides=RiskOverrides(stop_mode="sigma"))
+    assert cfg.profile().name == "Medium"
+
+
+def test_validate_reports_time_stop_problems():
+    """max_hold_bars < min_hold_bars (and not 0) can never fire — validate()
+    must name it. (Bad stop_mode / non-positive sigma multipliers are refused
+    earlier, by RiskOverrides.__post_init__ raising ValueError.)"""
+    bad = BotConfig(consensus=ConsensusConfig(min_hold_bars=5, max_hold_bars=2))
+    problems = validate(bad)
+    assert any("max_hold_bars" in p for p in problems)
+    assert validate(BotConfig()) == []
+    # a compatible time stop validates clean
+    ok = BotConfig(consensus=ConsensusConfig(min_hold_bars=5, max_hold_bars=5))
+    assert validate(ok) == []
+
+
+class _SigmaStub:
+    """Emits one sigma-carrying entry signal per tick, then goes silent."""
+
+    name = "sigma_stub"
+
+    def __init__(self, sigma: float | None) -> None:
+        self._sigma = sigma
+        self._fired = False
+
+    def on_tick(self, symbol, price, ts_ns, events):
+        if self._fired:
+            return []
+        self._fired = True
+        return [Signal(symbol=symbol, action=SignalAction.ENTER_LONG, strength=1.0,
+                       reason="stub", ts_ns=ts_ns, strategy=self.name,
+                       sigma=self._sigma)]
+
+
+def _run_one_entry(tmp_path, stop_mode: str, sigma: float | None):
+    cfg = BotConfig(
+        starting_cash=100_000.0, enable_crypto=False, enable_equities=False,
+        risk_overrides=RiskOverrides(stop_mode=stop_mode),
+    )
+    runner = BotRunner(cfg, run_dir=str(tmp_path))
+    runner.strategies = [_SigmaStub(sigma)]
+    runner.on_trade("SPY", 100.0, 1.0, "buy", 1_000)
+    pos = runner.portfolio.positions.get("SPY")
+    assert pos is not None, "stub entry must fill"
+    return runner, pos
+
+
+# equity costs resolve to 2 bps fee / 2 bps slippage -> BUY fill = 100.02
+_FILL = 100.0 * (1 + 2.0 / 10_000.0)
+
+
+def test_runner_sigma_mode_anchors_barriers_from_entry_sigma(tmp_path):
+    runner, pos = _run_one_entry(tmp_path, "sigma", sigma=0.001)
+    # stop = 1.5 * 0.001 = 0.15% below the fill, tp = 1.2 * 0.001 = 0.12% above
+    assert pos.stop_px == pytest.approx(_FILL * (1 - 0.0015))
+    assert pos.tp_px == pytest.approx(_FILL * (1 + 0.0012))
+    # the stash is consumed: nothing left behind for a later unrelated entry
+    assert runner._entry_sigma == {}
+
+
+def test_runner_sigma_mode_rejects_are_dropped(tmp_path):
+    """An entry the risk layer refuses must not leave a stale sigma behind."""
+    cfg = BotConfig(
+        enable_crypto=False, enable_equities=False,
+        risk_overrides=RiskOverrides(stop_mode="sigma", per_trade_pct=0.0),  # qty <= 0
+    )
+    runner = BotRunner(cfg, run_dir=str(tmp_path))
+    runner.strategies = [_SigmaStub(sigma=0.001)]
+    runner.on_trade("SPY", 100.0, 1.0, "buy", 1_000)
+    assert "SPY" not in runner.portfolio.positions
+    assert runner._entry_sigma == {}
+
+
+def test_runner_missing_sigma_falls_back_to_percent(tmp_path):
+    runner, pos = _run_one_entry(tmp_path, "sigma", sigma=None)
+    # 1 tick of history (< 5) -> no volatility scaling: MEDIUM 1% / 2%
+    assert pos.stop_px == pytest.approx(_FILL * (1 - MEDIUM.stop_loss_pct / 100))
+    assert pos.tp_px == pytest.approx(_FILL * (1 + MEDIUM.take_profit_pct / 100))
+
+
+def test_runner_percent_mode_ignores_entry_sigma(tmp_path):
+    runner, pos = _run_one_entry(tmp_path, "percent", sigma=0.001)
+    assert pos.stop_px == pytest.approx(_FILL * (1 - MEDIUM.stop_loss_pct / 100))
+    assert pos.tp_px == pytest.approx(_FILL * (1 + MEDIUM.take_profit_pct / 100))
