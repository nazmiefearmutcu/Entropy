import pytest


@pytest.fixture
def ns():
    """Helper to build nanosecond timestamps from float seconds."""
    return lambda s: int(s * 1_000_000_000)
