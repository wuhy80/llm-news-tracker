#!/usr/bin/env python3
"""Read and write sharded news indexes and self-contained article records."""

from __future__ import annotations

import json
import re
from pathlib import Path

from article_store import ARTICLES_DIR, ROOT, publication_path, utc_now

MANIFEST_FILE = ROOT / "data" / "news.json"
NEWS_DIR = ROOT / "data" / "news"
ARTICLE_INDEX_DIR = ROOT / "data" / "article-index"
SCHEMA_VERSION = 3
READABLE_KINDS = {"community", "feed", "page", "reader"}
ITEM_FIELDS = (
    "id", "title", "summary", "url", "source", "sourceDomain", "publishedAt",
    "category", "tags", "score", "signal",
)
INDEX_REVIEW_FIELDS = (
    "version", "isRelevant", "category", "tags", "reasonZh", "summaryZh",
    "importanceScore", "importanceLevel",
)


def atomic_write_json(path: Path, payload: object) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def day_parts(published_at: str) -> tuple[str, str, str]:
    path = publication_path(published_at)
    return path.parts[0], path.parts[1], path.parts[2]


def article_path(item: dict, articles_dir: Path = ARTICLES_DIR) -> Path:
    article_id = item.get("id", "")
    if not re.fullmatch(r"[0-9a-f]{12}", article_id):
        raise ValueError("invalid article id")
    year, month, day = day_parts(item.get("publishedAt"))
    return articles_dir / year / month / day / f"{article_id}.json"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_news(
    manifest_file: Path = MANIFEST_FILE,
    articles_dir: Path = ARTICLES_DIR,
    hydrate: bool = True,
) -> dict:
    manifest = _read_json(manifest_file)
    if isinstance(manifest.get("items"), list):
        return manifest

    news_dir = manifest_file.parent / "news"
    items = []
    for date in manifest.get("days", {}):
        year, month, day = date.split("-")
        shard = _read_json(news_dir / year / month / f"{day}.json")
        items.extend(shard.get("items", []))

    if hydrate:
        for item in items:
            try:
                record = _read_json(article_path(item, articles_dir))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            for field in ITEM_FIELDS:
                if field in record:
                    item[field] = record[field]
            for field in ("articleKind", "aiReview", "summaryZh"):
                if field in record:
                    item[field] = record[field]

    data = {
        key: value
        for key, value in manifest.items()
        if key not in {"schemaVersion", "days", "latestDate", "itemCount", "articleIndexPattern"}
    }
    data["items"] = items
    return data


def sync_article_record(item: dict, articles_dir: Path = ARTICLES_DIR, generated_at: str | None = None) -> dict:
    path = article_path(item, articles_dir)
    try:
        existing = _read_json(path)
    except (OSError, json.JSONDecodeError):
        existing = {
            "originalUrl": item.get("url", ""),
            "resolvedUrl": item.get("url", ""),
            "fetchedAt": generated_at or utc_now(),
            "contentKind": "summary",
            "archiveVersion": 2,
            "body": item.get("summary", ""),
            "note": "archive:not attempted",
        }

    record = {"schemaVersion": SCHEMA_VERSION}
    for field in ITEM_FIELDS:
        if field in item:
            record[field] = item[field]
    for key, value in existing.items():
        if key not in ITEM_FIELDS and key not in {"schemaVersion", "articleKind", "aiReview"}:
            record[key] = value
    record.setdefault("originalUrl", item.get("url", ""))
    record.setdefault("resolvedUrl", item.get("url", ""))
    record.setdefault("fetchedAt", generated_at or utc_now())
    record.setdefault("contentKind", "summary")
    record.setdefault("archiveVersion", 2)
    record.setdefault("body", item.get("summary", ""))

    kind = record.get("contentKind")
    if kind in READABLE_KINDS:
        record["articleKind"] = kind
        item["articleKind"] = kind
    else:
        item.pop("articleKind", None)

    review = item.get("aiReview")
    if isinstance(review, dict):
        record["aiReview"] = review
        if review.get("summaryZh"):
            record["summaryZh"] = review["summaryZh"]
    elif isinstance(existing.get("aiReview"), dict):
        record["aiReview"] = existing["aiReview"]

    atomic_write_json(path, record)
    return record


def index_item(record: dict) -> dict:
    item = {field: record[field] for field in ITEM_FIELDS if field in record}
    if record.get("articleKind"):
        item["articleKind"] = record["articleKind"]
    if record.get("summaryZh"):
        item["summaryZh"] = record["summaryZh"]
    review = record.get("aiReview")
    if isinstance(review, dict):
        item["aiReview"] = {
            field: review[field]
            for field in INDEX_REVIEW_FIELDS
            if field in review
        }
    return item


def _remove_stale_json(directory: Path, expected: set[Path]) -> None:
    if not directory.exists():
        return
    for path in directory.rglob("*.json"):
        if path not in expected:
            path.unlink()
    for path in sorted((entry for entry in directory.rglob("*") if entry.is_dir()), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass


def save_news(
    data: dict,
    manifest_file: Path = MANIFEST_FILE,
    articles_dir: Path = ARTICLES_DIR,
    news_dir: Path | None = None,
    article_index_dir: Path | None = None,
) -> dict[str, int]:
    news_dir = news_dir or manifest_file.parent / "news"
    article_index_dir = article_index_dir or manifest_file.parent / "article-index"
    generated_at = data.get("generatedAt") or utc_now()
    records = []
    seen_ids = set()
    for item in data.get("items", []):
        article_id = item.get("id")
        if article_id in seen_ids:
            raise ValueError(f"duplicate article id: {article_id}")
        seen_ids.add(article_id)
        records.append(sync_article_record(item, articles_dir, generated_at))
    records.sort(key=lambda item: (item.get("publishedAt", ""), item.get("score", 0)), reverse=True)

    by_day: dict[str, list[dict]] = {}
    locators: dict[str, dict[str, str]] = {}
    for record in records:
        year, month, day = day_parts(record.get("publishedAt"))
        date = f"{year}-{month}-{day}"
        by_day.setdefault(date, []).append(index_item(record))
        locators.setdefault(record["id"][:2], {})[record["id"]] = f"{year}/{month}/{day}"

    expected_news = set()
    for date, items in by_day.items():
        year, month, day = date.split("-")
        path = news_dir / year / month / f"{day}.json"
        expected_news.add(path)
        atomic_write_json(path, {"date": date, "items": items})
    _remove_stale_json(news_dir, expected_news)

    expected_indexes = set()
    for prefix, mapping in locators.items():
        path = article_index_dir / f"{prefix}.json"
        expected_indexes.add(path)
        atomic_write_json(path, dict(sorted(mapping.items())))
    _remove_stale_json(article_index_dir, expected_indexes)

    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": generated_at,
        "historyPolicy": data.get("historyPolicy", "append-only"),
        "sources": data.get("sources", []),
        "itemCount": len(records),
        "latestDate": max(by_day, default=None),
        "days": {date: len(by_day[date]) for date in sorted(by_day, reverse=True)},
        "articleIndexPattern": "data/article-index/{prefix}.json",
    }
    if isinstance(data.get("aiReview"), dict):
        manifest["aiReview"] = data["aiReview"]
    atomic_write_json(manifest_file, manifest)
    return {"items": len(records), "days": len(by_day), "prefixes": len(locators)}
