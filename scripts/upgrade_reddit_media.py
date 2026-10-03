#!/usr/bin/env python3
"""Repoint archived Reddit previews at the original image, after verifying it.

preview.redd.it serves a resized copy behind a signature bound to that exact
size, so a 140px preview cannot be asked for a larger one - and the reader then
upscales it to 920px wide. i.redd.it serves the original, but not always: the
probe found one in eight missing. So every replacement is fetched first and only
the ones that really answer are rewritten.

Only the ``src`` field is touched, so ``originalUrl`` keeps the preview link as
provenance. Substitutions are byte-level and limited to the two JSON spacings in
use, so line endings and every other byte survive untouched.

Deletion-free and reversible: nothing is removed, and git history keeps the old
values.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
MEDIA_DIR = DATA_DIR / "article-media"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dedupe_article_media import collect_referenced

USER_AGENT = "LLM-Pulse/1.0 (+https://github.com/wuhy80/llm-news-tracker)"
TIMEOUT = 25
SMALL_WIDTH = 400
SRC_PREVIEW = re.compile(r'"src"\s*:\s*"(https://preview\.redd\.it/[^"]+)"')


def original_for(preview_url: str) -> str:
    parsed = urllib.parse.urlparse(preview_url)
    return f"https://i.redd.it{parsed.path}"


def collect_candidates(data_dir: Path, media_dir: Path) -> tuple[dict[str, str], list[Path]]:
    """Map every small preview URL found in a src field to its original."""
    _referenced, documents = collect_referenced(data_dir, media_dir)
    candidates: dict[str, str] = {}
    for path in documents:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in SRC_PREVIEW.finditer(text):
            url = match.group(1)
            widths = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("width") or []
            try:
                width = int(widths[0]) if widths else 0
            except ValueError:
                width = 0
            if width and width < SMALL_WIDTH:
                candidates.setdefault(url, original_for(url))
    return candidates, documents


def verify(url: str) -> dict:
    """Confirm the original is retrievable without downloading it whole."""
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            length = int(response.headers.get("Content-Length") or 0)
            return {"ok": response.status == 200, "status": response.status, "bytes": length}
    except urllib.error.HTTPError as error:
        if error.code in (403, 405):  # some CDNs refuse HEAD
            return verify_with_range(url)
        return {"ok": False, "status": error.code, "bytes": 0}
    except Exception as error:
        return {"ok": False, "status": 0, "bytes": 0, "error": type(error).__name__}


def verify_with_range(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return {"ok": response.status in (200, 206), "status": response.status, "bytes": 0}
    except urllib.error.HTTPError as error:
        return {"ok": False, "status": error.code, "bytes": 0}
    except Exception as error:
        return {"ok": False, "status": 0, "bytes": 0, "error": type(error).__name__}


def build_plan(data_dir: Path, media_dir: Path, checker=None, limit: int = 0) -> dict:
    # Resolved here rather than as a default argument: a default binds the
    # function object at definition time, so patching verify() would not reach
    # it and the caller could not substitute a checker at all.
    checker = verify if checker is None else checker
    candidates, documents = collect_candidates(data_dir, media_dir)
    ordered = sorted(candidates)
    if limit:
        ordered = ordered[:limit]

    accepted: dict[str, str] = {}
    rejected: list[dict] = []
    for preview in ordered:
        result = checker(candidates[preview])
        if result.get("ok"):
            accepted[preview] = candidates[preview]
        else:
            rejected.append({"preview": preview, "status": result.get("status"),
                             "error": result.get("error")})
    return {"candidates": len(candidates), "checked": len(ordered),
            "accepted": len(accepted), "rejected": rejected,
            "bytes": sum(0 for _ in accepted), "mapping": accepted,
            "documents": documents}


def rewrite(documents: list[Path], mapping: dict[str, str]) -> int:
    """Point src at the original, leaving originalUrl and everything else alone."""
    pairs = []
    for preview, original in mapping.items():
        old = preview.encode("utf-8")
        new = original.encode("utf-8")
        pairs.append((b'"src": "' + old + b'"', b'"src": "' + new + b'"'))
        pairs.append((b'"src":"' + old + b'"', b'"src":"' + new + b'"'))
    changed = 0
    for path in documents:
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        updated = payload
        for old_bytes, new_bytes in pairs:
            if old_bytes in updated:
                updated = updated.replace(old_bytes, new_bytes)
        if updated != payload:
            path.write_bytes(updated)
            changed += 1
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the verified replacements (default: report only)")
    parser.add_argument("--limit", type=int, default=0, help="check only the first N candidates")
    parser.add_argument("--report-limit", type=int, default=10)
    args = parser.parse_args(argv)

    if not MEDIA_DIR.is_dir():
        print(f"[reddit-original] no media directory at {MEDIA_DIR}; nothing to do")
        return 0

    plan = build_plan(DATA_DIR, MEDIA_DIR, limit=args.limit)
    mapping = plan.pop("mapping")
    documents = plan.pop("documents")
    plan["applied"] = bool(args.apply)
    print("[reddit-original] " + json.dumps(plan, ensure_ascii=False), flush=True)

    for item in plan["rejected"][:args.report_limit]:
        print(f"[reddit-original] rejected status={item['status']}  {item['preview']}", flush=True)
    for preview in sorted(mapping)[:args.report_limit]:
        print(f"[reddit-original] {preview}\n[reddit-original]   -> {mapping[preview]}", flush=True)

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as stream:
            stream.write("\n### Reddit originals\n\n```json\n"
                         + json.dumps(plan, ensure_ascii=False, indent=2) + "\n```\n")

    if not args.apply:
        print("[reddit-original] dry run: nothing was changed", flush=True)
        return 0

    if not mapping:
        print("[reddit-original] nothing verified, so nothing to rewrite", flush=True)
        return 0
    changed = rewrite(documents, mapping)
    print(f"[reddit-original] rewrote {changed} document(s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
