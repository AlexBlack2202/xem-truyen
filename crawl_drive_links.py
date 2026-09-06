#!/usr/bin/env python3
"""Extract and download public Google Drive links from an HTML/text file."""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import html
import json
import os
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse

import requests
from ftfy import fix_text


DRIVE_URL_RE = re.compile(r"https?://drive\.google\.com/[^\s<>\"']+", re.IGNORECASE)
FILE_PATH_RE = re.compile(r"/file/d/(?P<id>[A-Za-z0-9_-]+)", re.IGNORECASE)
FOLDER_PATH_RE = re.compile(
    r"/drive/(?:u/\d+/)?folders/(?P<id>[A-Za-z0-9_-]+)", re.IGNORECASE
)
JS_STRING_RE = re.compile(r"'((?:[^'\\]|\\.)*)'")
FOLDER_MIME = "application/vnd.google-apps.folder"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)


@dataclass(frozen=True)
class DriveLink:
    kind: str
    id: str
    url: str
    resource_key: str | None = None


@dataclass(frozen=True)
class DownloadTarget:
    id: str
    url: str
    relative_dir: str = "files"
    suggested_name: str | None = None
    resource_key: str | None = None


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Tách các link Google Drive trong HTML/text và tải file công khai "
            "vào thư mục tmp."
        )
    )
    parser.add_argument("input", type=Path, help="File HTML/text chứa các link")
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("tmp"), help="Thư mục đầu ra (mặc định: tmp)"
    )
    parser.add_argument(
        "--workers", type=int, default=3, help="Số file tải đồng thời (mặc định: 3)"
    )
    parser.add_argument(
        "--retries", type=int, default=3, help="Số lần thử lại cho mỗi request"
    )
    parser.add_argument(
        "--timeout", type=int, default=120, help="Read timeout, tính bằng giây"
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Chỉ xử lý N link đầu tiên (dùng để kiểm thử)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Chỉ tách link, không truy cập mạng hoặc tải file"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Tải lại cả khi file đích đã tồn tại"
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers phải lớn hơn hoặc bằng 1")
    if args.retries < 1:
        parser.error("--retries phải lớn hơn hoặc bằng 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit phải lớn hơn hoặc bằng 1")
    return args


def canonical_url(kind: str, file_id: str, resource_key: str | None = None) -> str:
    if kind == "folder":
        base = f"https://drive.google.com/drive/folders/{file_id}"
    else:
        base = f"https://drive.google.com/file/d/{file_id}/view"
    if resource_key:
        return f"{base}?resourcekey={resource_key}"
    return base


def _resource_key(raw_url: str) -> str | None:
    decoded = html.unescape(raw_url)
    query = dict(parse_qsl(urlparse(decoded).query))
    return query.get("resourcekey")


def extract_drive_links(content: str) -> list[DriveLink]:
    """Return unique Drive file/folder links, preserving source order."""
    decoded = html.unescape(content)
    unique: list[DriveLink] = []
    positions: dict[tuple[str, str], int] = {}
    for match in DRIVE_URL_RE.finditer(decoded):
        raw_url = match.group(0).rstrip(".,;:!?)\]}\u201d\u2019")
        parsed = urlparse(raw_url)
        file_match = FILE_PATH_RE.search(parsed.path)
        folder_match = FOLDER_PATH_RE.search(parsed.path)
        query = dict(parse_qsl(parsed.query))
        if file_match:
            kind, drive_id = "file", file_match.group("id")
        elif folder_match:
            kind, drive_id = "folder", folder_match.group("id")
        elif parsed.path.rstrip("/").lower() in {"/open", "/uc"} and query.get("id"):
            kind, drive_id = "file", query["id"]
        else:
            continue

        resource_key = _resource_key(raw_url)
        link = DriveLink(
            kind=kind,
            id=drive_id,
            url=canonical_url(kind, drive_id, resource_key),
            resource_key=resource_key,
        )
        key = (link.kind, link.id)
        if key not in positions:
            positions[key] = len(unique)
            unique.append(link)
        elif resource_key and not unique[positions[key]].resource_key:
            # Some duplicated render blocks omit the key while another copy has it.
            unique[positions[key]] = link
    return unique


def request_with_retry(
    session: requests.Session,
    url: str,
    *,
    retries: int,
    timeout: int,
    stream: bool = False,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = session.get(
                url,
                allow_redirects=True,
                headers=headers,
                stream=stream,
                timeout=(15, timeout),
            )
            if response.status_code in {429, 500, 502, 503, 504}:
                response.close()
                raise requests.HTTPError(f"HTTP {response.status_code}")
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2 ** (attempt - 1), 8))
    assert last_error is not None
    raise last_error


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def _decode_drive_json_string(encoded: str) -> Any:
    # _DRIVE_ivd stores JSON inside a JavaScript single-quoted string.
    decoded = bytes(encoded.replace(r"\/", "/"), "utf-8").decode("unicode_escape")
    return json.loads(decoded)


