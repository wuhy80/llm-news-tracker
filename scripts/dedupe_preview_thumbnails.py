#!/usr/bin/env python3
"""Drop preview-redd.it thumbnail duplicates from archived articles.

Reddit serves one upload at i.redd.it and as sized preview.redd.it copies;
community_image_refs used to keep both, and the media layout could place the
tiny thumbnail inline while the original fell to the attachments section. This
collapses those variants in already-archived snapshots (keeping the original),
rewrites the media layout to point at the kept image, and rebuilds the shards,
the manifest, the locator tables and the search index through
news_store.save_news.

Dry run by default; --apply rewrites.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTICLES_DIR = ROOT / "data" / "articles"

sys.path.insert(0, str(Path(__file__).resolve().parent))

import article_store
import news_store


def snapshot_paths(articles_dir: Path) -> list[Path]:
    return sorted(path for path in articles_dir.rglob("*.json") if path.is_file())


def entry_url(entry: dict) -> str:
    return str(entry.get("src") or entry.get("originalUrl") or "")


def collapse_images(images: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """Keep the best variant per upload; return kept images and dropped id map.

    The dropped map points each removed image id at the id of the kept twin.
    """
    kept: list[dict] = []
    identity: dict[str, int] = {}
    dropped: dict[str, str] = {}
    for entry in images:
        key = article_store.reddit_media_identity(entry_url(entry))
        if key is None:
            kept.append(entry)
            continue
        at = identity.get(key)
        if at is None:
            identity[key] = len(kept)
            kept.append(entry)
            continue
        twin = kept[at]
        if article_store.reddit_variant_rank(entry_url(entry)) < article_store.reddit_variant_rank(entry_url(twin)):
            dropped[str(entry.get("id") or "")] = str(twin.get("id") or "")
            identity[key] = len(kept) - 1
            kept[at] = entry
        else:
            dropped[str(entry.get("id") or "")] = str(twin.get("id") or "")
    return kept, dropped


def rewrite_layout(layout: list[dict], unplaced: list[dict], dropped: dict[str, str]) -> tuple[list[dict], list[dict]]:
    new_layout = []
    placed_ids = set()
    for entry in layout:
        media_id = str(entry.get("mediaId") or "")
        if media_id in dropped:
            twin = dropped[media_id]
            if twin and twin not in placed_ids:
                # the twin takes over the inline slot the thumbnail held
                new_layout.append(dict(entry, mediaId=twin))
                placed_ids.add(twin)
            continue
        placed_ids.add(media_id)
        new_layout.append(entry)
    kept_unplaced = []
    for entry in unplaced:
        media_id = str(entry.get("mediaId") or "")
        if media_id in dropped:
            continue
        if media_id in placed_ids:
            continue
        kept_unplaced.append(entry)
    return new_layout, kept_unplaced


def repair_record(record: dict) -> bool:
    images = record.get("images")
    if not isinstance(images, list) or not images:
        return False
    kept, dropped = collapse_images(images)
    if not dropped:
        return False
    record["images"] = kept
    if isinstance(record.get("mediaLayout"), list):
        unplaced = record.get("mediaLayoutUnplaced") if isinstance(record.get("mediaLayoutUnplaced"), list) else []
        record["mediaLayout"], record["mediaLayoutUnplaced"] = rewrite_layout(
            record["mediaLayout"], unplaced, dropped)
        if not record["mediaLayoutUnplaced"] and record.get("mediaLayoutStatus") == "partial":
            record["mediaLayoutStatus"] = "full"
    return True


def build_plan(articles_dir: Path = ARTICLES_DIR) -> dict:
    plan = {"scanned": 0, "matched": 0, "changes": []}
    for path in snapshot_paths(articles_dir):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        plan["scanned"] += 1
        before = json.dumps(record.get("images"), ensure_ascii=False)
        if repair_record(record):
            plan["matched"] += 1
            plan["changes"].append({
                "path": str(path),
                "images": record["images"],
                "layout": record.get("mediaLayout"),
                "unplaced": record.get("mediaLayoutUnplaced"),
                "status": record.get("mediaLayoutStatus"),
                "before": before,
            })
    return plan


def apply_plan(plan: dict) -> int:
    written = 0
    for change in plan["changes"]:
        path = Path(change["path"])
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        repair_record(record)
        # Same serialisation as article_store.write_snapshot, so diffs stay small.
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written += 1
    if written:
        news_store.save_news(news_store.load_news())
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="rewrite the snapshots (default: dry run)")
    parser.add_argument("--report-limit", type=int, default=8)
    args = parser.parse_args(argv)

    if not ARTICLES_DIR.is_dir():
        print(f"[thumbs] no articles directory at {ARTICLES_DIR}; nothing to do")
        return 0

    plan = build_plan()
    print(f"[thumbs] scanned {plan['scanned']} snapshots; "
          f"preview-thumbnail duplicates: {plan['matched']}", flush=True)
    for change in plan["changes"][:args.report_limit]:
        kept = ", ".join(entry.get("src", "")[:60] for entry in change["images"])
        print(f"[thumbs] would dedupe {change['path']} -> {kept}", flush=True)

    if not args.apply:
        print("[thumbs] dry run: nothing was changed", flush=True)
        return 0

    written = apply_plan(plan)
    print(f"[thumbs] deduped {written} snapshot(s); shards and the search index were rebuilt", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
