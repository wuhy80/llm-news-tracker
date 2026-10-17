#!/usr/bin/env python3
"""Probe which larger variant of a Reddit preview image is actually reachable.

Reddit serves images from ``preview.redd.it`` behind a signed, size-parameterised
URL. The archive picked some of them at 140px wide, which the reader then
upscaled, so the only way to make those images large *and* sharp is to fetch a
bigger variant from the source. This asks the source which alternatives work, so
that fix can be written against evidence instead of a guess.

Read-only: it downloads into a temporary file, reports, and deletes nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
MEDIA_DIR = DATA_DIR / "article-media"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dedupe_article_media import collect_referenced

PREVIEW_HOSTS = {"preview.redd.it", "external-preview.redd.it"}
USER_AGENT = "LLM-Pulse/1.0 (+https://github.com/wuhy80/llm-news-tracker)"
TIMEOUT = 25
SMALL_WIDTH = 400


def collect_targets(data_dir: Path, media_dir: Path) -> dict[str, dict]:
    """Smallest stored Reddit preview URL per image name, and how many snapshots use it."""
    _referenced, documents = collect_referenced(data_dir, media_dir)
    targets: dict[str, dict] = {}
    for path in documents:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        seen: set[str] = set()
        for match in re.finditer(r"https://(preview|external-preview)\.redd\.it/([^\"\\\s]+)", text):
            url = "https://" + match.group(1) + ".redd.it/" + match.group(2)
            parsed = urllib.parse.urlparse(url)
            width = int(urllib.parse.parse_qs(parsed.query).get("width", ["0"])[0] or 0)
            name = Path(parsed.path).name
            record = targets.setdefault(name, {"name": name, "host": parsed.hostname,
                                               "width": width, "url": url, "uses": 0})
            # The same URL appears twice per snapshot (src and originalUrl), so
            # count the snapshots that use it rather than raw occurrences.
            if name not in seen:
                record["uses"] += 1
                seen.add(name)
            if width and width < record["width"]:
                record.update({"width": width, "url": url})
    return targets


def candidates(record: dict) -> list[tuple[str, str]]:
    parsed = urllib.parse.urlparse(record["url"])
    query = urllib.parse.parse_qs(parsed.query)
    base = f"https://{parsed.hostname}{parsed.path}"
    signature = query.get("s", [""])[0]
    out: list[tuple[str, str]] = []
    if parsed.hostname == "preview.redd.it":
        out.append(("original host i.redd.it", f"https://i.redd.it{parsed.path}"))
    out.append(("params stripped", base))
    if signature:
        out.append(("width=1080 with signature",
                    f"{base}?width=1080&auto=webp&s={urllib.parse.quote(signature)}"))
    out.append(("width=1080 without signature", f"{base}?width=1080&auto=webp"))
    return out


def dimensions(payload: bytes) -> str:
    if not shutil.which("ffprobe"):
        return "?"
    handle, path = tempfile.mkstemp()
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30)
        return result.stdout.strip() or "?"
    except Exception:
        return "?"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def probe(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = response.read(4_000_000)
            return {"status": response.status, "bytes": len(payload),
                    "dimensions": dimensions(payload)}
    except urllib.error.HTTPError as error:
        return {"status": error.code, "bytes": 0, "dimensions": "-"}
    except Exception as error:
        return {"status": 0, "bytes": 0, "dimensions": "-", "error": type(error).__name__}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=8, help="how many images to probe")
    args = parser.parse_args(argv)

    if not MEDIA_DIR.is_dir():
        print(f"[reddit] no media directory at {MEDIA_DIR}; nothing to do")
        return 0

    targets = collect_targets(DATA_DIR, MEDIA_DIR)
    small = sorted((r for r in targets.values() if r["width"] and r["width"] < SMALL_WIDTH),
                   key=lambda r: r["width"])
    print(f"[reddit] distinct preview images referenced: {len(targets)}")
    print(f"[reddit] of them narrower than {SMALL_WIDTH}px : {len(small)}")
    report = {"distinct": len(targets), "small": len(small), "probes": []}

    for record in small[:args.limit]:
        print(f"\n[reddit] {record['name']}  stored width={record['width']}  uses={record['uses']}")
        for label, url in candidates(record):
            result = probe(url)
            print(f"[reddit]   {label:<28} status={result['status']:<4} "
                  f"bytes={result['bytes']:>9,}  size={result['dimensions']}")
            report["probes"].append({"name": record["name"], "label": label, "url": url, **result})

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as stream:
            stream.write("\n### Reddit preview variants\n\n```json\n"
                         + json.dumps(report, ensure_ascii=False, indent=2) + "\n```\n")
    print("\n[reddit] read-only probe: nothing was modified", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
