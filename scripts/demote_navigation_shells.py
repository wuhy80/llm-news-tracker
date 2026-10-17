#!/usr/bin/env python3
"""Demote navigation-shell bodies back to the summary fallback.

JS-rendered mirrors (aiera.com.cn) were archived as successful reader reads
while the body held only the site menu, so the reader page showed the menu
instead of the article. This demotes those snapshots to the canonical
fetch-failure state (contentKind summary, empty body, failure note), which the
reader page renders as the RSS summary and the archive retry cooldown treats
like any other failure. The daily shards, the manifest, the locator tables and
the search index are rebuilt through news_store.save_news.

Dry run by default; --apply rewrites.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTICLES_DIR = ROOT / "data" / "articles"

sys.path.insert(0, str(Path(__file__).resolve().parent))

import article_store
import news_store


def snapshot_paths(articles_dir: Path) -> list[Path]:
    return sorted(path for path in articles_dir.rglob("*.json") if path.is_file())


def demote_note(record: dict) -> str | None:
    kind = record.get("contentKind")
    if kind not in {"reader", "page"}:
        return None
    if not article_store.is_navigation_shell(str(record.get("body") or "")):
        return None
    return f"{kind}:navigation-shell"


def build_plan(articles_dir: Path = ARTICLES_DIR) -> dict:
    plan = {"scanned": 0, "matched": 0, "changes": [], "hosts": {}}
    for path in snapshot_paths(articles_dir):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        plan["scanned"] += 1
        note = demote_note(record)
        if not note:
            continue
        plan["matched"] += 1
        host = (urllib.parse.urlparse(str(record.get("resolvedUrl") or record.get("url") or "")).hostname or "unknown").lower()
        plan["hosts"][host] = 1 + plan["hosts"].get(host, 0)
        plan["changes"].append({"path": str(path), "note": note})
    return plan


def apply_plan(plan: dict) -> int:
    written = 0
    for change in plan["changes"]:
        path = Path(change["path"])
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        record["contentKind"] = "summary"
        record["body"] = ""
        record["note"] = change["note"]
        record.pop("articleKind", None)
        # Same serialisation as article_store.write_snapshot, so diffs stay small.
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written += 1
    if written:
        # Rebuild the shards, the manifest, the locators and the search index
        # from the demoted snapshots.
        news_store.save_news(news_store.load_news())
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="rewrite the snapshots (default: dry run)")
    parser.add_argument("--report-limit", type=int, default=8)
    args = parser.parse_args(argv)

    if not ARTICLES_DIR.is_dir():
        print(f"[shells] no articles directory at {ARTICLES_DIR}; nothing to do")
        return 0

    plan = build_plan()
    summary = {key: value for key, value in plan.items() if key != "changes"}
    print("[shells] " + json.dumps(summary, ensure_ascii=False), flush=True)
    for change in plan["changes"][:args.report_limit]:
        print(f"[shells] would demote {change['path']}", flush=True)

    if not args.apply:
        print("[shells] dry run: nothing was changed", flush=True)
        return 0

    written = apply_plan(plan)
    print(f"[shells] demoted {written} snapshot(s); shards and the search index were rebuilt", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
