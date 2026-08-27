#!/usr/bin/env python3
"""Organize article snapshots into publication-date directories."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from article_store import ARTICLES_DIR, ROOT, publication_path

NEWS_FILE = ROOT / "data" / "news.json"


def build_move_plan(news_file: Path, articles_dir: Path) -> tuple[list[tuple[Path, Path]], int]:
    news = json.loads(news_file.read_text(encoding="utf-8"))
    published_by_id = {
        item["id"]: item.get("publishedAt")
        for item in news.get("items", [])
        if isinstance(item, dict) and item.get("id")
    }
    moves: list[tuple[Path, Path]] = []
    destinations: dict[Path, list[Path]] = {}
    orphan_count = 0

    for source in sorted(articles_dir.rglob("*.json")):
        try:
            snapshot = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid snapshot JSON: {source}") from error
        article_id = snapshot.get("id")
        if not isinstance(article_id, str) or not re.fullmatch(r"[0-9a-f]{12}", article_id):
            raise ValueError(f"invalid article id in snapshot: {source}")
        if source.stem != article_id:
            raise ValueError(f"snapshot id does not match filename: {source}")

        published_at = published_by_id.get(article_id)
        if not published_at:
            orphan_count += 1
            published_at = snapshot.get("fetchedAt")
        try:
            destination = articles_dir / publication_path(published_at) / source.name
        except ValueError as error:
            raise ValueError(f"no valid date for snapshot: {source}") from error
        moves.append((source, destination))
        destinations.setdefault(destination, []).append(source)

    collisions = {
        destination: sources
        for destination, sources in destinations.items()
        if len(sources) > 1 or (destination.exists() and destination not in sources)
    }
    if collisions:
        destination, sources = next(iter(collisions.items()))
        joined = ", ".join(str(source) for source in sources)
        raise ValueError(f"snapshot destination collision at {destination}: {joined}")
    return moves, orphan_count


def organize_articles(news_file: Path, articles_dir: Path, dry_run: bool = False) -> dict[str, int]:
    moves, orphan_count = build_move_plan(news_file, articles_dir)
    pending = [(source, destination) for source, destination in moves if source != destination]
    if not dry_run:
        for source, destination in pending:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
    return {
        "total": len(moves),
        "moved": len(pending),
        "unchanged": len(moves) - len(pending),
        "orphans": orphan_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="validate and report without moving files")
    args = parser.parse_args()
    result = organize_articles(NEWS_FILE, ARTICLES_DIR, dry_run=args.dry_run)
    mode = "would move" if args.dry_run else "moved"
    print(
        f"[articles] {mode} {result['moved']} of {result['total']} snapshots; "
        f"{result['unchanged']} already organized; {result['orphans']} used fetchedAt fallback"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