def parse_drive_folder_page(content: str, folder_id: str) -> tuple[str, list[dict[str, str]]]:
    script = next(
        (match.group(0) for match in re.finditer(r"<script\b[^>]*>.*?</script>", content, re.I | re.S)
         if "_DRIVE_ivd" in match.group(0)),
        None,
    )
    if script is None:
        raise RuntimeError(
            "Không đọc được thư mục. Hãy đặt quyền Google Drive thành 'Anyone with the link'."
        )
    strings = list(JS_STRING_RE.finditer(script))
    if len(strings) < 2:
        raise RuntimeError("Không tìm thấy dữ liệu _DRIVE_ivd trong trang thư mục")

    folder_array = _decode_drive_json_string(strings[1].group(1))
    entries = [] if not folder_array or folder_array[0] is None else folder_array[0]
    children: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, list) or len(entry) < 4:
            continue
        children.append({"id": str(entry[0]), "name": str(entry[2]), "mime": str(entry[3])})

    title_match = re.search(r"<title>(.*?)</title>", content, re.I | re.S)
    title = html.unescape(title_match.group(1)).strip() if title_match else folder_id
    title = re.sub(r"\s+-\s+Google Drive\s*$", "", title, flags=re.I)
    return title or folder_id, children


def safe_name(value: str, fallback: str) -> str:
    value = fix_text(html.unescape(value)).replace("\x00", "")
    value = re.sub(r"[\\/:*?\"<>|]", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:180] or fallback


def crawl_folder(
    link: DriveLink,
    *,
    retries: int,
    timeout: int,
    seen_folders: set[str] | None = None,
    parent_dir: Path = Path("folders"),
) -> list[DownloadTarget]:
    seen_folders = seen_folders if seen_folders is not None else set()
    if link.id in seen_folders:
        return []
    seen_folders.add(link.id)

    session = new_session()
    try:
        response = request_with_retry(
            session, link.url, retries=retries, timeout=timeout
        )
        folder_name, children = parse_drive_folder_page(response.text, link.id)
        response.close()
    finally:
        session.close()

    if len(children) >= 50:
        raise RuntimeError(
            "Thư mục có ít nhất 50 mục; trang Drive công khai có thể đã bị phân trang, "
            "nên crawler dừng để tránh báo hoàn tất thiếu dữ liệu."
        )

    relative_dir = parent_dir / safe_name(folder_name, link.id)
    targets: list[DownloadTarget] = []
    for child in children:
        if child["mime"] == FOLDER_MIME:
            child_link = DriveLink(
                kind="folder",
                id=child["id"],
                url=canonical_url("folder", child["id"]),
            )
            targets.extend(
                crawl_folder(
                    child_link,
                    retries=retries,
                    timeout=timeout,
                    seen_folders=seen_folders,
                    parent_dir=relative_dir,
                )
            )
        else:
            targets.append(
                DownloadTarget(
                    id=child["id"],
                    url=canonical_url("file", child["id"]),
                    relative_dir=str(relative_dir),
                    suggested_name=child["name"],
                )
            )
    return targets


