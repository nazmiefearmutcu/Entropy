"""T8 Deliverable A: per-symbol ``vote_mode`` (string | dict) plumbing.

Pins the Round-2 fix for ETH's regime misclassification (R1: `adaptive` never
reads momentum on ETH because its Kaufman efficiency < trend_er, while `trend`
regresses BTC — so the mode must be selectable per symbol):

* the config field accepts a plain string (byte-compat) and a dict mapping raw
  symbol -> mode, and both forms survive the msgspec (de)serialization path
  (the repo's config "YAML");
* ``build_strategies`` threads the map through with ``adaptive`` as the
  strategy-level fallback;
* ``ConsensusStrategy._evaluate`` resolves the mode per symbol (raw AND
  canonical keys match) — proven by feeding two symbols on the same path with
  different modes and getting the mode-appropriate behavior;
* legacy pinning (total normalize + min_participation 0) applies when ANY
  per-symbol entry is "legacy", exactly as for the single string today;
* unknown map keys warn (not error) in ``warnings()``; bad values are real
  problems in ``validate()`` and raise at strategy construction.
"""

from __future__ import annotations

import msgspec
import pytest

from entropy import settings
from entropy.app import AppConfig
from entropy.bot.config import BotConfig, ConsensusConfig, build_strategies, validate, warnings
from entropy.bot.signals import SignalAction
from entropy.bot.strategies.consensus import ConsensusStrategy

from .test_consensus import feed_bars, path_trend

_NS = 1_000_000_000
_BAR_NS = 5 * _NS


# ---- serialization: both forms parse and build ----------------------------------


def test_string_form_stays_byte_compatible():
    """A plain string keeps the current behavior: the strategy-level vote_mode
    is the string and the per-symbol map is empty."""
    strat = ConsensusStrategy(vote_mode="trend")
    assert strat.vote_mode == "trend"
    assert strat._vote_mode_for == {}


def test_dict_form_decodes_from_msgspec_and_builds():
    """The config serialization path (settings.json / the "YAML" form) accepts
    the per-symbol dict and build_strategies wires it with an 'adaptive'
    fallback."""
    cfg = msgspec.json.decode(
        b'{"strategies": ["consensus"],'
        b' "symbols": ["binance-spot:BTCUSDT", "binance-spot:ETHUSDT"],'
        b' "consensus": {"vote_mode": {"ETHUSDT": "trend", "BTCUSDT": "adaptive"}}}',
        type=BotConfig,
    )
    assert cfg.consensus.vote_mode == {"ETHUSDT": "trend", "BTCUSDT": "adaptive"}
    strat = build_strategies(cfg)[0]
    assert strat.vote_mode == "adaptive"          # dict form -> adaptive fallback
    assert strat._vote_mode_for == {"ETHUSDT": "trend", "BTCUSDT": "adaptive"}


def test_string_form_decodes_from_msgspec_as_before():
    cfg = msgspec.json.decode(
        b'{"strategies": ["consensus"], "consensus": {"vote_mode": "trend"}}',
        type=BotConfig,
    )
    strat = build_strategies(cfg)[0]
    assert strat.vote_mode == "trend"
    assert strat._vote_mode_for == {}


def test_dict_form_survives_settings_round_trip(tmp_path):
    original = settings.EntropySettings(
        app=AppConfig(),
        bot=BotConfig(
            strategies=("consensus",),
            symbols=("binance-spot:BTCUSDT", "binance-spot:ETHUSDT"),
            consensus=ConsensusConfig(vote_mode={"ETHUSDT": "trend"}),
        ),
    )
    path = tmp_path / "settings.json"
    settings.save(original, path)
    loaded = settings.load(path)
    assert loaded.bot.consensus.vote_mode == {"ETHUSDT": "trend"}
    strat = build_strategies(loaded.bot)[0]
    assert strat._vote_mode_for == {"ETHUSDT": "trend"}
    assert strat.vote_mode == "adaptive"


# ---- validation: warn on unknown keys, error on bad values ----------------------


def test_unknown_map_key_warns_but_does_not_fail():
    cfg = BotConfig(
        symbols=("binance-spot:BTCUSDT",),
        consensus=ConsensusConfig(vote_mode={"NOTTRADED": "trend"}),
    )
    notes = warnings(cfg)
    assert any("vote_mode key 'NOTTRADED'" in n for n in notes)
    assert validate(cfg) == []          # a superset of keys is NOT a hard error


def test_known_keys_do_not_warn():
    cfg = BotConfig(
        symbols=("binance-spot:ETHUSDT",),
        consensus=ConsensusConfig(vote_mode={"ETHUSDT": "trend"}),
    )
    assert not any("vote_mode key" in n for n in warnings(cfg))
    assert validate(cfg) == []


def test_bad_value_is_a_validate_problem_and_a_constructor_error():
    cfg = BotConfig(
        symbols=("binance-spot:ETHUSDT",),
        consensus=ConsensusConfig(vote_mode={"ETHUSDT": "vibes"}),
    )
    assert any("vote_mode for 'ETHUSDT'" in p for p in validate(cfg))
    with pytest.raises(ValueError):
        ConsensusStrategy(vote_mode_for={"ETHUSDT": "vibes"})
    with pytest.raises(ValueError):
        ConsensusStrategy(vote_mode="vibes")   # single-string path unchanged


# ---- per-symbol resolution in _evaluate ------------------------------------------


