from entropy.engine.events import WindowName


def test_positional_members():
    assert WindowName.W0.value == "w0"
    assert WindowName.W1.value == "w1"
    assert WindowName.W2.value == "w2"
    assert WindowName.SESSION.value == "session"
    # exactly these four members
    assert {w.name for w in WindowName} == {"W0", "W1", "W2", "SESSION"}
