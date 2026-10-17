#!/usr/bin/env python3
"""Recompress stored screenshots to WebP and repoint every reference at them.

PNG is lossless, which makes it the worst possible choice for a screenshot: the
measured archive holds 113 MB of PNG across 636 files, and WebP at quality 90
renders the same pixels in 45 MB. JPEG shrinks too, though far less.

This is a lossy, one-way change to archived images, so it is a deliberate act:
the default run only reports, and a file is left alone unless the converted copy
is actually smaller. The ``--quality`` flag is the fidelity dial.

Rewrites are byte-level substitutions of the quoted path, so line endings, key
order and every other byte of a stored document survive untouched. Deletion only
affects the working tree; git history keeps the original bytes.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
MEDIA_DIR = DATA_DIR / "article-media"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dedupe_article_media import (MEDIA_PREFIX, collect_files, collect_referenced,
                                  rewrite_documents)

FFMPEG = os.getenv("FFMPEG_BINARY", "ffmpeg")
TIMEOUT = int(os.getenv("RECOMPRESS_TIMEOUT", "300"))
DEFAULT_QUALITY = int(os.getenv("RECOMPRESS_QUALITY", "90"))
SOURCE_EXTENSIONS = (".png", ".jpg", ".jpeg")


def to_webp(source: Path, destination: Path, quality: int) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", str(source),
         "-c:v", "libwebp", "-quality", str(quality), "-compression_level", "6",
         str(destination)],
        check=True,
        timeout=TIMEOUT,
    )


def candidates(referenced: set[str], files: dict[str, int]) -> list[str]:
    return sorted((rel for rel in referenced & set(files)
                   if rel.lower().endswith(SOURCE_EXTENSIONS)),
                  key=lambda rel: -files[rel])


def recompress_one(media_dir: Path, relative: str, quality: int, commit: bool,
                   workspace: Path, claimed: set[str]) -> dict:
    """Convert one file. Writes only when commit is set and the copy is smaller."""
    source = media_dir / relative
    before = source.stat().st_size
    # as_posix(), not str(): on Windows str(Path(...)) yields backslashes, which
    # would be written into the stored documents and break every path.
    new_relative = Path(relative).with_suffix(".webp").as_posix()
    if new_relative == relative or new_relative in claimed:
        return {"relative": relative, "status": "collision", "beforeBytes": before}
    destination = media_dir / new_relative
    if destination.exists():
        return {"relative": relative, "status": "collision", "beforeBytes": before}

    staged = workspace / Path(new_relative).name
    to_webp(source, staged, quality)
    after = staged.stat().st_size
    if after >= before:
        return {"relative": relative, "status": "not_smaller",
                "beforeBytes": before, "afterBytes": after}

    if commit:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(staged), str(destination))
        source.unlink()
    claimed.add(new_relative)
    return {"relative": relative, "status": "converted", "newRelative": new_relative,
            "beforeBytes": before, "afterBytes": after}


def run(media_dir: Path, data_dir: Path, quality: int, commit: bool, limit: int = 0) -> dict:
    referenced, documents = collect_referenced(data_dir, media_dir)
    files = collect_files(media_dir)
    todo = candidates(referenced, files)
    if limit:
        todo = todo[:limit]

    rows: list[dict] = []
    failed: list[dict] = []
    mapping: dict[str, str] = {}
    claimed: set[str] = set()
    with tempfile.TemporaryDirectory() as workspace:
        for relative in todo:
            try:
                row = recompress_one(media_dir, relative, quality, commit,
                                     Path(workspace), claimed)
            except Exception as error:  # report it; never fail the whole run
                failed.append({"path": relative, "error": type(error).__name__})
                continue
            rows.append(row)
            if row["status"] == "converted":
                mapping[relative] = row["newRelative"]

    rewritten = 0
    broken: list[str] = []
    if commit and mapping:
        rewritten = rewrite_documents(documents, mapping)
        directories = sorted((p for p in media_dir.rglob("*") if p.is_dir()),
                             key=lambda p: len(p.parts), reverse=True)
        for directory in directories:
            try:
                if next(directory.iterdir(), None) is None:
                    directory.rmdir()
            except OSError:
                pass
        referenced_after, _ = collect_referenced(data_dir, media_dir)
        broken = sorted(referenced_after - set(collect_files(media_dir)))

    converted = [row for row in rows if row["status"] == "converted"]
    before = sum(row["beforeBytes"] for row in converted)
    after = sum(row["afterBytes"] for row in converted)
    media_bytes = sum(files.values())
    return {
        "quality": quality,
        "applied": commit,
        "candidates": len(todo),
        "converted": len(converted),
        "notSmaller": sum(1 for row in rows if row["status"] == "not_smaller"),
        "collisions": sum(1 for row in rows if row["status"] == "collision"),
        "failed": failed,
        "documentsRewritten": rewritten,
        "beforeBytes": before,
        "afterBytes": after,
        "savingsBytes": before - after,
        "savingsPercent": round(100 * (before - after) / before, 1) if before else 0.0,
        "mediaBytes": media_bytes,
        "shareOfMediaPercent": round(100 * (before - after) / media_bytes, 1) if media_bytes else 0.0,
        "brokenReferences": broken,
        "rows": converted,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the WebP copies and repoint references")
    parser.add_argument("--quality", type=int, default=DEFAULT_QUALITY,
                        help=f"WebP quality 0-100 (default {DEFAULT_QUALITY})")
    parser.add_argument("--limit", type=int, default=0, help="only the N largest files (0 = all)")
    parser.add_argument("--report-limit", type=int, default=10)
    args = parser.parse_args(argv)

    if not MEDIA_DIR.is_dir():
        print(f"[webp] no media directory at {MEDIA_DIR}; nothing to do")
        return 0
    if not shutil.which(FFMPEG):
        print(f"[webp] {FFMPEG} is not on PATH; install ffmpeg or set FFMPEG_BINARY")
        return 2
    if not 0 <= args.quality <= 100:
        print(f"[webp] quality must be between 0 and 100, got {args.quality}")
        return 2

    report = run(MEDIA_DIR, DATA_DIR, args.quality, args.apply, limit=args.limit)
    rows = report.pop("rows")
    print("[webp] " + json.dumps(report, ensure_ascii=False), flush=True)
    for row in sorted(rows, key=lambda item: -(item["beforeBytes"] - item["afterBytes"]))[:args.report_limit]:
        ratio = row["beforeBytes"] / row["afterBytes"] if row["afterBytes"] else 0
        print(f"[webp] {row['beforeBytes']:>9,} -> {row['afterBytes']:>8,} B {ratio:>5.1f}x  "
              f"{MEDIA_PREFIX}{row['relative']}", flush=True)
    for failure in report["failed"][:args.report_limit]:
        print(f"[webp] FAILED {MEDIA_PREFIX}{failure['path']}  {failure['error']}", flush=True)
    for path in report["brokenReferences"][:args.report_limit]:
        print(f"[webp] BROKEN {MEDIA_PREFIX}{path}", flush=True)

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as stream:
            stream.write("\n### WebP recompression\n\n```json\n"
                         + json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n")

    if not args.apply:
        print("[webp] dry run: nothing was changed (pass --apply to convert)", flush=True)
        return 0
    if report["brokenReferences"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
