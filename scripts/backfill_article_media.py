#!/usr/bin/env python3
"""Repair images and videos in archived snapshots incrementally."""

from __future__ import annotations

import argparse
import copy
import threading
import json
import os
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone

from article_store import (
    FETCHED_IMAGE_REFS,
    FETCHED_VIDEO_REFS,
    MEDIA_FORMAT_VERSION,
    ROOT,
    community_api_url,
    download_images,
    fetch_community_text,
    fetch_page_text,
    fetch_reader_text,
    take_media_source,
    is_browser_incompatible_image,
    is_site_chrome_image,
    snapshot_path,
    utc_now,
)
from news_store import load_news
from media_layout import LAYOUT_VERSION, build_layout, layout_current, body_digest

NEWS_FILE = ROOT / "data" / "news.json"
READABLE_KINDS = {"community", "feed", "page", "reader"}


LAYOUT_READER_LOCK = threading.Lock()
LAYOUT_READER_REMAINING = int(os.getenv("ARTICLE_LAYOUT_READER_LIMIT", "10"))


def layout_backfill_item(item: dict, recheck: bool = True, path: Path | None = None) -> str:
    global LAYOUT_READER_REMAINING
    path = path or snapshot_path(item)
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if layout_current(snapshot) or not (snapshot.get("images") or snapshot.get("videos")):
        return "skip"
    target = snapshot.get("resolvedUrl") or item.get("url")
    best = None
    errors = []
    methods = [fetch_reader_text, fetch_page_text] if snapshot.get("contentKind") == "reader" else [fetch_page_text, fetch_reader_text]
    for method in methods:
        if method is fetch_reader_text:
            with LAYOUT_READER_LOCK:
                if LAYOUT_READER_REMAINING <= 0:
                    continue
                LAYOUT_READER_REMAINING -= 1
        try:
            _, resolved = method(target)
            source = take_media_source(resolved)
            FETCHED_IMAGE_REFS.pop(resolved, None)
            FETCHED_VIDEO_REFS.pop(resolved, None)
            if not source:
                continue
            candidate = copy.deepcopy(snapshot)
            candidate.update(build_layout(snapshot.get("body", ""), candidate.get("images", []), candidate.get("videos", []), source[0], source[1], resolved))
            if best is None or candidate['mediaLayoutMatched'] > best['mediaLayoutMatched']:
                best = candidate
            if candidate['mediaLayoutStatus'] == 'complete':
                break
        except Exception as error:
            errors.append(type(error).__name__)
    if best is None:
        return 'error:' + (','.join(errors) or 'source_unavailable')
    if (snapshot.get('mediaLayoutBodyHash') == body_digest(snapshot.get('body', ''))
            and snapshot.get('mediaLayoutMatched', 0) > best['mediaLayoutMatched']):
        return 'partial:' + str(snapshot['mediaLayoutMatched'])
    best['mediaLayoutCheckedAt'] = utc_now()
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(best, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)
    return ('layout:' if best['mediaLayoutStatus'] == 'complete' else 'partial:') + str(best['mediaLayoutMatched'])


def has_problematic_images(snapshot: dict) -> bool:
    for image in snapshot.get("images") or []:
        if not isinstance(image, dict):
            continue
        source = str(image.get("originalUrl") or image.get("src") or "")
        if is_site_chrome_image(source) or (is_browser_incompatible_image(source) and not str(image.get("src", "")).startswith("data/article-media/")):
            return True
    return False


def matches_domain(item: dict, snapshot: dict, domain: str) -> bool:
    wanted = domain.casefold().strip().lstrip(".")
    if not wanted:
        return True
    if wanted in str(item.get("sourceDomain", "")).casefold():
        return True
    for value in [snapshot.get("resolvedUrl"), item.get("url")]:
        hostname = (urllib.parse.urlparse(str(value or "")).hostname or "").casefold()
        if hostname == wanted or hostname.endswith("." + wanted):
            return True
    for image in snapshot.get("images") or []:
        if not isinstance(image, dict):
            continue
        for value in (image.get("originalUrl"), image.get("src")):
            hostname = (urllib.parse.urlparse(str(value or "")).hostname or "").casefold()
            if hostname == wanted or hostname.endswith("." + wanted):
                return True
    return False


def media_backfill_item(item: dict, recheck: bool = False, path: Path | None = None) -> str:
    path = path or snapshot_path(item)
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return "missing"
    if snapshot.get("contentKind") not in READABLE_KINDS:
        return "skip"
    if not recheck and (snapshot.get("images") or snapshot.get("mediaCheckedAt")):
        return "skip"
    if recheck and snapshot.get("mediaFormatVersion", 0) >= MEDIA_FORMAT_VERSION and not has_problematic_images(snapshot):
        return "skip"
    target_url = snapshot.get("resolvedUrl") or item.get("url")
    try:
        if community_api_url(target_url):
            _, resolved_url = fetch_community_text(target_url)
        else:
            _, resolved_url = fetch_page_text(target_url)
        image_refs = FETCHED_IMAGE_REFS.pop(resolved_url, [])
        images = download_images(item, image_refs)
        videos = FETCHED_VIDEO_REFS.pop(resolved_url, [])
        # A successful empty extraction must clear previously misattributed media.
        snapshot.update({
            "images": images, "videos": videos,
            "mediaCheckedAt": utc_now(), "mediaFormatVersion": MEDIA_FORMAT_VERSION,
        })
        source = take_media_source(resolved_url)
        if source:
            snapshot.update(build_layout(snapshot.get("body", ""), snapshot["images"], snapshot["videos"], source[0], source[1], resolved_url))
            snapshot["mediaLayoutCheckedAt"] = utc_now()
        if recheck:
            snapshot["mediaRecheckedAt"] = utc_now()
        # Media repair must not re-normalize text, drop unknown fields or change fetchedAt.
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return f"{len(images)} images, {len(videos)} videos"
    except Exception as error:
        # Keep old data on fetch failure; do not mark it as successfully rechecked.
        return f"error:{type(error).__name__}"


