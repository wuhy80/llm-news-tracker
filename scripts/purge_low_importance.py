#!/usr/bin/env python3
"""Drop importance-1 articles and routine releases from the archive.

Level-1 items are never translated and the archive owner does not want their
bodies downloaded at all. Routine releases (patch bumps, pre-releases, bare
build numbers, pinned package bumps — the same titles ai_review.is_routine_release
caps at review time) carry no information worth an archive slot either, so they
are removed with the same pipeline. The purge removes both kinds from the daily
shards; news_store.save_news (called here, as build_news_data does) rebuilds the
manifest and locator shards and prunes the snapshots that fall out. Media
directories and zh-CN translation records of the purged articles are removed
too. Purged URLs go into the manifest blockedUrls so fetch_news does not
re-import them from feeds during the 120-day feed window.

Dry run by default; --apply performs the rewrite.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
NEWS_DIR = DATA_DIR / "news"
MEDIA_DIR = DATA_DIR / "article-media"
TRANSLATIONS_DIR = DATA_DIR / "translations" / "zh-CN"
BLOCKLIST_FILE = DATA_DIR / "news-blocklist.json"
BLOCKLIST_CAP = 100_000

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_review
import news_store
from article_store import publication_path


def is_importance_one(item: dict) -> bool:
    review = item.get("aiReview")
    return isinstance(review, dict) and review.get("importanceLevel") == 1


def purge_reason(item: dict) -> str | None:
    """Why the item leaves the archive, or None when it stays."""
    if is_importance_one(item):
        return "importance-1"
    if ai_review.is_routine_release(item):
        return "routine-release"
    # Items stored before fetch_news carried the releaseFeed flag: a "* Releases"
    # source whose title is a product name plus a patch/pre-release tag is the
    # same churn, so the purge catches those legacy entries too.
    source = str(item.get("source") or "")
    if source.endswith("Releases") and ai_review.is_routine_feed_tag(str(item.get("title") or "")):
        return "routine-release"
    return None


def shard_paths(news_dir: Path) -> list[Path]:
    return sorted(path for path in news_dir.rglob("*.json") if path.is_file())


def build_plan(news_dir: Path | None = None) -> dict:
    news_dir = NEWS_DIR if news_dir is None else news_dir
    plan = {"shards": 0, "items": 0, "dropped": 0, "routine": 0, "changes": [], "purged": []}
    for path in shard_paths(news_dir):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        items = payload.get("items")
        if not isinstance(items, list):
            continue
        plan["shards"] += 1
        plan["items"] += len(items)
        kept = []
        for item in items:
            reason = purge_reason(item)
            if reason:
                plan["dropped"] += 1
                if reason == "routine-release":
                    plan["routine"] += 1
                plan["purged"].append({"id": item.get("id"),
                                       "publishedAt": item.get("publishedAt"),
                                       "url": item.get("url"),
                                       "reason": reason})
                continue
            kept.append(item)
        if len(kept) != len(items):
            plan["changes"].append({"path": str(path), "kept": kept})
    return plan


def apply_plan(plan: dict, manifest_file: Path = None, articles_dir: Path = None,
               media_dir: Path = MEDIA_DIR, translations_dir: Path = TRANSLATIONS_DIR) -> None:
    from news_store import MANIFEST_FILE, ARTICLES_DIR
    manifest_file = manifest_file or MANIFEST_FILE
    articles_dir = articles_dir or ARTICLES_DIR
    for change in plan["changes"]:
        path = Path(change["path"])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        payload["items"] = change["kept"]
        news_store.atomic_write_json(path, payload)
    # Rebuild manifest, locator shards and prune snapshots that fell out.
    data = news_store.load_news(manifest_file, articles_dir)
    # Purged urls land in the manifest so fetch_news skips them during the
    # 120-day feed window; the manifest is on the commit path, a standalone
    # file would not be.
    blocked = set(data.get("blockedUrls") or [])
    removed_media = removed_translations = 0
    for entry in plan["purged"]:
        article_id, published_at = entry.get("id"), entry.get("publishedAt")
        if not article_id or not published_at:
            continue
        url = str(entry.get("url") or "").strip()
        if url:
            blocked.add(url)
        media = media_dir / publication_path(published_at) / str(article_id)
        if media.exists():
            shutil.rmtree(media, ignore_errors=True)
            removed_media += 1
        record = translations_dir / publication_path(published_at) / f"{article_id}.json"
        if record.exists():
            record.unlink()
            removed_translations += 1
    data["blockedUrls"] = sorted(blocked)[:BLOCKLIST_CAP]
    news_store.save_news(data, manifest_file, articles_dir)
    print(f"[purge] removed media dirs: {removed_media}; translation records: {removed_translations}; "
          f"blocklist holds {len(data['blockedUrls'])} urls")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="rewrite the archive (default: dry run)")
    parser.add_argument("--news-dir", type=Path, default=None, help="override the daily shard directory (for tests)")
    args = parser.parse_args(argv)
    news_dir = args.news_dir if args.news_dir is not None else NEWS_DIR
    plan = build_plan(news_dir)
    print(f"[purge] scanned {plan['shards']} shards / {plan['items']} items; "
          f"importance-1 articles: {plan['dropped'] - plan['routine']}; "
          f"routine releases: {plan['routine']}")
    if not args.apply:
        for entry in plan["purged"][:10]:
            print(f"  would purge [{entry.get('reason')}] {entry['publishedAt']} {entry['id']} {str(entry.get('url'))[:80]}")
        print("[purge] dry run; pass --apply to rewrite the archive")
        return 0
    if not plan["changes"]:
        print("[purge] nothing to remove")
        return 0
    apply_plan(plan)
    print(f"[purge] dropped {plan['dropped']} articles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
