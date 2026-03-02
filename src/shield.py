"""Auto-private Twitter/X account on viral detection."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from statistics import mean

import httpx
from dotenv import load_dotenv
from twikit import Client

SPIKE_MULTIPLIER = 3.0
STATIC_FLOOR = 100
ENGAGEMENT_STATIC_FLOOR = 50
RECENT_TWEETS_COUNT = 20
CHECK_WINDOW_HOURS = 24
STATE_FILE = Path(__file__).parent.parent / "state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


class SpikeType(Enum):
    ADAPTIVE = "adaptive"
    STATIC_FLOOR = "static_floor"
    BOTH = "both"


@dataclass
class SpikeResult:
    """Result of spike detection across multiple signals."""

    followers: SpikeType | None = None
    engagement: SpikeType | None = None

    @property
    def is_spike(self) -> bool:
        return self.followers is not None or self.engagement is not None

    def __str__(self) -> str:
        parts = []
        if self.followers:
            parts.append(f"followers:{self.followers.value}")
        if self.engagement:
            parts.append(f"engagement:{self.engagement.value}")
        return ", ".join(parts)


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


def _check_signal(
    current_value: int,
    history: list[dict],
    key: str,
    static_floor: int,
) -> SpikeType | None:
    """Check a single metric for spikes using adaptive + static floor."""
    deltas = [
        history[i].get(key, 0) - history[i - 1].get(key, 0)
        for i in range(1, len(history))
    ]
    avg_delta = mean(deltas) if deltas else 0
    current_delta = current_value - history[-1].get(key, 0)

    adaptive = current_delta > avg_delta * SPIKE_MULTIPLIER and avg_delta > 0
    static = current_delta >= static_floor

    if adaptive and static:
        return SpikeType.BOTH
    if adaptive:
        return SpikeType.ADAPTIVE
    if static:
        return SpikeType.STATIC_FLOOR
    return None


def detect_spike(current: dict, state: dict) -> SpikeResult | None:
    """Detect spikes in followers and engagement.

    Returns a SpikeResult if any spike is detected, or None.
    """
    history = state.get("history", [])
    if len(history) < 2:
        return None

    followers_spike = _check_signal(
        current["followers"], history, "followers", STATIC_FLOOR
    )
    engagement_spike = _check_signal(
        current.get("engagement", 0), history, "engagement", ENGAGEMENT_STATIC_FLOOR
    )

    result = SpikeResult(followers=followers_spike, engagement=engagement_spike)
    return result if result.is_spike else None


async def get_metrics(client: Client, screen_name: str) -> dict:
    """Fetch current follower count, notification count, and tweet engagement."""
    user = await client.get_user_by_screen_name(screen_name)
    followers = user.followers_count

    try:
        notifications = await client.get_notifications("All", count=40)
        notification_count = len(list(notifications))
    except Exception:
        log.warning("failed to fetch notifications, defaulting to 0")
        notification_count = 0

    try:
        tweets = await client.get_user_tweets(user.id, "Tweets", count=RECENT_TWEETS_COUNT)
        engagement = sum(
            (tweet.reply_count or 0) + (tweet.quote_count or 0)
            for tweet in tweets
        )
    except Exception:
        log.warning("failed to fetch tweet engagement, defaulting to 0")
        engagement = 0

    return {"followers": followers, "notifications": notification_count, "engagement": engagement}


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="X Shield: auto-private on viral detection")
    parser.add_argument(
        "--test",
        action="store_true",
        help="test mode: trigger protect + notify immediately, then exit",
    )
    return parser.parse_args(argv)


async def main(argv: list[str] | None = None) -> None:
    """Main loop: fetch metrics, detect spikes, protect if needed."""
    args = parse_args(argv)
    load_dotenv()

    ct0 = os.environ.get("CT0")
    auth_token = os.environ.get("AUTH_TOKEN")
    ntfy_topic = os.environ.get("NTFY_TOPIC")
    screen_name = os.environ.get("SCREEN_NAME")

    if not ct0 or not auth_token or not screen_name:
        log.error("CT0, AUTH_TOKEN, and SCREEN_NAME must be set in .env")
        sys.exit(1)

    client = Client()
    client.set_cookies({"ct0": ct0, "auth_token": auth_token})

    if args.test:
        log.info("test mode: triggering protect + notify")
        await set_protected(client)
        log.info("account set to protected mode")
        if ntfy_topic:
            await notify(ntfy_topic, "Test: protect + notify triggered manually.", title="X Shield Test")
            log.info("test notification sent to %s", ntfy_topic)
        else:
            log.warning("NTFY_TOPIC not set, skipping notification")
        return

    state = load_state(STATE_FILE)

    try:
        current = await get_metrics(client, screen_name)
    except Exception as exc:
        msg = f"failed to fetch metrics: {exc}"
        log.error(msg)
        if ntfy_topic:
            await notify(ntfy_topic, f"Error: {msg}. Check cookies.", title="X Shield Error")
        return

    log.info(
        "current metrics — followers: %d, notifications: %d, engagement: %d",
        current["followers"],
        current["notifications"],
        current["engagement"],
    )

    spike_result = detect_spike(current, state)

    if spike_result and not state.get("is_protected", False):
        log.warning("spike detected: %s", spike_result)

        try:
            await set_protected(client)
            state["is_protected"] = True
            state["last_spike_at"] = datetime.now(UTC).isoformat()
            log.info("account set to protected mode")
        except Exception as exc:
            log.error("failed to set protected: %s", exc)

        if ntfy_topic:
            follower_delta = (
                current["followers"] - state["history"][-1]["followers"]
                if state["history"]
                else 0
            )
            engagement_delta = (
                current["engagement"] - state["history"][-1].get("engagement", 0)
                if state["history"]
                else 0
            )
            await notify(
                ntfy_topic,
                f"Spike: {spike_result}\n"
                f"Follower delta: +{follower_delta}\n"
                f"Engagement delta: +{engagement_delta}\n"
                f"Total followers: {current['followers']}\n"
                f"Account is now PRIVATE.",
            )
    elif spike_result:
        log.info("spike detected but account already protected")
    else:
        log.info("no spike detected")

    state["history"].append({
        "timestamp": datetime.now(UTC).isoformat(),
        "followers": current["followers"],
        "notifications": current["notifications"],
        "engagement": current["engagement"],
    })
    state["history"] = prune_history(state["history"])

    save_state(STATE_FILE, state)
    log.info("state saved")


if __name__ == "__main__":
    asyncio.run(main())
