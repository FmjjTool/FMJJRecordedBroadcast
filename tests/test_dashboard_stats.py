import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from toolbox import dashboard_stats


class DashboardStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config_dir = self.root / "config"
        self.config_dir.mkdir()
        self.export_dirs = {}
        for name in ("tcdoc", "tracker", "music_tracker"):
            path = self.root / name
            path.mkdir()
            self.export_dirs[name] = path

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_env(self):
        return (
            patch.object(dashboard_stats, "get_config_dir", return_value=self.config_dir),
            patch.object(dashboard_stats, "get_export_subdir",
                         side_effect=lambda name: self.export_dirs.get(name, self.root / name)),
            patch.object(dashboard_stats, "tracked_accounts_snapshot", return_value=[]),
        )

    def test_increment_counts_and_week_series(self):
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        with closing(sqlite3.connect(self.config_dir / "increment.db")) as conn:
            conn.execute("CREATE TABLE bilibili (fetched_at TEXT)")
            conn.executemany(
                "INSERT INTO bilibili (fetched_at) VALUES (?)",
                [(f"{today} 12:00:00",), (f"{yesterday} 12:00:00",)])
            conn.commit()

        p1, p2, p3 = self._patch_env()
        with p1, p2, p3:
            data = dashboard_stats.load_dashboard_data()

        self.assertEqual(data["stats"]["platform_items"], 2)
        self.assertEqual(data["by_source"]["bilibili"], 2)
        self.assertEqual(data["counts"][-1], 1)
        self.assertEqual(sum(data["counts"]), 2)
        self.assertEqual(data["week_total"], 2)
        self.assertEqual(data["today_total"], 1)
        self.assertEqual(len(data["labels"]), 7)

    def test_missing_databases_return_zeroed_dto(self):
        p1, p2, p3 = self._patch_env()
        with p1, p2, p3:
            data = dashboard_stats.load_dashboard_data()
        self.assertEqual(data["stats"]["platform_items"], 0)
        self.assertEqual(data["stats"]["chaohua_posts"], 0)
        self.assertEqual(data["stats"]["tcdoc_rows"], 0)
        self.assertEqual(data["stats"]["asset_files"], 0)
        self.assertEqual(data["stats"]["tracked_total"], 0)
        self.assertEqual(data["week_total"], 0)
        self.assertEqual(data["today_total"], 0)


    def test_tcdoc_docs_falls_back_to_local_db(self):
        tcdoc = self.export_dirs["tcdoc"]
        with closing(sqlite3.connect(tcdoc / "tcdoc.db")) as conn:
            conn.execute("CREATE TABLE daily (doc_key TEXT)")
            conn.executemany("INSERT INTO daily VALUES (?)", [("a",), ("b",)])
            conn.commit()
        with patch.object(dashboard_stats, "get_export_subdir", return_value=tcdoc), \
                patch("toolbox.tcdoc.config.load_docs", return_value=[]):
            docs = dashboard_stats.tcdoc_docs()
        self.assertEqual([d["key"] for d in docs], ["a", "b"])

    def test_load_tcdoc_history_reads_daily_and_revisions(self):
        tcdoc = self.export_dirs["tcdoc"]
        with closing(sqlite3.connect(tcdoc / "tcdoc.db")) as conn:
            conn.execute(
                "CREATE TABLE daily (id INTEGER PRIMARY KEY, doc_key TEXT, ts TEXT,"
                " date TEXT, views INTEGER, views_delta INTEGER, members INTEGER,"
                " members_delta INTEGER, readers INTEGER)")
            conn.executemany(
                "INSERT INTO daily (doc_key, ts, date, views, views_delta, members,"
                " members_delta, readers) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [("a", "2026-11-17 10:00:00", "2026-11-17", 10, 1, 5, 0, 3),
                 ("a", "2026-11-16 10:00:00", "2026-11-16", 9, 1, 5, 0, 2)])
            conn.commit()
        with closing(sqlite3.connect(tcdoc / "revisions.db")) as conn:
            conn.execute(
                "CREATE TABLE revision_rows (doc_key TEXT, ts_ms INTEGER)")
            conn.execute("INSERT INTO revision_rows VALUES ('a', 1700000000000)")
            conn.commit()
        with patch.object(dashboard_stats, "get_export_subdir", return_value=tcdoc):
            data = dashboard_stats.load_tcdoc_history("a")
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["rows"]), 2)
        self.assertEqual(sum(data["revisions"].values()), 1)

    def test_load_account_history_uses_source_store(self):
        with patch("tracker.accounts.load_history", return_value=[1, 2, 3]):
            self.assertEqual(
                dashboard_stats.load_account_history(
                    {"source": "tracker", "name": "a"}), [1, 2])
        with patch("music_account.accounts.load_history", return_value=[4]):
            self.assertEqual(
                dashboard_stats.load_account_history(
                    {"source": "music", "name": "b"}), [4])


if __name__ == "__main__":
    unittest.main()
