#!/usr/bin/env python3
"""Audit reader JSON and repair navigation using existing chapter indices.

Chapter IDs, titles, URLs and article bodies are never rewritten. In particular,
printed numbers in chapter titles are not used to infer order: the Kiếm Lai
source intentionally retains repeated, skipped and restarted chapter numbers.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def ordered_chapters(rows: list[dict]) -> list[dict]:
    """Reject ambiguous records before returning order by explicit index."""
    ids = [str(row["chapterid"]) for row in rows]
    indices = [row["chapterindex"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate chapter IDs; repair requires a unique identity.")
    if any(type(index) is not int for index in indices):
        raise ValueError("Chapter indices must be integers.")
    if sorted(indices) != list(range(1, len(rows) + 1)):
        raise ValueError("Chapter indices must be unique and continuous from 1.")
    return sorted(rows, key=lambda row: row["chapterindex"])


def navigation(rows: list[dict], position: int) -> dict:
    result = {}
    for prefix, offset in (("pre", -1), ("next", 1)):
        neighbor = position + offset
        if 0 <= neighbor < len(rows):
            result[f"{prefix}chapterid"] = rows[neighbor]["chapterid"]
            result[f"{prefix}url"] = rows[neighbor]["url"]
        else:
            result[f"{prefix}chapterid"] = None
            result[f"{prefix}url"] = None
    return result


def story_paths(root: Path, item: dict) -> tuple[Path, Path]:
    base = root / item.get("dataPath", "data")
    return base, base / f'chapter_{item["id"]}.json'


def repair_story_order(root: Path, story_id: str) -> list[str]:
    """Apply a prevalidated repair, retaining existing chapter identities."""
    catalog = read_json(root / "data/catalog.json")
    item = next((item for item in catalog if str(item["id"]) == str(story_id)), None)
    if item is None:
        raise ValueError(f"Unknown story: {story_id}")
    base, list_path = story_paths(root, item)
    old_rows = read_json(list_path)
    rows = ordered_chapters(old_rows)
    changes = []
    # Validate all input before writing any files.
    for position, row in enumerate(rows):
        path = base / f'{row["chapterid"]}.json'
        detail = read_json(path)
        for field in ("chapterid", "chapterindex", "header"):
            if detail.get(field) != row.get(field):
                raise ValueError(f"List/detail {field} mismatch: {path}")
        if str(detail.get("storeid")) != str(story_id):
            raise ValueError(f"Chapter belongs to a different story: {path}")
        if detail.get("cururl") != row["url"]:
            raise ValueError(f"List/detail URL mismatch: {path}")
        corrected = dict(detail)
        for field, value in navigation(rows, position).items():
            if value is None:
                # Both absent and null mean no adjacent chapter in the reader.
                if corrected.get(field) is not None:
                    corrected.pop(field)
            else:
                corrected[field] = value
        if corrected != detail:
            changes.append((path, corrected))
    if rows != old_rows:
        changes.append((list_path, rows))
    for path, value in changes:
        write_json(path, value)
    return sorted(str(path.relative_to(root)) for path, _ in changes)


def plain_text(content: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", content))).strip()


def audit_repository(root: Path) -> dict:
    report = {"storyCount": 0, "chapterCount": 0, "stories": [], "structuralErrors": [],
              "contentIssues": [], "suggestedNoindexChapterIds": []}
    catalog = read_json(root / "data/catalog.json")
    seen_ids = set()
    for item in catalog:
        base, list_path = story_paths(root, item)
        metadata = read_json(base / f's_{item["id"]}.json')
        rows = read_json(list_path)
        story_id = str(item["id"])
        story = {"id": story_id, "title": metadata["storyname"], "chapterCount": len(rows),
                 "outOfOrderPositions": sum(row["chapterindex"] != i + 1 for i, row in enumerate(rows))}
        report["stories"].append(story)
        report["chapterCount"] += len(rows)
        try:
            ordered_chapters(rows)
        except ValueError as exc:
            report["structuralErrors"].append({"storyId": story_id, "error": str(exc)})
        content_groups = defaultdict(list)
        for position, row in enumerate(rows):
            chapter_id = str(row["chapterid"])
            context = {"storyId": story_id, "chapterId": chapter_id, "header": row["header"]}
            if chapter_id in seen_ids:
                report["structuralErrors"].append({**context, "error": "Duplicate global chapter ID"})
            seen_ids.add(chapter_id)
            path = base / f"{chapter_id}.json"
            try:
                detail = read_json(path)
            except (OSError, ValueError) as exc:
                report["structuralErrors"].append({**context, "error": str(exc)})
                continue
            for field in ("chapterid", "chapterindex", "header"):
                if detail.get(field) != row.get(field):
                    report["structuralErrors"].append({**context, "error": f"List/detail {field} mismatch"})
            if str(detail.get("storeid")) != story_id or detail.get("storyname") != metadata["storyname"]:
                report["structuralErrors"].append({**context, "error": "Story identity mismatch"})
            query = parse_qs(urlsplit(row["url"]).query)
            if detail.get("cururl") != row["url"] or query.get("chapter") != [chapter_id] or query.get("story") != [story_id]:
                report["structuralErrors"].append({**context, "error": "Chapter URL identity mismatch"})
            for field, value in navigation(rows, position).items():
                if detail.get(field) != value:
                    report["structuralErrors"].append({**context, "error": f"Incorrect {field}"})
            content = detail.get("datacontent", "")
            plain = plain_text(content)
            if not plain:
                report["structuralErrors"].append({**context, "error": "Empty chapter content"})
            if len(plain) < 100:
                report["contentIssues"].append({**context, "type": "short-content", "textLength": len(plain),
                                               "status": "Requires source verification; content retained"})
                report["suggestedNoindexChapterIds"].append(chapter_id)
            if "\ufffd" in plain:
                report["contentIssues"].append({**context, "type": "unicode-replacement-character",
                                               "status": "Requires source verification; content retained"})
            content_groups[hashlib.sha256(plain.encode("utf-8")).hexdigest()].append(context)
        for group in content_groups.values():
            if len(group) > 1:
                report["contentIssues"].append({"storyId": story_id, "type": "duplicate-chapter-content",
                                               "chapters": group,
                                               "status": "No trustworthy replacement found locally; content retained"})
                report["suggestedNoindexChapterIds"].extend(chapter["chapterId"] for chapter in group[1:])
    report["storyCount"] = len(catalog)
    report["suggestedNoindexChapterIds"] = sorted(set(report["suggestedNoindexChapterIds"]), key=int)
    report["notes"] = [
        "Suggested noindex IDs are editorial review hints; this report does not change page metadata.",
        "Kiếm Lai printed chapter numbers intentionally follow its PDF source, including repeats and restarts. Do not reorder by title.",
        "No reliable alternative Ngận Thuần Ngận Ái Muội source was found in the local tmp files or manifests during review.",
    ]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repair-story", help="Opt-in repair using explicit, unique chapterindex values")
    parser.add_argument("--report", type=Path, help="Write the deterministic audit JSON to this path")
    args = parser.parse_args()
    root = args.root.resolve()
    changed = repair_story_order(root, args.repair_story) if args.repair_story else []
    report = audit_repository(root)
    if args.report:
        write_json(args.report.resolve(), report)
    print(json.dumps({"changedFiles": changed, "storyCount": report["storyCount"],
                      "chapterCount": report["chapterCount"], "structuralErrors": len(report["structuralErrors"]),
                      "outOfOrderPositions": sum(story["outOfOrderPositions"] for story in report["stories"]),
                      "contentIssues": len(report["contentIssues"])}, ensure_ascii=False, indent=2))
    return 1 if report["structuralErrors"] or any(story["outOfOrderPositions"] for story in report["stories"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
