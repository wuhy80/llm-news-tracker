#!/usr/bin/env python3
"""Validate persisted article translation sidecars."""

from __future__ import annotations

import json
import re
from pathlib import Path

from article_store import ARTICLES_DIR, ROOT
from translate_articles import TRANSLATION_VERSION, TRANSLATIONS_DIR, body_hash


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("root must be an object")
    return value


def validate(translations_dir: Path = TRANSLATIONS_DIR, articles_dir: Path = ARTICLES_DIR) -> dict[str, int]:
    errors = []
    partial = 0
    complete = 0
    for path in translations_dir.rglob("*.json") if translations_dir.exists() else []:
        try:
            relative = path.relative_to(translations_dir)
            if len(relative.parts) != 4 or not re.fullmatch(r"[0-9a-f]{12}\.json", relative.name):
                errors.append(f"invalid translation path: {relative}")
                continue
            year, month, day, filename = relative.parts
            article_id = filename[:-5]
            record = read_json(path)
            source_path = articles_dir / year / month / day / filename
            source = read_json(source_path)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            errors.append(f"cannot read {path}: {error}")
            continue
        if record.get("articleId") != article_id:
            errors.append(f"{relative} article id mismatch")
        if record.get("translationVersion") != TRANSLATION_VERSION:
            errors.append(f"{relative} translation version mismatch")
        if record.get("sourceBodyHash") != body_hash(str(source.get("body", ""))):
            errors.append(f"{relative} source body hash mismatch")
        status = record.get("status")
        if status == "complete":
            complete += 1
        elif status == "partial":
            partial += 1
        else:
            errors.append(f"{relative} invalid status: {status}")
        blocks = record.get("blocks")
        if not isinstance(blocks, list):
            errors.append(f"{relative} blocks must be an array")
            continue
        ids = [block.get("id") for block in blocks if isinstance(block, dict)]
        if len(ids) != len(blocks) or len(ids) != len(set(ids)):
            errors.append(f"{relative} has invalid or duplicate block ids")
        if any(not str(block.get("translationZh", "")).strip() for block in blocks if isinstance(block, dict)):
            errors.append(f"{relative} has an empty translated block")
        translated = int(record.get("translatedBlocks", -1))
        total = int(record.get("totalBlocks", -1))
        if translated != len(blocks) or total < translated:
            errors.append(f"{relative} block counts do not match")
        if status == "complete" and translated != total:
            errors.append(f"{relative} complete translation is missing blocks")
        word_wise = record.get("wordWise", [])
        if not isinstance(word_wise, list):
            errors.append(f"{relative} wordWise must be an array")
        elif any(
            not isinstance(entry, dict)
            or not re.search(r"[A-Za-z]", str(entry.get("term", "")))
            or not re.search(r"[\u4e00-\u9fff]", str(entry.get("briefZh", "")))
            for entry in word_wise
        ):
            errors.append(f"{relative} has an invalid Word Wise entry")
    if errors:
        detail = "\n".join(f"- {error}" for error in errors[:50])
        suffix = f"\n- ... and {len(errors) - 50} more" if len(errors) > 50 else ""
        raise ValueError(f"translation validation failed:\n{detail}{suffix}")
    return {"partial": partial, "complete": complete, "total": partial + complete}


def main() -> int:
    result = validate()
    print(
        f"[translations] validated {result['total']} records: "
        f"{result['complete']} complete, {result['partial']} partial"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
