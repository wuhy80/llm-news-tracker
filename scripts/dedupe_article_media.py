#!/usr/bin/env python3
"""Collapse byte-identical article media onto a single stored copy.

Media files are named after a hash of their *source URL*, not of their content,
so the same bytes get written more than once: an image reused across articles,
or one asset fetched under several CDN URLs. Every copy costs a full copy on
disk and in the published site.

This keeps one copy per distinct content hash, repoints every holder of a
removed path at the copy that stays, and deletes the rest.

Only files that a document still references are considered, so unreferenced
residue is left to ``prune_orphan_media.py`` and can never become a canonical
copy. The survivor is the lexicographically first path in its group: arbitrary
but deterministic, which keeps repeated runs stable.

Rewrites are byte-level substitutions of the quoted path inside the stored
documents, so line endings, key order and every other byte survive untouched.
Deletion only affects the working tree; git history keeps the bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
MEDIA_DIR = DATA_DIR / "article-media"
MEDIA_PREFIX = MEDIA_DIR.relative_to(ROOT).as_posix() + "/"
REFERENCE = re.compile(re.escape(MEDIA_PREFIX) + r"([^\"\\]+)")
CHUNK = 1 << 20


def collect_files(media_dir: Path) -> dict[str, int]:
    """Every stored media file as path-relative-to-the-media-dir -> bytes."""
    files: dict[str, int] = {}
    for path in media_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            files[path.relative_to(media_dir).as_posix()] = path.stat().st_size
        except OSError:
            continue
    return files


def collect_referenced(data_dir: Path, media_dir: Path) -> tuple[set[str], list[Path]]:
    """Media paths referenced by any stored document, plus the documents themselves."""
    referenced: set[str] = set()
    documents: list[Path] = []
    for path in sorted(data_dir.rglob("*")):
        if not path.is_file() or media_dir in path.parents:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        documents.append(path)
        for match in REFERENCE.finditer(text):
            referenced.add(match.group(1))
    return referenced, documents


def content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_plan(data_dir: Path, media_dir: Path) -> dict:
    referenced, documents = collect_referenced(data_dir, media_dir)
    files = collect_files(media_dir)
    missing = sorted(referenced - set(files))

    groups: dict[str, list[str]] = {}
    for relative in sorted(referenced & set(files)):
        groups.setdefault(content_hash(media_dir / relative), []).append(relative)

    duplicates: dict[str, str] = {}
    for paths in groups.values():
        if len(paths) < 2:
            continue
        survivor = paths[0]
        for other in paths[1:]:
            duplicates[other] = survivor

    savings = sum(files[relative] for relative in duplicates)
    return {
        "documentsScanned": len(documents),
        "referencedPaths": len(referenced),
        "mediaFiles": len(files),
        "distinctContents": len(groups),
        "duplicateFiles": len(duplicates),
        "savingsBytes": savings,
        "missingReferences": missing,
        "duplicates": duplicates,
        "groups": {digest: paths for digest, paths in groups.items() if len(paths) > 1},
    }


def rewrite_documents(documents: list[Path], mapping: dict[str, str]) -> int:
    """Point every holder of a removed path at the surviving copy.

    Byte-level so nothing else about a document changes, not even its line
    endings. The quoted form is replaced to avoid matching a longer path that
    merely starts with the same characters.
    """
    pairs = [(('"' + MEDIA_PREFIX + old + '"').encode(),
              ('"' + MEDIA_PREFIX + new + '"').encode()) for old, new in mapping.items()]
    changed = 0
    for path in documents:
        try:
            payload = path.read_bytes()
        except OSError:
            continue
        updated = payload
        for old_bytes, new_bytes in pairs:
            if old_bytes in updated:
                updated = updated.replace(old_bytes, new_bytes)
        if updated != payload:
            path.write_bytes(updated)
            changed += 1
    return changed


def apply_plan(data_dir: Path, media_dir: Path, plan: dict) -> dict:
    documents = collect_referenced(data_dir, media_dir)[1]
    rewritten = rewrite_documents(documents, plan["duplicates"])

    removed = 0
    for relative in sorted(plan["duplicates"]):
        try:
            (media_dir / relative).unlink()
            removed += 1
        except OSError as error:
            print(f"[dedupe] could not delete {MEDIA_PREFIX}{relative}: {error}", flush=True)
    directories = sorted((p for p in media_dir.rglob("*") if p.is_dir()),
                         key=lambda p: len(p.parts), reverse=True)
    for directory in directories:
        try:
            if next(directory.iterdir(), None) is None:
                directory.rmdir()
        except OSError:
            pass

    # Nothing a document still points at may be gone.
    referenced, _ = collect_referenced(data_dir, media_dir)
    files = collect_files(media_dir)
    still_missing = sorted(referenced - set(files))
    print(f"[dedupe] rewrote {rewritten} document(s), deleted {removed} file(s)", flush=True)
    if still_missing:
        print(f"[dedupe] BROKEN after apply: {len(still_missing)} reference(s) have no file", flush=True)
    return {"documentsRewritten": rewritten, "filesDeleted": removed,
            "brokenReferences": still_missing}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="collapse the duplicates; without this the run only reports")
    parser.add_argument("--report-limit", type=int, default=10)
    args = parser.parse_args(argv)

    if not MEDIA_DIR.is_dir():
        print(f"[dedupe] no media directory at {MEDIA_DIR}; nothing to do")
        return 0

    plan = build_plan(DATA_DIR, MEDIA_DIR)
    duplicates = plan.pop("duplicates")
    groups = plan.pop("groups")
    plan["applied"] = bool(args.apply)
    print("[dedupe] " + json.dumps(plan, ensure_ascii=False), flush=True)

    ranked = sorted(groups.items(), key=lambda item: -sum(
        (MEDIA_DIR / p).stat().st_size for p in item[1][1:] if (MEDIA_DIR / p).exists()))
    for digest, paths in ranked[:args.report_limit]:
        waste = sum((MEDIA_DIR / p).stat().st_size for p in paths[1:] if (MEDIA_DIR / p).exists())
        print(f"[dedupe] {len(paths)} copies, {waste:,} B reclaimable  {digest[:12]}", flush=True)
        for path in paths:
            print(f"[dedupe]     {MEDIA_PREFIX}{path}", flush=True)
    for path in plan["missingReferences"][:args.report_limit]:
        print(f"[dedupe] MISSING  {MEDIA_PREFIX}{path}", flush=True)

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as stream:
            stream.write("\n### Duplicate article media\n\n```json\n"
                         + json.dumps(plan, ensure_ascii=False, indent=2) + "\n```\n")

    if not args.apply:
        print("[dedupe] dry run: nothing was changed (pass --apply to collapse)", flush=True)
        return 0

    result = apply_plan(DATA_DIR, MEDIA_DIR, {"duplicates": duplicates})
    if result["brokenReferences"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
