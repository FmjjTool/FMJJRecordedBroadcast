import json
import tempfile
import unittest
from pathlib import Path

from platforms.base import VideoInfo
from toolbox import collected_store


class CollectedStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "collected.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _item(self, vid, title="作品"):
        return VideoInfo(platform="bilibili", video_id=vid, title=title)

    def test_insert_dedups_and_loads_in_order(self):
        first = self._item("BV1")
        duplicate = self._item("BV1", "重复")
        second = self._item("BV2")

        self.assertEqual(collected_store.insert_items([first, duplicate], db_path=self.db), 1)
        self.assertEqual(collected_store.insert_items([second], db_path=self.db), 1)

        items = collected_store.load_items(db_path=self.db)
        self.assertEqual([i.video_id for i in items], ["BV1", "BV2"])
        self.assertEqual(items[0].title, "作品")

    def test_save_all_rewrites_order(self):
        collected_store.insert_items(
            [self._item("BV1"), self._item("BV2")], db_path=self.db)
        collected_store.save_all(
            [self._item("BV2"), self._item("BV1", "新标题")], db_path=self.db)

        items = collected_store.load_items(db_path=self.db)
        self.assertEqual([i.video_id for i in items], ["BV2", "BV1"])
        self.assertEqual(items[1].title, "新标题")

    def test_ingest_items_projects_only_in_incremental_mode(self):
        seen = []

        def projector(items):
            seen.extend(items)
            return {"bilibili": len(items)}

        items = [self._item("BV1")]
        self.assertEqual(
            collected_store.ingest_items(items, db_path=self.db), {})
        self.assertEqual(seen, [])
        self.assertEqual(len(collected_store.load_items(db_path=self.db)), 1)

        self.assertEqual(
            collected_store.ingest_items(
                [self._item("BV2")], incremental=True,
                db_path=self.db, projector=projector),
            {"bilibili": 1})
        self.assertEqual([i.video_id for i in seen], ["BV2"])

    def test_legacy_json_migrates_and_is_backed_up(self):
        legacy = Path(self.tmp.name) / "collected.json"
        legacy.write_text(json.dumps([{
            "platform": "weibo", "video_id": "W1", "title": "旧数据",
        }], ensure_ascii=False), encoding="utf-8")

        items = collected_store.load_items(db_path=self.db)
        self.assertEqual([i.video_id for i in items], ["W1"])
        self.assertFalse(legacy.exists())
        self.assertTrue(legacy.with_suffix(".json.bak").exists())


if __name__ == "__main__":
    unittest.main()
