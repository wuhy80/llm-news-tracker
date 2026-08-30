#!/usr/bin/env python3
"""Progressively refresh legacy article snapshots with structured code blocks."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from article_store import (
    BODY_FORMAT_VERSION,
    MIN_BODY_CHARS,
    ROOT,
    community_api_url,
    error_note,
    fetch_community_text,
    fetch_page_text,
    fetch_reader_text,
    has_flattened_code,
    resolve_google_news_urls,
    snapshot_path,
    utc_now,
    write_snapshot,
)
from news_store import atomic_write_json, load_news, save_news

NEWS_FILE = ROOT / "data" / "news.json"
READABLE_KINDS = {"community", "feed", "page", "reader"}


def read_snapshot(item: dict) -> tuple[Path, dict] | None:
    path = snapshot_path(item)
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def likely_flattened_code(record: dict) -> bool:
    return has_flattened_code(record.get("body", ""))


def needs_format_refresh(item: dict, now: datetime, retry_days: int = 7) -> bool:
    loaded = read_snapshot(item)
    if not loaded:
        return False
    _, record = loaded
    if record.get("contentKind") not in READABLE_KINDS:
        return False
    if record.get("bodyFormatVersion", 0) >= BODY_FORMAT_VERSION:
        return False
    attempted_at = record.get("formatRefreshAttemptedAt")
    if not attempted_at:
        return True
    try:
        attempted = datetime.fromisoformat(attempted_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return True
    return attempted <= now - timedelta(days=max(1, retry_days))


def acceptable_refresh(previous: dict, body: str) -> bool:
    previous_body = previous.get("body", "")
    if len(body) < max(MIN_BODY_CHARS, int(len(previous_body) * 0.55)):
        return False
    if likely_flattened_code(previous):
        if "```" not in body or has_flattened_code(body):
            return False
    return True


def mark_failure(path: Path, record: dict, errors: list[str]) -> None:
    record["formatRefreshAttemptedAt"] = utc_now()
    record["formatRefreshError"] = " | ".join(errors)[:240]
    atomic_write_json(path, record)


def refresh_item(item: dict, fetch_url: str | None = None, allow_reader: bool = True) -> str | None:
    loaded = read_snapshot(item)
    if not loaded:
        return None
    path, previous = loaded
    target_url = fetch_url or item["url"]
    attempts = []
    if community_api_url(target_url):
        attempts.append(("community", fetch_community_text))
    attempts.append(("page", fetch_page_text))
    if allow_reader:
        attempts.append(("reader", fetch_reader_text))

    errors = []
    for kind, fetcher in attempts:
        try:
            body, resolved_url = fetcher(target_url)
            if not acceptable_refresh(previous, body):
                raise ValueError("refreshed body did not preserve enough structured content")
            write_snapshot(item, body, kind, resolved_url=resolved_url)
            return kind
        except Exception as error:
            errors.append(error_note(kind, error))
    mark_failure(path, previous, errors)
    return None


def select_candidates(items: list[dict], now: datetime, limit: int, retry_days: int) -> list[tuple[dict, dict]]:
    candidates = []
    for item in items:
        if not needs_format_refresh(item, now, retry_days):
            continue
        loaded = read_snapshot(item)
        if loaded:
            candidates.append((item, loaded[1]))
    candidates.sort(key=lambda pair: pair[0].get("publishedAt", ""), reverse=True)
    candidates.sort(key=lambda pair: not likely_flattened_code(pair[1]))
    return candidates[:max(0, limit)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=int(os.getenv("ARTICLE_FORMAT_REFRESH_LIMIT", "150")))
    parser.add_argument("--workers", type=int, default=int(os.getenv("ARTICLE_FORMAT_REFRESH_WORKERS", "8")))
    parser.add_argument("--reader-limit", type=int, default=int(os.getenv("ARTICLE_FORMAT_READER_LIMIT", "12")))
    parser.add_argument("--retry-days", type=int, default=int(os.getenv("ARTICLE_FORMAT_RETRY_DAYS", "7")))
    args = parser.parse_args()

    data = load_news(NEWS_FILE)
    now = datetime.now(timezone.utc)
    selected = select_candidates(data.get("items", []), now, args.limit, args.retry_days)
    if not selected:
        print("[format] no legacy article snapshots need refreshing")
        return 0

    items = [item for item, _ in selected]
    resolved_urls = resolve_google_news_urls([item["url"] for item in items])
    reader_ids = {item["id"] for item in items[:max(0, args.reader_limit)]}
    counts = {"community": 0, "page": 0, "reader": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(items)))) as pool:
        futures = {
            pool.submit(
                refresh_item,
                item,
                resolved_urls.get(item["url"]),
                item["id"] in reader_ids,
            ): item
            for item in items
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                kind = future.result()
            except Exception as error:
                kind = None
                print(f"[format:warn] {item.get('id', '?')}: {type(error).__name__}: {error}")
            if kind:
                counts[kind] += 1
                item["articleKind"] = kind
                print(f"[format:{kind}] {item['id']} {item['title'][:80]}")
            else:
                counts["failed"] += 1

    save_news(data, NEWS_FILE)
    print(
        f"[format] processed {len(items)} legacy snapshots: "
        f"{counts['community']} community, {counts['page']} page, "
        f"{counts['reader']} reader, {counts['failed']} preserved for retry"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
