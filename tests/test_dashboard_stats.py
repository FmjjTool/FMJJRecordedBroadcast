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


if __name__ == "__main__":
    unittest.main()
