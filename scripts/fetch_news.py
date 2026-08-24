#!/usr/bin/env python3
"""Fetch, classify, score and archive LLM news without third-party packages."""

from __future__ import annotations

import hashlib
import html
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "news.json"
USER_AGENT = "LLM-Pulse/1.0 (+https://github.com/wuhy80/llm-news-tracker; by /u/wuhy80)"

SOURCES = [
    {"name": "OpenAI", "url": "https://openai.com/news/rss.xml", "domain": "openai.com", "official": True},
    {"name": "Google AI", "url": "https://blog.google/technology/ai/rss/", "domain": "blog.google", "official": True},
    {"name": "Hugging Face", "url": "https://huggingface.co/blog/feed.xml", "domain": "huggingface.co", "official": True},
    {"name": "Microsoft AI", "url": "https://blogs.microsoft.com/ai/feed/", "domain": "microsoft.com", "official": True},
    {"name": "NVIDIA AI", "url": "https://blogs.nvidia.com/blog/category/deep-learning/feed/", "domain": "nvidia.com", "official": True},
    {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/", "domain": "venturebeat.com", "official": False},
    {
        "name": "Reddit · LocalLLaMA",
        "url": "https://www.reddit.com/r/LocalLLaMA/new/.rss?limit=100",
        "domain": "reddit.com",
        "official": False,
    },
    {
        "name": "LINUX DO · 444",
        "url": "https://linux.do/tag/444-tag.rss",
        "fallback_urls": ["https://www.bing.com/news/search?q=site%3Alinux.do%2Ft%2F+%28%22%E5%A4%A7%E6%A8%A1%E5%9E%8B%22+OR+%22Agent%22+OR+%22%E6%A8%A1%E5%9E%8B%E5%8F%91%E5%B8%83%22+OR+%22%E8%AF%84%E6%B5%8B%22%29&format=rss"],
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
        "domain": "linux.do",
        "official": False,
    },
    {
        "name": "全球大模型动态",
        "url": "https://news.google.com/rss/search?q=%22large+language+model%22+OR+LLM+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen",
        "domain": "news.google.com", "official": False,
    },
    {
        "name": "Agent 技术动态",
        "url": "https://news.google.com/rss/search?q=%22AI+agent%22+OR+%22agentic+AI%22+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen",
        "domain": "news.google.com", "official": False,
        "hint": "agent",
    },
    {
        "name": "模型发布与评测",
        "url": "https://news.google.com/rss/search?q=%22AI+model%22+release+OR+benchmark+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen",
        "domain": "news.google.com", "official": False,
        "hint": "release",
    },
    {
        "name": "中文大模型动态",
        "url": "https://news.google.com/rss/search?q=%E5%A4%A7%E6%A8%A1%E5%9E%8B+OR+AI%E6%99%BA%E8%83%BD%E4%BD%93+when%3A7d&hl=zh-CN&gl=CN&ceid=CN%3Azh-Hans",
        "domain": "news.google.com", "official": False,
    },
]

KEYWORDS = {
    "agent": ("agent", "agentic", "multi-agent", "智能体", "mcp", "tool use", "computer use", "workflow"),
    "benchmark": ("benchmark", "leaderboard", "evaluation", "evals", "arena", "基准", "评测", "榜单", "score"),
    "release": ("release", "launch", "introducing", "announce", "unveil", "open source", "weights", "发布", "开源", "模型上线"),
    "industry": ("enterprise", "customer", "deploy", "production", "business", "industry", "adoption", "应用", "落地", "企业"),
}
TAG_PATTERNS = {
    "OpenAI": r"\bopenai\b", "GPT": r"\bgpt(?:-?\d[\w.-]*)?\b", "Anthropic": r"\banthropic\b",
    "Claude": r"\bclaude\b", "Google": r"\bgoogle\b", "Gemini": r"\bgemini\b",
    "Meta": r"\bmeta\b", "Llama": r"\bllama\b", "Microsoft": r"\bmicrosoft\b",
    "Qwen": r"\bqwen\b|通义千问", "DeepSeek": r"\bdeepseek\b", "Mistral": r"\bmistral\b",
    "Agent": r"\bagents?\b|智能体", "MCP": r"\bmcp\b|model context protocol",
    "RAG": r"\brag\b|retrieval.augmented", "开源": r"open.source|open weights|开源",
    "多模态": r"multimodal|多模态", "推理": r"reasoning|inference|推理",
    "Benchmark": r"benchmark|leaderboard|arena|评测|榜单",
}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(value: str) -> str:
    parser = TextExtractor()
    try:
        parser.feed(html.unescape(value or ""))
        text = " ".join(parser.parts)
    except Exception:
        text = value or ""
    return re.sub(r"\s+", " ", text).strip()


def find_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in node.iter():
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in names and child.text:
            return child.text.strip()
    return ""


def find_link(node: ET.Element) -> str:
    for child in node.iter():
        if child.tag.rsplit("}", 1)[-1].lower() != "link":
            continue
        href = child.attrib.get("href")
        if href and child.attrib.get("rel", "alternate") == "alternate":
            return href
        if child.text and child.text.strip():
            return child.text.strip()
    return ""


def parse_date(value: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        date = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc)


def fetch(url: str, extra_headers: dict[str, str] | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=12) as response:
        return response.read()


def parse_feed(payload: bytes, source: dict) -> list[dict]:
    root = ET.fromstring(payload)
    nodes = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() in {"item", "entry"}]
    items = []
    for node in nodes:
        title = strip_html(find_text(node, ("title",)))
        url = find_link(node)
        if not title or not url:
            continue
        summary = strip_html(find_text(node, ("description", "summary", "content")))
        published = parse_date(find_text(node, ("pubdate", "published", "updated", "date")))
        source_name = source["name"]
        source_domain = source["domain"]
        embedded_source = find_text(node, ("source",))
        if source["domain"] == "news.google.com" and embedded_source:
            source_name = embedded_source
            if " - " + embedded_source in title:
                title = title.rsplit(" - " + embedded_source, 1)[0].strip()
        items.append({
            "title": title,
            "url": url,
            "summary": summary[:420],
            "published": published,
            "source": source_name,
            "sourceDomain": source_domain,
            "official": source.get("official", False),
            "hint": source.get("hint"),
        })
    return items

