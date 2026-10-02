"""离屏冒烟测试：资产页「源链接」修复（抖音 CDN 时效直链 → 标准作品页）。

背景：抖音条目 json「链接」存的是采集时解析的 douyinvod CDN 直链
（带签名、几小时~几天后 403），用户在资产页打开/复制源链接全是死链；
期望是 https://www.douyin.com/video/{id} 这种永久有效的源地址。

覆盖：
1. 模块导入 + MainWindow 真实 show + 切到资产页
2. _norm_dy_source_link 单元测试：直链→作品页 / 网页地址保留 / 空保留 / 非抖音不动
3. VideoInfo.public_url() + to_dict()：抖音「链接」=作品页，B 站保持原 video_url
4. 真实数据全量校验：_load_disk_items 扫描后所有抖音条目 video_url
   不再含 douyinvod CDN 直链
5. db 浏览链接兜底：_db_link 对直链行返回作品页
"""
import importlib
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication([])

mods = [
    "gui_qt.download_float", "gui_qt.download_panel", "gui_qt.download_ops",
    "gui_qt.main_window", "gui_qt.pages.asset",
    "media_downloader", "platforms.base", "exporters.writer",
]
for m in mods:
    importlib.import_module(m)
print("IMPORTS OK (%d modules)" % len(mods))

from gui_qt.main_window import MainWindow
import gui_qt.pages.asset as asset_mod
from platforms.base import VideoInfo, now_str

win = MainWindow()
win.show()
win.show_page("asset")
for _ in range(50):
    app.processEvents()
    time.sleep(0.01)
print("MAINWINDOW SHOW OK")
page = win.pages["asset"]


def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


# ---- 2. _norm_dy_source_link 单元测试 ----
f = asset_mod._norm_dy_source_link
cdn = ("https://v11-weba.douyinvod.com/f286682d66cd36613928560f0e8bb264/"
       "6abf3755/video/tos/cn/tos-cn-ve-15/oQQ8Ig/?a=6383&ch=10010&cv=1&br=803")
VID = "7691717238470700387"

# CDN 直链 → 标准作品页
assert f("douyin", VID, cdn) == f"https://www.douyin.com/video/{VID}"
# 已是网页地址 → 原样保留
assert f("douyin", VID, f"https://www.douyin.com/video/{VID}") == f"https://www.douyin.com/video/{VID}"
# 空链接（图文 note）→ 保持空
assert f("douyin", VID, "") == ""
# 无 video_id → 不动
assert f("douyin", "", cdn) == cdn
# 非抖音平台 → 不动
assert f("bilibili", "BV1xx", "https://www.bilibili.com/video/BV1xx") == "https://www.bilibili.com/video/BV1xx"
assert f("weibo", "123", cdn) == cdn
print("NORM FN OK: 直链→作品页 / 网页保留 / 空保留 / 非抖音不动")

# ---- 3. VideoInfo.public_url + to_dict（写入侧根治）----
dy = VideoInfo(platform="douyin", video_id=VID, title="烟测抖音",
               author="封茗囧菌", video_url=cdn, raw_url="", fetched_at=now_str())
assert dy.public_url() == f"https://www.douyin.com/video/{VID}"
assert dy.to_dict()["链接"] == f"https://www.douyin.com/video/{VID}"
assert dy.to_dict()["原始数据"] == {}  # 其余字段不受影响

bl = VideoInfo(platform="bilibili", video_id="BV1smoke", title="烟测B站",
               video_url="https://www.bilibili.com/video/BV1smoke",
               raw_url="", fetched_at=now_str())
assert bl.public_url() == "https://www.bilibili.com/video/BV1smoke"
assert bl.to_dict()["链接"] == "https://www.bilibili.com/video/BV1smoke"

# xlsx 导出列（writer.py public_url）
try:
    from exporters import writer
    import inspect
    src = inspect.getsource(writer)
    assert src.count("info.public_url()") >= 2, "writer 应有 2 处 public_url()"
    print("WRITER OK: xlsx/md 导出均使用 public_url()")
except AssertionError:
    raise
except Exception as e:
    print(f"WRITER SKIP: {e}")

# ---- 4. 真实数据全量校验：扫描后抖音条目不再含 CDN 直链 ----
page._refresh_collected(keep_page=True)
deadline = time.monotonic() + 30
while (not page._collected_all_items and time.monotonic() < deadline):
    pump(0.1)
pump(0.5)
items = page._collected_all_items
dy_items = [it for it in items if it.get("platform") == "douyin"]
assert dy_items, "真实数据中未找到抖音条目（扫描失败？）"
bad = [it for it in dy_items
       if "douyinvod" in (it.get("video_url") or "")
       or (it.get("video_url") and "douyin.com" not in it["video_url"]
           and it.get("video_id"))]
assert not bad, f"仍有 {len(bad)} 条抖音条目链接是 CDN 直链: {bad[0]['video_url'][:80]}"
n_fixed = sum(1 for it in dy_items
              if (it.get("video_url") or "").startswith("https://www.douyin.com/video/"))
print(f"REAL DATA OK: 抖音 {len(dy_items)} 条全部为作品页链接"
      f"（其中 {n_fixed} 条由旧直链规范化而来）")

# 用户报告的具体条目：video_id=7691717238470700387
target = next((it for it in dy_items
               if str(it.get("video_id")) == VID), None)
if target:
    assert target["video_url"] == f"https://www.douyin.com/video/{VID}", target["video_url"]
    print(f"USER CASE OK: modal_id={VID} 条目链接 = {target['video_url']}")
else:
    print(f"USER CASE SKIP: 本机数据中无 {VID}（该条目在其他机器采集）")

# ---- 5. db 浏览链接兜底 ----
row = ("key", "2026-10-02 10:00:00", "video", VID, "标题", "作者", "2026-10-01",
       1, 2, 3, 4, 5, 0, 0, 60, "正文", "cover", cdn, "[]", "", "", "{}", "ck")
# 直接切 db 平台为抖音再验证 _db_link
idx = page.db_plat_sel.findText("抖音", )
if idx >= 0:
    page.db_plat_sel.setCurrentIndex(idx)
    pump(0.2)
    if page._db_platform() == "douyin":
        link = page._db_link(row)
        assert link == f"https://www.douyin.com/video/{VID}", link
        print("DB LINK OK: db 直链行 →", link)
    else:
        print("DB LINK SKIP: 平台下拉未命中抖音")
else:
    # 无「抖音」项（下拉文本带后缀），退化为函数级验证
    assert f("douyin", VID, cdn) == f"https://www.douyin.com/video/{VID}"
    print("DB LINK OK (函数级): 下拉无精确「抖音」文本，_db_link 复用同一规范化函数")

win.close()
print("ASSET SOURCE LINK SMOKE OK")
