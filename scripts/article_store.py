#!/usr/bin/env python3
"""Create safe, plain-text article snapshots for the static reader."""

from __future__ import annotations

import base64
import html
import ipaddress
import json
import os
import re
import socket
import threading
import time
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
READER_PREFIX = "https://r.jina.ai/"
READER_DELAY_SECONDS = float(os.getenv("ARTICLE_READER_DELAY", "4"))
READER_LOCK = threading.Lock()
READER_LAST_REQUEST = 0.0

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
    "privacy policy", "sign in", "sign up", "sponsored by", "subscribe to", "terms of use",
    "use cookies", "版权所有", "登录后", "隐私政策",
)

SECRET_PATTERNS = (
    (re.compile(r"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{20,}(?![A-Za-z0-9])"), "hf_[REDACTED]"),
    (re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,}(?![A-Za-z0-9_])"), "github_pat_[REDACTED]"),
    (re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}(?![A-Za-z0-9])"), "gh_[REDACTED]"),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"), "sk-[REDACTED]"),
    (re.compile(r"(?<![A-Za-z0-9])AIza[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), "AIza[REDACTED]"),
    (re.compile(r"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])"), "AKIA[REDACTED]"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redact_secrets(value: str) -> str:
    redacted = value or ""
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_snapshot(snapshot: dict) -> dict:
    return {
        key: redact_secrets(value) if isinstance(value, str) else value
        for key, value in snapshot.items()
    }


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


def text_from_reader(value: str) -> str:
    value = re.sub(r"!\[[^]]*]\([^)]*\)", "", value or "")
    value = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", value)
    blocks = []
    for block in re.split(r"\n\s*\n", value):
        lines = []
        for line in block.splitlines():
            line = re.sub(r"^\s{0,3}(?:#{1,6}|>|[-*+]\s|\d+[.)]\s)\s*", "", line).strip()
            if not line or line.startswith("```"):
                continue
            if re.match(r"^(?:Title|URL Source|Published Time|Markdown Content):", line, re.IGNORECASE):
                continue
            lines.append(line)
        text = re.sub(r"\s+", " ", " ".join(lines)).strip()
        if text:
            blocks.append(text)
    return "\n\n".join(clean_blocks(blocks))[:MAX_BODY_CHARS].strip()


def snapshot_path(article_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", article_id or ""):
        raise ValueError("invalid article id")
    return ARTICLES_DIR / f"{article_id}.json"


def snapshot_kind(article_id: str) -> str | None:
    try:
        snapshot = json.loads(snapshot_path(article_id).read_text(encoding="utf-8"))
    except (ValueError, OSError, json.JSONDecodeError):
        return None
    kind = snapshot.get("contentKind")
    return kind if kind in {"community", "feed", "page", "reader", "summary"} else None


def write_snapshot(
    item: dict,
    body: str,
    content_kind: str,
    resolved_url: str | None = None,
    error: str | None = None,
) -> Path:
    path = snapshot_path(item["id"])
    payload = redact_snapshot({
        "id": item["id"],
        "title": item["title"],
        "source": item["source"],
        "originalUrl": item["url"],
        "resolvedUrl": resolved_url or item["url"],
        "fetchedAt": utc_now(),
        "contentKind": content_kind,
        "archiveVersion": 2,
        "body": body.strip()[:MAX_BODY_CHARS],
    })
    if error:
        payload["note"] = redact_secrets(error[:180])
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


def _google_news_token(value: str) -> str | None:
    parsed = urllib.parse.urlparse(value)
    path = parsed.path.rstrip("/").split("/")
    if parsed.hostname == "news.google.com" and len(path) >= 2 and path[-2] in {"articles", "read"}:
        return path[-1]
    return None


def _decode_legacy_google_token(token: str) -> str | None:
    try:
        payload = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError):
        return None
    if payload.startswith(b"\x08\x13\x22"):
        payload = payload[3:]
    if payload.endswith(b"\xd2\x01\x00"):
        payload = payload[:-3]
    if not payload:
        return None
    length_size = 2 if payload[0] >= 0x80 else 1
    length = payload[0] & 0x7F
    if length_size == 2 and len(payload) > 1:
        length |= payload[1] << 7
    decoded = payload[length_size:length_size + length].decode("utf-8", errors="ignore")
    return decoded if decoded.startswith(("http://", "https://")) else None


def _decode_google_batch(tokens: list[str]) -> list[str]:
    envelopes = []
    for index, token in enumerate(tokens, start=1):
        inner = (
            '["garturlreq",[["en-US","US",["FINANCE_TOP_INDICES","WEB_TEST_1_0_0"],'
            'null,null,1,1,"US:en",null,180,null,null,null,null,null,0,null,null,'
            f'[1608992183,723341000]],"en-US","US",1,[2,3,4,8],1,0,'
            f'"655000234",0,0,null,0],"{token}"]'
        )
        envelopes.append(json.dumps(["Fbv4je", inner, None, str(index)], separators=(",", ":")))
    payload = f"[[{','.join(envelopes)}]]"
    request = urllib.request.Request(
        "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je",
        data=urllib.parse.urlencode({"f.req": payload}).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
            "Referer": "https://news.google.com/",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.build_opener(PublicRedirectHandler()).open(request, timeout=20) as response:
        document = response.read(MAX_DOWNLOAD_BYTES).decode("utf-8", errors="replace")
    escaped_urls = re.findall(r'\[\\"garturlres\\",\\"(.*?)\\",', document)
    urls = []
    for escaped in escaped_urls:
        try:
            urls.append(json.loads(f'"{escaped}"'))
        except json.JSONDecodeError:
            urls.append(escaped.replace(r"\u003d", "=").replace(r"\/", "/"))
    return urls


def resolve_google_news_urls(values: list[str], batch_size: int = 80) -> dict[str, str]:
    resolved = {value: value for value in values}
    pending: list[tuple[str, str]] = []
    for value in dict.fromkeys(values):
        parsed = urllib.parse.urlparse(value)
        if parsed.hostname in {"bing.com", "www.bing.com"}:
            target = urllib.parse.parse_qs(parsed.query).get("url", [None])[0]
            if target:
                try:
                    validate_public_url(target)
                    resolved[value] = target
                    continue
                except ValueError:
                    pass
        token = _google_news_token(value)
        if not token:
            continue
        legacy = _decode_legacy_google_token(token)
        if legacy:
            resolved[value] = legacy
        else:
            pending.append((value, token))
    for offset in range(0, len(pending), max(1, batch_size)):
        chunk = pending[offset:offset + max(1, batch_size)]
        try:
            decoded = _decode_google_batch([token for _, token in chunk])
        except Exception:
            continue
        for (original, _), target in zip(chunk, decoded):
            try:
                validate_public_url(target)
            except ValueError:
                continue
            resolved[original] = target
    return resolved


def community_api_url(url: str) -> str | None:
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if hostname in {"reddit.com", "www.reddit.com", "old.reddit.com"} and "/comments/" in parsed.path:
        return urllib.parse.urlunparse(("https", "www.reddit.com", parsed.path.rstrip("/") + ".json", "", "raw_json=1", ""))
    if hostname == "linux.do" and "/t/" in parsed.path:
        return urllib.parse.urlunparse(("https", "linux.do", parsed.path.rstrip("/") + ".json", "", "", ""))
    return None


def fetch_community_text(url: str) -> tuple[str, str]:
    api_url = community_api_url(url)
    if not api_url:
        raise ValueError("unsupported community URL")
    validate_public_url(api_url)
    request = urllib.request.Request(
        api_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.build_opener(PublicRedirectHandler()).open(request, timeout=15) as response:
        payload = response.read(MAX_DOWNLOAD_BYTES + 1)
        if len(payload) > MAX_DOWNLOAD_BYTES:
            payload = payload[:MAX_DOWNLOAD_BYTES]
        charset = response.headers.get_content_charset() or "utf-8"
    document = json.loads(payload.decode(charset, errors="replace"))
    if isinstance(document, list):
        post = document[0]["data"]["children"][0]["data"]
        body = (post.get("selftext") or "").strip()
    else:
        posts = document.get("post_stream", {}).get("posts", [])
        body = text_from_html(posts[0].get("cooked", "")) if posts else ""
    if len(body) < MIN_BODY_CHARS:
        raise ValueError("community body not found")
    return body[:MAX_BODY_CHARS], url


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


def fetch_reader_text(url: str) -> tuple[str, str]:
    global READER_LAST_REQUEST
    validate_public_url(url)
    reader_url = f"{READER_PREFIX}{url}"
    request = urllib.request.Request(
        reader_url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain,text/markdown;q=0.9"},
    )
    with READER_LOCK:
        delay = READER_DELAY_SECONDS - (time.monotonic() - READER_LAST_REQUEST)
        if delay > 0:
            time.sleep(delay)
        try:
            with urllib.request.build_opener(PublicRedirectHandler()).open(request, timeout=35) as response:
                payload = response.read(MAX_DOWNLOAD_BYTES + 1)
                if len(payload) > MAX_DOWNLOAD_BYTES:
                    payload = payload[:MAX_DOWNLOAD_BYTES]
                charset = response.headers.get_content_charset() or "utf-8"
        finally:
            READER_LAST_REQUEST = time.monotonic()
    body = text_from_reader(payload.decode(charset, errors="replace"))
    if len(body) < MIN_BODY_CHARS:
        raise ValueError("reader body not found")
    return body, url


def error_note(stage: str, error: Exception) -> str:
    message = re.sub(r"\s+", " ", str(error)).strip()
    return f"{stage}:{type(error).__name__}{':' + message if message else ''}"[:120]


def archive_item(item: dict, fetch_url: str | None = None, allow_reader: bool = True) -> tuple[str, str]:
    target_url = fetch_url or item["url"]
    errors = []
    if community_api_url(target_url):
        try:
            body, resolved_url = fetch_community_text(target_url)
            write_snapshot(item, body, "community", resolved_url=resolved_url)
            return item["id"], "community"
        except Exception as error:
            errors.append(error_note("community", error))
    try:
        body, resolved_url = fetch_page_text(target_url)
        write_snapshot(item, body, "page", resolved_url=resolved_url)
        return item["id"], "page"
    except Exception as error:
        errors.append(error_note("page", error))
    if allow_reader:
        try:
            body, resolved_url = fetch_reader_text(target_url)
            write_snapshot(item, body, "reader", resolved_url=resolved_url)
            return item["id"], "reader"
        except Exception as error:
            errors.append(error_note("reader", error))
    summary = (item.get("summary") or "").strip()
    write_snapshot(item, summary, "summary", resolved_url=target_url, error=" | ".join(errors))
    return item["id"], "summary"
