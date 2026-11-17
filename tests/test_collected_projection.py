import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from toolbox import increment_db
from toolbox.collected_projection import project_items


class CollectedProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "increment.db"
        self._orig = increment_db.db_path
        increment_db.db_path = lambda: self.db_path

    def tearDown(self):
        increment_db.db_path = self._orig
        self.tmp.cleanup()

    def test_projects_items_to_increment_db(self):
        added = project_items([
            {"platform": "weibo", "video_id": "v1", "title": "标题", "author": "作者"},
        ])
        self.assertEqual(added.get("weibo"), 1)
        with closing(sqlite3.connect(self.db_path)) as conn:
            count = conn.execute('SELECT COUNT(*) FROM "weibo"').fetchone()[0]
        self.assertEqual(count, 1)

    def test_projection_is_idempotent(self):
        item = {"platform": "weibo", "video_id": "v1", "title": "标题", "author": "作者"}
        project_items([item])
        added = project_items([item])
        self.assertEqual(added.get("weibo", 0), 0)


if __name__ == "__main__":
    unittest.main()
