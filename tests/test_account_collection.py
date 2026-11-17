import threading
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import accounts_manager
import platforms
from toolbox import account_collection


class _FakeParser:
    def __init__(self, posts):
        self._posts = posts

    def fetch_user_posts(self, url, count=0, **kwargs):
        return list(self._posts)


class AccountCollectionTests(unittest.TestCase):
    def setUp(self):
        self.accounts = [
            SimpleNamespace(url="https://example.com/u1", name="账号1", kind=""),
            SimpleNamespace(url="https://example.com/u2", name="账号2", kind=""),
        ]
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    def _run(self, posts, accounts=None, **kwargs):
        insert_calls, reveal_calls, export_calls, events = [], [], [], []
        parser = _FakeParser(posts)
        accounts = self.accounts if accounts is None else accounts

        def export_batch(items, incremental):
            export_calls.append((items, incremental))
            return ("已保存", {"bilibili": 1} if incremental else 1)

        with (
            patch.object(accounts_manager, "infer_kind_from_url", return_value=""),
            patch.object(platforms.URLRecognizer, "recognize", return_value="bilibili"),
            patch.object(platforms, "get_parser", return_value=parser),
        ):
            result = account_collection.collect_accounts(
                accounts, 10,
                on_event=events.append,
                insert_items=insert_calls.append,
                reveal_items=lambda items, rid: reveal_calls.append((items, rid)),
                export_batch=export_batch,
                round_id=7,
                **kwargs)
        return result, insert_calls, reveal_calls, export_calls, events

    def test_persist_batch_replaces_final_insert_and_export(self):
        posts = [SimpleNamespace(title="作品", publish_time=self.today, kind="video")]
        persist_calls = []
        (total, log, all_posts), inserts, reveals, exports, events = self._run(
            posts, incremental=True,
            persist_batch=lambda items, inc: (
                persist_calls.append((items, inc)) or ("已保存", {"bilibili": 1})))

        self.assertEqual(total, 2)
        self.assertEqual(len(all_posts), 2)
        self.assertEqual(len(persist_calls), 2)
        self.assertTrue(all(inc for _items, inc in persist_calls))
        self.assertEqual(inserts, [])
        self.assertEqual(exports, [])
        self.assertEqual(len(reveals), 2)

    def test_collects_and_reports_callbacks(self):
        posts = [SimpleNamespace(title="作品", publish_time=self.today, kind="video")]
        (total, log, all_posts), inserts, reveals, exports, events = self._run(
            posts, incremental=True)
        self.assertEqual(total, 2)
        self.assertEqual(len(all_posts), 2)
        self.assertEqual(len(inserts), 2)
        self.assertEqual(len(reveals), 2)
        self.assertTrue(all(rid == 7 for _, rid in reveals))
        self.assertEqual(len(exports), 2)
        self.assertTrue(any(e.get("label") == "全部完成" for e in events))
        self.assertTrue(log)

    def test_date_filter_keeps_in_range_and_unparsed(self):
        posts = [
            SimpleNamespace(title="今天", publish_time=f"{self.today} 12:00:00", kind="video"),
            SimpleNamespace(title="昨天", publish_time=f"{self.yesterday} 12:00:00", kind="video"),
            SimpleNamespace(title="未知", publish_time="", kind="video"),
        ]
        (total, _log, all_posts), inserts, _reveals, _exports, _events = self._run(
            posts, accounts=self.accounts[:1],
            date_start=self.today, date_end=self.today)
        self.assertEqual(total, 2)  # 今天 + 无法解析的时间
        self.assertEqual(len(inserts), 1)
        self.assertEqual([p.title for p in all_posts], ["今天", "未知"])

    def test_cancel_skips_all_accounts(self):
        cancel = threading.Event()
        cancel.set()
        (total, log, all_posts), inserts, reveals, exports, events = self._run(
            [SimpleNamespace(title="作品", publish_time=self.today, kind="video")],
            cancel_event=cancel)
        self.assertEqual((total, all_posts), (0, []))
        self.assertEqual((inserts, reveals, exports), ([], [], []))
        self.assertTrue(any("已取消" in line for line in log))
        self.assertTrue(any(e.get("label") == "已取消" for e in events))


if __name__ == "__main__":
    unittest.main()
