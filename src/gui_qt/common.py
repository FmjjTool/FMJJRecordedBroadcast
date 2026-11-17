"""PySide6 共享工具：后台线程回主线程 + 跨页面共享状态 + 格式化工具。

与原版 info_pages.py 顶部的共享逻辑对应，只是把 app.after 换成信号排队。
"""

import re
import threading
import functools
import time
from collections import Counter

from PySide6.QtCore import QObject, Qt, Signal, Slot


class _MainBridge(QObject):
    """post_to_main 专用桥：信号携带可调用对象，主线程执行。"""
    call = Signal(object)


class _BgBridge(QObject):
    """run_bg 专用桥：信号携带回调与结果，主线程执行。"""
    done = Signal(object, object, object)


_main_bridge = _MainBridge()   # 在主线程（首次 import 时）创建，线程亲和性 = 主线程
_bg_bridge = _BgBridge()


@Slot(object)
def _on_main_call(fn):
    try:
        fn()
    except Exception:
        pass


@Slot(object, object, object)
def _on_bg_done(cb, result, err):
    try:
        cb(result, err)
    except Exception:
        pass


# 只连接一次、永不断开：避免逐次 connect/disconnect 产生的
# "Failed to disconnect ... from signal" RuntimeWarning
_main_bridge.call.connect(_on_main_call)
_bg_bridge.done.connect(_on_bg_done)


# ==================== 共享状态 ====================

# 已获取的视频信息列表（VideoInfo 对象），跨页面共享
COLLECTED = []

# 全局清晰度设置（已获取页 / 视频下载页 / 资产管理 共享）
CURRENT_QUALITY = "最高画质"

# 全局下载线程数（已获取页 / 资产管理页共享；2/4/8/16/32/64）
# 单文件大文件 = 分段数（IDM 风格）；批量 = 同时在下的文件数
CURRENT_THREADS = 8

# 线程数下拉选项（各下载页共用）
THREAD_OPTIONS = ["2", "4", "8", "16", "32", "64"]


# ==================== 后台线程 ====================

class BackgroundTasks:
    """主程序级任务租约；业务完成和主线程回调完成之后才释放宿主。"""

    def __init__(self):
        self._owners = Counter()
        self._lock = threading.RLock()

    def acquire(self, owners):
        with self._lock:
            self._owners.update(owners)

    def release(self, owners):
        with self._lock:
            for owner in owners:
                self._owners[owner] -= 1
                if self._owners[owner] <= 0:
                    del self._owners[owner]

    def has_owner(self, owner):
        with self._lock:
            return self._owners.get(owner, 0) > 0


TASKS = BackgroundTasks()
PERFORMANCE_RECORDER = None  # 仅用户开启诊断期间连接；默认不采样、不启用追踪。


def _performance_call(recorder, method, *args):
    if recorder is None:
        return None
    try:
        return getattr(recorder, method)(*args)
    except Exception:
        return None  # 诊断故障不能影响业务线程或任务租约释放。


def _callback_pages(*callbacks):
    """识别绑定方法、闭包、默认参数及 partial 中的页面引用。"""
    from gui_qt.pages.base import BasePage
    seen, pages = set(), set()

    def visit(value, depth=0):
        if value is None or id(value) in seen or depth > 6:
            return
        seen.add(id(value))
        if isinstance(value, BasePage):
            pages.add(value)
        elif isinstance(value, functools.partial):
            visit(value.func, depth + 1)
            for item in value.args:
                visit(item, depth + 1)
            for item in (value.keywords or {}).values():
                visit(item, depth + 1)
        elif callable(value):
            visit(getattr(value, "__self__", None), depth + 1)
            for item in getattr(value, "__defaults__", None) or ():
                visit(item, depth + 1)
            for item in (getattr(value, "__kwdefaults__", None) or {}).values():
                visit(item, depth + 1)
            for cell in getattr(value, "__closure__", None) or ():
                try:
                    visit(cell.cell_contents, depth + 1)
                except ValueError:
                    pass

    for callback in callbacks:
        visit(callback)
    return tuple(pages)


