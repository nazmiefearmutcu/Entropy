# commits
b91e644 docs: WR>60 OOS evidence table, honest caveats, reproduce commands
ee027b1 feat(scripts): rolling WR gate
894f976 feat(bot): ship WR>60 OOS defaults
e5064ae feat(scripts): wave3 sweep space with selection guards
f2a90be docs(bot): warn that RiskOverrides.active() is lossy for barrier fields
e07670d fix(bot): sigma-mode entry cost gate judges the actual sigma barriers
b0606b2 feat(scripts): --long-only/--max-hold-bars/--stop-mode/--*-sigma-mult flags in accuracy harness
3fd4053 feat(bot): sigma-scaled stop/TP barriers via RiskOverrides stop_mode + runner plumbing
276ad9e feat(bot): long_only flag, max_hold_bars time stop, entry-bar sigma on consensus signals
c313e81 fix(scripts): direction-aware intrabar barrier order — shorts resolve stop first too
877d88c fix(scripts): pessimistic intrabar barrier resolution + level fills in accuracy harness
9aee08e fix(bot): reset consensus trail peak on entry and close, track it through min_hold

# stat
 PROJECT.md                              |  66 ++++++
 progress.md                             |  11 +
 scripts/entropy_accuracy_btc15m.py      | 373 ++++++++++++++++++++++++++------
 scripts/entropy_wr_gate.py              | 233 ++++++++++++++++++++
 scripts/entropy_wr_sweep.py             | 142 ++++++++++--
 src/entropy/bot/config.py               |  63 +++++-
 src/entropy/bot/risk/manager.py         | 100 +++++++--
 src/entropy/bot/runner.py               |  41 +++-
 src/entropy/bot/signals.py              |   4 +
 src/entropy/bot/strategies/consensus.py |  76 ++++++-
 tests/bot/test_accuracy_harness.py      | 345 +++++++++++++++++++++++++++++
 tests/bot/test_consensus.py             | 251 ++++++++++++++++++++-
 tests/bot/test_sigma_barriers.py        | 246 +++++++++++++++++++++
 tests/bot/test_wr_gate.py               | 201 +++++++++++++++++
 tests/bot/test_wr_sweep_space.py        | 265 +++++++++++++++++++++++
 15 files changed, 2304 insertions(+), 113 deletions(-)
