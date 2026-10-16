from __future__ import annotations

import asyncio

from portbridge.bridge_server import main as bridge_main


def main() -> None:
    asyncio.run(bridge_main())


if __name__ == "__main__":
    main()
