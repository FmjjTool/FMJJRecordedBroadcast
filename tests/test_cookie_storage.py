"""配置探测竞态与登录路径回归；只使用隔离目录及虚构凭据。"""
import importlib
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from toolbox import config_manager as cm


class CookieStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fmjj-cookie-storage-")
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.appdata = self.root / "roaming"
        self.patches = [patch.object(cm, "get_app_root", return_value=self.app),
                        patch.dict(os.environ, {"APPDATA": str(self.appdata)})]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_parallel_checks_never_switch_a_writable_store(self):
        # 将文件创建对齐，保证所有探测文件同时存在；关闭顺序不能相互干扰。
        workers = 12
        barrier = threading.Barrier(workers)
        original = cm.tempfile.TemporaryFile
        def overlapping_probe(*args, **kwargs):
            probe = original(*args, **kwargs)
            try:
                barrier.wait(timeout=10)
            except BaseException:
                probe.close()
                raise
            return probe
        with patch.object(cm.tempfile, "TemporaryFile", overlapping_probe):
            with ThreadPoolExecutor(max_workers=workers) as pool:
                paths = list(pool.map(lambda _: cm.get_config_dir(), range(workers)))
        self.assertEqual(paths, [self.app / "config"] * workers)
        self.assertEqual(list((self.app / "config").iterdir()), [])
        self.assertFalse(self.appdata.exists())

    def test_true_permission_failure_still_uses_appdata(self):
        with patch.object(cm.tempfile, "TemporaryFile", side_effect=PermissionError):
            path = cm.get_config_dir()
        self.assertEqual(path, self.appdata / "FengMingToolbox" / "config")
        self.assertTrue(path.is_dir())

    def test_missing_appdata_uses_home_instead_of_working_directory(self):
        with patch.dict(os.environ, {"APPDATA": ""}), \
                patch.object(Path, "home", return_value=self.root / "home"), \
                patch.object(cm.tempfile, "TemporaryFile", side_effect=PermissionError):
            path = cm.get_config_dir()
        self.assertEqual(path, self.root / "home" / "AppData" / "Roaming" / "FengMingToolbox" / "config")

    def test_reading_original_logins_does_not_touch_either_store(self):
        primary = self.app / "config" / "cookies.json"
        fallback = self.appdata / "FengMingToolbox" / "config" / "cookies.json"
        for path in (primary, fallback):
            path.parent.mkdir(parents=True)
        platforms = ["douyin", "bilibili", "weibo", "netease", "qq", "kugou",
                     "kuwo", "5sing", "xiaohongshu", "kuaishou", "taobao", "tcdoc", "tcdoc_openapi"]
        primary.write_text(json.dumps({"cookies": {p: "fictional-session-" + p for p in platforms},
                                      "cookie_enabled": True}), encoding="utf-8")
        fallback.write_text(json.dumps({"cookies": {"douyin": "fictional-new-session"}}), encoding="utf-8")
        before = {p: p.read_bytes() for p in (primary, fallback)}
        # 导入时也只能定位到本次隔离路径。全套测试可能已导入 auth，故另补路径覆盖。
        auth = importlib.import_module("auth")
        with patch.object(auth, "COOKIE_FILE", str(cm.get_config_dir() / "cookies.json")), \
                patch.object(auth, "_raw_cache", None), patch.object(auth, "_raw_cache_mtime", None), \
                patch.object(auth, "save_cookies", side_effect=AssertionError("read must not save")):
            self.assertEqual(set(auth.load_cookies()), set(platforms))
            self.assertEqual(auth.get_cookie("douyin"), "fictional-session-douyin")
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(set(primary.parent.iterdir()), {primary})
        self.assertEqual(set(fallback.parent.iterdir()), {fallback})


if __name__ == "__main__":
    unittest.main()
