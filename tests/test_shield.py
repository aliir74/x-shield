"""Unit tests for spike detection and state management."""

import json
from datetime import UTC, datetime, timedelta

from src.shield import (
    SpikeReason,
    detect_spike,
    load_state,
    prune_history,
    save_state,
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
