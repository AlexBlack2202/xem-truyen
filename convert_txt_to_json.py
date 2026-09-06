#!/usr/bin/env python3
"""Convert every TXT book under tmp/ to a reader-compatible JSON file."""

from __future__ import annotations

import argparse
import html
import json
import re
import unicodedata
from pathlib import Path


DEFAULT_CHAPTER_ID = 100_000
DEFAULT_STORY_ID = 10_000

TLD_PATTERN = (
    r"(?:ai|app|asia|au|biz|blog|ca|cc|cloud|club|cn|co|com|de|dev|digital|"
    r"edu|eu|fr|gov|id|in|info|io|jp|live|ly|me|mobi|name|net|news|online|"
    r"org|pro|pub|ru|shop|site|store|tech|tk|tv|uk|us|vn|website|world|xyz)"
)
DOMAIN_BOUNDARY = r"(?![a-z]|\.[a-z0-9])"
WEB_LINK_RE = re.compile(
    rf"(?ix)(?:"
    rf"(?:mailto:)?[\w.+-]+@[\w.-]+\.{TLD_PATTERN}{DOMAIN_BOUNDARY}"
    rf"|(?:https?://|ftp://|www\.)[^\s<>\"']+"
    rf"|(?<!:)//(?:[a-z0-9-]+\.)+{TLD_PATTERN}{DOMAIN_BOUNDARY}(?:/[^\s<>\"']*)?"
    rf"|(?<![@\w])(?:[a-z0-9](?:[a-z0-9-]{{0,61}}[a-z0-9])?\.)+"
    rf"{TLD_PATTERN}{DOMAIN_BOUNDARY}(?:/[^\s<>\"']*)?"
    rf"|(?<![@\w])jd\.com{DOMAIN_BOUNDARY}(?:/[^\s<>\"']*)?"
    rf")"
)
IP_LINK_RE = re.compile(
    r"(?x)(?<![\d.])(?:"
    r"(?:25[0-5]|2[0-4]\d|1?\d?\d)\."
    r"){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?::\d{1,5})?(?:/[^\s<>\"']*)?"
)
HTML_ANCHOR_RE = re.compile(r"(?is)<a\b[^>]*>(.*?)</a>")
HTML_ANCHOR_TAG_RE = re.compile(r"(?is)</?a\b[^>]*>")
MARKDOWN_LINK_RE = re.compile(r"(?s)\[([^\]\n]+)\]\(([^)\n]+)\)")
TRAILING_LINK_PUNCTUATION = ".,;:!?)]}"
FILENAME_SOURCE_RE = re.compile(
    r"""(?ix)
    (?:\[\s*)?(?:www\.)?(?:
        123doc|downloadsachmienphi|downloadsach|dtv-ebook|epub|isach|
        khosachonline|khoahoctamlinh|linktruyen|nhasachmienphi|sachmoi|
        sachvui|tailieutuoi|taisachmoi|thuvienpdf|truyenclub
    )\s*\.\s*(?:com|info|net|vn)(?:\s*\])?
    |\b(?:
        downloadsachmienphi|downloadsach|ebook\s*(?:tuấn\s*lê|tuan\s*le)|
        ebooktuanle|kho\s+epub\s+(?:tuấn\s+lê|tuan\s+le)|
        kho\s+sách\s+epub\s+(?:tuấn\s+lê|tuan\s+le)|khosachonline|
        linktruyen|nhasachmienphi|nhatbook|sachmoi|sachvui|tailieutuoi|
        taisachmoi|thuvienpdf|truyenclub|tuanle|vieclamvui
    )\b
    """
)


def story_name(source: Path) -> str:
    name = unicodedata.normalize("NFC", html.unescape(source.stem)).replace("_", " ")
    name = FILENAME_SOURCE_RE.sub(" ", name)
    name = re.sub(r"(?i)(?:https?://|ftp://|www\.)\S+", " ", name)
    name = re.sub(r"^\s*(?:\d{5,}|5169)\s*[-–—:]\s*", "", name)
    name = re.sub(r"\s*[-–—_]\s*\d{9,}(?:\s*[-–—_]\s*\d+)?\s*$", "", name)
    name = re.sub(r"(?i)\b(?:pdf|epub|ebook)\b", " ", name)
    name = re.sub(r"(?i)\(\s*download\s+(?:tại|tai)\s*\)", " ", name)
    name = re.sub(r"(?i)(?:\s*[-–—:]\s*)?(?:download\s*)?(?:tại|tai)\s*$", "", name)
    name = re.sub(r"(?:\s*\(\s*\d+\s*\))+\s*$", "", name)
    name = re.sub(
        r"[ ._-]+(?=[a-zA-Z0-9]{8,}$)(?=[a-zA-Z0-9]*\d)(?=[a-zA-Z0-9]*[a-zA-Z])[a-zA-Z0-9]{8,}$",
        "",
        name,
    )
    name = re.sub(r"\[\s*\]", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" ._-–—")

    # Các tên tải về thường là một slug dài; đổi chúng thành tên dễ đọc.
    if name.count(" ") <= 1 and "-" in name:
        name = re.sub(r"-+", " ", name)
        name = re.sub(r"\s+", " ", name).strip()
    if name and name == name.lower():
        name = name.title()
    return name or "Truyện chưa đặt tên"