def _content_disposition_filename(header: str | None) -> str | None:
    if not header:
        return None
    utf8_match = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", header, re.I)
    if utf8_match:
        return unquote(utf8_match.group(1).strip().strip('"'))
    plain_match = re.search(r"filename\s*=\s*(?:\"([^\"]+)\"|([^;]+))", header, re.I)
    if plain_match:
        return unquote((plain_match.group(1) or plain_match.group(2)).strip())
    return None


def _confirmation_url(response: requests.Response) -> str | None:
    content = response.text
    download_match = re.search(r'"downloadUrl":"([^"]+)', content)
    if download_match:
        return (
            download_match.group(1)
            .replace(r"\u003d", "=")
            .replace(r"\u0026", "&")
            .replace(r"\/", "/")
        )

    form_match = re.search(
        r'<form[^>]+(?:id="download-form"[^>]+)?action="([^"]+)"[^>]*>(.*?)</form>',
        content,
        re.I | re.S,
    )
    if form_match:
        params = re.findall(
            r'<input[^>]+name="([^"]+)"[^>]+value="([^"]*)"', form_match.group(2), re.I
        )
        return f"{html.unescape(form_match.group(1))}?{urlencode(params)}"

    href_match = re.search(r'href="(/uc\?export=download[^"]+)', content, re.I)
    if href_match:
        return urljoin("https://drive.google.com", html.unescape(href_match.group(1)))
    return None


def open_download_response(
    session: requests.Session,
    target: DownloadTarget,
    *,
    retries: int,
    timeout: int,
    range_start: int = 0,
) -> requests.Response:
    params = {"export": "download", "id": target.id, "confirm": "t"}
    if target.resource_key:
        params["resourcekey"] = target.resource_key
    url = "https://drive.google.com/uc?" + urlencode(params)
    headers = {"Range": f"bytes={range_start}-"} if range_start else None

    for _ in range(3):
        response = request_with_retry(
            session,
            url,
            retries=retries,
            timeout=timeout,
            stream=True,
            headers=headers,
        )
        if _content_disposition_filename(response.headers.get("Content-Disposition")):
            return response
        next_url = _confirmation_url(response)
        response.close()
        if not next_url:
            raise RuntimeError(
                "Google Drive không trả về file (link không public, vượt quota hoặc đã bị xóa)"
            )
        url = next_url
    raise RuntimeError("Google Drive yêu cầu xác nhận tải quá nhiều lần")


class PathAllocator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claimed: dict[Path, str] = {}

    def allocate(self, directory: Path, filename: str, file_id: str) -> Path:
        candidate = directory / safe_name(filename, file_id)
        with self._lock:
            owner = self._claimed.get(candidate)
            if owner is None or owner == file_id:
                self._claimed[candidate] = file_id
                return candidate
            suffix = candidate.suffix
            candidate = candidate.with_name(f"{candidate.stem}__{file_id[:8]}{suffix}")
            self._claimed[candidate] = file_id
            return candidate


def download_one(
    target: DownloadTarget,
    *,
    output_dir: Path,
    retries: int,
    timeout: int,
    overwrite: bool,
    allocator: PathAllocator,
) -> dict[str, Any]:
    session = new_session()
    response: requests.Response | None = None
    part_path: Path | None = None
    try:
        # Ask Drive for the real filename before choosing the final path.
        response = open_download_response(
            session, target, retries=retries, timeout=timeout
        )
        drive_name = _content_disposition_filename(response.headers.get("Content-Disposition"))
        filename = target.suggested_name or drive_name or target.id
        destination_dir = output_dir / target.relative_dir
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = allocator.allocate(destination_dir, filename, target.id)

        if destination.exists() and destination.stat().st_size > 0 and not overwrite:
            response.close()
            return {
                "id": target.id,
                "url": target.url,
                "status": "skipped",
                "path": str(destination),
                "bytes": destination.stat().st_size,
            }

        part_path = destination.with_name(f".{destination.name}.{target.id}.part")
        existing_bytes = part_path.stat().st_size if part_path.exists() and not overwrite else 0
        if existing_bytes:
            response.close()
            response = open_download_response(
                session,
                target,
                retries=retries,
                timeout=timeout,
                range_start=existing_bytes,
            )
        append = existing_bytes > 0 and response.status_code == 206
        if existing_bytes and not append:
            existing_bytes = 0
        mode = "ab" if append else "wb"
        with part_path.open(mode) as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)
        response.close()
        if part_path.stat().st_size == 0:
            raise RuntimeError("Google Drive trả về file rỗng (0 byte)")
        os.replace(part_path, destination)
        return {
            "id": target.id,
            "url": target.url,
            "status": "downloaded",
            "path": str(destination),
            "bytes": destination.stat().st_size,
        }
    except Exception as exc:  # Continue downloading other independent links.
        return {
            "id": target.id,
            "url": target.url,
            "status": "error",
            "error": str(exc),
            "part_path": str(part_path) if part_path and part_path.exists() else None,
        }
    finally:
        if response is not None:
            response.close()
        session.close()


