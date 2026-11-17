import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from toolbox import increment_db
from toolbox.dedup import collect_key


class IncrementDedupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "increment.db"
        self._orig_db_path = increment_db.db_path
        increment_db.db_path = lambda: self.db_path

    def tearDown(self):
        increment_db.db_path = self._orig_db_path
        self.tmp.cleanup()

    @staticmethod
    def _post(platform, video_id, title="同一内容", author="同一作者"):
        return {"platform": platform, "video_id": video_id,
                "title": title, "author": author}

    def test_collect_key_priority(self):
        self.assertEqual(collect_key({"video_id": "v", "video_url": "u",
                                      "raw_url": "r"}), "v")
        self.assertEqual(collect_key({"video_url": "u", "raw_url": "r"}), "u")
        self.assertEqual(collect_key(SimpleNamespace(raw_url="r")), "r")
        self.assertEqual(collect_key({}), "")

    def test_cross_platform_duplicates_are_kept(self):
        increment_db.save_incremental([
            self._post("weibo", "w1"),
            self._post("bilibili", "b1"),
        ])
        with closing(sqlite3.connect(self.db_path)) as conn:
            weibo = conn.execute('SELECT COUNT(*) FROM "weibo"').fetchone()[0]
            bili = conn.execute('SELECT COUNT(*) FROM "bilibili"').fetchone()[0]
            keys = conn.execute(
                'SELECT content_key FROM "weibo" UNION SELECT content_key FROM "bilibili"'
            ).fetchall()
        self.assertEqual((weibo, bili), (1, 1))
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0][0])

    def test_same_platform_same_key_is_deduped(self):
        post = self._post("weibo", "w1")
        increment_db.save_incremental([post])
        increment_db.save_incremental([post])
        with closing(sqlite3.connect(self.db_path)) as conn:
            count = conn.execute('SELECT COUNT(*) FROM "weibo"').fetchone()[0]
        self.assertEqual(count, 1)

    def test_migration_backfills_without_cross_row_delete(self):
        with increment_db._lock:
            conn = increment_db._connect()
            try:
                increment_db._ensure_table(conn, "weibo")
                conn.executemany(
                    'INSERT INTO "weibo" (dedup_key, title, author) VALUES (?, ?, ?)',
                    [("w1", "同一内容", "同一作者"), ("w2", "同一内容", "同一作者")])
                conn.commit()
                increment_db._ensure_migrated(conn)
                count = conn.execute('SELECT COUNT(*) FROM "weibo"').fetchone()[0]
                keys = conn.execute(
                    'SELECT content_key FROM "weibo" ORDER BY rowid').fetchall()
            finally:
                conn.close()
        self.assertEqual(count, 2)
        self.assertEqual(len(keys), 2)
        self.assertTrue(all(row[0] for row in keys))


if __name__ == "__main__":
    unittest.main()