def _callbacks_alive(owners):
    from shiboken6 import isValid
    return all(not p._disposed and isValid(p) for p in owners)

def run_bg(fn, on_done, *, owner=None):
    """后台线程执行 fn()，完成后回主线程调用 on_done(result, err)。

    信号在 import 时已连接好（QueuedConnection 语义：后台线程 emit 自动排队到主线程），
    每次调用只 emit 携带回调的信号，不再动态 connect/disconnect。
    """
    owners = _callback_pages(fn, on_done, owner)
    if not _callbacks_alive(owners):
        return None
    TASKS.acquire(owners)
    recorder = PERFORMANCE_RECORDER
    token = _performance_call(recorder, "task_started", fn)

    def done(result, err):
        started = time.perf_counter() if token is not None else 0
        failed = bool(err)
        try:
            if _callbacks_alive(owners) and on_done is not None:
                on_done(result, err)
        except Exception:
            failed = True
            raise
        finally:
            if token is not None:
                _performance_call(recorder, "task_finished", token, "callback", (time.perf_counter() - started) * 1000, failed)
            TASKS.release(owners)

    def _w():
        result = None
        err = None
        started = time.perf_counter() if token is not None else 0
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001
            err = str(e)
        if token is not None:
            _performance_call(recorder, "task_finished", token, "worker", (time.perf_counter() - started) * 1000, bool(err))
        try:
            _bg_bridge.done.emit(done, result, err)
        except Exception:
            if token is not None:
                _performance_call(recorder, "task_finished", token, "callback", 0, True)
            TASKS.release(owners)

    thread = threading.Thread(target=_w, daemon=True)
    try:
        thread.start()
    except Exception:
        if token is not None:
            _performance_call(recorder, "task_finished", token, "callback", 0, True)
        TASKS.release(owners)
        raise
    return thread


def start_page_thread(owner, target, args=()):
    """原生工作线程统一交给任务管理，切页不终止任务。"""
    return run_bg(lambda: target(*args), None, owner=owner)


def post_to_main(fn, *args):
    """把 fn(*args) 调度到主线程执行（跨线程安全，用于进度更新等轻量回调）。"""
    owners = _callback_pages(fn)
    if not _callbacks_alive(owners):
        return
    TASKS.acquire(owners)

    def invoke():
        try:
            if _callbacks_alive(owners):
                fn(*args)
        finally:
            TASKS.release(owners)

    try:
        _main_bridge.call.emit(invoke)
    except Exception:
        TASKS.release(owners)
        raise


# ==================== 仅图标小按钮样式 ====================

def icon_btn_style(emoji: bool = True, extra: str = "") -> str:
    """仅图标小按钮的完整内联样式（返回值给 setStyleSheet）。

    全局 QSS 给 QPushButton 设了 padding: 6px 12px —— 小尺寸按钮会被内边距
    吃掉图标绘制区（30px 宽只剩 6px，图标被挤成一团）；且全局字体微软雅黑
    不含 emoji 字形。emoji=True 指定 Segoe UI Emoji（真 emoji 图标）；
    False 只清内边距（↑↓■▶↻ 等文本符号雅黑自带）。
    extra 追加 color/background/border/font-size 等声明。
    """
    fam = 'font-family: "Segoe UI Emoji"; ' if emoji else ""
    return f"QPushButton {{ padding: 0; {fam}{extra} }}"


def icon_button(text, tip="", size=(30, 26), emoji=True, extra="", on_click=None):
    """创建一个仅图标的小按钮（固定尺寸 + icon_btn_style + 悬停提示）。

    统一全应用的行内小图标按钮；tooltip 必填场景下
    用户无法从文字得知功能，全靠悬停提示。
    """
    from PySide6.QtWidgets import QPushButton
    b = QPushButton(text)
    b.setFixedSize(*size)
    b.setStyleSheet(icon_btn_style(emoji, extra))
    if tip:
        b.setToolTip(tip)
    if on_click:
        b.clicked.connect(on_click)
    return b


