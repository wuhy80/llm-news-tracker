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

from article_store import resolve_google_news_urls, snapshot_kind, store_feed_snapshot
from news_store import load_news, save_news

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "news.json"
USER_AGENT = "LLM-Pulse/1.0 (+https://github.com/wuhy80/llm-news-tracker; by /u/wuhy80)"
TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref_src", "s_cid",
}

def indexed_source(
    name: str,
    query: str,
    domain: str,
    hint: str,
    official: bool = True,
) -> dict:
    google_query = urllib.parse.quote_plus(f"{query} when:30d")
    bing_query = urllib.parse.quote_plus(query)
    return {
        "name": name,
        "url": f"https://www.bing.com/news/search?q={bing_query}&format=rss",
        "fallback_urls": [f"https://news.google.com/rss/search?q={google_query}&hl=en-US&gl=US&ceid=US%3Aen"],
        "domain": domain,
        "official": official,
        "hint": hint,
        "extract_embedded_source": True,
    }


SOURCES = [
    {"name": "OpenAI", "url": "https://openai.com/news/rss.xml", "domain": "openai.com", "official": True},
    {"name": "Google AI", "url": "https://blog.google/technology/ai/rss/", "domain": "blog.google", "official": True},
    {"name": "Hugging Face", "url": "https://huggingface.co/blog/feed.xml", "domain": "huggingface.co", "official": True},
    {
        "name": "arXiv · 大模型研究",
        "url": "https://export.arxiv.org/api/query?search_query=%28cat%3Acs.CL%20OR%20cat%3Acs.AI%29%20AND%20%28all%3A%22large%20language%20model%22%20OR%20all%3A%22foundation%20model%22%20OR%20all%3A%22language%20model%22%20OR%20all%3ALLM%29&start=0&max_results=100&sortBy=submittedDate&sortOrder=descending",
        "domain": "arxiv.org",
        "official": False,
    },
    {"name": "Microsoft AI", "url": "https://blogs.microsoft.com/ai/feed/", "domain": "microsoft.com", "official": True},
    {"name": "NVIDIA AI", "url": "https://blogs.nvidia.com/blog/category/deep-learning/feed/", "domain": "nvidia.com", "official": True},
    indexed_source("Anthropic News", "site:anthropic.com/news", "anthropic.com", "release"),
    {
        "name": "Claude Blog",
        "url": "https://claude.com/blog",
        "domain": "claude.com",
        "official": True,
        "hint": "agent",
        "format": "html-cards",
        "link_prefix": "/blog/",
    },
    {
        "name": "Claude Code Releases",
        "url": "https://github.com/anthropics/claude-code/releases.atom",
        "domain": "github.com",
        "official": True,
        "hint": "agent",
        "title_prefix": "Claude Code",
    },
    indexed_source("Mistral AI News", "site:mistral.ai/news", "mistral.ai", "release"),
    indexed_source("xAI News", "site:x.ai/news", "x.ai", "release"),
    {
        "name": "Google DeepMind",
        "url": "https://deepmind.google/blog/rss.xml",
        "domain": "deepmind.google",
        "official": True,
    },
    {
        "name": "AWS Machine Learning",
        "url": "https://aws.amazon.com/blogs/machine-learning/feed/",
        "domain": "aws.amazon.com",
        "official": True,
    },
    {
        "name": "GitHub AI & ML",
        "url": "https://github.blog/ai-and-ml/feed/",
        "domain": "github.blog",
        "official": True,
        "hint": "agent",
    },
    {
        "name": "Cloudflare AI",
        "url": "https://blog.cloudflare.com/tag/ai/rss/",
        "domain": "blog.cloudflare.com",
        "official": True,
    },
    indexed_source(
        "LMArena",
        '"LMArena" benchmark OR "Chatbot Arena" benchmark',
        "news.google.com",
        "benchmark",
        False,
    ),
    indexed_source(
        "Artificial Analysis",
        "site:artificialanalysis.ai/articles",
        "artificialanalysis.ai",
        "benchmark",
    ),
    {
        "name": "LangGraph Releases",
        "url": "https://github.com/langchain-ai/langgraph/releases.atom",
        "domain": "github.com",
        "official": True,
        "hint": "agent",
    },
    {
        "name": "MCP Specification Releases",
        "url": "https://github.com/modelcontextprotocol/specification/releases.atom",
        "domain": "github.com",
        "official": True,
        "hint": "agent",
    },
    {
        "name": "Simon Willison",
        "url": "https://simonwillison.net/atom/everything/",
        "domain": "simonwillison.net",
        "official": False,
    },
    {
        "name": "Interconnects",
        "url": "https://www.interconnects.ai/feed",
        "domain": "interconnects.ai",
        "official": False,
    },
    {
        "name": "量子位",
        "url": "https://www.qbitai.com/feed",
        "domain": "qbitai.com",
        "official": False,
    },
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
        "fallback_urls": ["https://news.google.com/rss/search?q=site%3Alinux.do%2Ft%2F+%28%22%E5%A4%A7%E6%A8%A1%E5%9E%8B%22+OR+%22Agent%22+OR+%22%E6%A8%A1%E5%9E%8B%E5%8F%91%E5%B8%83%22+OR+%22%E8%AF%84%E6%B5%8B%22%29+when%3A30d&hl=zh-CN&gl=CN&ceid=CN%3Azh-Hans", "https://www.bing.com/news/search?q=site%3Alinux.do%2Ft%2F+%28%22%E5%A4%A7%E6%A8%A1%E5%9E%8B%22+OR+%22Agent%22+OR+%22%E6%A8%A1%E5%9E%8B%E5%8F%91%E5%B8%83%22+OR+%22%E8%AF%84%E6%B5%8B%22%29&format=rss"],
        "headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
        "domain": "linux.do",
        "official": False,
    },
    {
        "name": "全球大模型动态",
        "url": "https://www.bing.com/news/search?q=%22large+language+model%22+OR+LLM&format=rss",
        "fallback_urls": ["https://news.google.com/rss/search?q=%22large+language+model%22+OR+LLM+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen"],
        "domain": "news.google.com", "official": False,
    },
    {
        "name": "Agent 技术动态",
        "url": "https://www.bing.com/news/search?q=%22AI+agent%22+OR+%22agentic+AI%22&format=rss",
        "fallback_urls": ["https://news.google.com/rss/search?q=%22AI+agent%22+OR+%22agentic+AI%22+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen"],
        "domain": "news.google.com", "official": False,
        "hint": "agent",
    },
    {
        "name": "模型发布与评测",
        "url": "https://www.bing.com/news/search?q=%22AI+model%22+release+OR+benchmark&format=rss",
        "fallback_urls": ["https://news.google.com/rss/search?q=%22AI+model%22+release+OR+benchmark+when%3A7d&hl=en-US&gl=US&ceid=US%3Aen"],
        "domain": "news.google.com", "official": False,
        "hint": "release",
    },
    {
        "name": "中文大模型动态",
        "url": "https://www.bing.com/news/search?q=%E5%A4%A7%E6%A8%A1%E5%9E%8B+OR+AI%E6%99%BA%E8%83%BD%E4%BD%93&format=rss",
        "fallback_urls": ["https://news.google.com/rss/search?q=%E5%A4%A7%E6%A8%A1%E5%9E%8B+OR+AI%E6%99%BA%E8%83%BD%E4%BD%93+when%3A7d&hl=zh-CN&gl=CN&ceid=CN%3Azh-Hans"],
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


class BlogCardParser(HTMLParser):
    def __init__(self, link_prefix: str) -> None:
        super().__init__()
        self.link_prefix = link_prefix
        self.cards: list[dict[str, str]] = []
        self.div_depth = 0
        self.card_depth: int | None = None
        self.date_depth: int | None = None
        self.title_parts: list[str] = []
        self.date_parts: list[str] = []
        self.link = ""
        self.capture_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "div":
            self.div_depth += 1
            if attributes.get("role") == "listitem":
                self.card_depth = self.div_depth
                self.title_parts = []
                self.date_parts = []
                self.link = ""
            classes = (attributes.get("class") or "").split()
            if self.card_depth is not None and "u-text-style-caption" in classes:
                self.date_depth = self.div_depth
        elif self.card_depth is not None and tag == "h2":
            self.capture_title = True
        elif self.card_depth is not None and tag == "a":
            href = attributes.get("href") or ""
            if href.startswith(self.link_prefix):
                self.link = href
                if attributes.get("data-cta-copy"):
                    self.title_parts = [attributes["data-cta-copy"] or ""]

    def handle_endtag(self, tag: str) -> None:
        if tag == "h2":
            self.capture_title = False
        if tag != "div":
            return
        if self.date_depth == self.div_depth:
            self.date_depth = None
        if self.card_depth == self.div_depth:
            title = strip_html(" ".join(self.title_parts))
            published = strip_html(" ".join(self.date_parts))
            if title and published and self.link:
                self.cards.append({"title": title, "published": published, "url": self.link})
            self.card_depth = None
            self.date_depth = None
        self.div_depth = max(0, self.div_depth - 1)

    def handle_data(self, data: str) -> None:
        if self.capture_title:
            self.title_parts.append(data)
        if self.date_depth is not None:
            self.date_parts.append(data)


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


def find_rich_text(node: ET.Element) -> str:
    candidates = []
    for child in node.iter():
        local = child.tag.rsplit("}", 1)[-1].lower()
        if local in {"content", "description", "encoded", "summary"} and child.text:
            candidates.append(child.text.strip())
    return max(candidates, key=len, default="")


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
            for date_format in ("%B %d, %Y", "%b %d, %Y"):
                try:
                    date = datetime.strptime(value, date_format)
                    break
                except ValueError:
                    continue
            else:
                return datetime.now(timezone.utc)
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc)