def dedupe_targets(targets: Iterable[DownloadTarget]) -> list[DownloadTarget]:
    result: list[DownloadTarget] = []
    seen: set[str] = set()
    for target in targets:
        if target.id not in seen:
            result.append(target)
            seen.add(target.id)
    return result


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    output_dir = args.output.expanduser().resolve()
    if not input_path.is_file():
        print(f"Không tìm thấy file đầu vào: {input_path}", file=sys.stderr)
        return 2

    links = extract_drive_links(input_path.read_text(encoding="utf-8", errors="replace"))
    if args.limit is not None:
        links = links[: args.limit]
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "links.json", [asdict(link) for link in links])

    file_count = sum(link.kind == "file" for link in links)
    folder_count = sum(link.kind == "folder" for link in links)
    print(f"Đã tách {file_count} file và {folder_count} thư mục Google Drive.")
    print(f"Danh sách link: {output_dir / 'links.json'}")
    if args.dry_run:
        print("Dry run hoàn tất, chưa tải dữ liệu.")
        return 0

    targets = [
        DownloadTarget(
            id=link.id,
            url=link.url,
            resource_key=link.resource_key,
        )
        for link in links
        if link.kind == "file"
    ]
    folder_errors: list[dict[str, str]] = []
    for folder in (link for link in links if link.kind == "folder"):
        print(f"Đang đọc thư mục: {folder.url}")
        try:
            targets.extend(
                crawl_folder(folder, retries=args.retries, timeout=args.timeout)
            )
        except Exception as exc:
            folder_errors.append({"id": folder.id, "url": folder.url, "error": str(exc)})
            print(f"  Lỗi thư mục {folder.id}: {exc}", file=sys.stderr)

    targets = dedupe_targets(targets)
    print(f"Bắt đầu tải {len(targets)} file với {args.workers} luồng...")
    allocator = PathAllocator()
    started_at = utc_now()
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(
                download_one,
                target,
                output_dir=output_dir,
                retries=args.retries,
                timeout=args.timeout,
                overwrite=args.overwrite,
                allocator=allocator,
            ): target
            for target in targets
        }
        for index, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            result = future.result()
            results.append(result)
            marker = "OK" if result["status"] != "error" else "LỖI"
            detail = result.get("path") or result.get("error", "")
            print(f"[{index}/{len(targets)}] {marker}: {detail}")

    counts = {
        status: sum(result["status"] == status for result in results)
        for status in ("downloaded", "skipped", "error")
    }
    manifest = {
        "input": str(input_path),
        "output": str(output_dir),
        "started_at": started_at,
        "finished_at": utc_now(),
        "counts": counts,
        "folder_errors": folder_errors,
        "items": sorted(results, key=lambda item: item["id"]),
    }
    write_json(output_dir / "manifest.json", manifest)
    print(
        "Hoàn tất: "
        f"{counts['downloaded']} tải mới, {counts['skipped']} đã có, "
        f"{counts['error']} lỗi."
    )
    print(f"Manifest: {output_dir / 'manifest.json'}")
    return 1 if counts["error"] or folder_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
