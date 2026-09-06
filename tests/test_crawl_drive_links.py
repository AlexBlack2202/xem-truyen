import unittest

from crawl_drive_links import (
    _content_disposition_filename,
    extract_drive_links,
    safe_name,
)


class ExtractDriveLinksTests(unittest.TestCase):
    def test_extracts_supported_links_and_deduplicates_by_id(self):
        content = """
        <a href="https://drive.google.com/file/d/file-123/view?usp=sharing">A</a>
        https://drive.google.com/file/d/file-123/view?resourcekey=secret-key
        https://drive.google.com/drive/folders/folder_456
        https://drive.google.com/open?usp=drive_link&amp;id=legacy789
        https://play.google.com/store/apps/details?id=ignored
        """

        links = extract_drive_links(content)

        self.assertEqual([link.id for link in links], ["file-123", "folder_456", "legacy789"])
        self.assertEqual(links[0].resource_key, "secret-key")
        self.assertEqual(links[1].kind, "folder")

    def test_parses_content_disposition(self):
        self.assertEqual(
            _content_disposition_filename("attachment; filename=book.epub"),
            "book.epub",
        )
        self.assertEqual(
            _content_disposition_filename(
                "attachment; filename*=UTF-8''S%C3%A1ch%20hay.epub"
            ),
            "Sách hay.epub",
        )

    def test_sanitizes_output_name(self):
        self.assertEqual(safe_name('  a/b:c?.epub  ', "fallback"), "a_b_c_.epub")

    def test_repairs_mojibake_in_output_name(self):
        self.assertEqual(
            safe_name("Lá»\x8bch sá»\xad Viá»\x87t Nam.epub", "fallback"),
            "Lịch sử Việt Nam.epub",
        )


if __name__ == "__main__":
    unittest.main()
