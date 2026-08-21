"""PySide6 共享工具：后台线程回主线程 + 跨页面共享状态 + 格式化工具。

与原版 info_pages.py 顶部的共享逻辑对应，只是把 app.after 换成信号排队。
"""

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


# ==================== 格式化工具 ====================

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
    """用于去重的唯一标识：优先链接，其次视频ID。"""
    return (info.video_url or info.raw_url or info.video_id or "").strip()


def _collect(infos):
    """批量加入共享列表（自动去重），返回新增条数。"""
    existing = {_dedup_key(i) for i in COLLECTED if _dedup_key(i)}
    cnt = 0
    for i in infos:
        if i is None:
            continue
        key = _dedup_key(i)
        if key and key in existing:
            continue
        if key:
            existing.add(key)
        COLLECTED.append(i)
        cnt += 1
    if cnt:
        save_collected()
    return cnt


# ==================== 已获取数据持久化（重启不丢） ====================

_collected_lock = threading.Lock()


def _collected_path():
    from toolbox.config_manager import get_config_dir
    return get_config_dir() / "collected.json"


def save_collected():
    """把已获取列表写入 config/collected.json（线程安全）。"""
    import json
    from dataclasses import asdict, is_dataclass
    try:
        data = [asdict(i) for i in COLLECTED if is_dataclass(i)]
        with _collected_lock:
            import os
            path = _collected_path()
            os.makedirs(path.parent, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
    except Exception:
        pass


def load_collected():
    """启动时从 config/collected.json 恢复已获取列表（失败静默，保持空列表）。

    按去重键跳过已存在的条目，重复调用安全。
    """
    import json
    from platforms.base import VideoInfo
    try:
        path = _collected_path()
        if not path.exists():
            return
        existing = {_dedup_key(i) for i in COLLECTED if _dedup_key(i)}
        data = json.loads(path.read_text(encoding="utf-8"))
        fields = set(VideoInfo.__dataclass_fields__)
        for d in data:
            if not isinstance(d, dict):
                continue
            info = VideoInfo(**{k: v for k, v in d.items() if k in fields})
            key = _dedup_key(info)
            if key and key in existing:
                continue
            if key:
                existing.add(key)
            COLLECTED.append(info)
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
