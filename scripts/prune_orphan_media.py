#!/usr/bin/env python3
"""Remove article media that no longer backs any stored snapshot.

Article media files are named after a hash of their source URL. Earlier
extraction cycles wrote far more of them than the snapshots reference today:
each snapshot keeps at most ``MAX_IMAGES_PER_ARTICLE`` images, so any file that
dropped out of that window was left behind on disk.

Reference detection is deliberately over-inclusive. Every file under ``data/``
except the media directory itself is scanned as text for media paths, so an
unknown field, an index file or a quoted path only ever makes a file *more*
likely to be kept. Deleting is the dangerous direction, so nothing here should
be clever about the schema.

Deletion only touches the working tree. The bytes stay in git history, so a
mistake is recoverable with a checkout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
MEDIA_DIR = DATA_DIR / "article-media"
MEDIA_PREFIX = MEDIA_DIR.relative_to(ROOT).as_posix() + "/"
# Stop only at the closing quote or a backslash. Media paths are stored inside
# JSON strings, so that is the real boundary. Deliberately generous in between:
# a name containing a space or other punctuation must never look orphaned, and
# an over-long match taken from body text costs nothing because it matches no
# file on disk.
REFERENCE = re.compile(re.escape(MEDIA_PREFIX) + r"([^\"\\]+)")


def collect_referenced(data_dir: Path, media_dir: Path) -> tuple[set[str], int]:
    """Return media paths referenced anywhere under data_dir, and files scanned."""
    referenced: set[str] = set()
    scanned = 0
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file() or media_dir in path.parents:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1
        for match in REFERENCE.finditer(text):
            referenced.add(match.group(1))
    return referenced, scanned


def collect_files(media_dir: Path) -> dict[str, int]:
    """Return every stored media file as path-relative-to-the-media-dir -> bytes."""
    files: dict[str, int] = {}
    for path in media_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            files[path.relative_to(media_dir).as_posix()] = path.stat().st_size
        except OSError:
            continue
    return files


def build_report(data_dir: Path, media_dir: Path) -> dict:
    referenced, scanned = collect_referenced(data_dir, media_dir)
    files = collect_files(media_dir)
    orphans = {rel: size for rel, size in files.items() if rel not in referenced}
    total_bytes = sum(files.values())
    orphan_bytes = sum(orphans.values())
    return {
        "documentsScanned": scanned,
        "referencedPaths": len(referenced),
        "mediaFiles": len(files),
        "keptFiles": len(files) - len(orphans),
        "orphanFiles": len(orphans),
        "totalBytes": total_bytes,
        "orphanBytes": orphan_bytes,
        "orphanSharePercent": round(100 * orphan_bytes / total_bytes, 2) if total_bytes else 0.0,
        "danglingReferences": sorted(referenced - set(files)),
        "orphans": orphans,
    }


def apply_prune(media_dir: Path, orphans: dict[str, int]) -> int:
    """Delete the given orphan files, then drop directories left empty."""
    removed = 0
    for relative in sorted(orphans):
        target = media_dir / relative
        try:
            target.unlink()
            removed += 1
        except OSError as error:
            print(f"[prune] could not delete {MEDIA_PREFIX}{relative}: {error}", flush=True)
    directories = sorted((p for p in media_dir.rglob("*") if p.is_dir()),
                         key=lambda p: len(p.parts), reverse=True)
    for directory in directories:
        try:
            if next(directory.iterdir(), None) is None:
                directory.rmdir()
        except OSError:
            pass
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="delete the orphans; without this the run only reports")
    parser.add_argument("--report-limit", type=int, default=20,
                        help="how many of the largest orphans to list")
    args = parser.parse_args(argv)

    if not MEDIA_DIR.is_dir():
        print(f"[prune] no media directory at {MEDIA_DIR}; nothing to do")
        return 0

    report = build_report(DATA_DIR, MEDIA_DIR)
    orphans = report.pop("orphans")
    report["applied"] = bool(args.apply)
    print("[prune] " + json.dumps(report, ensure_ascii=False), flush=True)

    for relative, size in sorted(orphans.items(), key=lambda item: -item[1])[:args.report_limit]:
        print(f"[prune] largest  {size:>10,} B  {MEDIA_PREFIX}{relative}", flush=True)
    for relative in report["danglingReferences"][:args.report_limit]:
        print(f"[prune] MISSING  {MEDIA_PREFIX}{relative}", flush=True)

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as stream:
            stream.write("\n### Orphan article media\n\n```json\n"
                         + json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n")

    if not args.apply:
        print("[prune] dry run: nothing was deleted (pass --apply to delete)", flush=True)
        return 0

    # A scan that finds no references at all means the detector is broken, not
    # that every file is disposable. Refuse rather than wipe the archive.
    if not report["referencedPaths"] and report["mediaFiles"]:
        print("[prune] refusing to delete: no references were found at all", flush=True)
        return 2

    removed = apply_prune(MEDIA_DIR, orphans)
    print(f"[prune] deleted {removed} file(s), {report['orphanBytes']:,} bytes", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
