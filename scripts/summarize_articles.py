#!/usr/bin/env python3
"""Add concise Chinese summaries to successfully archived article snapshots."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from article_store import ARTICLES_DIR, ROOT

NEWS_FILE = ROOT / "data" / "news.json"
TRANSLATE_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
ARCHIVED_KINDS = {"community", "feed", "page", "reader"}
CATEGORY_LABELS = {
    "industry": "行业落地",
    "agent": "Agent 技术",
    "release": "模型发布",
    "benchmark": "评测榜单",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def extract_summary_text(snapshot: dict, item: dict) -> str:
    title = re.sub(r"\s+", " ", snapshot.get("title") or item.get("title") or "").strip()
    body = snapshot.get("body", "")
    candidates = []
    for paragraph in re.split(r"\n\s*\n", body):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if len(paragraph) < 40 or paragraph.casefold().startswith(("posted ", "sponsor ")):
            continue
        for sentence in re.split(r"(?<=[.!?。！？])\s+", paragraph):
            sentence = sentence.strip()
            if len(sentence) >= 35:
                candidates.append(sentence)
            if len(candidates) >= 3:
                break
        if len(candidates) >= 3:
            break
    selected = " ".join(candidates)[:900].strip()
    return f"{title}。{selected}" if selected and title not in selected else (selected or title)


def is_chinese(value: str) -> bool:
    letters = re.findall(r"[A-Za-z\u4e00-\u9fff]", value)
    chinese = re.findall(r"[\u4e00-\u9fff]", value)
    return bool(letters) and len(chinese) / len(letters) >= 0.35


def translate_to_chinese(value: str, attempts: int = 1) -> str:
    if is_chinese(value):
        return value
    query = urllib.parse.urlencode({
        "client": "gtx",
        "sl": "auto",
        "tl": "zh-CN",
        "dt": "t",
        "q": value,
    })
    request = urllib.request.Request(
        f"{TRANSLATE_ENDPOINT}?{query}",
        headers={"User-Agent": "LLM-Pulse/1.0"},
    )
    for attempt in range(max(1, attempts)):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt + 1 >= max(1, attempts):
                raise
            retry_after = error.headers.get("Retry-After") if error.headers else None
            time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else 5 * (attempt + 1))
    translated = "".join(part[0] for part in payload[0] if part and part[0])
    return re.sub(r"\s+", " ", translated).strip()


def normalize_summary(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    sentences = re.split(r"(?<=[。！？])", value)
    summary = "".join(sentences[:3]).strip()
    return (summary or value)[:260].rstrip("，,；;：:")


def metadata_summary(item: dict) -> str:
    source = item.get("source") or "公开信息源"
    title = item.get("title") or "该文章"
    category = CATEGORY_LABELS.get(item.get("category"), "大模型行业动态")
    tags = "、".join((item.get("tags") or [])[:3])
    topics = f"，重点涉及{tags}" if tags else ""
    return f"本文来自{source}，围绕“{title}”介绍{category}方面的最新进展{topics}。下方为系统保存的原文正文。"[:260]


def load_candidates(limit: int) -> list[tuple[Path, dict, dict]]:
    news = json.loads(NEWS_FILE.read_text(encoding="utf-8"))
    items = {item["id"]: item for item in news.get("items", [])}
    candidates = []
    for path in ARTICLES_DIR.glob("*.json"):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if snapshot.get("contentKind") not in ARCHIVED_KINDS or snapshot.get("summaryZh"):
            continue
        item = items.get(snapshot.get("id"))
        if item:
            candidates.append((path, snapshot, item))
    candidates.sort(key=lambda entry: entry[1].get("fetchedAt", ""), reverse=True)
    return candidates[:max(0, limit)]


def write_summary(path: Path, snapshot: dict, summary: str) -> None:
    snapshot["summaryZh"] = summary
    snapshot["summaryGeneratedAt"] = utc_now()
    snapshot["summaryModel"] = "extractive-translate-v1"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=int(os.getenv("ARTICLE_SUMMARY_LIMIT", "60")))
    parser.add_argument("--delay", type=float, default=float(os.getenv("ARTICLE_SUMMARY_DELAY", "1.5")))
    args = parser.parse_args()
    candidates = load_candidates(args.limit)
    if not candidates:
        print("[summaries] no archived articles need summaries")
        return 0
    completed = 0
    for path, snapshot, item in candidates:
        try:
            source_text = extract_summary_text(snapshot, item)
            summary = normalize_summary(translate_to_chinese(source_text))
            if len(summary) < 20 or not re.search(r"[\u4e00-\u9fff]", summary):
                raise ValueError("Chinese summary not produced")
        except Exception as error:
            print(f"[summary:warn] {snapshot.get('id')}: {type(error).__name__}: {error}")
            summary = metadata_summary(item)
        write_summary(path, snapshot, summary)
        completed += 1
        if args.delay > 0:
            time.sleep(args.delay)
    print(f"[summaries] wrote {completed}/{len(candidates)} Chinese summaries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
