#!/usr/bin/env python3
"""Download images for existing readable article snapshots incrementally."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from article_store import (
    FETCHED_IMAGE_REFS,
    ROOT,
    community_api_url,
    download_images,
    fetch_community_text,
    fetch_page_text,
    fetch_reader_text,
    snapshot_path,
    write_snapshot,
)
from news_store import load_news

NEWS_FILE = ROOT / "data" / "news.json"
READABLE_KINDS = {"community", "feed", "page", "reader"}


def media_backfill_item(item: dict) -> str:
    path = snapshot_path(item)
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return "missing"
    if snapshot.get("contentKind") not in READABLE_KINDS or snapshot.get("images"):
        return "skip"
    target_url = snapshot.get("resolvedUrl") or item.get("url")
    try:
        if community_api_url(target_url):
            _, resolved_url = fetch_community_text(target_url)
        elif snapshot.get("contentKind") == "reader":
            _, resolved_url = fetch_reader_text(target_url)
        else:
            _, resolved_url = fetch_page_text(target_url)
        image_refs = FETCHED_IMAGE_REFS.pop(resolved_url, [])
        images = download_images(item, image_refs)
        if not images:
            return "none"
        write_snapshot(
            item,
            snapshot.get("body", ""),
            snapshot["contentKind"],
            resolved_url=resolved_url,
            images=images,
        )
        return f"{len(images)} images"
    except Exception as error:
        return f"error:{type(error).__name__}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=int(os.getenv("ARTICLE_MEDIA_BACKFILL_LIMIT", "25")))
    parser.add_argument("--workers", type=int, default=int(os.getenv("ARTICLE_MEDIA_BACKFILL_WORKERS", "4")))
    args = parser.parse_args()
    data = load_news(NEWS_FILE)
    candidates = []
    for item in data.get("items", []):
        try:
            snapshot = json.loads(snapshot_path(item).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if snapshot.get("contentKind") in READABLE_KINDS and not snapshot.get("images"):
            candidates.append(item)
    candidates = candidates[:max(0, args.limit)]
    if not candidates:
        print("[media] no snapshots need image backfill")
        return 0
    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(candidates)))) as pool:
        futures = {pool.submit(media_backfill_item, item): item for item in candidates}
        for future in as_completed(futures):
            item = futures[future]
            result = future.result()
            counts[result] = counts.get(result, 0) + 1
            print(f"[media:{result}] {item['id']} {item.get('title', '')[:80]}")
    print(f"[media] processed {len(candidates)} snapshots: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