# ==================== 复选框开/关 toast ====================

# 装饰性符号（emoji/警示符/变体选择符）：toast 文案里剔除，只留功能名
_CB_DECOR_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2190-\u2BFF\uFE00-\uFE0F]")


def _cb_feature_name(cb) -> str:
    return _CB_DECOR_RE.sub("", cb.text()).strip()


def bind_checkbox_toasts(root):
    """给 root 子树内的功能型复选框绑定「已开启/已关闭 XXX」toast（幂等）。

    绑定 clicked(checked) 而非 toggled：clicked 只在用户点击/空格触发，
    程序 setChecked（配置恢复/批量刷新）不会发，避免启动时假提示。
    跳过两类非功能型复选框：无文字的行内勾选框、视图内条目勾选框
    （表格/树/列表的行选择框，提示会是噪音）。
    """
    from PySide6.QtWidgets import QAbstractItemView, QCheckBox
    if root is None:
        return
    for cb in root.findChildren(QCheckBox):
        if getattr(cb, "_toast_bound", False) or not _cb_feature_name(cb):
            continue
        w = cb.parentWidget()
        in_view = False
        while w is not None:
            if isinstance(w, QAbstractItemView):
                in_view = True
                break
            w = w.parentWidget()
        if in_view:
            continue
        cb._toast_bound = True

        def _on_click(checked, c=cb):
            from gui_qt.toast import show_toast
            name = _cb_feature_name(c)
            if not name:
                return
            verb = "已开启" if checked else "已关闭"
            show_toast(c.window(), f"{verb}「{name}」功能",
                       "success" if checked else "info")

        cb.clicked.connect(_on_click)


# ==================== 格式化工具 ====================

def first_number(s: str):
    """提取字符串里第一个数字（表格排序用），无数字返回 None。"""
    import re
    m = re.search(r"\d+(?:\.\d+)?", s or "")
    return float(m.group()) if m else None


def sales_number(s: str):
    """销量文本 → 数字（"月销 12"→12，"1万+"→10000），无数字返回 None。"""
    import re
    m = re.search(r"(\d+(?:\.\d+)?)\s*(万)?", s or "")
    if not m:
        return None
    v = float(m.group(1))
    return v * 10000 if m.group(2) else v


def stable_sort(items, key, desc):
    """原地稳定排序；空值（None/空串）恒排末尾（升序降序皆然）。"""
    pairs = [(it, key(it)) for it in items]
    non_empty = [(it, v) for it, v in pairs
                 if v is not None and v != ""]
    empties = [(it, v) for it, v in pairs
               if v is None or v == ""]
    non_empty.sort(key=lambda p: p[1], reverse=desc)
    items[:] = [it for it, _ in non_empty] + [it for it, _ in empties]


