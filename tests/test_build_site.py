import json
from pathlib import Path
import tempfile
import unittest
from html.parser import HTMLParser
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import build_site


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class BuildSiteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "data").mkdir()
        (self.source / "assets").mkdir()
        (self.source / "templates").mkdir()
        (self.source / "templates/page.html").write_text((Path(build_site.__file__).parent / "templates/page.html").read_text())
        (self.source / "assets/cover.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.write("catalog.json", [{"id": "1", "title": "Đọc & Ngẫm", "label": "Tiên hiệp"}])
        self.write("s_1.json", {"id": 1, "storyname": "Đọc & Ngẫm", "author": "A < B", "shortcontent": "<p>Giới thiệu &amp; đọc.</p>", "imageurl": "assets/cover.svg"})
        self.write("chapter_1.json", [
            {"chapterid": 5, "chapterindex": 1, "header": "Chương 1: Một < Hai"},
            {"chapterid": 6, "chapterindex": 2, "header": "Chương 2: Kết"},
        ])
        for chapter_id, index in ((5, 1), (6, 2)):
            self.write(f"{chapter_id}.json", {"chapterid": chapter_id, "chapterindex": index, "storeid": 1, "datacontent": '<p>Nội dung thực &amp; nguyên vẹn.</p><script>alert(1)</script><a href="https://evil.example">Chữ liên kết</a>'})
        self.output = self.root / "dist"

    def write(self, name, data):
        (self.source / "data" / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_content_metadata_links_and_no_json_copies(self):
        result = build_site.build_site(self.source, self.output)
        self.assertEqual(result["chapters"], 2)
        chapter_text = (self.output / "doc-ngam/chuong-1/index.html").read_text()
        self.assertIn("Nội dung thực &amp; nguyên vẹn.", chapter_text)
        self.assertIn("Chương 1: Một &lt; Hai", chapter_text)
        self.assertNotIn("alert(1)", chapter_text)
        self.assertNotIn("evil.example", chapter_text)
        self.assertIn("Chữ liên kết", chapter_text)
        self.assertNotIn("{{", chapter_text)
        page = Page(chapter_text)
        canonical = [attrs["href"] for tag, attrs in page.tags if tag == "link" and attrs.get("rel") == "canonical"]
        self.assertEqual(canonical, [build_site.DEFAULT_BASE_URL + "doc-ngam/chuong-1/"])
        next_links = [attrs["href"] for tag, attrs in page.tags if tag == "a" and attrs.get("rel") == "next"]
        self.assertEqual(next_links, ["/xem-truyen/doc-ngam/chuong-2/"] * 2)
        self.assertFalse(any(attrs.get("rel") == "prev" for tag, attrs in page.tags if tag == "a"))
        self.assertFalse((self.output / "data").exists())
        self.assertEqual(list(self.output.rglob("*.json")), [self.output / "assets/legacy-routes.json"])
        self.assertTrue((self.output / "assets/cover.svg").is_file())
        for html_file in self.output.rglob("*.html"):
            for tag, attrs in Page(html_file.read_text()).tags:
                target = attrs.get("href") or attrs.get("src") or ""
                if not target.startswith("/xem-truyen/"):
                    continue
                path = self.output / urlsplit(target).path.removeprefix("/xem-truyen/")
                self.assertTrue(path.is_file() or (path / "index.html").is_file(), f"Broken link in {html_file}: {target}")
        locations = {node.text for node in ET.parse(self.output / "sitemap.xml").findall(".//{*}loc")}
        self.assertEqual(locations, {build_site.DEFAULT_BASE_URL + route for route in ("", "doc-ngam/", "doc-ngam/chuong-1/", "doc-ngam/chuong-2/")})
        self.assertIn('content="noindex,follow"', (self.output / "404.html").read_text())
        self.assertNotIn('rel="canonical"', (self.output / "404.html").read_text())

    def test_legacy_mapping_and_editorial_noindex(self):
        self.write("integrity-audit.json", {"suggestedNoindexChapterIds": ["6"]})
        result = build_site.build_site(self.source, self.output)
        routes = json.loads((self.output / "assets/legacy-routes.json").read_text())
        self.assertEqual(routes, {"stories": {"1": "doc-ngam"}, "chapters": {"5": ["1", 1], "6": ["1", 2]}})
        self.assertIn('content="noindex,follow"', (self.output / "doc-ngam/chuong-2/index.html").read_text())
        locations = {node.text for node in ET.parse(self.output / "sitemap.xml").findall(".//{*}loc")}
        self.assertNotIn(build_site.DEFAULT_BASE_URL + "doc-ngam/chuong-2/", locations)
        self.assertEqual(result["indexable_pages"], 3)
        self.assertIn("noindex,follow", (self.output / "index.html").read_text())

    def test_broken_data_fails_without_destroying_previous_output(self):
        build_site.build_site(self.source, self.output)
        previous = (self.output / "doc-ngam/chuong-1/index.html").read_text()
        (self.source / "data/5.json").unlink()
        with self.assertRaisesRegex(ValueError, "Missing chapter"):
            build_site.build_site(self.source, self.output)
        self.assertEqual((self.output / "doc-ngam/chuong-1/index.html").read_text(), previous)

    def test_identity_mismatch_and_duplicate_routes_rejected(self):
        self.write("6.json", {"chapterid": 6, "chapterindex": 2, "storeid": 2, "datacontent": "Sai truyện"})
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            build_site.build_site(self.source, self.output)
        self.write("chapter_1.json", [{"chapterid": 5, "chapterindex": 1, "header": "Một"}, {"chapterid": 6, "chapterindex": 1, "header": "Trùng"}])
        with self.assertRaisesRegex(ValueError, "Duplicate chapter"):
            build_site.build_site(self.source, self.output)

    def test_build_cannot_replace_source_or_existing_user_files(self):
        with self.assertRaisesRegex(ValueError, "source files"):
            build_site.build_site(self.source, self.source)
        self.output.mkdir()
        (self.output / "important.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "non-generated"):
            build_site.build_site(self.source, self.output)
        self.assertEqual((self.output / "important.txt").read_text(), "keep")

    def test_alternative_base_path_is_applied_to_routes(self):
        build_site.build_site(self.source, self.output, "http://localhost:8000/reader/")
        text = (self.output / "doc-ngam/chuong-1/index.html").read_text()
        self.assertIn('href="http://localhost:8000/reader/doc-ngam/chuong-1/"', text)
        self.assertIn('href="/reader/doc-ngam/chuong-2/"', text)
        self.assertIn("Sitemap: http://localhost:8000/reader/sitemap.xml", (self.output / "robots.txt").read_text())


if __name__ == "__main__":
    unittest.main()
