"""Shared test fixtures."""

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

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


@pytest.fixture()
def mock_client():
    """Return a mock twikit Client with AsyncMock methods."""
    client = MagicMock()
    user = MagicMock()
    user.followers_count = 1500
    client.user = AsyncMock(return_value=user)
    client.get_notifications = AsyncMock(return_value=[MagicMock(), MagicMock(), MagicMock()])
    client.post = AsyncMock(return_value=(None, MagicMock(status_code=200)))
    type(client)._base_headers = PropertyMock(return_value={"Authorization": "Bearer test"})
    return client


@pytest.fixture()
def env_vars(monkeypatch):
    """Set required environment variables for main() and prevent .env override."""
    monkeypatch.setenv("CT0", "test_ct0")
    monkeypatch.setenv("AUTH_TOKEN", "test_auth_token")
    monkeypatch.setenv("NTFY_TOPIC", "test_topic")
    with patch("src.shield.load_dotenv"):
        yield
