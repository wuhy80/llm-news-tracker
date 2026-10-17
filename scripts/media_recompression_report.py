#!/usr/bin/env python3
"""Measure what the stored media would cost in a smaller format.

GIF is a 1987 format with a 256-colour palette and no inter-frame compression;
PNG is lossless and therefore enormous for screenshots. Both can be replaced
with formats the site already understands: ``kind: "video"`` entries with mp4
sources render as ``<video>``, and WebP renders in the existing ``<img>`` paths.

This run only reports. Every candidate is converted into a temporary directory,
the resulting size is recorded, and the temporary file is discarded. Nothing in
the repository is touched, so it is safe to run at any time; acting on the
numbers is a separate change.
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
TIMEOUT = int(os.getenv("RECOMPRESS_TIMEOUT", "300"))


def _ffmpeg(args: list[str], source: Path, destination: Path) -> None:
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-i", str(source), *args, str(destination)],
        check=True,
        timeout=TIMEOUT,
    )


def gif_to_mp4(source: Path, destination: Path) -> None:
    """H.264/MP4: even dimensions, no audio, faststart for progressive playback."""
    _ffmpeg([
        "-an", "-movflags", "+faststart",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
        "-pix_fmt", "yuv420p",
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
    ], source, destination)


def to_webp(quality: int | None = None, lossless: bool = False):
    """WebP encoder at a given quality, or lossless."""
    def convert(source: Path, destination: Path) -> None:
        args = ["-c:v", "libwebp", "-compression_level", "6"]
        args += ["-lossless", "1"] if lossless else ["-quality", str(quality)]
        _ffmpeg(args, source, destination)
    return convert


# Ordered so the report reads from the biggest lever to the smallest.
STRATEGIES: dict[str, dict] = {
    "gif-to-mp4": {"extensions": (".gif",), "suffix": ".mp4", "convert": gif_to_mp4},
    "png-to-webp-80": {"extensions": (".png",), "suffix": ".webp", "convert": to_webp(80)},
    "png-to-webp-90": {"extensions": (".png",), "suffix": ".webp", "convert": to_webp(90)},
    "png-to-webp-lossless": {"extensions": (".png",), "suffix": ".webp", "convert": to_webp(lossless=True)},
    "jpeg-to-webp-80": {"extensions": (".jpg", ".jpeg"), "suffix": ".webp", "convert": to_webp(80)},
}


def measure(media_dir: Path, referenced: set[str], files: dict[str, int],
            extensions: tuple[str, ...], suffix: str, convert, limit: int = 0) -> dict:
    """Convert every referenced candidate of these extensions and record the sizes."""
    candidates = sorted((rel for rel in referenced & set(files)
                         if rel.lower().endswith(extensions)),
                        key=lambda rel: -files[rel])
    if limit:
        candidates = candidates[:limit]

    rows: list[dict] = []
    failed: list[dict] = []
    with tempfile.TemporaryDirectory() as workspace:
        for relative in candidates:
            destination = Path(workspace) / (Path(relative).stem + suffix)
            try:
                convert(media_dir / relative, destination)
                after = destination.stat().st_size
            except Exception as error:  # report it; never fail the whole run
                failed.append({"path": relative, "error": type(error).__name__})
                continue
            rows.append({"path": relative, "beforeBytes": files[relative], "afterBytes": after})

    before = sum(row["beforeBytes"] for row in rows)
    after = sum(row["afterBytes"] for row in rows)
    saved = before - after
    return {
        "candidates": len(candidates),
        "converted": len(rows),
        "failed": failed,
        "beforeBytes": before,
        "afterBytes": after,
        "savingsBytes": saved,
        "savingsPercent": round(100 * saved / before, 1) if before else 0.0,
        "rows": rows,
    }


def build_report(media_dir: Path, referenced: set[str], files: dict[str, int],
                 strategies: dict[str, dict] | None = None, limit: int = 0) -> dict:
    strategies = STRATEGIES if strategies is None else strategies
    media_bytes = sum(files.values())
    results = {}
    for name, strategy in strategies.items():
        result = measure(media_dir, referenced, files, strategy["extensions"],
                         strategy["suffix"], strategy["convert"], limit=limit)
        result["shareOfMediaPercent"] = (
            round(100 * result["savingsBytes"] / media_bytes, 1) if media_bytes else 0.0)
        results[name] = result
    return {"mediaBytes": media_bytes, "strategies": results}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="only the N largest files per strategy (0 = all)")
    parser.add_argument("--report-limit", type=int, default=5, help="rows to list per strategy")
    parser.add_argument("--strategy", default="", help="restrict to one strategy name")
    args = parser.parse_args(argv)

    if not MEDIA_DIR.is_dir():
        print(f"[recompress] no media directory at {MEDIA_DIR}; nothing to do")
        return 0
    if not shutil.which(FFMPEG):
        print(f"[recompress] {FFMPEG} is not on PATH; install ffmpeg or set FFMPEG_BINARY")
        return 2

    strategies = STRATEGIES
    if args.strategy:
        strategies = {name: STRATEGIES[name] for name in STRATEGIES if name == args.strategy}
        if not strategies:
            print(f"[recompress] unknown strategy {args.strategy!r}; known: {', '.join(STRATEGIES)}")
            return 2

    referenced, _ = collect_referenced(DATA_DIR, MEDIA_DIR)
    files = collect_files(MEDIA_DIR)
    report = build_report(MEDIA_DIR, referenced, files, strategies=strategies, limit=args.limit)
    media_bytes = report["mediaBytes"]
    print(f"[recompress] media on disk: {media_bytes:,} bytes", flush=True)
    print(f"[recompress] {'strategy':<24}{'files':>7}{'before MB':>11}{'after MB':>10}"
          f"{'saved MB':>10}{'saved %':>9}{'of media':>10}", flush=True)

    compact = {}
    for name, result in report["strategies"].items():
        compact[name] = {key: value for key, value in result.items() if key != "rows"}
        print(f"[recompress] {name:<24}{result['converted']:>7}"
              f"{result['beforeBytes']/1e6:>11.1f}{result['afterBytes']/1e6:>10.1f}"
              f"{result['savingsBytes']/1e6:>10.1f}{result['savingsPercent']:>8.1f}%"
              f"{result['shareOfMediaPercent']:>9.1f}%", flush=True)
        for row in sorted(result["rows"], key=lambda item: -(item["beforeBytes"] - item["afterBytes"]))[:args.report_limit]:
            ratio = row["beforeBytes"] / row["afterBytes"] if row["afterBytes"] else 0
            print(f"[recompress]   {row['beforeBytes']:>9,} -> {row['afterBytes']:>8,} B "
                  f"{ratio:>5.1f}x  {MEDIA_PREFIX}{row['path']}", flush=True)
        for failure in result["failed"][:args.report_limit]:
            print(f"[recompress]   FAILED {MEDIA_PREFIX}{failure['path']}  {failure['error']}", flush=True)

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as stream:
            stream.write("\n### Media recompression potential\n\n```json\n"
                         + json.dumps(compact, ensure_ascii=False, indent=2) + "\n```\n")
    print("[recompress] read-only report: the repository was not modified", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
