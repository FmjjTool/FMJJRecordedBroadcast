"""media_hash_manager 单元测试（随迁自源项目 tests/test_media_hash_manager.py）"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from image_sorter.core.media_hash_manager import MediaHashManager
from image_sorter.core.sqlite_cache import SQLiteCacheStore


class TestMediaHashManager(unittest.TestCase):
    def test_calculate_md5_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = SQLiteCacheStore(tmp / "cache.db")
            manager = MediaHashManager(sqlite_store=store)
            f = tmp / "a.jpg"
            f.write_bytes(b"hello world")

            md5_1 = manager.calculate_md5(f)
            self.assertEqual(len(md5_1), 32)

            # 第二次应命中 SQLite 缓存（结果一致）
            md5_2 = manager.calculate_md5(f)
            self.assertEqual(md5_1, md5_2)

    def test_calculate_md5_missing_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = SQLiteCacheStore(tmp / "cache.db")
            manager = MediaHashManager(sqlite_store=store)
            self.assertEqual(manager.calculate_md5(tmp / "missing.jpg"), "")

    def test_build_hash_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = SQLiteCacheStore(tmp / "cache.db")
            manager = MediaHashManager(sqlite_store=store)
            f1 = tmp / "a.jpg"
            f2 = tmp / "b.jpg"
            f1.write_bytes(b"111")
            f2.write_bytes(b"222")
            hashes = manager.build_hash_set([f1, f2])
            self.assertEqual(len(hashes), 2)


if __name__ == "__main__":
    unittest.main()
