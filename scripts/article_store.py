#!/usr/bin/env python3
"""Create safe, plain-text article snapshots for the static reader."""

from __future__ import annotations

import base64
import hashlib
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
MEDIA_DIR = ROOT / "data" / "article-media"
USER_AGENT = "LLM-Pulse/1.0 (+https://github.com/wuhy80/llm-news-tracker)"
MAX_DOWNLOAD_BYTES = 2_500_000
MAX_IMAGE_BYTES = 5_000_000
MAX_IMAGES_PER_ARTICLE = 12
MAX_BODY_CHARS = 30_000
MIN_BODY_CHARS = 280
BODY_FORMAT_VERSION = 3
READER_PREFIX = "https://r.jina.ai/"
READER_DELAY_SECONDS = float(os.getenv("ARTICLE_READER_DELAY", "4"))
READER_LOCK = threading.Lock()
READER_LAST_REQUEST = 0.0
FETCHED_IMAGE_REFS: dict[str, list[dict[str, str]]] = {}

BLOCK_TAGS = {
    "address", "article", "blockquote", "br", "div", "figcaption", "h1", "h2", "h3",
    "h4", "h5", "h6", "li", "main", "p", "pre", "section", "td",
}
SKIP_TAGS = {
    "aside", "button", "canvas", "dialog", "footer", "form", "header", "iframe",
    "nav", "noscript", "script", "style", "svg", "template",
}
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}
SKIP_CLASS_NAMES = {
    "article_info", "author", "line_font", "person_box", "share_pc", "tags",
    "xiangguan", "yaowen", "related", "recommend", "recommendation",
}
NOISE_PATTERNS = (
    "accept cookies", "all rights reserved", "cookie policy", "enable javascript",
    "privacy policy", "sign in", "sign up", "sponsored by", "subscribe to", "terms of use",
    "use cookies", "版权所有", "登录后", "隐私政策",
)
UNFENCED_CODE_HINT_PATTERN = re.compile(
    r"(?:\bdef\s+[A-Za-z_]\w*\s*\("
    r"|\bclass\s+[A-Za-z_]\w*\s*[:({]"
    r"|\b(?:async\s+)?function\s+[A-Za-z_$]\w*\s*\("
    r"|\b(?:const|let|var)\s+[A-Za-z_$]\w*\s*="
    r"|\bfrom\s+[A-Za-z_][\w.]*\s+import\s+"
    r"|\bimport\s+[A-Za-z_][\w.]*"
    r"|\b(?:pip|npm|yarn|pnpm)\s+(?:install|add|run|exec)\b"
    r"|\b(?:docker|kubectl|git)\s+[a-z-]+\b"
    r"|(?:curl|wget)\s+https?://"
    r"|\{%|\{\{|</?[a-z][^>]*>"
    r"|\bSELECT\s+[\w*].+\bFROM\s+[A-Za-z_])",
    re.IGNORECASE,
)
FENCED_CODE_PATTERN = re.compile(r"```[^\r\n]*\r?\n([\s\S]*?)\r?\n```")
INLINE_FENCE_PATTERN = re.compile(r"(?m)^.*\S[ \t]+```(?:[^\r\n`]*)")
LATEX_MARKUP_PATTERN = re.compile(
    r"\\(?:textbf|textit|texttt|textrm|textsf|textsc|textup|textsl|emph|"
    r"textsuperscript|textsubscript|mathrm|mathbf|mathit|mathsf|mathtt|"
    r"operatorname|underline|overline|frac|sqrt|url|href|cite|ref|left|right|"
    r"begin|end|[A-Za-z]{2,})(?:\s*\{|\s|$)|(?<!\\)\${1,2}"
)
LATEX_CONTENT_COMMANDS = (
    "textbf", "textit", "texttt", "textrm", "textsf", "textsc", "textup", "textsl",
    "emph", "textsuperscript", "textsubscript", "mathrm", "mathbf", "mathit", "mathsf",
    "mathtt", "operatorname", "underline", "overline", "text", "url", "cite", "ref",
)
LATEX_SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π", "sigma": "σ", "phi": "φ",
    "omega": "ω", "times": "×", "cdot": "·", "pm": "±", "leq": "≤", "geq": "≥",
    "neq": "≠", "infty": "∞",
}
YAML_KEY_PATTERN = re.compile(r"(?<![\w-])(?:[A-Za-z_][\w.-]*):(?=\s|$)")
SHELL_COMMAND_PATTERN = re.compile(
    r"(?:hf\s+download|(?:cd|curl|wget|docker|git|kubectl|npm|pip|pnpm|python(?:\d+)?)\s+)",
    re.IGNORECASE,
)

