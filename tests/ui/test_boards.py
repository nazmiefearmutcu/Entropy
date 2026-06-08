from entropy.ui.widgets.boards import row_text
from entropy.engine.leaderboard import LeaderRow

def test_row_text_colors_by_sign():
    cells = row_text(LeaderRow("AAPL", 5, 191.2, 2.4))
    assert cells[0].plain == "AAPL"
    assert cells[3].plain == "+2.40%"
    cells_dn = row_text(LeaderRow("NVDA", 3, 88.0, -1.1))
    assert cells_dn[3].plain == "-1.10%"
