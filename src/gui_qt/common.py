"""PySide6 共享工具：后台线程回主线程 + 跨页面共享状态 + 格式化工具。

与原版 info_pages.py 顶部的共享逻辑对应，只是把 app.after 换成信号排队。
"""

import re
import threading

from PySide6.QtCore import QObject, Signal, Slot


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


# ==================== 后台线程 ====================

def run_bg(fn, on_done):
    """后台线程执行 fn()，完成后回主线程调用 on_done(result, err)。

    信号在 import 时已连接好（QueuedConnection 语义：后台线程 emit 自动排队到主线程），
    每次调用只 emit 携带回调的信号，不再动态 connect/disconnect。
    """
    def _w():
        result = None
        err = None
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001
            err = str(e)
        try:
            _bg_bridge.done.emit(on_done, result, err)
        except Exception:
            pass

    threading.Thread(target=_w, daemon=True).start()


def post_to_main(fn, *args):
    """把 fn(*args) 调度到主线程执行（跨线程安全，用于进度更新等轻量回调）。"""
    if args:
        _main_bridge.call.emit(lambda: fn(*args))
    else:
        _main_bridge.call.emit(fn)


# ==================== 仅图标小按钮样式 ====================

def icon_btn_style(emoji: bool = True, extra: str = "") -> str:
    """仅图标小按钮的完整内联样式（返回值给 setStyleSheet）。

    全局 QSS 给 QPushButton 设了 padding: 6px 12px —— 小尺寸按钮会被内边距
    吃掉图标绘制区（30px 宽只剩 6px，图标被挤成一团）；且全局字体微软雅黑
    不含 emoji 字形。emoji=True 指定 Segoe UI Emoji（真 emoji 图标）；
    False 只清内边距（↑↓✕■▶↻ 等文本符号雅黑自带）。
    extra 追加 color/background/border/font-size 等声明。
    """
    fam = 'font-family: "Segoe UI Emoji"; ' if emoji else ""
    return f"QPushButton {{ padding: 0; {fam}{extra} }}"


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
    lines = [
        f"平台: {info.platform}",
        f"类型: {'动态' if getattr(info, 'kind', '') == 'dynamic' else '视频/作品'}",
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
    lines.append(f"链接: {info.video_url or info.raw_url}")
    return "\n".join(lines)


def _fmt(n):
    if not n:
        return "0"
    if n >= 10000:
        return f"{n:,} ({n/10000:.1f}万)"
    return f"{n:,}"


def _dedup_key(info) -> str:
    """用于去重的唯一标识：video_id（稳定，如抖音 aweme_id/B站 bvid/微博 status id）
    优先，其次 video_url，最后 raw_url——与 increment_db 键序一致。

    抖音 CDN 直链带签名/过期参数（每次抓取都变）不能当键；用户作品条目的
    raw_url 是主页链接（同账号所有条目相同）也不能当键（曾因此整列重复入库）。
    """
    return (getattr(info, "video_id", "") or getattr(info, "video_url", "")
            or getattr(info, "raw_url", "") or "").strip()


def _collect(infos):
    """批量加入共享列表（自动去重，SQLite 增量落库），返回新增条数。"""
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
    _db_insert(new_items)
    return len(new_items)


# ==================== 已获取数据持久化（SQLite 增量存储） ====================
#
# config/collected.db 表 videos：
#   dedup_key TEXT PRIMARY KEY  去重键（video_url/raw_url/video_id）
#   platform  TEXT              平台（bilibili/douyin/weibo，便于按平台查询）
#   created_at REAL             入库时间戳
#   data      TEXT              VideoInfo asdict 的 JSON
# rowid 隐式自增 → 插入顺序即列表顺序。
# 旧 collected.json 首次启动自动迁移入库，原文件改名 .bak 保留备份。

_collected_lock = threading.Lock()


def _db_path():
    from toolbox.config_manager import get_config_dir
    return get_config_dir() / "collected.db"


def _collected_path():
    from toolbox.config_manager import get_config_dir
    return get_config_dir() / "collected.json"


def _db_connect():
    import sqlite3
    import os
    p = _db_path()
    os.makedirs(p.parent, exist_ok=True)
    conn = sqlite3.connect(p, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS videos ("
        " dedup_key TEXT PRIMARY KEY,"
        " platform  TEXT,"
        " created_at REAL,"
        " data      TEXT NOT NULL)")
    return conn


def _row_of(info, now):
    import json
    from dataclasses import asdict
    return (_dedup_key(info), (info.platform or ""), now,
            json.dumps(asdict(info), ensure_ascii=False))


def _db_insert(items):
    """增量写入：只插新行（主键冲突自动忽略），不再全量重写文件。"""
    import time
    try:
        now = time.time()
        rows = [_row_of(i, now) for i in items]
        with _collected_lock:
            conn = _db_connect()
            try:
                conn.executemany(
                    "INSERT OR IGNORE INTO videos"
                    " (dedup_key, platform, created_at, data) VALUES (?, ?, ?, ?)",
                    rows)
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


def _migrate_from_json(conn):
    """旧 collected.json → db（仅在 db 为空时执行一次），成功后 json 改名 .bak。"""
    import json
    from platforms.base import VideoInfo
    jp = _collected_path()
    if not jp.exists():
        return
    try:
        data = json.loads(jp.read_text(encoding="utf-8"))
    except Exception:
        return
    import time
    now = time.time()
    fields = set(VideoInfo.__dataclass_fields__)
    for d in data:
        if not isinstance(d, dict):
            continue
        info = VideoInfo(**{k: v for k, v in d.items() if k in fields})
        key = _dedup_key(info)
        if not key:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO videos"
            " (dedup_key, platform, created_at, data) VALUES (?, ?, ?, ?)",
            _row_of(info, now))
    conn.commit()
    try:
        jp.rename(jp.with_suffix(".json.bak"))
    except OSError:
        pass


def save_collected():
    """全量同步内存列表到 db（清空/重建场景用；日常新增走 _db_insert 增量）。

    保留每条原有的 created_at，按当前列表顺序重排 rowid（列表顺序 = 展示顺序）。
    """
    import time
    from dataclasses import asdict, is_dataclass
    import json as _json
    try:
        items = [i for i in COLLECTED if is_dataclass(i)]
        with _collected_lock:
            conn = _db_connect()
            try:
                old = dict(conn.execute(
                    "SELECT dedup_key, created_at FROM videos").fetchall())
                now = time.time()
                rows = [(_dedup_key(i), (i.platform or ""),
                         old.get(_dedup_key(i)) or now,
                         _json.dumps(asdict(i), ensure_ascii=False))
                        for i in items]
                conn.execute("DELETE FROM videos")
                conn.executemany(
                    "INSERT OR REPLACE INTO videos"
                    " (dedup_key, platform, created_at, data) VALUES (?, ?, ?, ?)",
                    rows)
                conn.commit()
            finally:
                conn.close()
    except Exception:
        pass


def load_collected():
    """启动时从 config/collected.db 恢复已获取列表（失败静默，保持空列表）。

    首次运行自动把旧 collected.json 迁移入库；按去重键跳过已存在条目，重复调用安全。
    """
    import json
    from platforms.base import VideoInfo
    try:
        import os
        os.makedirs(_db_path().parent, exist_ok=True)
        with _collected_lock:
            conn = _db_connect()
            try:
                n = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
                if n == 0:
                    _migrate_from_json(conn)
                rows = conn.execute(
                    "SELECT data, created_at FROM videos ORDER BY rowid").fetchall()
            finally:
                conn.close()
        existing = {_dedup_key(i) for i in COLLECTED if _dedup_key(i)}
        fields = set(VideoInfo.__dataclass_fields__)
        kept = 0
        for data, _created_at in rows:
            d = json.loads(data)
            if not isinstance(d, dict):
                continue
            info = VideoInfo(**{k: v for k, v in d.items() if k in fields})
            key = _dedup_key(info)
            if key and key in existing:
                continue
            if key:
                existing.add(key)
            COLLECTED.append(info)
            kept += 1
        # 旧库可能存有按会过期的 CDN 直链键写入的重复行（同一 video_id 多行），
        # 加载去重丢过行就把库物理重写成干净键，避免脏行越积越多
        if kept < len(rows):
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
