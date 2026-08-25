#!/usr/bin/env python3
"""Incrementally backfill internal article snapshots."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from article_store import ARTICLES_DIR, ROOT, archive_item, snapshot_path

NEWS_FILE = ROOT / "data" / "news.json"


def needs_archive(item: dict, now: datetime) -> bool:
    path = snapshot_path(item.get("id", ""))
    if not path.exists():
        return True
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    if snapshot.get("contentKind") != "summary":
        return False
    try:
        published = datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00"))
        attempted = datetime.fromisoformat(snapshot["fetchedAt"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False
    return published >= now - timedelta(days=30) and attempted <= now - timedelta(days=7)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=int(os.getenv("ARTICLE_FETCH_LIMIT", "80")))
    parser.add_argument("--workers", type=int, default=int(os.getenv("ARTICLE_FETCH_WORKERS", "8")))
    args = parser.parse_args()
    data = json.loads(NEWS_FILE.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    candidates = [item for item in data.get("items", []) if needs_archive(item, now)][:max(0, args.limit)]
    if not candidates:
        print("[articles] no snapshots need backfilling")
        return 0

    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    counts = {"page": 0, "summary": 0}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(candidates)))) as pool:
        futures = {pool.submit(archive_item, item): item for item in candidates}
        for future in as_completed(futures):
            item = futures[future]
            try:
                _, kind = future.result()
                counts[kind] += 1
                print(f"[article:{kind}] {item['id']} {item['title'][:80]}")
            except Exception as error:
                print(f"[article:warn] {item.get('id', '?')}: {error}")
    print(f"[articles] archived {len(candidates)} items: {counts['page']} pages, {counts['summary']} summaries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
