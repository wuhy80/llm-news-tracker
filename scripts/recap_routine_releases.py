#!/usr/bin/env python3
"""Re-cap routine release notes that were reviewed before the rule existed.

Release feeds publish a version bump per commit, and the reviewer used to rate
them as worth reading: a sample of level four and five items included
"v0.40.0-rc4: MLX: version bump (#18720)" and "langgraph==1.2.13". Ingest now
caps such titles at level two, but ai_review only ever revisits an item whose
aiReview.version differs from the current one, so everything reviewed before the
rule keeps its inflated level permanently.

This applies the same cap to those items, through the same cap_dimensions and
level_for_score the review path uses, so the dimensions, the score and the level
stay consistent with each other. No model calls.

Dry run by default. Only data/articles/**/*.json is rewritten; the daily shards
and the locator tables are rebuilt from those by the next news_store.save_news.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTICLES_DIR = ROOT / "data" / "articles"

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_review

# The same ceiling normalize_review applies to an acknowledged rehash.
RECAP_CEILING = 49


def snapshot_paths(articles_dir: Path) -> list[Path]:
    return sorted(path for path in articles_dir.rglob("*.json") if path.is_file())


def capped_review(review: dict, now: datetime) -> dict | None:
    """The re-capped review, or None when it is already within the ceiling."""
    dimensions = review.get("dimensions")
    if not isinstance(dimensions, dict) or not dimensions:
        return None
    level_ceiling = ai_review.level_for_score(RECAP_CEILING)
    score = review.get("importanceScore")
    level = review.get("importanceLevel")
    if isinstance(score, int) and isinstance(level, int) and score <= RECAP_CEILING and level <= level_ceiling:
        return None
    capped = ai_review.cap_dimensions({key: int(value) for key, value in dimensions.items()}, RECAP_CEILING)
    updated = dict(review)
    updated["dimensions"] = capped
    updated["importanceScore"] = sum(capped.values())
    updated["importanceLevel"] = ai_review.level_for_score(updated["importanceScore"])
    updated["recappedAt"] = now.isoformat().replace("+00:00", "Z")
    return updated


def build_plan(articles_dir: Path | None = None, now: datetime | None = None) -> dict:
    # Resolved here rather than as default arguments, so tests can patch them.
    articles_dir = ARTICLES_DIR if articles_dir is None else articles_dir
    now = now or datetime.now(timezone.utc)
    plan = {"scanned": 0, "matched": 0, "changed": 0, "changes": [], "levels": {}}
    for path in snapshot_paths(articles_dir):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        plan["scanned"] += 1
        review = snapshot.get("aiReview")
        if not isinstance(review, dict) or not ai_review.is_routine_release(snapshot):
            continue
        plan["matched"] += 1
        updated = capped_review(review, now)
        if updated is None:
            continue
        plan["changed"] += 1
        before = f"{review.get('importanceLevel')}"
        after = f"{updated['importanceLevel']}"
        key = f"{before}->{after}"
        plan["levels"][key] = 1 + plan["levels"].get(key, 0)
        plan["changes"].append({"path": str(path), "review": updated})
    return plan


def apply_plan(plan: dict) -> int:
    written = 0
    for change in plan["changes"]:
        path = Path(change["path"])
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        snapshot["aiReview"] = change["review"]
        # Same serialisation as article_store.write_snapshot, so the diff stays small.
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the re-capped reviews (default: report only)")
    parser.add_argument("--report-limit", type=int, default=8)
    args = parser.parse_args(argv)

    if not ARTICLES_DIR.is_dir():
        print(f"[recap] no articles directory at {ARTICLES_DIR}; nothing to do")
        return 0

    plan = build_plan()
    summary = {key: value for key, value in plan.items() if key != "changes"}
    print("[recap] " + json.dumps(summary, ensure_ascii=False), flush=True)
    for change in plan["changes"][:args.report_limit]:
        print(f"[recap] would re-cap {change['path']}", flush=True)

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as stream:
            stream.write("\n### Routine release re-cap\n\n```json\n"
                         + json.dumps(summary, ensure_ascii=False, indent=2) + "\n```\n")

    if not args.apply:
        print("[recap] dry run: nothing was changed", flush=True)
        return 0

    written = apply_plan(plan)
    print(f"[recap] re-capped {written} snapshot(s); the next update-news run "
          f"rebuilds the daily shards from them", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
