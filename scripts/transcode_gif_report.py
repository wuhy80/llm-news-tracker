#!/usr/bin/env python3
"""Measure what the stored GIFs would cost as MP4, without changing anything.

GIF is a 1987 format: a 256-colour palette and no inter-frame compression, so a
single animation routinely costs several megabytes. The same animation as
H.264/MP4 is typically five to twenty times smaller, and the archive already
understands ``kind: "video"`` entries with mp4 sources - media-layout.js renders
them as ``<video controls preload="none">``.

This run only reports. Every GIF that a document still references is transcoded
into a temporary directory, the resulting size is recorded, and the temporary
file is discarded. Nothing in the repository is touched, so it is safe to run at
any time; acting on the numbers is a separate change.
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
from dedupe_article_media import MEDIA_PREFIX, collect_files, collect_referenced

FFMPEG = os.getenv("FFMPEG_BINARY", "ffmpeg")
TRANSCODE_TIMEOUT = int(os.getenv("GIF_TRANSCODE_TIMEOUT", "300"))


def transcode_mp4(source: Path, destination: Path) -> None:
    """Transcode one animation to H.264/MP4, even dimensions, no audio track."""
    subprocess.run(
        [
            FFMPEG, "-y", "-loglevel", "error", "-i", str(source),
            "-an", "-movflags", "+faststart",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
            "-pix_fmt", "yuv420p",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            str(destination),
        ],
        check=True,
        timeout=TRANSCODE_TIMEOUT,
    )


def build_report(media_dir: Path, referenced: set[str], files: dict[str, int],
                 transcode=transcode_mp4, limit: int = 0) -> dict:
    """Transcode each referenced GIF in turn and record what it costs as MP4."""
    gifs = sorted((rel for rel in referenced & set(files) if rel.lower().endswith(".gif")),
                  key=lambda rel: -files[rel])
    if limit:
        gifs = gifs[:limit]

    rows: list[dict] = []
    failed: list[dict] = []
    with tempfile.TemporaryDirectory() as workspace:
        for relative in gifs:
            destination = Path(workspace) / (Path(relative).stem + ".mp4")
            try:
                transcode(media_dir / relative, destination)
                after = destination.stat().st_size
            except Exception as error:  # report it; never fail the whole run
                failed.append({"path": relative, "error": type(error).__name__})
                continue
            rows.append({"path": relative, "gifBytes": files[relative], "mp4Bytes": after})

    before = sum(row["gifBytes"] for row in rows)
    after = sum(row["mp4Bytes"] for row in rows)
    saved = before - after
    return {
        "referencedGifs": len(gifs),
        "transcoded": len(rows),
        "failed": failed,
        "gifBytes": before,
        "mp4Bytes": after,
        "savingsBytes": saved,
        "savingsPercent": round(100 * saved / before, 1) if before else 0.0,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="only the N largest GIFs (0 = all)")
    parser.add_argument("--report-limit", type=int, default=15)
    args = parser.parse_args(argv)

    if not MEDIA_DIR.is_dir():
        print(f"[gif] no media directory at {MEDIA_DIR}; nothing to do")
        return 0
    if not shutil.which(FFMPEG):
        print(f"[gif] {FFMPEG} is not on PATH; install ffmpeg or set FFMPEG_BINARY")
        return 2

    referenced, _ = collect_referenced(DATA_DIR, MEDIA_DIR)
    files = collect_files(MEDIA_DIR)
    report = build_report(MEDIA_DIR, referenced, files, limit=args.limit)
    rows = report.pop("rows")
    media_bytes = sum(files.values())
    report["mediaBytes"] = media_bytes
    report["savingsShareOfMediaPercent"] = (
        round(100 * report["savingsBytes"] / media_bytes, 1) if media_bytes else 0.0)
    report["mediaBytesAfter"] = media_bytes - report["savingsBytes"]
    print("[gif] " + json.dumps(report, ensure_ascii=False), flush=True)

    for row in sorted(rows, key=lambda item: -(item["gifBytes"] - item["mp4Bytes"]))[:args.report_limit]:
        ratio = row["gifBytes"] / row["mp4Bytes"] if row["mp4Bytes"] else 0
        print(f"[gif] {row['gifBytes']:>9,} -> {row['mp4Bytes']:>8,} B  {ratio:>5.1f}x  "
              f"{MEDIA_PREFIX}{row['path']}", flush=True)
    for failure in report["failed"][:args.report_limit]:
        print(f"[gif] FAILED {MEDIA_PREFIX}{failure['path']}  {failure['error']}", flush=True)

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as stream:
            stream.write("\n### GIF transcode potential\n\n```json\n"
                         + json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n")

    print("[gif] read-only report: the repository was not modified", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
