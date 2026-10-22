import json
import tempfile
import unittest
from pathlib import Path

from audit_reader_data import audit_repository, navigation, repair_story_order, write_json


class ReaderOrderingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.base = self.root / "data"
        write_json(self.base / "catalog.json", [{"id": "7", "title": "Story"}])
        write_json(self.base / "s_7.json", {"id": "7", "storyname": "Story"})
        # ID order and printed title numbers deliberately differ from chapterindex.
        self.rows = [
            {"chapterid": 80, "chapterindex": 1, "header": "Chương 9", "url": "?type=chapter&chapter=80&story=7&name=old-name"},
            {"chapterid": 20, "chapterindex": 3, "header": "Chương 1", "url": "?type=chapter&chapter=20&story=7&name=kept"},
            {"chapterid": 50, "chapterindex": 2, "header": "Chương 5", "url": "?type=chapter&chapter=50&story=7&name=original"},
        ]
        write_json(self.base / "chapter_7.json", self.rows)
        for i, row in enumerate(self.rows):
            detail = {k: v for k, v in row.items() if k != "url"}
            detail.update({"cururl": row["url"], "storeid": 7, "storyname": "Story",
                           "datacontent": f"<p>Original content of {row['chapterid']} stays intact. {'Text. ' * 20}</p>"})
            detail.update({k: v for k, v in navigation(self.rows, i).items() if v is not None})
            write_json(self.base / f"{row['chapterid']}.json", detail)

    def test_repairs_navigation_preserving_urls_headers_and_content(self):
        before = {row["chapterid"]: json.loads((self.base / f"{row['chapterid']}.json").read_text()) for row in self.rows}
        changed = repair_story_order(self.root, "7")
        self.assertIn("data/chapter_7.json", changed)
        rows = json.loads((self.base / "chapter_7.json").read_text())
        self.assertEqual([row["chapterid"] for row in rows], [80, 50, 20])
        self.assertEqual(rows, sorted(self.rows, key=lambda row: row["chapterindex"]))
        for position, row in enumerate(rows):
            detail = json.loads((self.base / f"{row['chapterid']}.json").read_text())
            nav = navigation(rows, position)
            for field, value in nav.items():
                self.assertEqual(detail.get(field), value)
            self.assertEqual({k: v for k, v in detail.items() if k not in nav},
                             {k: v for k, v in before[row["chapterid"]].items() if k not in nav})
        self.assertEqual(audit_repository(self.root)["structuralErrors"], [])
        self.assertEqual(repair_story_order(self.root, "7"), [])

    def test_ambiguous_indices_fail_before_any_write(self):
        self.rows[2]["chapterindex"] = 3
        write_json(self.base / "chapter_7.json", self.rows)
        before = {path.name: path.read_bytes() for path in self.base.glob("*.json")}
        with self.assertRaisesRegex(ValueError, "unique and continuous"):
            repair_story_order(self.root, "7")
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.base.glob("*.json")})

    def test_audit_detects_broken_navigation(self):
        path = self.base / "80.json"
        detail = json.loads(path.read_text())
        detail["nexturl"] = "?type=chapter&chapter=999&story=7"
        write_json(path, detail)
        errors = audit_repository(self.root)["structuralErrors"]
        self.assertTrue(any(error["chapterId"] == "80" and error["error"] == "Incorrect nexturl" for error in errors))


if __name__ == "__main__":
    unittest.main()
