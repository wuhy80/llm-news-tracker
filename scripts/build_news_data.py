#!/usr/bin/env python3
"""Build sharded indexes and synchronize self-contained article records."""

from news_store import load_news, save_news


def main() -> int:
    result = save_news(load_news())
    print(
        f"[data] synchronized {result['items']} articles across {result['days']} daily indexes "
        f"and {result['prefixes']} locator shards"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
