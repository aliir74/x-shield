# X Shield

Auto-private Twitter/X account on viral detection using twikit.

## Project structure

```
src/shield.py                   — Main script: spike detection, auto-private, notifications
tests/                          — Unit tests for pure logic (no API mocking)
state.json                      — Runtime state (gitignored, auto-created)
com.aliir74.x-shield.plist      — macOS launchd plist for scheduling
```

## Commands

```bash
uv sync --all-extras        # Install all dependencies
uv run ruff check src tests # Lint
uv run pyright src           # Type check
uv run pytest                # Run tests
uv run pytest --cov=src --cov-report=term-missing  # Tests with coverage
uv run python -m src.shield  # Run the script
```

## Conventions

- Python 3.11+, async-first (twikit is async)
- All config in `pyproject.toml` — ruff, pyright, pytest
- Ruff rules: E, W, F, I, B, C4, UP, PLC0415. Line length 100
- Pyright in basic mode
- Use `datetime.UTC` alias (not `timezone.utc`) per UP017
- Use double quotes for strings
- All imports at the top of the file
- Dependencies pinned to latest stable versions
- Commit titles in lowercase

## CI

GitHub Actions workflow (`.github/workflows/ci.yml`) runs 3 jobs on PRs to main:
- **Lint** — ruff
- **Type Check** — pyright
- **Test** — pytest with coverage

Branch protection requires all 3 to pass before merge.

## Scheduling

Runs via macOS launchd (not cron). Plist file: `com.aliir74.x-shield.plist`

```bash
launchctl load ~/Library/LaunchAgents/com.aliir74.x-shield.plist    # start
launchctl unload ~/Library/LaunchAgents/com.aliir74.x-shield.plist  # stop
launchctl list | grep x-shield                                       # check status
```

- Runs every 15 minutes (900s interval)
- Logs to `cron.log` in project root (gitignored)

## Key technical details

- twikit `Client.post()` returns `tuple[dict | Any, Response]` — always unpack with `_, response`
- `client._base_headers` provides authenticated headers (Bearer token + CSRF)
- Protected mode toggle: POST to `https://api.x.com/1.1/account/settings.json` with `protected=true`
- Spike detection: multi-signal (followers + engagement). Each signal: adaptive (3x rolling 24h avg) + static floor (followers: 100+, engagement: 50+)
- Notifications via ntfy.sh HTTP POST
