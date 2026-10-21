"""冒烟：资产管理「已获取资源」扫描索引缓存——性能 + 与原始实现等价。

1. 清空索引 → 首次加载（全量解析建索引）计时；
2. 二次加载（全部命中缓存）计时；
3. 命中结果与首扫一致；单个 json 改动只重建它；
4. 与 git HEAD 原始实现逐条目对比（忽略 raw_data / 缓存私有键 / 组内文件顺序）；
5. 媒体目录缓存：新增/删除文件与子目录立刻反映；
6. 端到端：on_show → 骨架 → 后台扫描 → 分页渲染。

运行：python _smoke_asset_scan_cache.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
ROOT = os.path.dirname(os.path.abspath(__file__))

from PySide6.QtWidgets import QApplication, QWidget

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

from gui_qt.pages.asset import AssetPage
from toolbox import asset_index

# 原始实现：从 git HEAD 抽出到临时模块，跑完即删（拿不到就跳过对比）
_ORIG_TMP = os.path.join(ROOT, "src", "gui_qt", "pages", "_asset_origcheck_tmp.py")
orig_mod = None
try:
    import subprocess
    src = subprocess.check_output(["git", "show", "HEAD:src/gui_qt/pages/asset.py"],
                                  cwd=ROOT, stderr=subprocess.DEVNULL)
    with open(_ORIG_TMP, "wb") as f:
        f.write(src)
    from gui_qt.pages import _asset_origcheck_tmp as orig_mod
except Exception as e:
    print(f"  [SKIP] 取不到 git HEAD 原版（{e}），跳过等价性对比")
finally:
    try:
        os.remove(_ORIG_TMP)
    except OSError:
        pass

print("== 0. 准备：清空索引 ==")
asset_index.clear()
n, dn, sz = asset_index.stats()
check("索引已清空", n == 0 and dn == 0, f"json={n}, 媒体目录={dn}")

page = AssetPage(_StubApp())

print("== 1. 首次加载（建索引） ==")
t = time.perf_counter()
a = page._load_disk_items()
first = time.perf_counter() - t
n, dn, sz = asset_index.stats()
check("索引已写入", n > 1000 and dn > 10, f"json={n}, 媒体目录={dn}, db={sz/1e6:.1f}MB")
print(f"  条目 {len(a)}，耗时 {first:.2f}s")

print("== 2. 二次加载（全命中缓存） ==")
t = time.perf_counter()
b = page._load_disk_items()
warm = time.perf_counter() - t
print(f"  条目 {len(b)}，耗时 {warm:.2f}s")
check("缓存后条目数一致", len(a) == len(b), f"{len(a)} vs {len(b)}")
check("缓存后明显更快（< 首次 40%）", warm < first * 0.4,
      f"{first:.2f}s -> {warm:.2f}s")

print("== 3. 命中与首扫结果逐字段一致 ==")


def norm(items):
    out = []
    for it in items:
        d = {k: v for k, v in it.items()
             if k not in ("raw_data", "_raw_video", "_pic_n", "_live_n", "_v")}
        # 同组媒体文件按路径排序后比较：组内顺序只影响缩略图取哪一张，
        # 原实现顺序由 Path.rglob 的文件系统枚举顺序决定（不可复现），
        # 新版为「目录内按名排序 + 先进先出」的确定性顺序。
        d["media_files"] = sorted((m["path"], m["type"], m.get("date_part", ""),
                                   m.get("subdir", ""), m.get("pkg", ""))
                                  for m in it.get("media_files") or [])
        out.append(d)
    return out


na, nb = norm(a), norm(b)
check("逐条目相等", na == nb)
if na != nb:
    for i, (x, y) in enumerate(zip(na, nb)):
        if x != y:
            print("   首个差异 idx", i)
            for k in set(x) | set(y):
                if x.get(k) != y.get(k):
                    print("   ", k, repr(x.get(k))[:120], "!=", repr(y.get(k))[:120])
            break

print("== 4. 与原始实现（git HEAD）结果一致 ==")
if orig_mod is None:
    print("  [SKIP] 无原始实现可比")
else:
    opage = orig_mod.AssetPage(_StubApp())
    t = time.perf_counter()
    o = opage._load_disk_items()
    orig_t = time.perf_counter() - t
    print(f"  原版耗时 {orig_t:.2f}s，条目 {len(o)}")
    check("条目数一致", len(o) == len(a), f"原版 {len(o)} vs 新版 {len(a)}")
    no = norm(o)
    check("顺序与内容逐字段一致", no == na,
          f"首个差异：" + (next((str(i) for i, (x, y) in enumerate(zip(no, na)) if x != y),
                              "无" if no == na else "?")) if no != na else "")
    if no != na:
        for i, (x, y) in enumerate(zip(no, na)):
            if x != y:
                print("   首个差异 idx", i, x.get("file_path"), "|", y.get("file_path"))
                for k in set(x) | set(y):
                    if x.get(k) != y.get(k):
                        print("   ", k, repr(x.get(k))[:150], "!=", repr(y.get(k))[:150])
                break

print("== 5. 改动一个 json 只重建它 ==")
import json
target = a[len(a) // 2]["file_path"]
st = os.stat(target)
os.utime(target, (st.st_atime, st.st_mtime + 5))
t = time.perf_counter()
c = page._load_disk_items()
delta = time.perf_counter() - t
check("改动后条目数不变", len(c) == len(a), f"{len(c)} vs {len(a)}")
print(f"  改动后耗时 {delta:.2f}s（初扫 {first:.2f}s / 缓存 {warm:.2f}s）")
check("单文件改动不需全量重建", delta < first * 0.6, f"{delta:.2f}s")
os.utime(target, (st.st_atime, st.st_mtime))

print("== 6. 媒体目录缓存：新增/删除文件能立刻反映 ==")
import tempfile
import shutil
tmp = tempfile.mkdtemp(prefix="fmjj_media_probe_")
try:
    sub = os.path.join(tmp, "抖音_媒体", "测试账号")
    os.makedirs(sub)

    def scan():
        mm = {}
        page._scan_media_files(tmp, mm)
        return sorted((m["path"], m["type"]) for fs in mm.values() for m in fs)

    p1 = os.path.join(sub, "20250101_测试账号_标题_1.jpg")
    open(p1, "wb").close()
    n1 = scan()
    check("新增 1 个媒体文件被扫到", len(n1) == 1, str(n1))
    n2 = scan()
    check("无变化时缓存复用结果一致", n2 == n1)
    p2 = os.path.join(sub, "20250101_测试账号_标题_2.jpg")
    open(p2, "wb").close()
    n3 = scan()
    check("新增第 2 个文件立即生效", len(n3) == 2, str(len(n3)))
    os.remove(p2)
    n4 = scan()
    check("删除文件立即生效", len(n4) == 1, str(len(n4)))
    newdir = os.path.join(sub, "子目录")
    os.makedirs(newdir)
    p3 = os.path.join(newdir, "20250101_测试账号_标题_3.mp4")
    open(p3, "wb").close()
    n5 = scan()
    check("新增子目录立即生效", len(n5) == 2, str(len(n5)))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("== 7. 端到端：on_show → 骨架 → 后台扫描 → 分页渲染 ==")
page.on_show()
t = time.perf_counter()
deadline = time.time() + 30
while time.time() < deadline:
    qapp.processEvents()
    if getattr(page, "_collected_all_items", None):
        break
    time.sleep(0.02)
e2e = time.perf_counter() - t
n_items = len(getattr(page, "_collected_all_items", []) or [])
n_skeleton = len([w for w in page.collected_scroll.findChildren(QWidget)
                  if w.property("skeleton") is True]) if hasattr(page, "collected_scroll") else -1
check("列表渲染出数据", n_items > 0, f"{n_items} 条，耗时 {e2e:.2f}s")
check("骨架占位已清空", n_skeleton in (-1, 0), f"骨架行 {n_skeleton}")
rows = page.collected_layout.count() if hasattr(page, "collected_layout") else -1
# 20 行 + 页码条 1 + 尾部 stretch 1
check("每页行数不超过 BATCH", 0 < rows <= 22, f"布局项 {rows}")
# 翻页 + 筛选/搜索不应触发全表重扫（磁盘扫描次数不应增加）
before = page._reload_gen
page._goto_collected_page(2)
qapp.processEvents()
check("翻页不重新扫盘", page._reload_gen == before)
page.plat_sel.setCurrentText("抖音")
qapp.processEvents()
check("切平台筛选会刷新列表", page._reload_gen > before)

print()
print(f"性能汇总：原版 {orig_t:.2f}s → 首扫 {first:.2f}s → 缓存 {warm:.2f}s "
      f"（提速 {orig_t / max(warm, 0.001):.0f}x）")
if _fails:
    print("失败项：" + "; ".join(_fails))
    sys.exit(1)
print("全部通过")
