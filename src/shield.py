"""Auto-private Twitter/X account on viral detection."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from statistics import mean

import httpx
from dotenv import load_dotenv
from twikit import Client

SPIKE_MULTIPLIER = 3.0
STATIC_FLOOR = 100
CHECK_WINDOW_HOURS = 24
STATE_FILE = Path(__file__).parent.parent / "state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


class SpikeReason(Enum):
    ADAPTIVE = "adaptive"
    STATIC_FLOOR = "static_floor"
    BOTH = "both"


def load_state(path: Path) -> dict:
    """Load state from JSON file, returning default state if missing."""
    if not path.exists():
        return {"history": [], "is_protected": False, "last_spike_at": None}
    with open(path) as f:
        return json.load(f)


def save_state(path: Path, state: dict) -> None:
    """Save state to JSON file."""
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def prune_history(history: list[dict], window_hours: int = CHECK_WINDOW_HOURS) -> list[dict]:
    """Remove history entries older than the check window."""
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    return [
        entry
        for entry in history
        if datetime.fromisoformat(entry["timestamp"]) >= cutoff
    ]


def detect_spike(current: dict, state: dict) -> SpikeReason | None:
    """Detect follower spike using adaptive and static floor methods.

    Returns the reason for the spike, or None if no spike detected.
    """
    history = state.get("history", [])
    if len(history) < 2:
        return None

    deltas = [
        history[i]["followers"] - history[i - 1]["followers"]
        for i in range(1, len(history))
    ]
    avg_delta = mean(deltas) if deltas else 0
    current_delta = current["followers"] - history[-1]["followers"]

    adaptive = current_delta > avg_delta * SPIKE_MULTIPLIER and avg_delta > 0
    static = current_delta >= STATIC_FLOOR

    if adaptive and static:
        return SpikeReason.BOTH
    if adaptive:
        return SpikeReason.ADAPTIVE
    if static:
        return SpikeReason.STATIC_FLOOR
    return None


async def get_metrics(client: Client) -> dict:
    """Fetch current follower count and notification count."""
    user = await client.user()
    followers = user.followers_count

    try:
        notifications = await client.get_notifications("All", count=40)
        notification_count = len(list(notifications))
    except Exception:
        log.warning("failed to fetch notifications, defaulting to 0")
        notification_count = 0

    return {"followers": followers, "notifications": notification_count}


async def set_protected(client: Client) -> None:
    """Set account to protected/private mode via Twitter v1.1 API."""
    headers = client._base_headers  # noqa: SLF001
    headers["content-type"] = "application/x-www-form-urlencoded"
    _, response = await client.post(
        "https://api.x.com/1.1/account/settings.json",
        data={"protected": "true"},
        headers=headers,
    )
    log.info("set_protected response status: %s", response.status_code)


async def notify(topic: str, message: str, title: str = "X Shield Alert") -> None:
    """Send push notification via ntfy.sh."""
    async with httpx.AsyncClient() as http:
        await http.post(
            f"https://ntfy.sh/{topic}",
            content=message,
            headers={"Title": title, "Priority": "high", "Tags": "shield"},
        )


async def main() -> None:
    """Main loop: fetch metrics, detect spikes, protect if needed."""
    load_dotenv()

    ct0 = os.environ.get("CT0")
    auth_token = os.environ.get("AUTH_TOKEN")
    ntfy_topic = os.environ.get("NTFY_TOPIC")

    if not ct0 or not auth_token:
        log.error("CT0 and AUTH_TOKEN must be set in .env")
        sys.exit(1)

    client = Client()
    client.set_cookies({"ct0": ct0, "auth_token": auth_token})

    state = load_state(STATE_FILE)

    try:
        current = await get_metrics(client)
    except Exception as exc:
        msg = f"failed to fetch metrics: {exc}"
        log.error(msg)
        if ntfy_topic:
            await notify(ntfy_topic, f"Error: {msg}. Check cookies.", title="X Shield Error")
        return

    log.info(
        "current metrics — followers: %d, notifications: %d",
        current["followers"],
        current["notifications"],
    )

    spike_reason = detect_spike(current, state)

    if spike_reason and not state.get("is_protected", False):
        log.warning("spike detected: %s", spike_reason.value)

        try:
            await set_protected(client)
            state["is_protected"] = True
            state["last_spike_at"] = datetime.now(UTC).isoformat()
            log.info("account set to protected mode")
        except Exception as exc:
            log.error("failed to set protected: %s", exc)

        if ntfy_topic:
            delta = current["followers"] - state["history"][-1]["followers"] if state["history"] else 0
            await notify(
                ntfy_topic,
                f"Spike: {spike_reason.value}\n"
                f"Follower delta: +{delta}\n"
                f"Total followers: {current['followers']}\n"
                f"Account is now PRIVATE.",
            )
    elif spike_reason:
        log.info("spike detected but account already protected")
    else:
        log.info("no spike detected")

    state["history"].append({
        "timestamp": datetime.now(UTC).isoformat(),
        "followers": current["followers"],
        "notifications": current["notifications"],
    })
    state["history"] = prune_history(state["history"])

    save_state(STATE_FILE, state)
    log.info("state saved")


if __name__ == "__main__":
    asyncio.run(main())
