import unittest

from convert_txt_to_json import find_links, remove_links, text_to_html


class LinkCleaningTests(unittest.TestCase):
    def test_removes_url_schemes_domains_and_email(self):
        source = "https://example.com/a, www.example.co.uk; thivien.net; foo@example.com"
        self.assertEqual(remove_links(source), ", ; ;")
        self.assertEqual(find_links(remove_links(source)), [])

    def test_removes_ip_and_scheme_relative_links(self):
        source = "http://192.168.1.10:8080/a //example.com/path"
        self.assertEqual(remove_links(source), "")
        self.assertEqual(find_links(remove_links(source)), [])

    def test_preserves_visible_text_for_html_and_markdown_links(self):
        source = '<a href="https://example.com">Trang chủ</a> [Tài liệu](https://example.com/doc)'
        self.assertEqual(remove_links(source), "Trang chủ Tài liệu")

    def test_removes_dangling_attribution_separator(self):
        self.assertEqual(remove_links("Lý Bạch – thivien.net"), "Lý Bạch")

    def test_does_not_remove_dotted_names_without_supported_tld(self):
        source = "MR.SU gặp Mr.Su và nhắc tới Mr.Cao"
        self.assertEqual(remove_links(source), source)

    def test_text_to_html_rejects_no_remaining_links(self):
        self.assertNotIn("example.com", text_to_html("Xem example.com"))
        self.assertEqual(find_links(text_to_html("Xem example.com")), [])


if __name__ == "__main__":
    unittest.main()
