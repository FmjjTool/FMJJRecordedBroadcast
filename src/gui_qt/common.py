"""PySide6 共享工具：后台线程回主线程 + 跨页面共享状态 + 格式化工具。

与原版 info_pages.py 顶部的共享逻辑对应，只是把 app.after 换成 QTimer.singleShot。
"""

import threading

from PySide6.QtCore import QObject, Signal


class _Bridge(QObject):
    """跨线程信号桥：后台线程 emit 信号，自动排队到主线程执行槽。"""
    done = Signal(object, object)


_bridge = _Bridge()  # 在主线程（首次 import 时）创建，线程亲和性 = 主线程


# ==================== 共享状态 ====================

# 已获取的视频信息列表（VideoInfo 对象），跨页面共享
COLLECTED = []

# 全局清晰度设置（已获取页 / 视频下载页 / 资产管理 共享）
CURRENT_QUALITY = "最高画质"


# ==================== 后台线程 ====================

def run_bg(fn, on_done):
    """后台线程执行 fn()，完成后回主线程调用 on_done(result, err)。

    用信号（Signal）跨线程：后台线程 emit，PySide6 自动 QueuedConnection 排队到主线程。
    注意：不能用 QTimer.singleShot（它在后台线程创建 QTimer 但无事件循环，回调永远不执行）。
    """
    def _handler(result, err):
        try:
            _bridge.done.disconnect(_handler)
        except Exception:
            pass
        try:
            on_done(result, err)
        except Exception:
            pass

    _bridge.done.connect(_handler)

    def _w():
        result = None
        err = None
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001
            err = str(e)
        try:
            _bridge.done.emit(result, err)
        except Exception:
            pass

    threading.Thread(target=_w, daemon=True).start()


def post_to_main(fn, *args):
    """把 fn(*args) 调度到主线程执行（跨线程安全，用于进度更新等轻量回调）。

    内部同样走 _bridge 信号，避免 QTimer.singleShot 在后台线程失效的问题。
    """
    def _handler(_r, _e):
        try:
            _bridge.done.disconnect(_handler)
        except Exception:
            pass
        try:
            fn(*args)
        except Exception:
            pass

    _bridge.done.connect(_handler)
    _bridge.done.emit(None, None)


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
    return cnt


def refresh_results_count(app):
    """通知所有页面刷新「已获取」计数（对应原版 _refresh_results_count）。"""
    for page in app.pages.values():
        fn = getattr(page, "refresh_count", None)
        if fn:
            try:
                fn()
            except Exception:
                pass
