# Hugging Face content repair

The old extractor selected page-wide media, allowing profile images to consume the 12-image budget. HF blog bodies now use the publisher's scoped `blog-content` body container. If that container changes, extraction fails without replacing the archive.

The canonical body preserves short prose, code, table cells and links. `inlineContent` contains safe text ranges and table coordinates; `codeContent` retains exact code even when it contains Markdown fences. Source HTML and scripts are never inserted into the reader. Unsupported embeds remain explicit links to the source. Media positions are emitted during the same traversal as the text, including images inside table cells, rather than inferred from paragraph proximity.

`contentIntegrity` distinguishes source media coverage from placement of already-extracted resources. Images have no arbitrary 12-image truncation within the scoped HF body. The bounded HTML/body size fails explicitly instead of saving silently truncated content.

## Whole-archive migration

```sh
python scripts/repair_huggingface.py --limit 80
python scripts/validate_translations.py
```

The command scans every year under `data/articles`, including summary-only HF records. Each article's body and translation remapping are prepared before writing. Unchanged source text is matched by content, not ordinal block number. Ambiguous matches and changed text remain pending; prior translated text is preserved in Git history. The state file lists total, repaired, remaining and retryable failures. The existing hourly repair workflow processes another bounded batch and stops on source rate limiting. No API keys, model calls, extra workflow privileges or automatic workflow dispatches are introduced.

For locally cached public HTML:

```sh
python scripts/repair_huggingface.py --cache-dir /path/to/cache --offline --retry
```

Only matching HTML files are processed; uncached articles remain pending. A completed migration means `remaining: 0`, not merely a successful workflow exit.

## Validation

```sh
python -m unittest discover -s tests
node tests/test_inline_content.js
node tests/test_media_layout.js
python scripts/validate_translations.py
```

Regression cases cover page chrome exclusion, more than 12 body images, table images and empty cells, short content, direct inline siblings, nested code fences, unsafe links, summary-only records, and translation ID shifts. Reader DOM verification additionally checks source text, links, table reconstruction and image error fallbacks.
