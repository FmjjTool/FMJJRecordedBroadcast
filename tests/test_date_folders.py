import os
import tempfile
import unittest

from toolbox.date_folders import (
    count_date_subfolders,
    date_folder_value,
    folder_date_label,
    has_date_subfolders,
    is_date_folder,
    match_date_folder,
    scan_date_folders,
    today_str,
)


class DateFolderTests(unittest.TestCase):
    def test_match_prefix_and_reject_non_date(self):
        self.assertEqual(match_date_folder("20260923").group(1), "20260923")
        self.assertEqual(match_date_folder("20260923_备份").group(1), "20260923")
        self.assertIsNone(match_date_folder("2026-09-23"))
        self.assertIsNone(match_date_folder("notes"))
        self.assertIsNone(match_date_folder(""))

    def test_date_folder_value_and_label(self):
        self.assertEqual(date_folder_value("20260923_备份"), "20260923")
        self.assertEqual(date_folder_value("2026-09-23"), "")
        self.assertEqual(folder_date_label("20260923"), "2026-09-23")
        self.assertEqual(folder_date_label("2026-09-23"), "2026-09-23")

    def test_is_date_folder_exact(self):
        self.assertTrue(is_date_folder("20260923"))
        self.assertTrue(is_date_folder("20260923_备份"))
        self.assertFalse(is_date_folder("20260923_备份", exact=True))
        self.assertTrue(is_date_folder("20260923", exact=True))
        self.assertFalse(is_date_folder("2026-09-23"))

    def test_scan_sorts_skips_and_ignores_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("20260103", "20260101_备份", "20260102", "notes", "2026-01-04"):
                os.mkdir(os.path.join(tmp, name))
            open(os.path.join(tmp, "20260105"), "w").close()

            result = scan_date_folders(tmp, skip_subfolders={"20260102"})
            self.assertEqual(
                result,
                [("20260101_备份", "2026-01-01"), ("20260103", "2026-01-03")],
            )

    def test_scan_missing_root_returns_empty(self):
        self.assertEqual(scan_date_folders(os.path.join(tempfile.gettempdir(), "不存在")), [])

    def test_has_and_count_date_subfolders(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(has_date_subfolders(tmp))
            self.assertEqual(count_date_subfolders(tmp), 0)
            os.mkdir(os.path.join(tmp, "20260101"))
            os.mkdir(os.path.join(tmp, "20260102_备份"))
            os.mkdir(os.path.join(tmp, "notes"))
            open(os.path.join(tmp, "20260103"), "w").close()
            self.assertTrue(has_date_subfolders(tmp))
            self.assertEqual(count_date_subfolders(tmp), 2)
            self.assertFalse(has_date_subfolders(os.path.join(tmp, "notes")))

    def test_today_str_is_valid_yyyymmdd(self):
        value = today_str()
        self.assertEqual(len(value), 8)
        self.assertTrue(value.isdigit())
        self.assertEqual(date_folder_value(value), value)


if __name__ == "__main__":
    unittest.main()
