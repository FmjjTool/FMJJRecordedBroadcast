"""duplicate_detector 单元测试（随迁自源项目 tests/test_duplicate_detector.py，
并补充四层轻量去重漏斗的行为验证）。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from image_sorter.core.duplicate_detector import DuplicateDetector
from image_sorter.core.sqlite_cache import SQLiteCacheStore


class TestGetFilesToKeep(unittest.TestCase):
    def test_latest_returns_first(self):
        d = DuplicateDetector()
        group = [Path("a.jpg"), Path("b.jpg")]
        self.assertEqual(d.get_files_to_keep(group, "latest"), Path("a.jpg"))

    def test_manual_returns_none(self):
        d = DuplicateDetector()
        group = [Path("a.jpg"), Path("b.jpg")]
        self.assertIsNone(d.get_files_to_keep(group, "manual"))

    def test_empty_group_returns_none(self):
        d = DuplicateDetector()
        self.assertIsNone(d.get_files_to_keep([], "latest"))


class TestFourLayerFunnel(unittest.TestCase):
    """四层漏斗：①尺寸 ②头尾 ③最终指纹 ④SQLite缓存"""

    def test_identical_files_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            a = tmp / "a.jpg"
            b = tmp / "b.jpg"
            a.write_bytes(b"same-content" * 100)
            b.write_bytes(b"same-content" * 100)

            d = DuplicateDetector()
            groups = d.detect_duplicates([a, b])
            self.assertEqual(len(groups), 1)
            self.assertEqual(set(groups[0]), {a, b})
            self.assertTrue(d.is_duplicate(a))
            self.assertTrue(d.is_duplicate(b))

    def test_different_content_same_size_not_flagged(self):
        """同尺寸不同内容：第②层头尾哈希即排除"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            a = tmp / "a.jpg"
            b = tmp / "b.jpg"
            a.write_bytes(b"A" * 4096)
            b.write_bytes(b"B" * 4096)

            d = DuplicateDetector()
            groups = d.detect_duplicates([a, b])
            self.assertEqual(groups, [])
            self.assertFalse(d.is_duplicate(a))

    def test_same_head_tail_different_middle_not_flagged(self):
        """头尾64KB相同但中间不同：第③层全量MD5排除（漏斗不误报）"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            block = 64 * 1024
            a = tmp / "a.jpg"
            b = tmp / "b.jpg"
            head = bytes(range(256)) * (block // 256)
            tail = b"T" * block
            middle_a = b"M1" * (block // 2)
            middle_b = b"M2" * (block // 2)
            a.write_bytes(head + middle_a + tail)
            b.write_bytes(head + middle_b + tail)
            self.assertEqual(a.stat().st_size, b.stat().st_size)

            d = DuplicateDetector()
            groups = d.detect_duplicates([a, b])
            self.assertEqual(groups, [])

    def test_identical_videos_detected(self):
        """视频线：相同字节副本（无需 ffmpeg，垃圾字节 probe 不到元数据时
        退化为 video:no-probe 指纹 + 尺寸前缀，仍能正确分组）"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            a = tmp / "a.mp4"
            b = tmp / "b.mp4"
            payload = b"mp4-garbage-but-identical" * 1000
            a.write_bytes(payload)
            b.write_bytes(payload)

            d = DuplicateDetector()
            groups = d.detect_duplicates([a, b])
            self.assertEqual(len(groups), 1)
            self.assertEqual(set(groups[0]), {a, b})

    def test_different_size_videos_not_merged(self):
        """不同尺寸的视频即使都 probe 不到元数据也不能并入同一组（尺寸前缀隔离）"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            a = tmp / "a.mp4"
            b = tmp / "b.mp4"
            a.write_bytes(b"X" * 100)
            b.write_bytes(b"X" * 200)

            d = DuplicateDetector()
            groups = d.detect_duplicates([a, b])
            self.assertEqual(groups, [])

    def test_with_sqlite_md5_cache(self):
        """④层：传入 SQLiteCacheStore 时全量 MD5 走持久缓存，结果一致"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            store = SQLiteCacheStore(tmp / "cache.db")
            a = tmp / "a.jpg"
            b = tmp / "b.jpg"
            a.write_bytes(b"cached-content" * 50)
            b.write_bytes(b"cached-content" * 50)

            d = DuplicateDetector(sqlite_store=store)
            groups = d.detect_duplicates([a, b])
            self.assertEqual(len(groups), 1)

            # MD5 已入缓存表
            cached = store.get_cached_md5(
                file_path=a, size=a.stat().st_size, mtime_ns=a.stat().st_mtime_ns)
            self.assertEqual(len(cached), 32)

    def test_progress_callback_reaches_total(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            a = tmp / "a.jpg"
            b = tmp / "b.jpg"
            a.write_bytes(b"x" * 100)
            b.write_bytes(b"y" * 100)

            calls = []

            def cb(cur, total):
                calls.append((cur, total))

            d = DuplicateDetector()
            d.detect_duplicates([a, b], progress_callback=cb)
            self.assertTrue(calls)
            self.assertEqual(calls[0][1], 2)
            self.assertEqual(calls[-1][0], calls[-1][1])  # 结束时 current == total

    def test_empty_input(self):
        d = DuplicateDetector()
        self.assertEqual(d.detect_duplicates([]), [])


if __name__ == "__main__":
    unittest.main()