def fetch(url: str, extra_headers: dict[str, str] | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html"}
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
        reader_html = find_rich_text(node)
        summary = strip_html(reader_html)
        published = parse_date(find_text(node, ("pubdate", "published", "updated", "date")))
        source_name = source["name"]
        source_domain = source["domain"]
        embedded_source = find_text(node, ("source",))
        if (source["domain"] == "news.google.com" or source.get("extract_embedded_source")) and embedded_source:
            source_name = embedded_source
            if " - " + embedded_source in title:
                title = title.rsplit(" - " + embedded_source, 1)[0].strip()
        title_prefix = source.get("title_prefix")
        if title_prefix and not title.casefold().startswith(title_prefix.casefold()):
            title = f"{title_prefix} {title}"
        items.append({
            "title": title,
            "url": url,
            "summary": summary[:420],
            "published": published,
            "source": source_name,
            "sourceDomain": source_domain,
            "official": source.get("official", False),
            "hint": source.get("hint"),
            "readerHtml": reader_html,
        })
    return items


def parse_blog_cards(payload: bytes, source: dict) -> list[dict]:
    parser = BlogCardParser(source["link_prefix"])
    parser.feed(payload.decode("utf-8", "replace"))
    items = []
    seen_urls = set()
    for card in parser.cards:
        url = urllib.parse.urljoin(source["url"], card["url"])
        if url in seen_urls:
            continue
        seen_urls.add(url)
        items.append({
            "title": card["title"],
            "url": url,
            "summary": "",
            "published": parse_date(card["published"]),
            "source": source["name"],
            "sourceDomain": source["domain"],
            "official": source.get("official", False),
            "hint": source.get("hint"),
            "readerHtml": "",
        })
    return items

def fetch_source(source: dict) -> list[dict]:
    errors = []
    for url in (source["url"], *source.get("fallback_urls", [])):
        try:
            payload = fetch(url, source.get("headers"))
            if source.get("format") == "html-cards":
                entries = parse_blog_cards(payload, source)
            else:
                entries = parse_feed(payload, source)
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


def canonical_url(value: str) -> str:
    """Normalize a public article URL without removing content-identifying parameters."""
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        port = parsed.port
    except (AttributeError, ValueError):
        return ""
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return ""
    scheme = parsed.scheme.casefold()
    host = parsed.hostname.casefold()
    netloc = host if port is None or (scheme, port) in {("http", 80), ("https", 443)} else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [
        (key, item)
        for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_QUERY_KEYS
    ]
    return urllib.parse.urlunsplit((scheme, netloc, path, urllib.parse.urlencode(sorted(query)), ""))


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
        return load_news(OUTPUT).get("items", [])
    except (json.JSONDecodeError, OSError):
        return []


def preserve_archive_metadata(item: dict, previous: dict | None) -> dict:
    if not isinstance(previous, dict):
        return item
    if previous.get("publishedAt"):
        item["publishedAt"] = previous["publishedAt"]
    for field in ("score", "signal"):
        if field in previous:
            item[field] = previous[field]
    if isinstance(previous.get("aiReview"), dict):
        item["aiReview"] = previous["aiReview"]
    return item


def previous_indexes(items: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
    by_id = {item["id"]: item for item in items if item.get("id")}
    by_url = {}
    for item in items:
        key = canonical_url(item.get("url", ""))
        if key:
            by_url.setdefault(key, item)
    return by_id, by_url


def news_content_changed(previous: list[dict], current: list[dict]) -> bool:
    def stable(items: list[dict]) -> str:
        ordered = sorted(items, key=lambda item: item.get("id", ""))
        return json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return stable(previous) != stable(current)


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

    previous_data = load_news(OUTPUT) if OUTPUT.exists() else {"items": []}
    previous_items = previous_data.get("items", [])
    previous_by_id, previous_by_url = previous_indexes(previous_items)
    merged = dict(previous_by_id)
    resolved_urls = resolve_google_news_urls([raw["url"] for raw in collected])
    resolved_count = 0
    for raw in collected:
        resolved_url = resolved_urls.get(raw["url"], raw["url"])
        if resolved_url != raw["url"]:
            raw["url"] = resolved_url
            raw["sourceDomain"] = urllib.parse.urlparse(resolved_url).hostname or raw["sourceDomain"]
            resolved_count += 1
    print(f"[links] resolved {resolved_count} Google News links")
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    feed_snapshots = 0
    for raw in sorted(collected, key=lambda item: item["published"], reverse=True):
        title_key = normalized_title(raw["title"])
        url_key = canonical_url(raw["url"])
        if not title_key or title_key in seen_titles or (url_key and url_key in seen_urls):
            continue
        seen_titles.add(title_key)
        if url_key:
            seen_urls.add(url_key)
        item = finalize(raw, now)
        previous = previous_by_id.get(item["id"]) or previous_by_url.get(url_key)
        if previous:
            item["id"] = previous["id"]
        preserve_archive_metadata(item, previous)
        merged[item["id"]] = item
        if store_feed_snapshot(item, raw.get("readerHtml", "")):
            feed_snapshots += 1

    items = list(merged.values())
    items.sort(key=lambda item: (item.get("publishedAt", ""), item.get("score", 0)), reverse=True)
    for item in items:
        kind = snapshot_kind(item)
        if kind and kind != "summary":
            item["articleKind"] = kind
        else:
            item.pop("articleKind", None)
    if not items:
        print("[error] no news items available", file=sys.stderr)
        return 1

    changed = news_content_changed(previous_items, items)
    generated_at = now.isoformat().replace("+00:00", "Z") if changed else previous_data.get("generatedAt")
    sources = sorted(successful_sources, key=lambda source: source["name"])
    if not changed and previous_data.get("sources"):
        sources = previous_data["sources"]
        print("[news] no article changes; preserving generatedAt and source metadata")
    payload = {
        "generatedAt": generated_at or now.isoformat().replace("+00:00", "Z"),
        "historyPolicy": "append-only",
        "sources": sources,
        "items": items,
    }
    if isinstance(previous_data.get("aiReview"), dict):
        payload["aiReview"] = previous_data["aiReview"]
    save_news(payload, OUTPUT)
    print(f"[articles] stored {feed_snapshots} feed snapshots")
    print(f"[done] wrote {len(items)} items to {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