def test_mode_resolves_per_symbol_raw_and_canonical_keys():
    strat = ConsensusStrategy(vote_mode_for={"BTCUSDT": "trend"})
    # raw-key match on a canonical symbol: the R1 fix's exact call pattern
    assert strat._mode_for("binance-spot:BTCUSDT") == "trend"
    assert strat._mode_for("binance-spot:ETHUSDT") == "adaptive"   # fallback
    canonical = ConsensusStrategy(vote_mode_for={"binance-spot:BTCUSDT": "legacy"})
    assert canonical._mode_for("binance-spot:BTCUSDT") == "legacy"  # exact key
    assert canonical.vote_mode == "adaptive"                        # fallback


def test_two_symbols_different_modes_both_exercised():
    """Same trending path, two symbols: the 'legacy' symbol is trend-blind
    (zero entries), the 'adaptive' symbol trades — proving _evaluate selects
    the mode per symbol rather than globally."""
    closes = path_trend(42, direction=1)
    strat = ConsensusStrategy(symbols=("A", "B"),
                              vote_mode_for={"A": "legacy", "B": "adaptive"})
    a_events = [a for _, a in feed_bars(strat, "A", closes)]
    b_events = [a for _, a in feed_bars(strat, "B", closes)]
    assert SignalAction.ENTER_LONG not in a_events    # legacy: trend-blind
    assert SignalAction.ENTER_LONG in b_events        # adaptive: rides the trend


def test_fallback_mode_applies_to_unlisted_symbols():
    closes = path_trend(7, direction=1)
    strat = ConsensusStrategy(symbols=("A", "B"), vote_mode_for={"A": "legacy"})
    # A pinned legacy; B resolves to the strategy fallback (adaptive) and trades
    assert SignalAction.ENTER_LONG not in [a for _, a in feed_bars(strat, "A", closes)]
    assert SignalAction.ENTER_LONG in [a for _, a in feed_bars(strat, "B", closes)]


# ---- legacy pinning, per-symbol ---------------------------------------------------


def test_legacy_pinning_applies_for_per_symbol_legacy_entries():
    """A 'legacy' entry for ANY symbol pins total normalize + no participation
    floor, exactly as the single-string legacy does today (the scoring knobs
    are strategy-global, so that is the only way the legacy symbol is scored
    faithfully)."""
    strat = ConsensusStrategy(vote_mode="adaptive",
                              vote_mode_for={"SPY": "legacy"},
                              normalize="participating", min_participation=0.9)
    assert strat.normalize == "total"
    assert strat.min_participation == 0.0


def test_legacy_pinning_not_applied_without_a_legacy_entry():
    strat = ConsensusStrategy(vote_mode="adaptive",
                              vote_mode_for={"SPY": "trend"},
                              normalize="participating", min_participation=0.9)
    assert strat.normalize == "participating"
    assert strat.min_participation == 0.9


def test_single_string_legacy_still_pins():
    strat = ConsensusStrategy(vote_mode="legacy", normalize="participating",
                              min_participation=0.9)
    assert strat.normalize == "total"
    assert strat.min_participation == 0.0


# ---- build_strategies union handling ---------------------------------------------


def test_build_strategies_passes_map_with_adaptive_fallback():
    cfg = BotConfig(
        strategies=("consensus",),
        symbols=("binance-spot:ETHUSDT", "binance-spot:BTCUSDT"),
        consensus=ConsensusConfig(vote_mode={"ETHUSDT": "trend"}),
    )
    strat = build_strategies(cfg)[0]
    assert isinstance(strat, ConsensusStrategy)
    assert strat.vote_mode == "adaptive"
    assert strat._vote_mode_for == {"ETHUSDT": "trend"}
    assert strat._mode_for("binance-spot:ETHUSDT") == "trend"
    assert strat._mode_for("binance-spot:BTCUSDT") == "adaptive"


def test_build_strategies_string_form_has_empty_map():
    cfg = BotConfig(consensus=ConsensusConfig(vote_mode="trend"))
    strat = build_strategies(cfg)[0]
    assert strat.vote_mode == "trend"
    assert strat._vote_mode_for == {}


# ---- warmup x per-symbol mode (T8 review cannot-verify #2) ---------------------


def test_warmup_then_per_symbol_modes_still_diverge():
    """Warmup-chained state must not flatten the per-symbol vote mode: after
    seeding both symbols from the same warmup bars, the legacy symbol stays
    trend-blind while the adaptive symbol rides the trend — i.e. _evaluate
    resolves _mode_for(symbol) at evaluation time, not at warmup time."""
    from entropy.strategy.engine import Bar

    closes = path_trend(42, direction=1)
    strat = ConsensusStrategy(symbols=("A", "B"),
                              vote_mode_for={"A": "legacy", "B": "adaptive"})
    seed = [Bar(ts_ns=(i + 1) * _BAR_NS, close=c) for i, c in enumerate(closes[:40])]
    strat.warmup(seed)
    # both symbols adopted the same seeded closes; the mode must still
    # diverge at evaluation
    assert list(strat._states["A"].closes) == list(strat._states["B"].closes)
    a_events = [a for _, a in feed_bars(strat, "A", closes[40:], start_bucket=41)]
    b_events = [a for _, a in feed_bars(strat, "B", closes[40:], start_bucket=41)]
    assert SignalAction.ENTER_LONG not in a_events    # legacy: trend-blind
    assert SignalAction.ENTER_LONG in b_events        # adaptive: rides the trend