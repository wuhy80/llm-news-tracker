#!/usr/bin/env python3
"""Create safe, plain-text article snapshots for the static reader."""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTICLES_DIR = ROOT / "data" / "articles"
USER_AGENT = "LLM-Pulse/1.0 (+https://github.com/wuhy80/llm-news-tracker)"
MAX_DOWNLOAD_BYTES = 2_500_000
MAX_BODY_CHARS = 30_000
MIN_BODY_CHARS = 280

BLOCK_TAGS = {
    "address", "article", "blockquote", "br", "div", "figcaption", "h1", "h2", "h3",
    "h4", "h5", "h6", "li", "main", "p", "pre", "section", "td",
}
SKIP_TAGS = {
    "aside", "button", "canvas", "dialog", "footer", "form", "header", "iframe",
    "nav", "noscript", "script", "style", "svg", "template",
}
NOISE_PATTERNS = (
    "accept cookies", "all rights reserved", "cookie policy", "enable javascript",
    "privacy policy", "sign in", "sign up", "subscribe to", "terms of use",
    "use cookies", "版权所有", "登录后", "隐私政策",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ReadableTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.main_depth = 0
        self.all_buffer: list[str] = []
        self.main_buffer: list[str] = []
        self.all_blocks: list[str] = []
        self.main_blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"article", "main"}:
            self._flush()
            self.main_depth += 1
        elif tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if tag in BLOCK_TAGS:
            self._flush()
        if tag in {"article", "main"}:
            self.main_depth = max(0, self.main_depth - 1)

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not data.strip():
            return
        self.all_buffer.append(data)
        if self.main_depth:
            self.main_buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        self._append_block(self.all_buffer, self.all_blocks)
        self._append_block(self.main_buffer, self.main_blocks)
        self.all_buffer = []
        self.main_buffer = []

    @staticmethod
    def _append_block(buffer: list[str], target: list[str]) -> None:
        text = re.sub(r"\s+", " ", html.unescape(" ".join(buffer))).strip()
        if text:
            target.append(text)


def clean_blocks(blocks: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        block = re.sub(r"\s+", " ", block).strip()
        folded = block.casefold()
        if len(block) < 24 or any(pattern in folded for pattern in NOISE_PATTERNS):
            continue
        key = re.sub(r"\W+", "", folded)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(block)
    return cleaned


def text_from_html(value: str, prefer_main: bool = False) -> str:
    parser = ReadableTextParser()
    try:
        parser.feed(value or "")
        parser.close()
    except Exception:
        return re.sub(r"\s+", " ", html.unescape(value or "")).strip()[:MAX_BODY_CHARS]
    main_blocks = clean_blocks(parser.main_blocks)
    all_blocks = clean_blocks(parser.all_blocks)
    blocks = main_blocks if prefer_main and sum(map(len, main_blocks)) >= 600 else all_blocks
    return "\n\n".join(blocks)[:MAX_BODY_CHARS].strip()


def snapshot_path(article_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", article_id or ""):
        raise ValueError("invalid article id")
    return ARTICLES_DIR / f"{article_id}.json"


def write_snapshot(
    item: dict,
    body: str,
    content_kind: str,
    resolved_url: str | None = None,
    error: str | None = None,
) -> Path:
    path = snapshot_path(item["id"])
    payload = {
        "id": item["id"],
        "title": item["title"],
        "source": item["source"],
        "originalUrl": item["url"],
        "resolvedUrl": resolved_url or item["url"],
        "fetchedAt": utc_now(),
        "contentKind": content_kind,
        "body": body.strip()[:MAX_BODY_CHARS],
    }
    if error:
        payload["note"] = error[:180]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def store_feed_snapshot(item: dict, feed_html: str) -> bool:
    if not feed_html:
        return False
    body = text_from_html(feed_html)
    if len(body) < MIN_BODY_CHARS:
        return False
    path = snapshot_path(item["id"])
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("contentKind") == "page" or len(current.get("body", "")) >= len(body):
                return False
        except (json.JSONDecodeError, OSError):
            pass
    write_snapshot(item, body, "feed")
    return True


def validate_public_url(value: str) -> None:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("unsupported URL")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ValueError("host lookup failed") from error
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError("non-public URL")


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_page_text(url: str) -> tuple[str, str]:
    validate_public_url(url)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
    )
    opener = urllib.request.build_opener(PublicRedirectHandler())
    with opener.open(request, timeout=12) as response:
        content_type = response.headers.get_content_type()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError(f"unsupported content type: {content_type}")
        resolved_url = response.geturl()
        validate_public_url(resolved_url)
        payload = response.read(MAX_DOWNLOAD_BYTES + 1)
        if len(payload) > MAX_DOWNLOAD_BYTES:
            payload = payload[:MAX_DOWNLOAD_BYTES]
        charset = response.headers.get_content_charset() or "utf-8"
    document = payload.decode(charset, errors="replace")
    body = text_from_html(document, prefer_main=True)
    if len(body) < MIN_BODY_CHARS:
        raise ValueError("readable body not found")
    return body, resolved_url


def archive_item(item: dict) -> tuple[str, str]:
    try:
        body, resolved_url = fetch_page_text(item["url"])
        write_snapshot(item, body, "page", resolved_url=resolved_url)
        return item["id"], "page"
    except Exception as error:
        summary = (item.get("summary") or "").strip()
        write_snapshot(item, summary, "summary", error=type(error).__name__)
        return item["id"], "summary"
