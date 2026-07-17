from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, StrEnum

from .ema import EmaState, ema_update


class Side(Enum):
    FLAT = 0
    LONG = 1
    SHORT = 2


class EventKind(StrEnum):
    INFO = "info"
    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"


@dataclass(frozen=True, slots=True)
class Bar:
    ts_ns: int
    close: float
    high: float | None = None
    low: float | None = None


@dataclass(frozen=True, slots=True)
class StrategyEvent:
    kind: EventKind
    ts_ns: int
    symbol: str
    price: float
    running_pnl: float | None = None
    trade_pnl: float | None = None
    text: str | None = None


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    symbol: str = "SPY"
    fast: int = 9
    slow: int = 21
    mode: str = "ema"
    breakout_lookback: int = 20
    size: float = 1.0
    fee_bps: float = 0.0
    allow_short: bool = True
    warmup_bars: int = 0


@dataclass(slots=True)
class Position:
    side: Side = Side.FLAT
    entry_px: float = 0.0
    size: float = 0.0
    entry_ts_ns: int = 0


def _gross_pnl(side: Side, entry: float, px: float, size: float) -> float:
    d = (px - entry) if side is Side.LONG else (entry - px)
    return d * size


def _fee(px: float, size: float, fee_bps: float) -> float:
    return abs(px * size) * (fee_bps / 10_000.0)


class Strategy:
    def __init__(self, config: StrategyConfig) -> None:
        self.cfg = config
        self._fast = EmaState(config.fast)
        self._slow = EmaState(config.slow)
        self._prev_sign = 0
        self.position = Position()
        self._warmup_target = config.warmup_bars or config.slow

    @property
    def is_warm(self) -> bool:
        return self._slow.count >= self._warmup_target

    def warmup(self, bars: Sequence[Bar]) -> list[StrategyEvent]:
        for b in bars:
            ema_update(self._fast, b.close)
            ema_update(self._slow, b.close)
        self._prev_sign = self._signum()
        n = len(bars)
        return [StrategyEvent(EventKind.INFO, bars[-1].ts_ns if bars else 0,
                              self.cfg.symbol, bars[-1].close if bars else 0.0,
                              text=f"{self.cfg.symbol} warmup: {n} bars, EMA ready")]

    def _signum(self) -> int:
        if self._fast.value is None or self._slow.value is None:
            return 0
        d = self._fast.value - self._slow.value
        return 1 if d > 0 else (-1 if d < 0 else 0)

    def running_pnl(self, last_px: float) -> float:
        if self.position.side is Side.FLAT:
            return 0.0
        return _gross_pnl(self.position.side, self.position.entry_px, last_px, self.position.size)

    def on_price(self, symbol: str, price: float, ts_ns: int) -> list[StrategyEvent]:
        if symbol != self.cfg.symbol:
            return []
        was_warm = self.is_warm
        ema_update(self._fast, price)
        ema_update(self._slow, price)
        if not self.is_warm:
            return []
        sign = self._signum()
        if not was_warm and self._prev_sign == 0:
            # Tick-driven warmup (no warmup() call — e.g. the bot runner): the first
            # warm tick only ESTABLISHES the baseline sign. Emitting here would fire
            # a phantom entry off the stale _prev_sign=0 with no actual crossover.
            # (The warmup() path is unaffected: it leaves the strategy already warm
            # with _prev_sign seeded, so was_warm is True on its first tick.)
            self._prev_sign = sign
            return []
        events: list[StrategyEvent] = []
        desired = self.position.side
        if self._prev_sign <= 0 and sign > 0:
            desired = Side.LONG
        elif self._prev_sign >= 0 and sign < 0:
            desired = Side.SHORT
        self._prev_sign = sign
        if desired is not self.position.side and desired is not Side.FLAT:
            if self.position.side is not Side.FLAT:
                events.append(self._close(price, ts_ns))
            if desired is Side.SHORT and not self.cfg.allow_short:
                return events
            events.append(self._open(desired, price, ts_ns))
        return events

    def _open(self, side: Side, price: float, ts_ns: int) -> StrategyEvent:
        self.position = Position(side=side, entry_px=price, size=self.cfg.size, entry_ts_ns=ts_ns)
        kind = EventKind.OPEN_LONG if side is Side.LONG else EventKind.OPEN_SHORT
        return StrategyEvent(kind, ts_ns, self.cfg.symbol, price, running_pnl=0.0)

    def _close(self, price: float, ts_ns: int) -> StrategyEvent:
        p = self.position
        gross = _gross_pnl(p.side, p.entry_px, price, p.size)
        realized = (
            gross
            - _fee(p.entry_px, p.size, self.cfg.fee_bps)
            - _fee(price, p.size, self.cfg.fee_bps)
        )
        kind = EventKind.CLOSE_LONG if p.side is Side.LONG else EventKind.CLOSE_SHORT
        self.position = Position()
        return StrategyEvent(kind, ts_ns, self.cfg.symbol, price, trade_pnl=realized)
