#!/usr/bin/env python3
"""Measure what the archive costs and how long the limits last.

Renders a Markdown block between two markers in README.md, so the repository
front page answers "how close am I to the limit, and when does it run out", and
appends every measurement to data/resource-usage.jsonl so the next run can
report a real growth rate instead of a guess.

Two numbers come from the GitHub API rather than the working tree, because they
are the ones GitHub actually enforces: the published Pages artifact and the
repository size as GitHub counts it.

Read-only apart from README.md and data/resource-usage.jsonl; pass --write to
change those, otherwise it only prints.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
HISTORY = ROOT / "data" / "resource-usage.jsonl"
DATA = ROOT / "data"

START = "<!-- resource-usage:start -->"
END = "<!-- resource-usage:end -->"

# GitHub Pages refuses to publish a site above 1 GiB, and GitHub recommends
# keeping a repository under 1 GiB (it warns above that and blocks at 5 GiB).
PAGES_LIMIT_BYTES = 1024 ** 3
REPO_LIMIT_BYTES = 1024 ** 3
REPO_HARD_LIMIT_BYTES = 5 * 1024 ** 3

TREES = {
    "mediaBytes": DATA / "article-media",
    "articleBytes": DATA / "articles",
    "newsBytes": DATA / "news",
    "translationBytes": DATA / "translations",
    "indexBytes": DATA / "article-index",
}


def tree_size(path: Path) -> tuple[int, int]:
    """Total bytes and file count under a directory."""
    total = 0
    files = 0
    if not path.exists():
        return 0, 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                continue
            files += 1
    return total, files


def github_json(path: str) -> dict | None:
    """GET an api.github.com path when a token and repository are available."""
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        return None
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "LLM-Pulse/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def measure(now: datetime) -> dict:
    record = {"measuredAt": now.isoformat().replace("+00:00", "Z")}
    tree_total = 0
    for key, path in TREES.items():
        size, files = tree_size(path)
        record[key] = size
        record[key.replace("Bytes", "Files")] = files
        tree_total += size
    record["treeBytes"] = tree_total

    manifest = DATA / "news.json"
    try:
        record["items"] = int(json.loads(manifest.read_text(encoding="utf-8")).get("itemCount") or 0)
    except (OSError, ValueError, TypeError):
        record["items"] = 0

    info = github_json("")
    record["repoBytes"] = int(info["size"]) * 1024 if info and info.get("size") else None

    artifacts = github_json("/actions/artifacts?name=github-pages&per_page=1")
    items = (artifacts or {}).get("artifacts") or []
    record["pagesArtifactBytes"] = int(items[0]["size_in_bytes"]) if items else None
    return record


def load_history() -> list[dict]:
    if not HISTORY.exists():
        return []
    records = []
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("measuredAt"):
            records.append(record)
    return records


def parse_moment(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def growth_per_day(history: list[dict], key: str, now: datetime, minimum_days: int = 7) -> float | None:
    """Bytes per day, measured between the newest sample and one at least a week older.

    A shorter window reports noise rather than a trend, so a single sample or a
    week of samples is not enough to answer the question the block asks.
    """
    points = []
    for record in history:
        moment = parse_moment(record.get("measuredAt", ""))
        value = record.get(key)
        if moment is None or not isinstance(value, (int, float)):
            continue
        points.append((moment, float(value)))
    if len(points) < 2:
        return None
    points.sort(key=lambda point: point[0])
    newest_at, newest = points[-1]
    for moment, value in points:
        days = (newest_at - moment).total_seconds() / 86400
        if days >= minimum_days:
            return (newest - value) / days
    return None


def human_bytes(value: float | None) -> str:
    if value is None:
        return "—"
    for unit, scale in (("GiB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if abs(value) >= scale:
            return f"{value / scale:,.1f} {unit}"
    return f"{value:,.0f} B"


def human_runway(current: float | None, limit: float, growth: float | None) -> str:
    if current is None:
        return "—"
    if current >= limit:
        return "**已超出**"
    # Unknown and zero are different answers: one means "not enough history yet",
    # the other means the number is genuinely flat.
    if growth is None:
        return "样本不足"
    if growth <= 0:
        return "增长为 0 或下降"
    days = (limit - current) / growth
    if days >= 60:
        return f"约 {days / 30.4:,.1f} 个月"
    return f"约 {days:,.0f} 天"


def render(record: dict, history: list[dict], now: datetime) -> str:
    pages_growth = growth_per_day(history, "pagesArtifactBytes", now)
    repo_growth = growth_per_day(history, "repoBytes", now)
    pages = record.get("pagesArtifactBytes")
    repo = record.get("repoBytes")

    rows = [
        ("GitHub Pages 站点", human_bytes(pages),
         f"{human_bytes(PAGES_LIMIT_BYTES)}",
         human_bytes(PAGES_LIMIT_BYTES - pages) if pages is not None else "—",
         human_runway(pages, PAGES_LIMIT_BYTES, pages_growth)),
        ("仓库体积", human_bytes(repo),
         f"{human_bytes(REPO_LIMIT_BYTES)}（建议）",
         human_bytes(REPO_LIMIT_BYTES - repo) if repo is not None else "—",
         human_runway(repo, REPO_LIMIT_BYTES, repo_growth)),
        ("媒体文件", f"{human_bytes(record.get('mediaBytes'))}（{record.get('mediaFiles', 0):,} 个）",
         "—", "—", "—"),
        ("文章记录", f"{record.get('items', 0):,} 篇（{human_bytes(record.get('articleBytes'))}）",
         "—", "—", "—"),
    ]

    lines = [
        START,
        f"_自动更新：{now.strftime('%Y-%m-%d %H:%M')} UTC（每周一，由 `resource-usage.yml` 写入）_",
        "",
        "| 资源 | 当前 | 上限 | 余量 | 按近期增速预计可用 |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [f"| {name} | {current} | {limit} | {left} | {runway} |" for name, current, limit, left, runway in rows]
    lines += [
        "",
        "近 14 天增速：站点 "
        + (f"{human_bytes(pages_growth)}/天" if pages_growth else "样本不足")
        + "，仓库 "
        + (f"{human_bytes(repo_growth)}/天" if repo_growth else "样本不足")
        + f"。仓库超过 1 GiB 后 GitHub 会提示性能下降，硬性上限为 {human_bytes(REPO_HARD_LIMIT_BYTES)}；"
        "`.github/workflows/rewrite-history.yml` 每季度自动清理历史中的失效媒体。",
        END,
    ]
    return "\n".join(lines)


def replace_block(text: str, block: str) -> str:
    start = text.find(START)
    end = text.find(END)
    if start >= 0 and end > start:
        return text[:start] + block + text[end + len(END):]
    return text.rstrip("\n") + "\n\n" + block + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="update README.md and append to the history file")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    history = load_history()
    record = measure(now)
    block = render(record, history, now)

    summary = {key: value for key, value in record.items() if key != "measuredAt"}
    print("[usage] " + json.dumps(summary, ensure_ascii=False), flush=True)
    print(block, flush=True)

    if not args.write:
        print("[usage] dry run: nothing was written", flush=True)
        return 0

    updated = replace_block(README.read_text(encoding="utf-8"), block)
    README.write_text(updated, encoding="utf-8")
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print("[usage] wrote README.md and appended to data/resource-usage.jsonl", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
