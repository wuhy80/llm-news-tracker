#!/usr/bin/env python3
"""Remove news items whose source no longer exists and trim over-long forum titles.

Two kinds of history do not match the current rules, and they need opposite
treatment.

Items from sources that were removed are noise and get dropped. The four Bing
News searches are the worst of them: they returned nothing for months and, on the
one day they worked, injected around thirty-five unrelated publishers into the
archive. They are matched by sourceDomain rather than by publisher name, because
the embedded-source feature only rewrote the name and left the domain as bing.com
for every one of them.

Forum titles over the 120 character cap are trimmed instead, reusing the same
shorten_title the ingest path uses. The content is fine; only the headline is not.

Old but readable articles are deliberately left alone: the 120-day feed cutoff
stops the back catalogue being re-imported, it does not say history should be
deleted, and the reader budget fix is already filling in their bodies.

Read-only by default. Only the daily shards are rewritten; the manifest, the
locator shards and the per-article snapshots are all rebuilt from them by the
next news_store.save_news, which also prunes the snapshots that fall out.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
NEWS_DIR = DATA_DIR / "news"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_news
import news_store

# Domains of every source that has been removed from SOURCES.
REMOVED_SOURCE_DOMAINS = (
    "bing.com",
    "lmarena.ai",
    "mistral.ai",
    "venturebeat.com",
    "linux.do",
)


def shard_paths(news_dir: Path) -> list[Path]:
    return sorted(path for path in news_dir.rglob("*.json") if path.is_file())


def is_removed_source(item: dict) -> bool:
    return str(item.get("sourceDomain") or "").casefold() in REMOVED_SOURCE_DOMAINS


def clean_items(items: list[dict]) -> tuple[list[dict], int, int]:
    """Return the kept items plus how many were dropped and how many trimmed."""
    kept: list[dict] = []
    dropped = 0
    trimmed = 0
    for item in items:
        if is_removed_source(item):
            dropped += 1
            continue
        if news_store.is_community_item(item):
            title = str(item.get("title") or "")
            shortened = fetch_news.shorten_title(title)
            if shortened != title:
                item = {**item, "title": shortened}
                trimmed += 1
        kept.append(item)
    return kept, dropped, trimmed


def display_path(value: str) -> str:
    """Relative to the repo when possible; tests point NEWS_DIR elsewhere."""
    path = Path(value)
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def build_plan(news_dir: Path | None = None) -> dict:
    # Resolved here rather than as a default argument: a default binds the value
    # at definition time, so patching NEWS_DIR would not reach it.
    news_dir = NEWS_DIR if news_dir is None else news_dir
    plan = {"shards": 0, "items": 0, "dropped": 0, "trimmed": 0, "changedFiles": 0,
            "droppedByDomain": {}, "changes": []}
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
        for item in items:
            if is_removed_source(item):
                domain = str(item.get("sourceDomain") or "?")
                plan["droppedByDomain"][domain] = 1 + plan["droppedByDomain"].get(domain, 0)
        kept, dropped, trimmed = clean_items(items)
        plan["dropped"] += dropped
        plan["trimmed"] += trimmed
        if dropped or trimmed:
            plan["changedFiles"] += 1
            plan["changes"].append({"path": str(path), "kept": kept})
    return plan


def apply_plan(plan: dict) -> int:
    written = 0
    for change in plan["changes"]:
        path = Path(change["path"])
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        payload["items"] = change["kept"]
        news_store.atomic_write_json(path, payload)
        written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the cleanup (default: report only)")
    parser.add_argument("--report-limit", type=int, default=8)
    args = parser.parse_args(argv)

    if not NEWS_DIR.is_dir():
        print(f"[cleanup] no news directory at {NEWS_DIR}; nothing to do")
        return 0

    plan = build_plan()
    summary = {key: value for key, value in plan.items() if key != "changes"}
    print("[cleanup] " + json.dumps(summary, ensure_ascii=False), flush=True)
    for domain, count in sorted(plan["droppedByDomain"].items(), key=lambda kv: -kv[1])[:args.report_limit]:
        print(f"[cleanup] dropping {count:>4} item(s) from removed source domain {domain}", flush=True)
    for change in plan["changes"][:args.report_limit]:
        print(f"[cleanup] would rewrite {display_path(change['path'])}", flush=True)

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as stream:
            stream.write("\n### Stale news cleanup\n\n```json\n"
                         + json.dumps(summary, ensure_ascii=False, indent=2) + "\n```\n")

    if not args.apply:
        print("[cleanup] dry run: nothing was changed", flush=True)
        return 0

    written = apply_plan(plan)
    print(f"[cleanup] rewrote {written} shard(s); the next update-news run "
          f"rebuilds the manifest and prunes the orphaned snapshots", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
