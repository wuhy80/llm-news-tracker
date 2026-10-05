#!/usr/bin/env python3
"""Incrementally backfill internal article snapshots."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from article_store import (
    ARTICLES_DIR,
    ROOT,
    archive_item,
    resolve_google_news_urls,
    snapshot_kind,
    snapshot_path,
)
from news_store import load_news, save_news

NEWS_FILE = ROOT / "data" / "news.json"


def needs_archive(item: dict, now: datetime) -> bool:
    path = snapshot_path(item)
    if not path.exists():
        return True
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    if snapshot.get("contentKind") != "summary":
        return False
    if snapshot.get("note") == "archive:not attempted":
        return True
    if snapshot.get("archiveVersion", 0) < 2:
        return True
    try:
        published = datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00"))
        attempted = datetime.fromisoformat(snapshot["fetchedAt"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False
    return published >= now - timedelta(days=30) and attempted <= now - timedelta(days=7)


def failed_fetch(path: Path) -> bool:
    """True when the last archive attempt recorded a fetch failure.

    openai.com answers 403 to a plain fetch, so every one of its articles needs
    the reader route. Ranking the reader budget by recency alone would leave that
    backlog untouched forever.
    """
    try:
        note = json.loads(path.read_text(encoding="utf-8")).get("note") or ""
    except (json.JSONDecodeError, OSError):
        return False
    return "HTTPError" in note or "URLError" in note


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=int(os.getenv("ARTICLE_FETCH_LIMIT", "300")))
    parser.add_argument("--workers", type=int, default=int(os.getenv("ARTICLE_FETCH_WORKERS", "12")))
    parser.add_argument("--reader-limit", type=int, default=int(os.getenv("ARTICLE_READER_LIMIT", "30")))
    args = parser.parse_args()
    data = load_news(NEWS_FILE)
    now = datetime.now(timezone.utc)
    candidates = [item for item in data.get("items", []) if needs_archive(item, now)][:max(0, args.limit)]
    if not candidates:
        print("[articles] no snapshots need backfilling")
        return 0

    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    resolved_urls = resolve_google_news_urls([item["url"] for item in candidates])
    resolved_count = sum(resolved_urls.get(item["url"]) != item["url"] for item in candidates)
    print(f"[articles] resolved {resolved_count} Google News links")
    # Give the reader budget to the items that actually need it, then fill the
    # rest by recency.
    reader_limit = max(0, args.reader_limit)
    failed = [item for item in candidates if failed_fetch(snapshot_path(item))]
    ordered = failed + [item for item in candidates if item not in failed]
    reader_ids = {
        item["id"] for item in ordered[:reader_limit]
    }
    counts = {"community": 0, "page": 0, "reader": 0, "summary": 0}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(candidates)))) as pool:
        futures = {
            pool.submit(
                archive_item,
                item,
                resolved_urls.get(item["url"]),
                item["id"] in reader_ids,
            ): item
            for item in candidates
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                _, kind = future.result()
                counts[kind] += 1
                if kind == "summary":
                    item.pop("articleKind", None)
                else:
                    item["articleKind"] = kind
                print(f"[article:{kind}] {item['id']} {item['title'][:80]}")
            except Exception as error:
                print(f"[article:warn] {item.get('id', '?')}: {error}")
    print(
        f"[articles] archived {len(candidates)} items: {counts['community']} community posts, "
        f"{counts['page']} pages, "
        f"{counts['reader']} reader copies, {counts['summary']} summaries"
    )
    for item in data.get("items", []):
        kind = snapshot_kind(item)
        if kind and kind != "summary":
            item["articleKind"] = kind
        else:
            item.pop("articleKind", None)
    save_news(data, NEWS_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
