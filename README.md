# X Shield

Auto-private your Twitter/X account when viral activity is detected. Monitors follower spikes and mention surges, then automatically switches the account to protected mode.

## How it works

Runs on a schedule via macOS launchd (every 15 minutes). Each run:

1. Fetches current follower count and notifications via twikit
2. Compares against a rolling 24-hour baseline
3. Detects spikes using two methods:
   - **Adaptive:** current delta > 3x rolling average delta
   - **Static floor:** current delta >= 100 followers
4. If spike detected → sets account to private and sends a push notification via ntfy.sh
5. Account stays private until you manually change it back

## Setup

### 1. Install dependencies

```bash
uv sync --all-extras
```

### 2. Get your Twitter cookies

1. Open [x.com](https://x.com) in your browser and log in
2. Open DevTools → Application → Cookies → `https://x.com`
3. Copy the values for `ct0` and `auth_token`

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` with your values:

```
CT0=your_ct0_cookie_value
AUTH_TOKEN=your_auth_token_cookie_value
SCREEN_NAME=your_twitter_username
NTFY_TOPIC=your_unique_topic_name
```

### 4. Set up ntfy.sh notifications

1. Install the [ntfy app](https://ntfy.sh/) on your phone
2. Subscribe to your chosen topic name (same as `NTFY_TOPIC` in `.env`)

### 5. Test run

```bash
uv run python -m src.shield
```

This will fetch your current metrics and write the initial `state.json`. No spike detection on first two runs (needs baseline data).

### 6. Set up launchd (macOS)

Copy the provided plist to your LaunchAgents directory:

```bash
cp com.aliir74.x-shield.plist ~/Library/LaunchAgents/
```

Edit the plist to update paths if needed, then load it:

```bash
launchctl load ~/Library/LaunchAgents/com.aliir74.x-shield.plist
```

Verify it's running:

```bash
launchctl list | grep x-shield
```

To stop:

```bash
launchctl unload ~/Library/LaunchAgents/com.aliir74.x-shield.plist
```

Logs are written to `cron.log` in the project directory.

## Manual operations

### Re-enable public mode

X Shield does not auto-revert to public. To go public again:

1. Go to [x.com/settings/audience_and_tagging](https://x.com/settings/audience_and_tagging)
2. Uncheck "Protect your posts"
3. Update `state.json` and set `"is_protected": false`

### Force trigger (for testing)

```bash
uv run python -m src.shield --test
```

This triggers protect + notify immediately without needing spike data.

## Running tests

```bash
uv run pytest
```

With coverage:

```bash
uv run pytest --cov=src --cov-report=term-missing
```

## Configuration

Constants in `src/shield.py`:

| Constant | Default | Description |
|---|---|---|
| `SPIKE_MULTIPLIER` | 3.0 | Adaptive threshold multiplier |
| `STATIC_FLOOR` | 100 | Minimum follower delta to trigger |
| `CHECK_WINDOW_HOURS` | 24 | Rolling window for baseline calculation |
