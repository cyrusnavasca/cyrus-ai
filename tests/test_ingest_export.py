"""File-export readers: CSV, XML and JSON must all produce the same records."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from style_ft.ingest import dummy_export
from style_ft.ingest.parse_export import parse_export


class TestParseExport(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        dummy_export.write_csv(self.tmp / "export.csv")
        dummy_export.write_xml(self.tmp / "export.xml")

    def test_csv_and_xml_agree(self) -> None:
        csv_rows = list(parse_export(self.tmp / "export.csv"))
        xml_rows = list(parse_export(self.tmp / "export.xml"))
        self.assertEqual(len(csv_rows), len(dummy_export.CONVERSATION))
        self.assertEqual([r["text"] for r in csv_rows], [r["text"] for r in xml_rows])
        self.assertEqual([r["direction"] for r in csv_rows], [r["direction"] for r in xml_rows])

    def test_direction_and_timestamp(self) -> None:
        rows = list(parse_export(self.tmp / "export.csv"))
        self.assertEqual(rows[0]["direction"], "in")
        self.assertEqual(rows[1]["direction"], "out")
        self.assertTrue(rows[0]["timestamp"].startswith("20"), rows[0]["timestamp"])

    def test_json_input(self) -> None:
        path = self.tmp / "export.json"
        path.write_text(json.dumps([
            {"body": "hello there friend", "date": 1700000000, "address": "+1555", "type": "2"}
        ]), encoding="utf-8")
        rows = list(parse_export(path))
        self.assertEqual(rows[0]["text"], "hello there friend")
        self.assertEqual(rows[0]["direction"], "out")

    def test_column_map_override(self) -> None:
        path = self.tmp / "weird.csv"
        path.write_text("Blah,Whatever\nsome long message body here,x\n", encoding="utf-8")
        rows = list(parse_export(path, column_map={"text": ["Blah"]}))
        self.assertEqual(rows[0]["text"], "some long message body here")


if __name__ == "__main__":
    unittest.main(verbosity=2)