SECRET_PATTERNS = (
    (re.compile(r"(?<![A-Za-z0-9])hf_[A-Za-z0-9]{20,}(?![A-Za-z0-9])"), "hf_[REDACTED]"),
    (re.compile(r"(?<![A-Za-z0-9])github_pat_[A-Za-z0-9_]{20,}(?![A-Za-z0-9_])"), "github_pat_[REDACTED]"),
    (re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{20,}(?![A-Za-z0-9])"), "gh_[REDACTED]"),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"), "sk-[REDACTED]"),
    (re.compile(r"(?<![A-Za-z0-9])AIza[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), "AIza[REDACTED]"),
    (re.compile(r"(?<![A-Z0-9])AKIA[A-Z0-9]{16}(?![A-Z0-9])"), "AKIA[REDACTED]"),
)
SNAPSHOT_ITEM_FIELDS = (
    "id", "title", "summary", "url", "source", "sourceDomain", "publishedAt",
    "category", "tags", "score", "signal", "aiReview",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redact_secrets(value: str) -> str:
    redacted = value or ""
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_snapshot(value):
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: redact_snapshot(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_snapshot(item) for item in value]
    return value


class ReadableTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_stack: list[tuple[str, int]] = []
        self.main_stack: list[tuple[str, int]] = []
        self.html_depth = 0
        self.main_depth = 0
        self.all_buffer: list[str] = []
        self.main_buffer: list[str] = []
        self.all_blocks: list[str] = []
        self.main_blocks: list[str] = []
        self.pre_depth = 0
        self.pre_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        is_void = tag in VOID_TAGS
        if not is_void:
            self.html_depth += 1
        if not is_void and classes & SKIP_CLASS_NAMES:
            self.skip_stack.append((tag, self.html_depth))
            return
        if self.skip_stack:
            return
        if tag in SKIP_TAGS:
            self.skip_stack.append((tag, self.html_depth))
            return
        if tag == "pre":
            self._flush()
            self.pre_depth += 1
            self.pre_buffer = []
            return
        if self.pre_depth:
            if tag == "br":
                self.pre_buffer.append("\n")
            return
        if tag in {"article", "main"} or "article" in classes:
            self._flush()
            self.main_stack.append((tag, self.html_depth))
            self.main_depth += 1
        elif tag in BLOCK_TAGS:
            self._flush()
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                marker = "#" * int(tag[1]) + " "
                self.all_buffer.append(marker)
                if self.main_depth:
                    self.main_buffer.append(marker)
            elif tag == "li":
                self.all_buffer.append("- ")
                if self.main_depth:
                    self.main_buffer.append("- ")
            elif tag == "blockquote":
                self.all_buffer.append("> ")
                if self.main_depth:
                    self.main_buffer.append("> ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        is_void = tag in VOID_TAGS
        current_depth = self.html_depth
        if self.skip_stack:
            if not is_void and self.skip_stack[-1] == (tag, current_depth):
                self.skip_stack.pop()
            if not is_void:
                self.html_depth = max(0, self.html_depth - 1)
            return
        if tag == "pre" and self.pre_depth:
            self._append_code(self.pre_buffer, self.all_blocks)
            if self.main_depth:
                self._append_code(self.pre_buffer, self.main_blocks)
            self.pre_depth = max(0, self.pre_depth - 1)
            self.pre_buffer = []
            if not is_void:
                self.html_depth = max(0, self.html_depth - 1)
            return
        if self.pre_depth:
            if tag in {"div", "li", "p"}:
                self.pre_buffer.append("\n")
            if not is_void:
                self.html_depth = max(0, self.html_depth - 1)
            return
        if tag in BLOCK_TAGS:
            self._flush()
        if self.main_stack and self.main_stack[-1] == (tag, current_depth):
            self.main_stack.pop()
            self.main_depth = max(0, self.main_depth - 1)
        if not is_void:
            self.html_depth = max(0, self.html_depth - 1)

    def handle_data(self, data: str) -> None:
        if self.skip_stack:
            return
        if self.pre_depth:
            self.pre_buffer.append(data)
            return
        if not data.strip():
            return
        self.all_buffer.append(data)
        if self.main_depth:
            self.main_buffer.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        if self.pre_depth:
            return
        self._append_block(self.all_buffer, self.all_blocks)
        self._append_block(self.main_buffer, self.main_blocks)
        self.all_buffer = []
        self.main_buffer = []

    @staticmethod
    def _append_block(buffer: list[str], target: list[str]) -> None:
        joined = re.sub(r"<\s+/?\s*[A-Za-z][^>]*>", " ", " ".join(buffer))
        text = re.sub(r"\s+", " ", html.unescape(joined)).strip()
        if text:
            target.append(text)

    @staticmethod
    def _append_code(buffer: list[str], target: list[str]) -> None:
        code = restore_collapsed_code(html.unescape("".join(buffer))).strip("\n")
        if code.strip():
            target.append(f"```\n{code}\n```")


class ImageReferenceParser(HTMLParser):
    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.references: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or attributes.get("itemprop") or "").casefold()
            if key not in {"og:image", "og:image:url", "twitter:image", "twitter:image:src", "image"}:
                return
            source = attributes.get("content", "")
        elif tag in {"img", "source"}:
            source = (
                attributes.get("src")
                or attributes.get("data-src")
                or attributes.get("data-original")
                or attributes.get("data-lazy-src")
                or attributes.get("data-original-src")
                or attributes.get("data-url")
            )
            if not source:
                srcset = (
                    attributes.get("srcset")
                    or attributes.get("data-srcset")
                    or attributes.get("data-lazy-srcset")
                )
                if srcset:
                    source = srcset.split(",", 1)[0].strip().rsplit(" ", 1)[0]
        else:
            return
        if not source or source.startswith(("data:", "blob:", "#")):
            return
        url = urllib.parse.urljoin(self.base_url, html.unescape(source.strip()))
        if urllib.parse.urlparse(url).scheme not in {"http", "https"}:
            return
        alt = attributes.get("alt") or attributes.get("title") or ""
        classes = f"{attributes.get('class', '')} {attributes.get('id', '')}".casefold()
        if any(token in classes for token in ("logo", "avatar", "icon", "favicon", "sprite", "tracking", "pixel")):
            return
        self.references.append({"url": url, "alt": re.sub(r"\s+", " ", alt).strip()})


def extract_image_refs(value: str, base_url: str = "") -> list[dict[str, str]]:
    parser = ImageReferenceParser(base_url)
    try:
        parser.feed(value or "")
        parser.close()
    except Exception:
        return []
    refs = []
    seen = set()
    for reference in parser.references:
        if reference["url"] in seen:
            continue
        seen.add(reference["url"])
        refs.append(reference)
    return refs[:MAX_IMAGES_PER_ARTICLE]


def extract_markdown_image_refs(value: str, base_url: str = "") -> list[dict[str, str]]:
    refs = []
    seen = set()
    for match in re.finditer(r"!\[([^]]*)\]\((\S+?)(?:\s+['\"][^)]*['\"])?\)", value or ""):
        url = urllib.parse.urljoin(base_url, html.unescape(match.group(2)))
        if urllib.parse.urlparse(url).scheme not in {"http", "https"} or url in seen:
            continue
        seen.add(url)
        refs.append({"url": url, "alt": re.sub(r"\s+", " ", match.group(1)).strip()})
    return refs[:MAX_IMAGES_PER_ARTICLE]


def download_images(item: dict, references: list[dict[str, str]] | None) -> list[dict[str, str]]:
    if not references:
        return []
    destination = MEDIA_DIR / publication_path(item.get("publishedAt")) / str(item["id"])
    result = []
    seen = set()
    for reference in references[:MAX_IMAGES_PER_ARTICLE]:
        url = str(reference.get("url", "")).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            validate_public_url(url)
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
            suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
            if suffix not in {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}:
                suffix = ".img"
            target = destination / f"{digest}{suffix}"
            if not target.exists():
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/*"})
                with urllib.request.build_opener(PublicRedirectHandler()).open(request, timeout=20) as response:
                    content_type = response.headers.get_content_type()
                    payload = response.read(MAX_IMAGE_BYTES + 1)
                detected_suffix = detect_image_suffix(payload, content_type)
                if not detected_suffix or len(payload) > MAX_IMAGE_BYTES:
                    continue
                if suffix == ".img":
                    suffix = detected_suffix
                    target = destination / f"{digest}{suffix}"
                    if target.exists():
                        result.append({
                            "src": "/".join(target.relative_to(ROOT).parts),
                            "alt": str(reference.get("alt", ""))[:240],
                            "originalUrl": url,
                        })
                        continue
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_suffix(target.suffix + ".tmp")
                temporary.write_bytes(payload)
                temporary.replace(target)
            result.append({
                "src": "/".join(target.relative_to(ROOT).parts),
                "alt": str(reference.get("alt", ""))[:240],
                "originalUrl": url,
            })
        except (OSError, ValueError, urllib.error.URLError, TimeoutError):
            continue
    return result


def detect_image_suffix(payload: bytes, content_type: str) -> str | None:
    """Accept common image signatures when a CDN returns a generic MIME type."""
    mime_suffixes = {
        "image/avif": ".avif",
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    normalized = (content_type or "").split(";", 1)[0].casefold()
    if normalized in mime_suffixes:
        return mime_suffixes[normalized]
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return ".webp"
    if len(payload) >= 12 and payload[4:8] == b"ftyp" and payload[8:12] in {b"avif", b"avis"}:
        return ".avif"
    return None


def clean_blocks(blocks: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        if block.lstrip().startswith("```"):
            lines = block.strip().splitlines()
            if len(lines) >= 3:
                code = restore_collapsed_code("\n".join(lines[1:-1])).rstrip()
                if code.strip():
                    cleaned.append(f"{lines[0].strip()}\n{code}\n```")
            continue
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


def has_flattened_code(value: str) -> bool:
    body = value or ""
    blocks = FENCED_CODE_PATTERN.findall(body)
    for code in blocks:
        nonempty_lines = [line for line in code.splitlines() if line.strip()]
        if len(nonempty_lines) > 2 or len(code) < 240:
            continue
        shell_commands = len(re.findall(r"\b(?:hf\s+download|pip|npm|docker|kubectl|git)\b", code, re.IGNORECASE))
        yaml_keys = len(re.findall(
            r"\b(?:apiVersion|kind|metadata|spec|containers|volumes|name|image|args|env|value):",
            code,
        ))
        if code.count(";") >= 2 or shell_commands >= 2 or yaml_keys >= 4:
            return True
    return not blocks and bool(UNFENCED_CODE_HINT_PATTERN.search(body))


def has_malformed_code_fence(value: str) -> bool:
    body = value or ""
    return body.count("```") % 2 != 0 or bool(INLINE_FENCE_PATTERN.search(body))


def has_corrupted_text(value: str) -> bool:
    body = value or ""
    replacement_count = body.count("\ufffd")
    control_count = sum(1 for char in body if ord(char) < 32 and char not in "\n\t")
    return replacement_count >= 3 or control_count >= 3


def normalize_fenced_body(value: str) -> str:
    """Turn inline or unterminated fences from legacy feeds into block fences."""
    lines = (value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    output: list[str] = []
    in_code = False
    for line in lines:
        marker = line.find("```")
        if not in_code:
            if marker < 0:
                output.append(line)
                continue
            before = line[:marker].rstrip()
            tail = line[marker + 3:]
            if before:
                output.append(before)
            if "```" in tail:
                middle, after = tail.split("```", 1)
                output.append("```")
                output.append(middle.strip())
                output.append("```")
                if after.strip():
                    output.append(after.strip())
                continue
            if tail.startswith((" ", "\t")):
                output.append("```")
                if tail.strip():
                    output.append(tail.strip())
            else:
                output.append("```" + tail.strip())
            in_code = True
            continue
        if marker >= 0:
            before = line[:marker].rstrip()
            if before:
                output.append(before)
            output.append("```")
            suffix = line[marker + 3:].strip()
            if suffix:
                output.append(suffix)
            in_code = False
        else:
            output.append(line)
    if in_code:
        output.append("```")
    return "\n".join(output)


def normalize_prose_markup(value: str) -> str:
    """Convert common LaTeX emitted by paper feeds into readable plain text."""
    text = html.unescape(value or "")
    for _ in range(5):
        previous = text
        for command in LATEX_CONTENT_COMMANDS:
            text = re.sub(rf"\\{command}\s*\{{([^{{}}]*)\}}", r"\1", text)
        text = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"(\1)/(\2)", text)
        text = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"sqrt(\1)", text)
        if text == previous:
            break
    for command, symbol in LATEX_SYMBOLS.items():
        text = re.sub(rf"\\{command}\b", symbol, text)
    text = re.sub(r"\\href\s*\{[^{}]*\}\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:left|right)\s*", "", text)
    text = re.sub(r"\\(?:quad|qquad|enspace|hspace\s*\{[^{}]*\}|[,;:!])", " ", text)
    text = re.sub(r"\\([%&_#$])", r"\1", text)
    text = re.sub(r"\${1,2}", "", text)
    text = re.sub(r"(?<!\\)(?:\^|_)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"(?<!\\)\^\s*([A-Za-z0-9+\-=()])", r"\1", text)
    text = re.sub(r"\\(?:begin|end)\s*\{[^{}]*\}", "", text)
    text = re.sub(r"\\[A-Za-z]+", "", text)
    text = re.sub(r"(?<!\\)[{}]", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def normalize_article_body(value: str) -> str:
    """Normalize prose while preserving fenced code exactly."""
    normalized = normalize_fenced_body(value or "")
    output: list[str] = []
    in_code = False
    for line in normalized.split("\n"):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            output.append(line)
        elif in_code:
            output.append(line)
        else:
            output.append(normalize_prose_markup(line))
    return "\n".join(output).strip()


def has_unrendered_markup(value: str) -> bool:
    """Detect source markup that would otherwise be shown literally in the reader."""
    prose = FENCED_CODE_PATTERN.sub("", normalize_fenced_body(value or ""))
    return bool(LATEX_MARKUP_PATTERN.search(prose))


def limit_body(value: str, limit: int = MAX_BODY_CHARS) -> str:
    """Limit an article without leaving an unterminated fenced code block."""
    body = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(body) <= limit:
        return body
    lines = body.splitlines()
    kept: list[str] = []
    used = 0
    in_code = False
    for line in lines:
        addition = line + "\n"
        if used + len(addition) > limit:
            break
        kept.append(line)
        used += len(addition)
        if line.lstrip().startswith("```"):
            in_code = not in_code
    if in_code:
        kept.append("```")
    return "\n".join(kept).strip()


def _split_outside_quotes(value: str, separator: str = ";") -> str:
    result: list[str] = []
    quote = ""
    escaped = False
    for char in value:
        if escaped:
            result.append(char)
            escaped = False
            continue
        if char == "\\" and quote:
            result.append(char)
            escaped = True
            continue
        if char in {"'", '"', "`"}:
            quote = "" if quote == char else char if not quote else quote
        if char == separator and not quote:
            result.extend((char, "\n"))
        else:
            result.append(char)
    return "".join(result)


def _restore_yaml_code(code: str) -> str:
    # Flattened YAML still exposes its keys, which gives us stable line boundaries.
    code = re.sub(r"\s+(?=[A-Za-z_][\w.-]*:(?=\s|$))", "\n", code)
    code = re.sub(r"\s+(?=-\s+|#\s*)", "\n", code)
    return code


def _restore_shell_code(code: str) -> str:
    code = re.sub(r"\\\s+", "\\\n ", code)
    return SHELL_COMMAND_PATTERN.sub(lambda match: ("\n" if match.start() else "") + match.group(0), code)


def _restore_program_code(code: str) -> str:
    code = _split_outside_quotes(code)
    code = re.sub(
        r"\s+(?=(?:from\s+\w|import\s+\w|(?:async\s+)?def\s+\w|class\s+\w|"
        r"(?:const|let|var)\s+\w|(?:if|for|while|try|with|return|assert)\b|"
        r"[A-Za-z_]\w*\s*=|[A-Za-z_]\w*\[[^]]+\]\s*=|[A-Za-z_]\w*\.(?:load|dump)\())",
        "\n",
        code,
    )
    code = re.sub(r":\s+(?=[A-Za-z_]\w*\s|[A-Za-z_]\w*\[)", ":\n    ", code)
    return code


def restore_collapsed_code(code: str) -> str:
    """Recover useful line boundaries when a source flattened a fenced block."""
    normalized = re.sub(r"[ \t]+", " ", code or "").strip()
    if not normalized or len(normalized.splitlines()) > 2:
        return code
    shell_commands = len(SHELL_COMMAND_PATTERN.findall(normalized))
    yaml_keys = len(YAML_KEY_PATTERN.findall(normalized))
    if yaml_keys >= 4 and (normalized.startswith(("apiVersion:", "kind:", "---")) or yaml_keys >= 8):
        return _restore_yaml_code(normalized)
    if shell_commands >= 2:
        return _restore_shell_code(normalized)
    if any(pattern.search(normalized) for pattern in (
        re.compile(r"\b(?:def|class|import|from)\b"),
        re.compile(r"\b(?:const|let|var|function)\b"),
    )):
        return _restore_program_code(normalized)
    return code


def text_from_html(value: str, prefer_main: bool = False) -> str:
    unescaped = html.unescape(value or "")
    if not re.search(r"<[A-Za-z][^>]*>", unescaped):
        # Atom summaries often encode paragraph breaks as a newline plus indentation.
        plain = re.sub(r"\r\n?", "\n", unescaped)
        paragraphs = re.split(r"\n[ \t]{2,}|\n[ \t]*\n", plain)
        blocks = [re.sub(r"\s+", " ", paragraph).strip() for paragraph in paragraphs]
        return limit_body(normalize_article_body("\n\n".join(block for block in blocks if block)))
    parser = ReadableTextParser()
    try:
        parser.feed(value or "")
        parser.close()
    except Exception:
        return limit_body(re.sub(r"\s+", " ", html.unescape(value or "")))
    main_blocks = clean_blocks(parser.main_blocks)
    all_blocks = clean_blocks(parser.all_blocks)
    blocks = main_blocks if prefer_main and sum(map(len, main_blocks)) >= 600 else all_blocks
    return limit_body("\n\n".join(blocks))


def text_from_reader(value: str) -> str:
    value = re.sub(r"!\[[^]]*]\([^)]*\)", "", value or "")
    value = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", value)
    blocks = []
    prose_lines: list[str] = []
    code_lines: list[str] | None = None
    code_language = ""

    def flush_prose() -> None:
        text = re.sub(r"\s+", " ", " ".join(prose_lines)).strip()
        if text:
            blocks.append(text)
        prose_lines.clear()

    for raw_line in value.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            if code_lines is None:
                flush_prose()
                code_lines = []
                code_language = stripped[3:].strip()
            else:
                code = "\n".join(code_lines).rstrip()
                if code.strip():
                    blocks.append(f"```{code_language}\n{code}\n```")
                code_lines = None
                code_language = ""
            continue
        if code_lines is not None:
            code_lines.append(raw_line.rstrip())
            continue
        line = re.sub(r"^\s{0,3}(?:>|[-*+]\s|\d+[.)]\s)\s*", "", raw_line).strip()
        if not line:
            flush_prose()
            continue
        if re.match(r"^(?:Title|URL Source|Published Time|Markdown Content):", line, re.IGNORECASE):
            continue
        prose_lines.append(line)
    if code_lines is not None:
        code = "\n".join(code_lines).rstrip()
        if code.strip():
            blocks.append(f"```{code_language}\n{code}\n```")
    flush_prose()
    return limit_body("\n\n".join(clean_blocks(blocks)))


def publication_path(published_at: str) -> Path:
    if not isinstance(published_at, str):
        raise ValueError("invalid publishedAt")
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid publishedAt") from error
    return Path(f"{published.year:04d}") / f"{published.month:02d}" / f"{published.day:02d}"


def snapshot_path(item: dict) -> Path:
    article_id = item.get("id", "")
    if not re.fullmatch(r"[0-9a-f]{12}", article_id):
        raise ValueError("invalid article id")
    return ARTICLES_DIR / publication_path(item.get("publishedAt")) / f"{article_id}.json"


def snapshot_kind(item: dict) -> str | None:
    try:
        snapshot = json.loads(snapshot_path(item).read_text(encoding="utf-8"))
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
    images: list[dict[str, str]] | None = None,
    media_checked_at: str | None = None,
) -> Path:
    path = snapshot_path(item)
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        existing = {}
    payload = {
        "schemaVersion": 3,
        **{
            field: item[field]
            for field in SNAPSHOT_ITEM_FIELDS
            if field in item
        },
        "originalUrl": item["url"],
        "resolvedUrl": resolved_url or item["url"],
        "fetchedAt": utc_now(),
        "contentKind": content_kind,
        "archiveVersion": 2,
        "bodyFormatVersion": BODY_FORMAT_VERSION,
        "body": limit_body(normalize_article_body(body)),
    }
    if images:
        payload["images"] = images
    elif existing.get("images"):
        payload["images"] = existing["images"]
    if media_checked_at:
        payload["mediaCheckedAt"] = media_checked_at
    elif existing.get("mediaCheckedAt"):
        payload["mediaCheckedAt"] = existing["mediaCheckedAt"]
    for field in ("summaryZh", "summaryGeneratedAt", "summaryModel"):
        if field in existing and field not in payload:
            payload[field] = existing[field]
    payload = redact_snapshot(payload)
    if error:
        payload["note"] = redact_secrets(error[:180])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def store_feed_snapshot(
    item: dict,
    feed_html: str,
    extra_image_refs: list[dict[str, str]] | None = None,
) -> bool:
    if not feed_html:
        return False
    body = text_from_html(feed_html)
    if len(body) < MIN_BODY_CHARS:
        return False
    image_refs = extract_image_refs(feed_html, item.get("url", ""))
    for reference in extra_image_refs or []:
        if reference.get("url") and reference["url"] not in {item["url"] for item in image_refs}:
            image_refs.append(reference)
    image_refs = image_refs[:MAX_IMAGES_PER_ARTICLE]
    path = snapshot_path(item)
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            current_body = current.get("body", "")
            if current.get("contentKind") == "page":
                return False
            current_paragraphs = current_body.count("\n\n")
            new_paragraphs = body.count("\n\n")
            if (
                current.get("bodyFormatVersion", 0) >= BODY_FORMAT_VERSION
                and not has_flattened_code(current_body)
                and not has_malformed_code_fence(current_body)
                and not has_corrupted_text(current_body)
                and len(current_body) >= len(body)
                and current_paragraphs >= new_paragraphs
                and (current.get("images") or not image_refs)
            ):
                return False
            if len(body) < max(MIN_BODY_CHARS, int(len(current_body) * 0.55)) and not image_refs:
                return False
            if has_flattened_code(current_body) and has_flattened_code(body) and not image_refs:
                return False
        except (json.JSONDecodeError, OSError):
            pass
    images = download_images(item, image_refs)
    write_snapshot(item, body, "feed", images=images)
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


def community_image_refs(post: dict) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []

    def add(value: str, alt: str = "") -> None:
        value = html.unescape(str(value or "")).strip()
        if value.startswith(("http://", "https://")):
            refs.append({"url": value, "alt": alt})

    destination = post.get("url_overridden_by_dest") or ""
    if re.search(r"\.(?:avif|gif|jpe?g|png|webp)(?:\?|$)", destination, re.IGNORECASE):
        add(destination, post.get("title", ""))
    preview = post.get("preview", {})
    for image in preview.get("images", []) if isinstance(preview, dict) else []:
        if isinstance(image, dict):
            source = image.get("source", {})
            add(source.get("url", "") if isinstance(source, dict) else "", post.get("title", ""))
    metadata = post.get("media_metadata", {})
    if isinstance(metadata, dict):
        for value in metadata.values():
            if isinstance(value, dict):
                add(value.get("s", {}).get("u", "") if isinstance(value.get("s"), dict) else "", post.get("title", ""))
    unique = []
    seen = set()
    for ref in refs:
        if ref["url"] not in seen:
            seen.add(ref["url"])
            unique.append(ref)
    return unique[:MAX_IMAGES_PER_ARTICLE]


def fetch_community_content(url: str) -> tuple[str, str, list[dict[str, str]]]:
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
        post = posts[0] if posts else {}
        body = text_from_html(post.get("cooked", "")) if posts else ""
    if len(body) < MIN_BODY_CHARS:
        raise ValueError("community body not found")
    if has_corrupted_text(body):
        raise ValueError("community body contains corrupted text")
    return limit_body(body), url, community_image_refs(post)


def fetch_community_text(url: str) -> tuple[str, str]:
    body, resolved_url, images = fetch_community_content(url)
    FETCHED_IMAGE_REFS[resolved_url] = images
    return body, resolved_url


def fetch_page_content(url: str) -> tuple[str, str, list[dict[str, str]]]:
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
    if has_corrupted_text(body):
        raise ValueError("page body contains corrupted text")
    return body, resolved_url, extract_image_refs(document, resolved_url)


def fetch_page_text(url: str) -> tuple[str, str]:
    body, resolved_url, images = fetch_page_content(url)
    FETCHED_IMAGE_REFS[resolved_url] = images
    return body, resolved_url


def fetch_reader_content(url: str) -> tuple[str, str, list[dict[str, str]]]:
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
    reader_text = payload.decode(charset, errors="replace")
    body = text_from_reader(reader_text)
    if len(body) < MIN_BODY_CHARS:
        raise ValueError("reader body not found")
    if has_corrupted_text(body):
        raise ValueError("reader body contains corrupted text")
    return body, url, extract_markdown_image_refs(reader_text, url)


def fetch_reader_text(url: str) -> tuple[str, str]:
    body, resolved_url, images = fetch_reader_content(url)
    FETCHED_IMAGE_REFS[resolved_url] = images
    return body, resolved_url


def error_note(stage: str, error: Exception) -> str:
    message = re.sub(r"\s+", " ", str(error)).strip()
    return f"{stage}:{type(error).__name__}{':' + message if message else ''}"[:120]


def archive_item(item: dict, fetch_url: str | None = None, allow_reader: bool = True) -> tuple[str, str]:
    target_url = fetch_url or item["url"]
    errors = []
    if community_api_url(target_url):
        try:
            body, resolved_url = fetch_community_text(target_url)
            write_snapshot(item, body, "community", resolved_url=resolved_url, images=download_images(item, FETCHED_IMAGE_REFS.pop(resolved_url, [])))
            return item["id"], "community"
        except Exception as error:
            errors.append(error_note("community", error))
    try:
        body, resolved_url = fetch_page_text(target_url)
        write_snapshot(item, body, "page", resolved_url=resolved_url, images=download_images(item, FETCHED_IMAGE_REFS.pop(resolved_url, [])))
        return item["id"], "page"
    except Exception as error:
        errors.append(error_note("page", error))
    if allow_reader:
        try:
            body, resolved_url = fetch_reader_text(target_url)
            write_snapshot(item, body, "reader", resolved_url=resolved_url, images=download_images(item, FETCHED_IMAGE_REFS.pop(resolved_url, [])))
            return item["id"], "reader"
        except Exception as error:
            errors.append(error_note("reader", error))
    summary = (item.get("summary") or "").strip()
    write_snapshot(item, summary, "summary", resolved_url=target_url, error=" | ".join(errors))
    return item["id"], "summary"