def _info_text(info) -> str:
    """将 VideoInfo 格式化为展示文本。"""
    view = "阅览量" if info.platform == "weibo" else "播放量"
    kind = getattr(info, "kind", "")
    type_label = {"dynamic": "动态", "note": "图文/笔记"}.get(kind, "视频/作品")
    lines = [
        f"平台: {info.platform}",
        f"类型: {type_label}",
        f"标题: {info.title or '(无标题)'}",
    ]
    if info.description:
        desc = info.description if len(info.description) <= 200 else info.description[:200] + "..."
        lines.append(f"正文: {desc}")
    lines.append(f"作者: @{info.author or '-'} (ID: {info.author_id or '-'})")
    lines.append(f"{view}: {_fmt(info.play_count)}")
    lines.append(f"点赞: {_fmt(info.like_count)}")
    lines.append(f"评论: {_fmt(info.comment_count)}")
    lines.append(f"分享: {_fmt(info.share_count)}")
    if info.favorite_count:
        lines.append(f"收藏: {_fmt(info.favorite_count)}")
    if info.danmaku_count:
        lines.append(f"弹幕: {_fmt(info.danmaku_count)}")
    if info.duration:
        m, s = divmod(info.duration, 60)
        h, m = divmod(m, 60)
        lines.append(f"时长: {f'{h}:{m:02d}:{s:02d}' if h else f'{m}:{s:02d}'}")
    if info.publish_time:
        lines.append(f"发布时间: {info.publish_time}")
    if info.tags:
        lines.append(f"标签: {', '.join(info.tags)}")
    if getattr(info, "images", None):
        lines.append(f"图片: {len(info.images)} 张")
    lines.append(f"链接: {info.video_url or info.raw_url}")
    return "\n".join(lines)


def _fmt(n):
    if not n:
        return "0"
    if n >= 10000:
        return f"{n:,} ({n/10000:.1f}万)"
    return f"{n:,}"


def _dedup_key(info) -> str:
    """「已获取」列表的去重键；统一由 toolbox.dedup.collect_key 提供。"""
    from toolbox.dedup import collect_key
    return collect_key(info)


def _collect(infos, persist=True):
    """批量加入共享列表（自动去重，默认 SQLite 增量落库），返回新增条数。

    persist=False 只进内存列表、不写数据库：用于采集线程已先行落库后，
    主线程按节奏把结果揭示到「已获取」页，避免重复写库。
    """
    existing = {_dedup_key(i) for i in COLLECTED if _dedup_key(i)}
    new_items = []
    for i in infos:
        if i is None:
            continue
        key = _dedup_key(i)
        if key and key in existing:
            continue
        if key:
            existing.add(key)
        new_items.append(i)
    if not new_items:
        return 0
    COLLECTED.extend(new_items)
    if persist:
        _db_insert(new_items)
    return len(new_items)


# ==================== 已获取数据持久化（L3 服务） ====================
#
# 归属：config/collected.db 是「已获取」页面展示/导出快照，唯一写入方是
# toolbox/collected_store.py；config/increment.db 是它的派生分平台归档，
# 由 collected_store.ingest_items(..., incremental=True) 投影写入。
# 两者可承载同一次采集结果但用途不同，暂不合并；去重键统一由 toolbox.dedup.collect_key 提供。


def _db_insert(items) -> int:
    """增量写入 collected.db；实际存储逻辑在 toolbox.collected_store。"""
    from toolbox.collected_store import insert_items
    return insert_items(items)


def save_collected():
    """全量同步内存列表到 db（清空/重建场景用；日常新增走 _db_insert 增量）。"""
    from toolbox.collected_store import save_all
    save_all(COLLECTED)


def load_collected():
    """启动时从 config/collected.db 恢复已获取列表（失败静默，保持空列表）。

    首次运行由存储服务把旧 collected.json 迁移入库；按去重键跳过已存在条目。
    """
    from toolbox.collected_store import load_items
    try:
        items = load_items()
        existing = {_dedup_key(i) for i in COLLECTED if _dedup_key(i)}
        kept = 0
        for info in items:
            key = _dedup_key(info)
            if key and key in existing:
                continue
            if key:
                existing.add(key)
            COLLECTED.append(info)
            kept += 1
        # 旧库可能存有按会过期的 CDN 直链键写入的重复行（同一 video_id 多行），
        # 加载去重丢过行就把库物理重写成干净键，避免脏行越积越多
        if kept < len(items):
            save_collected()
    except Exception:
        pass


def refresh_results_count(app):
    """通知所有页面刷新「已获取」计数（对应原版 _refresh_results_count）。"""
    for page in app.pages.values():
        fn = getattr(page, "refresh_count", None)
        if fn:
            try:
                fn()
            except Exception:
                pass
