from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class EmaState:
    span: int
    alpha: float = field(init=False)
    value: float | None = None
    count: int = 0

    def __post_init__(self) -> None:
        self.alpha = 2.0 / (self.span + 1.0)


def ema_update(st: EmaState, px: float) -> float:
    if st.value is None:
        st.value = px
    else:
        st.value += st.alpha * (px - st.value)
    st.count += 1
    return st.value
