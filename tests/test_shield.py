"""Unit tests for spike detection and state management."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.shield import (
    SpikeReason,
    detect_spike,
    get_metrics,
    load_state,
    main,
    notify,
    parse_args,
    prune_history,
    save_state,
    set_protected,
)


class TestDetectSpike:
    def test_adaptive_spike(self, state_with_history):
        """Spike at 3x+ rolling average triggers adaptive detection."""
        # Average delta is 10. Current delta of 35 is 3.5x → spike.
        current = {"followers": 1065, "notifications": 10}
        result = detect_spike(current, state_with_history)
        assert result == SpikeReason.ADAPTIVE

    def test_static_floor_spike(self, state_with_history):
        """Spike at 100+ followers triggers static floor detection."""
        # Make avg_delta high enough that adaptive doesn't trigger,
        # but current delta >= 100.
        state = {
            "history": [
                {"timestamp": "2026-03-01T10:00:00+00:00", "followers": 1000, "notifications": 5},
                {"timestamp": "2026-03-01T10:05:00+00:00", "followers": 1050, "notifications": 8},
                {"timestamp": "2026-03-01T10:10:00+00:00", "followers": 1100, "notifications": 6},
            ],
            "is_protected": False,
            "last_spike_at": None,
        }
        # avg_delta = 50, current_delta = 100. 100 > 50*3=150? No. But 100 >= 100 static floor.
        current = {"followers": 1200, "notifications": 10}
        result = detect_spike(current, state)
        assert result == SpikeReason.STATIC_FLOOR

    def test_both_spike(self, state_with_history):
        """Spike triggers both adaptive and static floor."""
        # avg_delta = 10, current_delta = 150. 150 > 10*3=30 → adaptive. 150 >= 100 → static.
        current = {"followers": 1180, "notifications": 10}
        result = detect_spike(current, state_with_history)
        assert result == SpikeReason.BOTH

    def test_no_spike(self, state_with_history):
        """Normal growth does not trigger spike detection."""
        # avg_delta = 10, current_delta = 5. Neither condition met.
        current = {"followers": 1035, "notifications": 5}
        result = detect_spike(current, state_with_history)
        assert result is None

    def test_first_run_empty_history(self, default_state):
        """Empty history returns None (first run)."""
        current = {"followers": 1000, "notifications": 0}
        result = detect_spike(current, default_state)
        assert result is None

    def test_insufficient_data_single_entry(self):
        """Single history entry returns None (not enough data)."""
        state = {
            "history": [
                {"timestamp": "2026-03-01T10:00:00+00:00", "followers": 1000, "notifications": 5},
            ],
        }
        current = {"followers": 1500, "notifications": 10}
        result = detect_spike(current, state)
        assert result is None

    def test_negative_delta_no_spike(self, state_with_history):
        """Losing followers does not trigger spike."""
        current = {"followers": 1020, "notifications": 5}
        result = detect_spike(current, state_with_history)
        assert result is None

    def test_zero_avg_delta_no_adaptive(self):
        """When average delta is 0, adaptive detection is skipped."""
        state = {
            "history": [
                {"timestamp": "2026-03-01T10:00:00+00:00", "followers": 1000, "notifications": 5},
                {"timestamp": "2026-03-01T10:05:00+00:00", "followers": 1000, "notifications": 5},
                {"timestamp": "2026-03-01T10:10:00+00:00", "followers": 1000, "notifications": 5},
            ],
        }
        # avg_delta = 0, current_delta = 50. Adaptive requires avg > 0, so only static check.
        current = {"followers": 1050, "notifications": 5}
        result = detect_spike(current, state)
        assert result is None  # 50 < 100 static floor


class TestStateManagement:
    def test_load_state_missing_file(self, tmp_path):
        """Missing state file returns default state."""
        result = load_state(tmp_path / "nonexistent.json")
        assert result == {"history": [], "is_protected": False, "last_spike_at": None}

    def test_save_and_reload(self, tmp_path):
        """Round-trip serialization works correctly."""
        state_path = tmp_path / "state.json"
        state = {
            "history": [
                {"timestamp": "2026-03-01T10:00:00+00:00", "followers": 1000, "notifications": 5},
            ],
            "is_protected": True,
            "last_spike_at": "2026-03-01T10:00:00+00:00",
        }
        save_state(state_path, state)
        loaded = load_state(state_path)
        assert loaded == state

    def test_save_creates_valid_json(self, tmp_path):
        """Saved state is valid JSON."""
        state_path = tmp_path / "state.json"
        save_state(state_path, {"history": [], "is_protected": False, "last_spike_at": None})
        with open(state_path) as f:
            data = json.load(f)
        assert "history" in data


class TestPruneHistory:
    def test_prune_old_entries(self):
        """Entries older than 24h are removed."""
        now = datetime.now(UTC)
        old = (now - timedelta(hours=25)).isoformat()
        recent = (now - timedelta(hours=1)).isoformat()

        history = [
            {"timestamp": old, "followers": 1000, "notifications": 5},
            {"timestamp": recent, "followers": 1010, "notifications": 6},
        ]
        result = prune_history(history)
        assert len(result) == 1
        assert result[0]["timestamp"] == recent

    def test_prune_keeps_recent(self):
        """All recent entries are preserved."""
        now = datetime.now(UTC)
        entries = [
            {"timestamp": (now - timedelta(hours=i)).isoformat(), "followers": 1000 + i, "notifications": 5}
            for i in range(5)
        ]
        result = prune_history(entries)
        assert len(result) == 5

    def test_prune_empty_history(self):
        """Empty history returns empty list."""
        result = prune_history([])
        assert result == []


class TestGetMetrics:
    async def test_get_metrics_success(self, mock_client):
        """Returns follower count and notification count."""
        result = await get_metrics(mock_client)
        assert result == {"followers": 1500, "notifications": 3}
        mock_client.user.assert_awaited_once()
        mock_client.get_notifications.assert_awaited_once_with("All", count=40)

    async def test_get_metrics_notification_failure(self, mock_client):
        """Falls back to 0 notifications when fetch fails."""
        mock_client.get_notifications = AsyncMock(side_effect=Exception("API error"))
        result = await get_metrics(mock_client)
        assert result == {"followers": 1500, "notifications": 0}


class TestSetProtected:
    async def test_set_protected_calls_api(self, mock_client):
        """Calls Twitter v1.1 API with correct URL and data."""
        await set_protected(mock_client)
        mock_client.post.assert_awaited_once_with(
            "https://api.x.com/1.1/account/settings.json",
            data={"protected": "true"},
            headers={
                "Authorization": "Bearer test",
                "content-type": "application/x-www-form-urlencoded",
            },
        )


class TestNotify:
    async def test_notify_sends_request(self):
        """Sends POST to ntfy.sh with correct headers."""
        mock_post = AsyncMock()
        mock_http = MagicMock()
        mock_http.post = mock_post
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("src.shield.httpx.AsyncClient", return_value=mock_http):
            await notify("test_topic", "test message", title="Test Title")

        mock_post.assert_awaited_once_with(
            "https://ntfy.sh/test_topic",
            content="test message",
            headers={"Title": "Test Title", "Priority": "high", "Tags": "shield"},
        )


class TestMain:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, env_vars):
        """Patch STATE_FILE to use temp directory."""
        self.state_path = tmp_path / "state.json"
        self._state_patch = patch("src.shield.STATE_FILE", self.state_path)
        self._state_patch.start()
        yield
        self._state_patch.stop()

    async def test_main_no_spike(self, mock_client):
        """Normal run: no spike, state saved."""
        with (
            patch("src.shield.Client", return_value=mock_client),
            patch("src.shield.get_metrics", new_callable=AsyncMock) as mock_get,
            patch("src.shield.notify", new_callable=AsyncMock) as mock_notify,
        ):
            mock_get.return_value = {"followers": 1005, "notifications": 2}
            await main([])

        mock_notify.assert_not_awaited()
        state = json.loads(self.state_path.read_text())
        assert len(state["history"]) == 1
        assert state["history"][0]["followers"] == 1005

    async def test_main_spike_sets_protected(self, mock_client):
        """Spike detected: account set to protected, notification sent."""
        # Seed state with history that will trigger a spike
        initial_state = {
            "history": [
                {"timestamp": "2026-03-01T10:00:00+00:00", "followers": 1000, "notifications": 5},
                {"timestamp": "2026-03-01T10:05:00+00:00", "followers": 1010, "notifications": 8},
                {"timestamp": "2026-03-01T10:10:00+00:00", "followers": 1020, "notifications": 6},
            ],
            "is_protected": False,
            "last_spike_at": None,
        }
        self.state_path.write_text(json.dumps(initial_state))

        with (
            patch("src.shield.Client", return_value=mock_client),
            patch("src.shield.get_metrics", new_callable=AsyncMock) as mock_get,
            patch("src.shield.set_protected", new_callable=AsyncMock) as mock_protect,
            patch("src.shield.notify", new_callable=AsyncMock) as mock_notify,
        ):
            mock_get.return_value = {"followers": 1200, "notifications": 10}
            await main([])

        mock_protect.assert_awaited_once()
        mock_notify.assert_awaited_once()
        state = json.loads(self.state_path.read_text())
        assert state["is_protected"] is True
        assert state["last_spike_at"] is not None

    async def test_main_spike_already_protected(self, mock_client):
        """Spike detected but already protected: no action taken."""
        initial_state = {
            "history": [
                {"timestamp": "2026-03-01T10:00:00+00:00", "followers": 1000, "notifications": 5},
                {"timestamp": "2026-03-01T10:05:00+00:00", "followers": 1010, "notifications": 8},
                {"timestamp": "2026-03-01T10:10:00+00:00", "followers": 1020, "notifications": 6},
            ],
            "is_protected": True,
            "last_spike_at": "2026-03-01T09:00:00+00:00",
        }
        self.state_path.write_text(json.dumps(initial_state))

        with (
            patch("src.shield.Client", return_value=mock_client),
            patch("src.shield.get_metrics", new_callable=AsyncMock) as mock_get,
            patch("src.shield.set_protected", new_callable=AsyncMock) as mock_protect,
            patch("src.shield.notify", new_callable=AsyncMock) as mock_notify,
        ):
            mock_get.return_value = {"followers": 1200, "notifications": 10}
            await main([])

        mock_protect.assert_not_awaited()
        mock_notify.assert_not_awaited()

    async def test_main_metrics_failure_notifies(self, mock_client):
        """Metrics fetch failure sends error notification."""
        with (
            patch("src.shield.Client", return_value=mock_client),
            patch("src.shield.get_metrics", new_callable=AsyncMock) as mock_get,
            patch("src.shield.notify", new_callable=AsyncMock) as mock_notify,
        ):
            mock_get.side_effect = Exception("connection error")
            await main([])

        mock_notify.assert_awaited_once()
        call_args = mock_notify.call_args
        assert "Error" in call_args.args[1]

    async def test_main_missing_env_exits(self, monkeypatch):
        """Missing CT0/AUTH_TOKEN causes sys.exit(1)."""
        monkeypatch.delenv("CT0", raising=False)
        monkeypatch.delenv("AUTH_TOKEN", raising=False)
        with pytest.raises(SystemExit, match="1"):
            await main([])

    async def test_main_set_protected_failure(self, mock_client):
        """set_protected failure: state not updated to protected."""
        initial_state = {
            "history": [
                {"timestamp": "2026-03-01T10:00:00+00:00", "followers": 1000, "notifications": 5},
                {"timestamp": "2026-03-01T10:05:00+00:00", "followers": 1010, "notifications": 8},
                {"timestamp": "2026-03-01T10:10:00+00:00", "followers": 1020, "notifications": 6},
            ],
            "is_protected": False,
            "last_spike_at": None,
        }
        self.state_path.write_text(json.dumps(initial_state))

        with (
            patch("src.shield.Client", return_value=mock_client),
            patch("src.shield.get_metrics", new_callable=AsyncMock) as mock_get,
            patch("src.shield.set_protected", new_callable=AsyncMock) as mock_protect,
            patch("src.shield.notify", new_callable=AsyncMock) as mock_notify,
        ):
            mock_get.return_value = {"followers": 1200, "notifications": 10}
            mock_protect.side_effect = Exception("API error")
            await main([])

        # Notification still sent despite protect failure
        mock_notify.assert_awaited_once()
        state = json.loads(self.state_path.read_text())
        assert state["is_protected"] is False


class TestParseArgs:
    def test_default_no_test(self):
        """Default args: --test is False."""
        args = parse_args([])
        assert args.test is False

    def test_test_flag(self):
        """--test flag sets test to True."""
        args = parse_args(["--test"])
        assert args.test is True


class TestTestMode:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, env_vars):
        """Patch STATE_FILE to use temp directory."""
        self.state_path = tmp_path / "state.json"
        self._state_patch = patch("src.shield.STATE_FILE", self.state_path)
        self._state_patch.start()
        yield
        self._state_patch.stop()

    async def test_test_mode_calls_protect_and_notify(self, mock_client):
        """--test triggers set_protected and notify, then exits without spike detection."""
        with (
            patch("src.shield.Client", return_value=mock_client),
            patch("src.shield.set_protected", new_callable=AsyncMock) as mock_protect,
            patch("src.shield.notify", new_callable=AsyncMock) as mock_notify,
            patch("src.shield.get_metrics", new_callable=AsyncMock) as mock_get,
        ):
            await main(["--test"])

        mock_protect.assert_awaited_once_with(mock_client)
        mock_notify.assert_awaited_once()
        assert "Test" in mock_notify.call_args.kwargs.get("title", mock_notify.call_args.args[1])
        # Spike detection should NOT run
        mock_get.assert_not_awaited()
        # State file should NOT be created
        assert not self.state_path.exists()

    async def test_test_mode_no_ntfy_topic(self, mock_client, monkeypatch):
        """--test without NTFY_TOPIC: protect runs, notify skipped."""
        monkeypatch.delenv("NTFY_TOPIC", raising=False)
        with (
            patch("src.shield.Client", return_value=mock_client),
            patch("src.shield.set_protected", new_callable=AsyncMock) as mock_protect,
            patch("src.shield.notify", new_callable=AsyncMock) as mock_notify,
        ):
            await main(["--test"])

        mock_protect.assert_awaited_once()
        mock_notify.assert_not_awaited()
