"""file_processor 单元测试（随迁自源项目 tests/test_file_processor.py）"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from image_sorter.core.file_processor import FileProcessor


class TestGetUniqueFilename(unittest.TestCase):
    def test_no_conflict_returns_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            result = FileProcessor._get_unique_filename(folder, "a.jpg")
            self.assertEqual(result, folder / "a.jpg")

    def test_conflict_adds_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "a.jpg").write_bytes(b"x")
            (folder / "a(1).jpg").write_bytes(b"x")
            result = FileProcessor._get_unique_filename(folder, "a.jpg")
            self.assertEqual(result, folder / "a(2).jpg")


class TestCopyMove(unittest.TestCase):
    def test_copy_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "src"
            dst = tmp / "dst"
            src.mkdir()
            (src / "a.jpg").write_bytes(b"data")
            result = FileProcessor.copy_file_to_folder(src / "a.jpg", dst)
            self.assertIsNotNone(result)
            self.assertTrue((dst / "a.jpg").exists())
            self.assertTrue((src / "a.jpg").exists())  # 复制保留源文件

    def test_move_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp / "src"
            dst = tmp / "dst"
            src.mkdir()
            (src / "a.jpg").write_bytes(b"data")
            result = FileProcessor.move_file_to_folder(src / "a.jpg", dst)
            self.assertIsNotNone(result)
            self.assertFalse((src / "a.jpg").exists())
            self.assertTrue((dst / "a.jpg").exists())


if __name__ == "__main__":
    unittest.main()