def fetch_source(source: dict) -> list[dict]:
    errors = []
    for url in (source["url"], *source.get("fallback_urls", [])):
        try:
            entries = parse_feed(fetch(url, source.get("headers")), source)
            if entries:
                return entries
        except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as error:
            errors.append(f"{url}: {error}")
    if errors:
        raise urllib.error.URLError("; ".join(errors))
    return []



def classify(text: str, hint: str | None = None) -> str:
    lowered = text.casefold()
    scores = {category: sum(1 for keyword in words if keyword in lowered) for category, words in KEYWORDS.items()}
    if hint:
        scores[hint] += 1
    priority = ("agent", "benchmark", "release", "industry")
    best = max(priority, key=lambda category: (scores[category], -priority.index(category)))
    return best if scores[best] else (hint or "industry")


def extract_tags(text: str, category: str) -> list[str]:
    tags = [label for label, pattern in TAG_PATTERNS.items() if re.search(pattern, text, re.IGNORECASE)]
    fallback = {"industry": "行业应用", "agent": "Agent", "release": "模型发布", "benchmark": "Benchmark"}
    if fallback[category] not in tags:
        tags.append(fallback[category])
    return tags[:5]


def normalized_title(value: str) -> str:
    value = re.sub(r"\s+-\s+[^-]{2,40}$", "", value.casefold())
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", value)


def finalize(raw: dict, now: datetime) -> dict:
    combined = f"{raw['title']} {raw['summary']}"
    category = classify(combined, raw.get("hint"))
    tags = extract_tags(combined, category)
    age_hours = max(0, (now - raw["published"]).total_seconds() / 3600)
    recency = max(0, 18 - int(age_hours / 8))
    score = 48 + recency + (18 if raw["official"] else 0) + min(14, (len(tags) - 1) * 4)
    if any(word in combined.casefold() for word in ("release", "launch", "benchmark", "发布", "开源")):
        score += 6
    score = min(100, score)
    key = normalized_title(raw["title"])
    return {
        "id": hashlib.sha1(key.encode("utf-8")).hexdigest()[:12],
        "title": raw["title"],
        "summary": raw["summary"] or "来自原始信息源的最新动态，点击标题查看完整内容。",
        "url": raw["url"],
        "source": raw["source"],
        "sourceDomain": raw["sourceDomain"],
        "publishedAt": raw["published"].isoformat().replace("+00:00", "Z"),
        "category": category,
        "tags": tags,
        "score": score,
        "signal": "high" if score >= 82 else "medium" if score >= 68 else "normal",
    }


def load_previous() -> list[dict]:
    if not OUTPUT.exists():
        return []
    try:
        return json.loads(OUTPUT.read_text(encoding="utf-8")).get("items", [])
    except (json.JSONDecodeError, OSError):
        return []


def main() -> int:
    now = datetime.now(timezone.utc)
    collected: list[dict] = []
    successful_sources: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(8, len(SOURCES))) as pool:
        futures = {pool.submit(fetch_source, source): source for source in SOURCES}
        for future in as_completed(futures):
            source = futures[future]
            try:
                entries = future.result()
                collected.extend(entries)
                successful_sources.append({"name": source["name"], "url": source["url"], "count": len(entries)})
                print(f"[ok] {source['name']}: {len(entries)}")
            except (urllib.error.URLError, TimeoutError, ET.ParseError, OSError) as error:
                print(f"[warn] {source['name']}: {error}", file=sys.stderr)

    merged = {item.get("id"): item for item in load_previous() if item.get("id")}
    seen_titles: set[str] = set()
    for raw in sorted(collected, key=lambda item: item["published"], reverse=True):
        key = normalized_title(raw["title"])
        if not key or key in seen_titles:
            continue
        seen_titles.add(key)
        item = finalize(raw, now)
        merged[item["id"]] = item

    items = list(merged.values())
    items.sort(key=lambda item: (item.get("publishedAt", ""), item.get("score", 0)), reverse=True)
    if not items:
        print("[error] no news items available", file=sys.stderr)
        return 1

    payload = {
        "generatedAt": now.isoformat().replace("+00:00", "Z"),
        "historyPolicy": "append-only",
        "sources": successful_sources,
        "items": items,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[done] wrote {len(items)} items to {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
