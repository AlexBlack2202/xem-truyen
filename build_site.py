#!/usr/bin/env python3
"""Build the reader as crawlable HTML. Python standard library only."""

from __future__ import annotations

import argparse
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET


DEFAULT_BASE_URL = "https://www.phamduytung.com/xem-truyen/"
MARKER = ".xem-truyen-build"
SAFE_TAGS = {"p", "br", "em", "i", "strong", "b", "blockquote", "hr", "h2", "h3", "ul", "ol", "li", "sup", "sub"}
BLOCKED_TAGS = {"script", "style", "iframe", "object", "svg", "math", "template"}


def escape(value: object) -> str:
    return html.escape(str(value), quote=True)


class StoryHTML(HTMLParser):
    """Keep prose formatting without executing markup from imported books."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.blocked: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in BLOCKED_TAGS:
            self.blocked.append(tag)
        elif not self.blocked and tag in SAFE_TAGS:
            self.output.append(f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in {"br", "hr"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self.blocked:
            if tag == self.blocked[-1]:
                self.blocked.pop()
        elif tag in SAFE_TAGS and tag not in {"br", "hr"}:
            self.output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.blocked:
            self.output.append(html.escape(data, quote=False))


def safe_prose(value: str) -> str:
    parser = StoryHTML()
    parser.feed(value)
    parser.close()
    return "".join(parser.output)


def plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value))).strip()


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.lower().replace("đ", "d"))
    value = value.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read {path}: {error}") from error


def positive_id(value: object, label: str) -> str:
    text = str(value)
    if not re.fullmatch(r"[1-9][0-9]*", text):
        raise ValueError(f"Invalid {label}: {value!r}")
    return text


def load_library(source: Path) -> list[dict]:
    stories = []
    used_slugs: set[str] = set()
    used_ids: set[str] = set()
    for item in read_json(source / "data/catalog.json"):
        story_id = positive_id(item["id"], "story id")
        folder = (source / item.get("dataPath", "data")).resolve()
        if not folder.is_relative_to((source / "data").resolve()):
            raise ValueError(f"Data folder escapes data/: {folder}")
        story = {**item, **read_json(folder / f"s_{story_id}.json")}
        slug = slugify(story["storyname"])
        if not slug or slug in used_slugs or story_id in used_ids:
            raise ValueError(f"Duplicate or empty story route: {story_id} / {slug}")
        used_slugs.add(slug)
        used_ids.add(story_id)
        chapters = read_json(folder / f"chapter_{story_id}.json")
        indexes: set[int] = set()
        ids: set[str] = set()
        for chapter in chapters:
            chapter_id = positive_id(chapter["chapterid"], "chapter id")
            index = int(positive_id(chapter["chapterindex"], "chapter index"))
            if index in indexes or chapter_id in ids:
                raise ValueError(f"Duplicate chapter index/id in {slug}: {chapter}")
            if not (folder / f"{chapter_id}.json").is_file():
                raise ValueError(f"Missing chapter file: {folder / (chapter_id + '.json')}")
            indexes.add(index)
            ids.add(chapter_id)
            chapter.update(chapterid=chapter_id, chapterindex=index, route=f"{slug}/chuong-{index}/")
        chapters.sort(key=lambda chapter: chapter["chapterindex"])
        story.update(id=story_id, slug=slug, folder=folder, chapters=chapters)
        stories.append(story)
    if not stories:
        raise ValueError("The library is empty")
    return stories


READER_JS = r"""(() => {
  'use strict';
  const read = (key, fallback) => { try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; } };
  const store = (key, value) => { try { localStorage.setItem(key, value); } catch (_) {} };
  const content = document.getElementById('reader-content');
  if (content) {
    let size = Number(read('readerSize', '20')) || 20;
    const apply = () => {
      size = Math.max(16, Math.min(28, size));
      document.documentElement.style.setProperty('--reader-size', `${size}px`);
      document.documentElement.style.setProperty('--reader-size-mobile', `${Math.max(16, size - 2)}px`);
      store('readerSize', String(size));
    };
    const focus = document.getElementById('focus-toggle');
    document.body.classList.toggle('focus-mode', read('readerFocus', '0') === '1');
    focus.setAttribute('aria-pressed', String(document.body.classList.contains('focus-mode')));
    document.querySelector('.reader-tools').hidden = false;
    document.getElementById('font-down').addEventListener('click', () => { size -= 1; apply(); });
    document.getElementById('font-up').addEventListener('click', () => { size += 1; apply(); });
    focus.addEventListener('click', () => {
      const enabled = document.body.classList.toggle('focus-mode');
      focus.setAttribute('aria-pressed', String(enabled));
      store('readerFocus', enabled ? '1' : '0');
    });
    document.addEventListener('keydown', event => {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey ||
          event.target.closest('input, textarea, select, button, a, [contenteditable]')) return;
      const direction = event.key === 'ArrowLeft' ? 'prev' : event.key === 'ArrowRight' ? 'next' : '';
      const link = direction && document.querySelector(`a[rel="${direction}"]`);
      if (link) { event.preventDefault(); location.assign(link.href); }
    });
    apply();
  }
  const search = document.getElementById('chapter-search');
  if (search) {
    search.closest('label').hidden = false;
    const normalize = value => value.toLowerCase().replace(/đ/g, 'd').normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    const rows = [...document.querySelectorAll('#chapter-list li')];
    const status = document.getElementById('chapter-status');
    search.addEventListener('input', () => {
      const query = normalize(search.value.trim());
      let count = 0;
      for (const row of rows) { row.hidden = !normalize(row.textContent).includes(query); if (!row.hidden) count++; }
      status.textContent = query ? `${count} chương phù hợp` : `${rows.length} chương hiện có`;
    });
  }
})();
"""


LEGACY_HEAD = """<script>
    if (['type', 'story', 'chapter'].some(key => new URLSearchParams(location.search).has(key))) {
      document.querySelector('meta[name="robots"]').content = 'noindex,follow';
      document.querySelectorAll('link[rel="canonical"],meta[property="og:url"]').forEach(node => node.remove());
      document.documentElement.classList.add('legacy-pending');
    }
  </script>"""


LEGACY_JS = r"""(() => {
  'use strict';
  const params = new URLSearchParams(location.search);
  if (!['type', 'story', 'chapter'].some(key => params.has(key))) return;
  const script = document.currentScript;
  const base = script.dataset.base;
  const fail = () => {
    document.title = 'Không tìm thấy nội dung — Phạm Duy Tùng';
    const main = document.getElementById('main-content');
    main.innerHTML = '<section class="shell notice"><h1>Không tìm thấy nội dung</h1><p>Liên kết truyện hoặc chương này không còn hợp lệ.</p><a class="button button-primary">Trở về thư viện</a></section>';
    main.querySelector('a').href = base;
    document.documentElement.classList.remove('legacy-pending');
  };
  fetch(base + 'assets/legacy-routes.json', {credentials: 'same-origin'})
    .then(response => { if (!response.ok) throw new Error('Manifest unavailable'); return response.json(); })
    .then(routes => {
      const type = params.get('type');
      const story = params.get('story');
      const chapter = params.get('chapter');
      let path;
      if (type === 'story' && routes.stories[story]) path = routes.stories[story] + '/';
      else if (type === 'chapter') {
        const found = routes.chapters[chapter];
        if (found && (!story || story === found[0])) path = routes.stories[found[0]] + '/chuong-' + found[1] + '/';
      }
      if (!path) { fail(); return; }
      location.replace(base + path);
    }).catch(fail);
})();
"""


class Builder:
    def __init__(self, source: Path, output: Path, base_url: str):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment or parsed.username:
            raise ValueError("--base-url must be an absolute http(s) URL without query or fragment")
        if any(part in {".", ".."} for part in parsed.path.split("/")):
            raise ValueError("--base-url cannot contain dot path segments")
        self.source = source.resolve()
        self.output = output
        self.base_url = base_url.rstrip("/") + "/"
        self.base_path = urlsplit(self.base_url).path
        self.template = (source / "templates/page.html").read_text(encoding="utf-8")
        style = re.search(r"<style>(.*?)</style>", self.template, re.S)
        if not style:
            raise ValueError("templates/page.html must contain the shared stylesheet")
        self.css = style.group(1) + "\n.legacy-pending #main-content { visibility: hidden; }\n"
        self.template = self.template[:style.start()] + '<link rel="stylesheet" href="{{BASE_PATH}}assets/site.css">' + self.template[style.end():]
        self.routes: list[str] = []

    def url(self, route: str = "") -> str:
        return self.base_path + route

    def image_url(self, story: dict) -> str:
        image = story.get("imageurl", "")
        parsed = urlsplit(image)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return image
        if not image:
            return ""
        relative = image.removeprefix("./")
        source = (self.source / relative).resolve()
        if not relative.startswith("assets/") or not source.is_relative_to(self.source / "assets") or not source.is_file():
            raise ValueError(f"Invalid or missing local story image: {image}")
        target = self.output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return self.url(relative)

    def page(self, route: str, title: str, description: str, main: str, *, image: str = "", error: bool = False, noindex: bool = False) -> None:
        canonical = self.base_url + route
        image_meta = f'<meta property="og:image" content="{escape(urljoin(self.base_url, image))}">' if image else ""
        values = {
            "TITLE": escape(title), "DESCRIPTION": escape(description[:180]),
            "ROBOTS": "noindex,follow" if error or noindex else "index,follow,max-image-preview:large",
            "OG_TYPE": "article" if "/chuong-" in route else "website",
            "CANONICAL": "" if error else f'<link rel="canonical" href="{escape(canonical)}">\n  <meta property="og:url" content="{escape(canonical)}">',
            "IMAGE_META": image_meta, "MAIN": main, "BASE_PATH": escape(self.base_path),
            "LEGACY_HEAD": LEGACY_HEAD if not route else "",
            "LEGACY_SCRIPT": f'<script defer src="{escape(self.url("assets/legacy.js"))}" data-base="{escape(self.base_path)}"></script>' if not route else "",
        }
        page = re.sub(r"\{\{([A-Z_]+)\}\}", lambda match: values[match[1]], self.template)
        path = self.output / ("404.html" if error else route + "index.html")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page, encoding="utf-8")
        if not error and not noindex:
            self.routes.append(canonical)

    def library_page(self, stories: list[dict]) -> None:
        cards = []
        for story in stories:
            image = story["cover"]
            poster = f'<img src="{escape(image)}" alt="Bìa truyện {escape(story["storyname"])}" width="300" height="385" loading="lazy" decoding="async">' if image else ""
            cards.append(f'''<a class="story-card" href="{self.url(story['slug'] + '/')}">
              <div class="story-cover">{poster}</div><div class="story-info">
              <span class="pill">{escape(story.get('label', 'Truyện'))}</span>
              <h3>{escape(story['storyname'])}</h3><p>{escape(plain_text(story.get('shortcontent', '')))}</p>
              <span class="text-link">{len(story['chapters'])} chương · Xem mục lục →</span></div></a>''')
        main = f'''<section class="shell"><div class="hero"><div class="hero-copy">
          <span class="eyebrow">Thư viện truyện</span><h1>Đọc chậm.<br><span class="gradient-text">Mơ thật xa.</span></h1>
          <p>Một góc nhỏ dành cho những thế giới lớn — chọn câu chuyện bạn yêu thích và tiếp tục hành trình ở bất cứ đâu.</p>
          <a class="button button-primary" href="#thu-vien">Khám phá thư viện →</a></div>
          <div class="hero-art" aria-hidden="true"><div class="book-stack"><div class="book-card book-back"></div><div class="book-card book-front"><svg viewBox="0 0 100 100" fill="none"><path d="M19 27c0-6.6 5.4-12 12-12h15c8.8 0 16 7.2 16 16v54c0-8.8-7.2-16-16-16H19V27Z" fill="rgba(255,255,255,.88)"/><path d="M81 27c0-6.6-5.4-12-12-12h-7v70c0-8.8 7.2-16 16-16h3V27Z" fill="rgba(255,255,255,.62)"/></svg></div></div></div></div>
          <div class="stats"><div class="stat"><strong>{len(stories)}</strong><span>Bộ truyện trong thư viện</span></div><div class="stat"><strong>{sum(len(s['chapters']) for s in stories):,}</strong><span>Chương hiện có</span></div><div class="stat"><strong>Mọi thiết bị</strong><span>Tùy chỉnh cỡ chữ và nền giấy</span></div></div>
          <section id="thu-vien"><div class="section-heading"><div><span class="eyebrow">Thư viện</span><h2>Chọn một thế giới</h2></div></div><div class="story-grid">{''.join(cards)}</div></section></section>'''
        self.page("", "Kho truyện — Phạm Duy Tùng", "Đọc Tru Tiên, Kiếm Lai, Đại Niết Bàn và các truyện trong thư viện Phạm Duy Tùng. Mục lục đầy đủ các chương hiện có, dễ đọc trên điện thoại.", main)

    def story_page(self, story: dict) -> None:
        chapters = story["chapters"]
        name = escape(story["storyname"])
        image = story["cover"]
        poster = f'<div class="story-poster"><img src="{escape(image)}" alt="Bìa truyện {name}" width="300" height="385"></div>' if image else ""
        first = f'<a class="button button-primary" href="{self.url(chapters[0]["route"])}">Đọc từ chương đầu →</a>' if chapters else ""
        rows = "".join(f'<li><a href="{self.url(c["route"])}"><span class="chapter-number">{c["chapterindex"]}</span><span>{escape(c["header"])}</span></a></li>' for c in chapters)
        main = f'''<section class="shell"><a class="back-link" href="{self.url()}">← Trở về thư viện</a>
          <div class="story-hero">{poster}<div class="story-detail"><span class="pill">{escape(story.get('label', 'Truyện'))}</span><h1>{name}</h1>
          <div class="author-line">Tác giả · {escape(story.get('author', 'Đang cập nhật'))}</div>
          <div class="story-summary">{safe_prose(story.get('shortcontent', ''))}</div><div class="button-row">{first}<a class="button button-soft" href="#chapter-list">Xem mục lục</a></div></div></div>
          <section class="chapter-section"><div class="chapter-toolbar"><h2>Danh sách chương</h2>
          <label class="search-wrap" hidden><span class="chapter-home">Tìm chương</span><input id="chapter-search" class="chapter-search" type="search" placeholder="Tìm tên hoặc số chương…" autocomplete="off" aria-label="Tìm tên hoặc số chương"></label></div>
          <p id="chapter-status" class="chapter-status" role="status">{len(chapters)} chương hiện có</p><ul id="chapter-list" class="chapter-list">{rows}</ul></section></section>'''
        self.page(story["slug"] + "/", f"{story['storyname']} — {story.get('author', '')} | Mục lục", f"{story['storyname']} của {story.get('author', '')}. {len(chapters)} chương hiện có. {plain_text(story.get('shortcontent', ''))}", main, image=image)

    def chapter_page(self, story: dict, index: int) -> None:
        chapters = story["chapters"]
        chapter = chapters[index]
        data = read_json(story["folder"] / (chapter["chapterid"] + ".json"))
        if str(data.get("storeid")) != story["id"] or str(data.get("chapterid")) != chapter["chapterid"] or data.get("chapterindex") != chapter["chapterindex"]:
            raise ValueError(f"Chapter identity mismatch: {story['slug']} / {chapter['chapterid']}")
        prose = safe_prose(data.get("datacontent", ""))
        if not plain_text(prose):
            raise ValueError(f"Empty chapter: {story['slug']} / {chapter['chapterid']}")
        story_url = self.url(story["slug"] + "/")
        previous = f'<a class="button button-soft" rel="prev" href="{self.url(chapters[index - 1]["route"])}">← Chương trước</a>' if index else '<span></span>'
        following = f'<a class="button button-primary" rel="next" href="{self.url(chapters[index + 1]["route"])}">Chương sau →</a>' if index + 1 < len(chapters) else '<span></span>'
        nav = f'<nav class="chapter-nav" aria-label="Điều hướng chương">{previous}<a class="chapter-home" href="{story_url}">Mục lục</a>{following}</nav>'
        main = f'''<section class="reader-shell"><a class="back-link" href="{story_url}">← Danh sách chương</a>
          <header class="reader-header"><a class="reader-story" href="{story_url}">{escape(story['storyname'])}</a><h1>{escape(chapter['header'])}</h1></header>
          <div class="reader-tools" aria-label="Tùy chỉnh trình đọc" hidden><span>Không gian đọc</span><div class="tool-buttons">
          <button id="font-down" class="tool-button" type="button" aria-label="Giảm cỡ chữ">A−</button><button id="font-up" class="tool-button" type="button" aria-label="Tăng cỡ chữ">A+</button>
          <button id="focus-toggle" class="tool-button" type="button" aria-label="Chế độ nền giấy" aria-pressed="false">◐</button></div></div>
          {nav}<article id="reader-content" class="reader-card">{prose}</article>{nav}</section>'''
        self.page(chapter["route"], f"{story['storyname']} — {chapter['header']}", f"{story['storyname']} — {chapter['header']}. {plain_text(prose)[:130]}", main, image=story["cover"], noindex=chapter["chapterid"] in self.noindex_ids)

    def build(self) -> dict:
        stories = load_library(self.source)
        audit = self.source / "data/integrity-audit.json"
        self.noindex_ids = set(map(str, read_json(audit).get("suggestedNoindexChapterIds", []))) if audit.is_file() else set()
        chapter_ids = {chapter["chapterid"] for story in stories for chapter in story["chapters"]}
        if not self.noindex_ids.issubset(chapter_ids):
            raise ValueError("Integrity audit references missing chapters")
        assets = self.output / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "site.css").write_text(self.css, encoding="utf-8")
        (assets / "reader.js").write_text(READER_JS, encoding="utf-8")
        (assets / "legacy.js").write_text(LEGACY_JS, encoding="utf-8")
        routes: dict = {"stories": {}, "chapters": {}}
        ambiguous: set[str] = set()
        for story in stories:
            story["cover"] = self.image_url(story)
            routes["stories"][story["id"]] = story["slug"]
            self.story_page(story)
            for i, chapter in enumerate(story["chapters"]):
                if chapter["chapterid"] in routes["chapters"]:
                    ambiguous.add(chapter["chapterid"])
                routes["chapters"][chapter["chapterid"]] = [story["id"], chapter["chapterindex"]]
                self.chapter_page(story, i)
        if ambiguous:
            raise ValueError(f"Ambiguous legacy chapter ids: {', '.join(sorted(ambiguous))}")
        (assets / "legacy-routes.json").write_text(json.dumps(routes, separators=(",", ":")), encoding="utf-8")
        self.library_page(stories)
        self.page("404.html", "Không tìm thấy nội dung — Phạm Duy Tùng", "Liên kết truyện hoặc chương không tồn tại.", f'<section class="shell notice"><h1>Không tìm thấy nội dung</h1><p>Liên kết truyện hoặc chương này không tồn tại.</p><a class="button button-primary" href="{self.url()}">Trở về thư viện</a></section>', error=True)
        namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
        ET.register_namespace("", namespace)
        sitemap = ET.Element(f"{{{namespace}}}urlset")
        for route in sorted(self.routes):
            url = ET.SubElement(sitemap, f"{{{namespace}}}url")
            ET.SubElement(url, f"{{{namespace}}}loc").text = route
        ET.ElementTree(sitemap).write(self.output / "sitemap.xml", encoding="utf-8", xml_declaration=True)
        # A subdirectory robots.txt is informational: the host's /robots.txt must list this sitemap.
        (self.output / "robots.txt").write_text(f"User-agent: *\nAllow: {self.base_path}\nSitemap: {self.base_url}sitemap.xml\n", encoding="utf-8")
        summary = {"stories": len(stories), "chapters": sum(len(s["chapters"]) for s in stories), "indexable_pages": len(self.routes), "noindex_chapters": len(self.noindex_ids), "base_url": self.base_url}
        (self.output / MARKER).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return summary


def build_site(source: Path, output: Path, base_url: str = DEFAULT_BASE_URL) -> dict:
    source, output = source.resolve(), output.resolve()
    if output == source or output in source.parents or (output.is_relative_to(source) and output.parts[len(source.parts):][0] in {"data", "assets", "tests", ".git"}):
        raise ValueError("Output must not replace source files or data")
    if output.exists() and (not output.is_dir() or (any(output.iterdir()) and not (output / MARKER).is_file())):
        raise ValueError(f"Refusing to replace non-generated output directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".xem-truyen-build-", dir=output.parent))
    try:
        result = Builder(source, temporary, base_url).build()
        if output.exists():
            shutil.rmtree(output)
        temporary.replace(output)
        return result
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path, help="Default: <source>/dist")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()
    try:
        result = build_site(args.source, args.output or args.source / "dist", args.base_url)
    except (ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Build failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