def _contains_link(text: str) -> bool:
    return bool(WEB_LINK_RE.search(text) or IP_LINK_RE.search(text))


def find_links(text: str) -> list[str]:
    """Return URL/link-like values that must not remain in chapter content."""
    matches = [match.group(0) for match in WEB_LINK_RE.finditer(text)]
    matches.extend(match.group(0) for match in IP_LINK_RE.finditer(text))
    matches.extend(match.group(0) for match in HTML_ANCHOR_TAG_RE.finditer(text))
    for match in MARKDOWN_LINK_RE.finditer(text):
        if _contains_link(match.group(2)):
            matches.append(match.group(0))
    return matches


def _remove_link_keep_punctuation(match: re.Match[str]) -> str:
    value = match.group(0)
    suffix = ""
    while value and value[-1] in TRAILING_LINK_PUNCTUATION:
        suffix = value[-1] + suffix
        value = value[:-1]
    return suffix


def _replace_markdown_link(match: re.Match[str]) -> str:
    label, target = match.groups()
    return label if _contains_link(target) else match.group(0)


def remove_links(text: str) -> str:
    # Giữ chữ hiển thị, nhưng bỏ đích của link HTML/Markdown và URL viết thẳng.
    text = HTML_ANCHOR_RE.sub(r"\1", text)
    text = MARKDOWN_LINK_RE.sub(_replace_markdown_link, text)
    text = WEB_LINK_RE.sub(_remove_link_keep_punctuation, text)
    text = IP_LINK_RE.sub(_remove_link_keep_punctuation, text)
    # Dọn dấu phân cách bị treo khi nguồn có dạng "Tác giả - example.com".
    text = re.sub(r"[ \t]+[-–—|][ \t]*(?=(?:\n|$|<br\s*/?>))", "", text)
    cleaned_lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t]{2,}", " ", line).rstrip()
        if line.strip() in {"()", "[]", "-", "–", "—"}:
            line = ""
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines)


def text_to_html(text: str) -> str:
    text = remove_links(text)
    remaining_links = find_links(text)
    if remaining_links:
        raise ValueError(f"Nội dung vẫn còn URL/link: {remaining_links[:3]}")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return html.escape(text, quote=False).replace("\n", "<br>")


def remove_previous_outputs(output_root: Path) -> None:
    manifest_path = output_root / "manifest.json"
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    for item in manifest.get("items", []):
        relative_output = item.get("output")
        if not isinstance(relative_output, str):
            continue
        destination = (output_root / relative_output).resolve()
        if destination.is_relative_to(output_root) and destination.suffix == ".json":
            destination.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chuyển toàn bộ file TXT thành JSON tương thích trình đọc."
    )
    parser.add_argument("--input", type=Path, default=Path("tmp"))
    parser.add_argument("--output", type=Path, default=Path("tmp/json"))
    parser.add_argument("--start-chapter-id", type=int, default=DEFAULT_CHAPTER_ID)
    parser.add_argument("--start-story-id", type=int, default=DEFAULT_STORY_ID)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_root = args.input.resolve()
    output_root = args.output.resolve()

    if not input_root.is_dir():
        raise SystemExit(f"Không tìm thấy thư mục đầu vào: {input_root}")

    sources = sorted(
        (path for path in input_root.rglob("*") if path.is_file() and path.suffix.lower() == ".txt"),
        key=lambda path: str(path.relative_to(input_root)).casefold(),
    )
    output_root.mkdir(parents=True, exist_ok=True)
    remove_previous_outputs(output_root)

    manifest_items: list[dict[str, object]] = []
    used_output_paths: set[str] = set()
    for index, source in enumerate(sources):
        relative_source = source.relative_to(input_root)
        name = story_name(source)
        output_stem = name
        duplicate_number = 2
        while True:
            relative_output = relative_source.parent / f"{output_stem}.json"
            output_key = relative_output.as_posix().casefold()
            if output_key not in used_output_paths:
                used_output_paths.add(output_key)
                break
            output_stem = f"{name} ({duplicate_number})"
            duplicate_number += 1
        destination = output_root / relative_output
        destination.parent.mkdir(parents=True, exist_ok=True)

        chapter_id = args.start_chapter_id + index
        store_id = args.start_story_id + index
        content = source.read_text(encoding="utf-8-sig")
        payload = {
            "chapterid": chapter_id,
            "chapterindex": 1,
            "datacontent": text_to_html(content),
            "header": name,
            "nextchapterid": None,
            "prechapterid": None,
            "storeid": store_id,
            "storyname": name,
        }

        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
        manifest_items.append(
            {
                "output": relative_output.as_posix(),
                "chapterid": chapter_id,
                "storeid": store_id,
                "storyname": name,
            }
        )

    manifest = {
        "sourceRoot": str(args.input),
        "outputRoot": str(args.output),
        "count": len(manifest_items),
        "items": manifest_items,
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Đã tạo {len(manifest_items)} file JSON trong {output_root}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
