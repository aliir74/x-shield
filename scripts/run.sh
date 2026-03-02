#!/bin/bash
# Wrapper script for launchd to run x-shield

set -e

cd /Users/aliirani/Downloads/Coding/x-shield

# Load environment variables from .env
set -a
source .env
set +a

exec /Users/aliirani/.local/bin/uv run python -u -m src.shield
