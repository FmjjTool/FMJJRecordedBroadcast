"""冒烟：资产管理「文件夹」筛选 + 本地资源逐级读取。

覆盖：
1. scan_assets 的 depth 语义（0 / 1 / 负数=全部）与 rel_dir 归属；
2. 同目录结果连续（分组渲染不乱序）；
3. 本地资源「文件夹」下拉：含各级父目录、树序、缩进；选父目录含子层级；
4. 懒加载渲染出现文件夹小标题；
5. 已获取资源 _rel_src_dir：已获取数据/{平台}_文件、B站资源/类别；
6. _dir_under 前缀匹配（父目录含子层级、不误配同前缀目录）；
7. 端到端：on_show → 列表渲染 → 文件夹下拉有选项 → 选中后列表全部落在该目录下。

运行：python _smoke_asset_folder_nav.py
"""
import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from PySide6.QtWidgets import QApplication, QLabel, QWidget

_fails = []


def check(name, cond, extra=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        _fails.append(name)


class _StubApp:
    def toast(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


qapp = QApplication.instance() or QApplication([])

from gui_qt.pages.asset import AssetPage, scan_assets

print("== 1. scan_assets 深度语义 ==")
tmp = tempfile.mkdtemp(prefix="fmjj_scan_depth_")
try:
    def touch(rel):
        p = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").close()
        return p

    touch("a.jpg")
    touch("sub1/b.jpg")
    touch("sub1/deep/c.mp4")
    touch("sub1/e.txt")          # 非媒体，必须忽略
    touch("sub2/d.mp3")
    touch("__pycache__/x.jpg")   # 跳过目录

    d0 = scan_assets(tmp, depth=0)
    d1 = scan_assets(tmp, depth=1)
    dall = scan_assets(tmp, depth=-1)
    check("depth=0 只扫当前层", len(d0) == 1 and d0[0]["name"] == "a.jpg", str([x["name"] for x in d0]))
    check("depth=1 含一层子目录", sorted(x["name"] for x in d1) == ["a.jpg", "b.jpg", "d.mp3"],
          str([x["name"] for x in d1]))
    check("depth=-1 递归到底", sorted(x["name"] for x in dall) == ["a.jpg", "b.jpg", "c.mp4", "d.mp3"],
          str([x["name"] for x in dall]))
    check("跳过 __pycache__", all("__pycache__" not in x["path"] for x in dall))
    rels = {x["name"]: x["rel_dir"] for x in dall}
    check("rel_dir 归属正确",
          rels.get("a.jpg") == "" and rels.get("b.jpg") == "sub1"
          and rels.get("c.mp4") == "sub1/deep" and rels.get("d.mp3") == "sub2",
          str(rels))
    # 同目录连续：sub1 的两项必须相邻
    names = [x["name"] for x in dall]
    check("同目录结果连续", abs(names.index("b.jpg") - names.index("c.mp4")) == 1, str(names))
    check("limit 生效", len(scan_assets(tmp, limit=2, depth=-1)) == 2)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("== 2. 本地资源：文件夹下拉 + 逐级筛选 ==")
page = AssetPage(_StubApp())
fake = [
    {"path": "/x/1.jpg", "name": "1.jpg", "size": 1, "type": "image", "mtime": "2026-01-01 00:00", "rel_dir": ""},
    {"path": "/x/sub1/2.jpg", "name": "2.jpg", "size": 1, "type": "image", "mtime": "2026-01-01 00:00", "rel_dir": "sub1"},
    {"path": "/x/sub1/deep/3.mp4", "name": "3.mp4", "size": 1, "type": "video", "mtime": "2026-01-01 00:00", "rel_dir": "sub1/deep"},
    {"path": "/x/sub2/4.mp3", "name": "4.mp3", "size": 1, "type": "audio", "mtime": "2026-01-01 00:00", "rel_dir": "sub2"},
]
page._all_files = fake
page._shown = 0
page._rebuild_local_dirs(fake)
items = [page.local_dir_sel.itemData(i) for i in range(page.local_dir_sel.count())]
check("下拉含全部 + 各级父目录", items == ["", "sub1", "sub1/deep", "sub2"], str(items))
texts = [page.local_dir_sel.itemText(i) for i in range(page.local_dir_sel.count())]
check("子目录带缩进", texts[2].startswith("　") and texts[1].count("　") == 0, str(texts))
check("tooltip 保留完整路径", page.local_dir_sel.itemData(2, 3) == "sub1/deep")

page.local_dir_sel.setCurrentIndex(items.index("sub1"))
qapp.processEvents()
check("选 sub1 含其下层级", len(page._local_view()) == 2, str([f["name"] for f in page._local_view()]))
page.local_dir_sel.setCurrentIndex(items.index("sub1/deep"))
qapp.processEvents()
check("下钻 sub1/deep", len(page._local_view()) == 1, str([f["name"] for f in page._local_view()]))
page.local_dir_sel.setCurrentIndex(0)
qapp.processEvents()
check("回到全部文件夹", len(page._local_view()) == 4)

# 分组标题：全部视图里应出现 根目录 / sub1 / sub1/deep / sub2 四条标题
headers = []
for i in range(page.local_layout.count()):
    w = page.local_layout.itemAt(i).widget()
    if isinstance(w, QLabel) and w.text().startswith("📁"):
        headers.append(w.text())
check("渲染出 4 个文件夹分组标题", len(headers) == 4, str(headers))
check("状态行含文件夹数", "文件夹" in page.local_status.text(), page.local_status.text())

print("== 3. 已获取资源：来源目录计算 ==")
root = os.path.join(tempfile.gettempdir(), "fmjj_srcprobe")
video_dir = os.path.join(root, "已获取数据")
download_root = root
cases = {
    os.path.join(video_dir, "抖音_文件", "20260101_douyin_x.json"): "抖音_文件",
    os.path.join(video_dir, "微博_媒体", "封茗囧菌", "20260101_x.jpg"): "微博_媒体/封茗囧菌",
    os.path.join(video_dir, "20260101_weibo_out.json"): "",
    os.path.join(download_root, "B站资源", "收藏夹", "包A", "v.mp4"): "B站资源/收藏夹/包A",
}
for p, want in cases.items():
    got = page._rel_src_dir(p, video_dir, download_root)
    check(f"src_dir({p.replace(root, '…')})", got == want, f"{got!r} != {want!r}")
check("空路径安全", page._rel_src_dir("", video_dir, download_root) == "")

print("== 4. _dir_under 前缀匹配 ==")
check("同目录命中", page._dir_under("抖音_媒体", "抖音_媒体"))
check("子目录命中", page._dir_under("抖音_媒体/封茗囧菌", "抖音_媒体"))
check("不误配同前缀目录", not page._dir_under("抖音_媒体2", "抖音_媒体"))
check("空筛选全通过", page._dir_under("任意", ""))

print("== 5. 端到端：已获取资源列表 + 文件夹筛选 ==")
page.on_show()
deadline = time.time() + 60
while time.time() < deadline:
    qapp.processEvents()
    if page._collected_all_items:
        break
    time.sleep(0.02)
n = len(page._collected_all_items or [])
check("列表渲染出数据", n > 0, f"{n} 条")
opts = [page.folder_sel.itemData(i) for i in range(page.folder_sel.count())]
opts = [o for o in opts if isinstance(o, str)]   # 跳过 insertSeparator 的空项
dir_opts = [o for o in opts if o.startswith("dir:")]
sub_opts = [o for o in opts if o.startswith("sub:")]
check("下拉含 download 分类目录", len(dir_opts) >= 5, str(dir_opts[:6]))
check("下拉含已获取数据内目录", len(sub_opts) > 5, f"{len(sub_opts)} 项")
check("条目都带 src_dir/src_dirs 字段",
      all("src_dir" in it and "src_dirs" in it for it in page._collected_all_items[:50]))
pick_sub = next((o for o in sub_opts if "/" in o), sub_opts[0] if sub_opts else None)
if pick_sub:
    rel = pick_sub[4:]
    page.folder_sel.setCurrentIndex(page.folder_sel.findData(pick_sub))
    deadline = time.time() + 60
    while time.time() < deadline:
        qapp.processEvents()
        if page._collected_all_items:
            break
        time.sleep(0.02)
    got = page._collected_all_items
    ok = all(page._item_in_folder(it, rel) for it in got)
    check(f"选「{rel}」后条目都命中该目录", ok and len(got) > 0, f"{len(got)} 条")
    page.folder_sel.setCurrentIndex(0)
    deadline = time.time() + 60
    while time.time() < deadline:
        qapp.processEvents()
        if len(page._collected_all_items) >= n:
            break
        time.sleep(0.02)
    check("切回全部文件夹恢复条目数", len(page._collected_all_items) == n,
          f"{len(page._collected_all_items)} vs {n}")

print("== 6. download 分类目录 → 文件清单视图 ==")
from toolbox.config_manager import get_export_dir

dl_root = get_export_dir()
cand = [o[4:] for o in dir_opts]
cand = [c for c in cand if c != "已获取数据"]
check("找到可测的下载目录", bool(cand), str(cand[:4]))
if cand:
    target_dir = cand[0]
    idx = [i for i in range(page.folder_sel.count())
           if page.folder_sel.itemData(i) == f"dir:{target_dir}"][0]
    page.folder_sel.setCurrentIndex(idx)
    deadline = time.time() + 60
    while time.time() < deadline:
        qapp.processEvents()
        if page._view_mode == "files" and page._plain_files:
            break
        time.sleep(0.02)
    check("切到 files 视图", page._view_mode == "files", page._view_mode)
    check(f"列出 {target_dir} 的文件", len(page._plain_files) > 0, f"{len(page._plain_files)} 个")
    check("文件项字段齐全",
          all(k in page._plain_files[0] for k in ("path", "name", "size", "type", "rel_dir")))
    check("批量下载按钮已禁用", not page.batch_dl_btn.isEnabled())
    check("列表渲染出文件行", page.collected_layout.count() > 1,
          f"布局项 {page.collected_layout.count()}")
    # 搜索过滤对文件清单生效
    page.search_entry.setText("zzz_不存在的名字")
    page._apply_search()
    deadline = time.time() + 30
    while time.time() < deadline:
        qapp.processEvents()
        if not page._plain_files:
            break
        time.sleep(0.02)
    check("搜索对文件清单生效", len(page._plain_files) == 0, f"{len(page._plain_files)} 个")
    page.search_entry.setText("")
    page._apply_search()
    deadline = time.time() + 30
    while time.time() < deadline:
        qapp.processEvents()
        if page._plain_files:
            break
        time.sleep(0.02)
    # 切回条目视图
    page.folder_sel.setCurrentIndex(0)
    deadline = time.time() + 60
    while time.time() < deadline:
        qapp.processEvents()
        if page._view_mode == "entries" and page._collected_all_items:
            break
        time.sleep(0.02)
    check("切回条目视图", page._view_mode == "entries" and len(page._collected_all_items) == n,
          f"{page._view_mode} / {len(page._collected_all_items)}")
    check("批量下载按钮恢复可用", page.batch_dl_btn.isEnabled())

print()
print("== 7. 本地资源：目录锁定（记住 + 只读 + 重启恢复 + 固定子目录） ==")
from toolbox.config_manager import load_convert_config, save_convert_config

_cfg_backup = load_convert_config()
_root = tempfile.mkdtemp(prefix="fmjj_lock_probe_")
try:
    os.makedirs(os.path.join(_root, "重要文件", "封面"), exist_ok=True)
    open(os.path.join(_root, "重要文件", "top.jpg"), "wb").close()
    open(os.path.join(_root, "重要文件", "封面", "c1.jpg"), "wb").close()
    inner = os.path.join(_root, "重要文件")

    # 保存 → 重新开页恢复
    page.dir_entry.setText(inner)
    page._folder = inner
    page._set_dir_locked(True)
    page._save_dir_var(inner)
    cfg = load_convert_config()
    check("锁定状态写入配置",
          cfg.get("local_folder") == inner and cfg.get("local_folder_locked") is True,
          str(cfg.get("local_folder"))[:60])
    page2 = AssetPage(_StubApp())
    check("重新开页恢复目录", page2.dir_entry.text() == inner, page2.dir_entry.text())
    check("重新开页恢复锁定", getattr(page2, "_dir_locked", False) is True)
    check("锁定时路径只读", page2.dir_entry.isReadOnly())
    check("锁定时选择按钮禁用", not page2.pick_btn.isEnabled())
    page2._toggle_dir_lock()
    check("解锁后路径可编辑", not page2.dir_entry.isReadOnly() and page2.pick_btn.isEnabled())
    check("解锁写回配置", load_convert_config().get("local_folder_locked") is False)

    # 目录不存在：不静默沿用
    page3 = AssetPage(_StubApp())
    page3._save_dir_var(os.path.join(_root, "不存在的目录"))
    page4 = AssetPage(_StubApp())
    check("失效目录不恢复进输入框", page4.dir_entry.text() == "",
          page4.dir_entry.text())
    check("失效目录给出提示", "不存在" in page4.local_status.text(), page4.local_status.text())

    # 固定子目录为扫描根目录（📌）：逐级下钻后把该子目录锁成根
    page5 = AssetPage(_StubApp())
    page5.dir_entry.setText(inner)
    page5._folder = inner
    page5._all_files = scan_assets(inner, depth=-1)
    page5._rebuild_local_dirs(page5._all_files)
    idx = page5.local_dir_sel.findData("封面")
    check("扫描后下拉出现「封面」", idx > 0, str([page5.local_dir_sel.itemData(i)
                                                for i in range(page5.local_dir_sel.count())]))
    page5.local_dir_sel.setCurrentIndex(idx)
    qapp.processEvents()
    _before = sorted(f["name"] for f in page5._all_files)
    page5._pin_local_dir()
    deadline = time.time() + 20
    while time.time() < deadline:
        qapp.processEvents()
        if sorted(f["name"] for f in page5._all_files) != _before:
            break
        time.sleep(0.02)
    check("📌 把子目录锁为扫描根", page5.dir_entry.text() == os.path.join(inner, "封面"),
          page5.dir_entry.text())
    check("📌 后自动锁定", page5._dir_locked is True)
    check("📌 后重新扫描该子目录",
          [f["name"] for f in page5._all_files] == ["c1.jpg"],
          str([f["name"] for f in page5._all_files]))
    check("📌 写入配置", load_convert_config().get("local_folder") == os.path.join(inner, "封面"))
finally:
    shutil.rmtree(_root, ignore_errors=True)
    save_convert_config(_cfg_backup)   # 还原用户真实配置

print()
if _fails:
    print("失败项：" + "; ".join(_fails))
    sys.exit(1)
print("全部通过")
