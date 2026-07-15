from textual.theme import Theme

ENTROPY_THEME = Theme(
    name="entropy", primary="#26d626", secondary="#ff3b3b", accent="#e6c200",
    foreground="#c8c8c8", background="#000000", success="#26d626",
    warning="#e6c200", error="#ff3b3b", surface="#000000", panel="#0a0a0a", dark=True,
)

DRACULA_THEME = Theme(
    name="dracula", primary="#bd93f9", secondary="#ff5555", accent="#ff79c6",
    foreground="#f8f8f2", background="#282a36", success="#50fa7b",
    warning="#f1fa8c", error="#ff5555", surface="#282a36", panel="#1e1f29", dark=True,
)

CYBERPUNK_THEME = Theme(
    name="cyberpunk", primary="#00f0ff", secondary="#ff0055", accent="#fffc00",
    foreground="#e0e0ff", background="#0a0014", success="#39ff14",
    warning="#ffb000", error="#ff0055", surface="#0a0014", panel="#120024", dark=True,
)

NORD_THEME = Theme(
    name="nord", primary="#88c0d0", secondary="#bf616a", accent="#ebcb8b",
    foreground="#d8dee9", background="#2e3440", success="#a3be8c",
    warning="#ebcb8b", error="#bf616a", surface="#2e3440", panel="#3b4252", dark=True,
)

FOREST_THEME = Theme(
    name="forest", primary="#a3be8c", secondary="#d08770", accent="#ebcb8b",
    foreground="#eceff4", background="#1b221a", success="#8fbcbb",
    warning="#ebcb8b", error="#bf616a", surface="#1b221a", panel="#242f23", dark=True,
)

MONOCHROME_THEME = Theme(
    name="monochrome", primary="#ffffff", secondary="#888888", accent="#aaaaaa",
    foreground="#dddddd", background="#000000", success="#ffffff",
    warning="#888888", error="#444444", surface="#000000", panel="#111111", dark=True,
)

SWEET_THEME = Theme(
    name="sweet", primary="#ff79c6", secondary="#8be9fd", accent="#f1fa8c",
    foreground="#f8f8f2", background="#1a0f24", success="#50fa7b",
    warning="#f1fa8c", error="#ff5555", surface="#1a0f24", panel="#231530", dark=True,
)

ALL_THEMES = [
    ENTROPY_THEME,
    DRACULA_THEME,
    CYBERPUNK_THEME,
    NORD_THEME,
    FOREST_THEME,
    MONOCHROME_THEME,
    SWEET_THEME
]

