from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass

from .universe import SECTORS, UNIVERSE, SymParams, build_params


@dataclass(slots=True)
class SymRuntime:
    px: float
    anchor: float
    sess_high: float
    sess_low: float
    new_high: bool = False
    new_low: bool = False

class EquitySimulator:
    """Deterministic given the injected rng. step_symbol advances one symbol."""
    def __init__(self, rng: random.Random, clock_ns: Callable[[], int]) -> None:
        self.rng = rng
        self.clock_ns = clock_ns
        self.params: dict[str, SymParams] = build_params(rng)
        self.rt: dict[str, SymRuntime] = {
            s: SymRuntime(px=p.s0, anchor=p.s0, sess_high=p.s0, sess_low=p.s0)
            for s, p in self.params.items()
        }
        self._spike: dict[str, int] = {}
        self._spike_dir: dict[str, float] = {}
        self._sector_keys = list(SECTORS.keys())

    def step_symbol(self, sym: str) -> tuple[str, float, float, str]:
        p = self.params[sym]
        r = self.rt[sym]
        z = self.rng.gauss(0.0, 1.0)
        mr = -p.mr_kappa * (r.px - r.anchor) / r.anchor
        ret = (p.drift_bps + mr * 10_000.0) * 1e-4 + (p.sigma_bps * 1e-4) * z
        rem = self._spike.get(sym, 0)
        if rem > 0:
            ret += self._spike_dir[sym] * (p.sigma_bps * 1e-4) * 8.0
            self._spike[sym] = rem - 1
        r.px = max(0.01, r.px * math.exp(ret))
        r.new_high = r.px > r.sess_high
        r.new_low = r.px < r.sess_low
        if r.new_high:
            r.sess_high = r.px
        if r.new_low:
            r.sess_low = r.px
        r.anchor *= math.exp((p.drift_bps * 1e-4) * 0.1)
        side = "buy" if ret >= 0 else "sell"
        size = max(1.0, p.base_size * (0.5 + self.rng.random()))
        return sym, r.px, round(size), side

    def maybe_inject_events(self) -> None:
        if self.rng.random() < 0.15:
            sym = self.rng.choice(UNIVERSE)
            self._spike[sym] = self.rng.randint(3, 12)
            self._spike_dir[sym] = self.rng.choice((1.0, -1.0))
        if self.rng.random() < 0.04:
            sec = self.rng.choice(self._sector_keys)
            direction = self.rng.choice((1.0, -1.0))
            for sym in SECTORS[sec]:
                if self.rng.random() < 0.6:
                    self._spike[sym] = self.rng.randint(4, 15)
                    self._spike_dir[sym] = direction