def archive_candidates(directory: Path, state: dict, retry_days: int = 7, year: int = 0, domain: str = "", layout: bool = False) -> list[tuple[dict, Path]]:
    candidates = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retry_days)).isoformat().replace("+00:00", "Z")
    failures = state.get("failures", {})
    for path in sorted(directory.rglob("*.json")):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if snapshot.get("contentKind") not in READABLE_KINDS:
            continue
        if year and str(snapshot.get("publishedAt", ""))[:4] != str(year):
            continue
        if not matches_domain(snapshot, snapshot, domain):
            continue
        if layout:
            if layout_current(snapshot) or not (snapshot.get("images") or snapshot.get("videos")):
                continue
        elif snapshot.get("mediaFormatVersion", 0) >= MEDIA_FORMAT_VERSION and not has_problematic_images(snapshot):
            continue
        key = path.relative_to(directory).as_posix()
        failed = failures.get(key, {})
        if failed.get("attemptedAt", "") > cutoff:
            continue
        # Prioritize never-attempted files, so dead sources cannot starve the backlog.
        candidates.append((snapshot, path))
    candidates.sort(key=lambda pair: (pair[1].relative_to(directory).as_posix() in failures, pair[1].as_posix()))
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=int(os.getenv("ARTICLE_MEDIA_BACKFILL_LIMIT", "25")))
    parser.add_argument("--workers", type=int, default=int(os.getenv("ARTICLE_MEDIA_BACKFILL_WORKERS", "4")))
    parser.add_argument("--year", type=int, default=int(os.getenv("ARTICLE_MEDIA_BACKFILL_YEAR", "0")))
    parser.add_argument("--domain", default=os.getenv("ARTICLE_MEDIA_BACKFILL_DOMAIN", ""))
    parser.add_argument("--recheck", action="store_true")
    parser.add_argument("--all-snapshots", action="store_true", help="Repair all archive years, including files absent from the news index")
    parser.add_argument("--retry-days", type=int, default=7)
    parser.add_argument("--layout", action="store_true", help="Restore media positions without rewriting archived text or media URLs")
    args = parser.parse_args()
    if args.layout:
        args.all_snapshots = True
    directory = ROOT / "data" / "articles"
    state_path = ROOT / "data" / ("media-layout-state.json" if args.layout else "media-repair-state.json")
    version_key = "mediaLayoutVersion" if args.layout else "mediaFormatVersion"
    version = LAYOUT_VERSION if args.layout else MEDIA_FORMAT_VERSION
    state = {"failures": {}}
    if args.all_snapshots:
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get(version_key) != version:
            state = {"failures": {}}
        candidates = archive_candidates(directory, state, max(0, args.retry_days), args.year, args.domain, args.layout)
    else:
        candidates = []
        for item in load_news(NEWS_FILE).get("items", []):
            path = snapshot_path(item)
            try:
                snapshot = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if snapshot.get("contentKind") not in READABLE_KINDS:
                continue
            if args.year and str(item.get("publishedAt", ""))[:4] != str(args.year):
                continue
            if not matches_domain(item, snapshot, args.domain):
                continue
            needs_media = (snapshot.get("mediaFormatVersion", 0) < MEDIA_FORMAT_VERSION or has_problematic_images(snapshot)) if args.recheck else not (snapshot.get("images") or snapshot.get("mediaCheckedAt"))
            if needs_media:
                candidates.append((item, path))
    eligible = len(candidates)
    candidates = candidates[:max(0, args.limit)]
    counts = {"repaired": 0, "partial": 0, "failed": 0, "skipped": 0}
    if candidates:
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, len(candidates)))) as pool:
            futures = {pool.submit(layout_backfill_item if args.layout else media_backfill_item, item, args.recheck or args.all_snapshots, path): (item, path) for item, path in candidates}
            for future in as_completed(futures):
                item, path = futures[future]
                result = future.result()
                key = path.relative_to(directory).as_posix()
                if result.startswith("partial:"):
                    counts["partial"] += 1
                    state.setdefault("failures", {})[key] = {"attemptedAt": utc_now(), "error": result}
                elif result.startswith("error:"):
                    counts["failed"] += 1
                    state.setdefault("failures", {})[key] = {"attemptedAt": utc_now(), "error": result}
                elif result in {"skip", "missing"}:
                    counts["skipped"] += 1
                else:
                    counts["repaired"] += 1
                    state.setdefault("failures", {}).pop(key, None)
                print(f"[media:{result}] {item.get('id', '')} {item.get('title', '')[:80]}", flush=True)
    report = {"checkedAt": utc_now(), "eligibleBeforeBatch": eligible, "attempted": len(candidates), **counts, "remainingEligible": eligible - len(candidates)}
    if args.all_snapshots and (candidates or not state_path.exists()):
        state.update({version_key: version, "lastBatch": report})
        state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(state_path)
    print(f"[media] {json.dumps(report, ensure_ascii=False)}")
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write("\n### Historical media repair\n\n```json\n" + json.dumps(report, indent=2) + "\n```\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
