"""Shared test fixtures."""

import pytest

from src.shield import SpikeReason  # noqa: F401


@pytest.fixture()
def default_state():
    """Return a default empty state."""
    return {"history": [], "is_protected": False, "last_spike_at": None}


@pytest.fixture()
def state_with_history():
    """Return a state with enough history for spike detection."""
    return {
        "history": [
            {"timestamp": "2026-03-01T10:00:00+00:00", "followers": 1000, "notifications": 5},
            {"timestamp": "2026-03-01T10:05:00+00:00", "followers": 1010, "notifications": 8},
            {"timestamp": "2026-03-01T10:10:00+00:00", "followers": 1020, "notifications": 6},
            {"timestamp": "2026-03-01T10:15:00+00:00", "followers": 1030, "notifications": 7},
        ],
        "is_protected": False,
        "last_spike_at": None,
    }
