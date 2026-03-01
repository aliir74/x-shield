"""Allow running as `python -m src`."""

import asyncio

from src.shield import main

if __name__ == "__main__":
    asyncio.run(main())
