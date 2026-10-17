#!/usr/bin/env python3
"""Validate sharded news indexes and self-contained article records."""

from __future__ import annotations

import json
import re
from pathlib import Path

from article_store import ROOT

DATA_DIR = ROOT / "data"
MANIFEST_FILE = DATA_DIR / "news.json"
NEWS_DIR = DATA_DIR / "news"
ARTICLES_DIR = DATA_DIR / "articles"
ARTICLE_INDEX_DIR = DATA_DIR / "article-index"
ARTICLE_FIELDS = {
    "schemaVersion", "id", "title", "summary", "url", "source", "publishedAt",
    "category", "tags", "score", "signal", "originalUrl", "resolvedUrl",
    "fetchedAt", "contentKind", "archiveVersion", "body",
}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def validate(root: Path = DATA_DIR) -> dict[str, int]:
    manifest_file = root / "news.json"
    news_dir = root / "news"
    articles_dir = root / "articles"
    locator_dir = root / "article-index"
    manifest = read_json(manifest_file)
    errors = []
    if "items" in manifest:
        errors.append("data/news.json still contains an items array")
    days = manifest.get("days")
    if not isinstance(days, dict):
        errors.append("manifest days must be an object")
        days = {}

    seen_ids = set()
    expected_article_paths = set()
    for date, expected_count in days.items():
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            errors.append(f"invalid manifest date: {date}")
            continue
        year, month, day = date.split("-")
        shard_path = news_dir / year / month / f"{day}.json"
        try:
            shard = read_json(shard_path)
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"cannot read {shard_path.relative_to(root)}: {error}")
            continue
        items = shard.get("items")
        if not isinstance(items, list):
            errors.append(f"{shard_path.relative_to(root)} items must be an array")
            continue
        if shard.get("date") != date or len(items) != expected_count:
            errors.append(f"{shard_path.relative_to(root)} date/count does not match manifest")
        for item in items:
            article_id = item.get("id", "")
            if not re.fullmatch(r"[0-9a-f]{12}", article_id):
                errors.append(f"invalid article id in {shard_path.relative_to(root)}: {article_id}")
                continue
            if article_id in seen_ids:
                errors.append(f"duplicate article id: {article_id}")
            seen_ids.add(article_id)
            article_path = articles_dir / year / month / day / f"{article_id}.json"
            expected_article_paths.add(article_path)
            try:
                record = read_json(article_path)
            except (OSError, json.JSONDecodeError) as error:
                errors.append(f"cannot read {article_path.relative_to(root)}: {error}")
                continue
            missing = ARTICLE_FIELDS - record.keys()
            if missing:
                errors.append(f"{article_path.relative_to(root)} missing fields: {', '.join(sorted(missing))}")
            if record.get("id") != article_id or not str(record.get("publishedAt", "")).startswith(date):
                errors.append(f"{article_path.relative_to(root)} identity/date mismatch")
            review = record.get("aiReview")
            if isinstance(review, dict) and review.get("version") == "ai-editor-v3" and not isinstance(review.get("glossary"), list):
                errors.append(f"{article_path.relative_to(root)} v3 review has no glossary array")

    actual_article_paths = set(articles_dir.rglob("*.json"))
    extras = actual_article_paths - expected_article_paths
    missing = expected_article_paths - actual_article_paths
    if extras:
        errors.append(f"{len(extras)} article files are absent from daily indexes")
    if missing:
        errors.append(f"{len(missing)} indexed article files are missing")
    flat_files = list(articles_dir.glob("*.json"))
    if flat_files:
        errors.append(f"{len(flat_files)} flat article files remain")

    locator_entries = {}
    for path in locator_dir.glob("*.json"):
        prefix = path.stem
        mapping = read_json(path)
        for article_id, location in mapping.items():
            if article_id[:2] != prefix or not re.fullmatch(r"\d{4}/\d{2}/\d{2}", location):
                errors.append(f"invalid locator entry: {article_id} -> {location}")
                continue
            target = articles_dir / location / f"{article_id}.json"
            if not target.exists():
                errors.append(f"locator target missing: {article_id} -> {location}")
            locator_entries[article_id] = location
    if set(locator_entries) != seen_ids:
        errors.append("locator ids do not match daily index ids")
    if manifest.get("itemCount") != len(seen_ids):
        errors.append("manifest itemCount does not match daily indexes")

    if errors:
        detail = "\n".join(f"- {error}" for error in errors[:50])
        suffix = f"\n- ... and {len(errors) - 50} more" if len(errors) > 50 else ""
        raise ValueError(f"data validation failed:\n{detail}{suffix}")
    return {"items": len(seen_ids), "days": len(days), "locators": len(locator_entries)}


def main() -> int:
    result = validate()
    print(f"[data] validated {result['items']} articles across {result['days']} days and {result['locators']} locators")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
