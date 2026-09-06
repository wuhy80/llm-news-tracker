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
    utc_now,
    write_snapshot,
)
from news_store import load_news

NEWS_FILE = ROOT / "data" / "news.json"
READABLE_KINDS = {"community", "feed", "page", "reader"}


def media_backfill_item(item: dict, recheck: bool = False) -> str:
    path = snapshot_path(item)
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return "missing"
    if snapshot.get("contentKind") not in READABLE_KINDS:
        return "skip"
    if not recheck and (snapshot.get("images") or snapshot.get("mediaCheckedAt")):
        return "skip"
    if recheck and snapshot.get("mediaRecheckedAt"):
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
            if not image_refs:
                existing_images = snapshot.get("images") or []
                migrated_images = [
                    {**image, "src": image.get("originalUrl", image.get("src", ""))}
                    for image in existing_images
                    if isinstance(image, dict) and (image.get("originalUrl") or image.get("src"))
                ]
                write_snapshot(
                    item,
                    snapshot.get("body", ""),
                    snapshot["contentKind"],
                    resolved_url=resolved_url,
                    images=migrated_images or None,
                    media_checked_at=utc_now(),
                    media_rechecked_at=utc_now() if recheck else None,
                )
            return "none"
        write_snapshot(
            item,
            snapshot.get("body", ""),
            snapshot["contentKind"],
            resolved_url=resolved_url,
            images=images,
            media_checked_at=utc_now(),
            media_rechecked_at=utc_now() if recheck else None,
        )
        return f"{len(images)} images"
    except Exception as error:
        if recheck:
            existing_images = snapshot.get("images") or []
            migrated_images = [
                {**image, "src": image.get("originalUrl", image.get("src", ""))}
                for image in existing_images
                if isinstance(image, dict) and image.get("originalUrl")
            ]
            if migrated_images:
                write_snapshot(
                    item,
                    snapshot.get("body", ""),
                    snapshot["contentKind"],
                    resolved_url=target_url,
                    images=migrated_images,
                    media_rechecked_at=utc_now(),
                )
                return f"{len(migrated_images)} migrated after {type(error).__name__}"
        return f"error:{type(error).__name__}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=int(os.getenv("ARTICLE_MEDIA_BACKFILL_LIMIT", "25")))
    parser.add_argument("--workers", type=int, default=int(os.getenv("ARTICLE_MEDIA_BACKFILL_WORKERS", "4")))
    parser.add_argument("--year", type=int, default=int(os.getenv("ARTICLE_MEDIA_BACKFILL_YEAR", "0")))
    parser.add_argument("--recheck", action="store_true")
    args = parser.parse_args()
    data = load_news(NEWS_FILE)
    candidates = []
    for item in data.get("items", []):
        try:
            snapshot = json.loads(snapshot_path(item).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        published_year = str(item.get("publishedAt", ""))[:4]
        in_year = not args.year or published_year == str(args.year)
        needs_media = (
            snapshot.get("contentKind") in READABLE_KINDS
            and in_year
            and not snapshot.get("mediaRecheckedAt")
            if args.recheck
            else snapshot.get("contentKind") in READABLE_KINDS and in_year and not snapshot.get("images") and not snapshot.get("mediaCheckedAt")
        )
        if needs_media:
            candidates.append(item)
    candidates = candidates[:max(0, args.limit)]
    if not candidates:
        print("[media] no snapshots need image backfill")
        return 0
    counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(candidates)))) as pool:
        futures = {pool.submit(media_backfill_item, item, args.recheck): item for item in candidates}
        for future in as_completed(futures):
            item = futures[future]
            result = future.result()
            counts[result] = counts.get(result, 0) + 1
            print(f"[media:{result}] {item['id']} {item.get('title', '')[:80]}")
    print(f"[media] processed {len(candidates)} snapshots: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
