"""账号采集用例（L3）：并发、平台限流、stdout 捕获、日期过滤与断点回调。

不依赖 Qt。UI 通过 on_event / reveal_items / export_batch 回调接入；
本模块不直接操作页面控件，也不负责渲染。
"""

from __future__ import annotations

import io
import sys
import threading
import time
from datetime import datetime, timedelta


def _parse_publish_ts(text):
    """发布时间字符串 → epoch 秒。解析失败返回 None（对应条目保留不过滤）。"""
    import re as _re
    from datetime import datetime as _dt, timedelta as _td
    s = str(text or "").strip()
    if not s:
        return None
    m = _re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m:
        try:
            hm = _re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s[m.end():])
            if hm:
                return _dt(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                           int(hm.group(1)), int(hm.group(2)),
                           int(hm.group(3) or 0)).timestamp()
            return _dt(int(m.group(1)), int(m.group(2)), int(m.group(3))).timestamp()
        except ValueError:
            pass
    now = _dt.now()
    if s.startswith(("刚刚", "刚才", "今天")):
        return now.timestamp()
    rm = _re.match(r"(\d+)\s*(秒|分钟|分|小时|时|天|日|周|月)前", s)
    if rm:
        n = int(rm.group(1))
        delta = {"秒": _td(seconds=n), "分钟": _td(minutes=n), "分": _td(minutes=n),
                 "小时": _td(hours=n), "时": _td(hours=n), "天": _td(days=n),
                 "日": _td(days=n), "周": _td(weeks=n),
                 "月": _td(days=30 * n)}[rm.group(2)]
        return (now - delta).timestamp()
    if s.startswith("昨天"):
        return (now - _td(days=1)).timestamp()
    if s.startswith("前天"):
        return (now - _td(days=2)).timestamp()
    return None


def _date_bound_ts(date_str, is_start):
    """'YYYY-MM-DD' → epoch 秒（开始=当天 00:00:00，结束=当天 23:59:59）；空/非法 → None。"""
    if not date_str:
        return None
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return None
    return d.timestamp() if is_start else (d + timedelta(days=1)).timestamp() - 1


def _filter_posts_by_date(posts, lo_ts, hi_ts):
    """保留发布时间落在 [lo_ts, hi_ts] 的条目；时间解析失败的条目保留（宁多勿漏）。"""
    if lo_ts is None and hi_ts is None:
        return posts
    keep = []
    for p in posts:
        ts = _parse_publish_ts(getattr(p, "publish_time", ""))
        if ts is None:
            keep.append(p)
            continue
        if lo_ts is not None and ts < lo_ts:
            continue
        if hi_ts is not None and ts > hi_ts:
            continue
        keep.append(p)
    return keep


def collect_accounts(
    accounts, count, *,
    rate_count=0, rate_interval=0, incremental=False,
    on_event=None, pause_event=None, cancel_event=None,
    date_start="", date_end="", round_id=0,
    insert_items=None, reveal_items=None, export_batch=None,
):
    """采集账号最新内容，返回 (total, log, all_posts)。

    on_event(dict)：实时事件回调，{"type": "log", ...} 或
    {"type": "progress", "done": x, "total": y, "label": ...}。
    insert_items(items)：采集线程落库回调（可选）。
    reveal_items(items, round_id)：主线程逐条揭示回调（可选）。
    export_batch(posts, incremental)：单账号即时导出回调，返回 (msg, saved)（可选）。
    """
    from accounts_manager import infer_kind_from_url
    from platforms import URLRecognizer, get_parser

    total = 0
    log = []

    def emit(line):
        log.append(line)
        if on_event is not None:
            on_event({"type": "log", "text": line})

    date_lo = _date_bound_ts(date_start, True)
    date_hi = _date_bound_ts(date_end, False)
    if date_lo is not None or date_hi is not None:
        emit(f"🗓 日期段过滤已启用: {date_start or '从头'} ~ {date_end or '到现在'}（只保留该范围内发布的内容）")

    n = len(accounts)
    if on_event is not None:
        on_event({"type": "progress", "done": 0, "total": n, "label": "准备中..."})
    all_posts = []
    fetch_count = count if count > 0 else 99999999999999

    class _TLStdout(io.TextIOBase):
        def __init__(self, orig):
            self._local = threading.local()
            self._orig = orig

        def write(self, s):
            buf = getattr(self._local, "buf", None)
            if buf is not None:
                buf.write(s)
            else:
                self._orig.write(s)
            return len(s)

        def flush(self):
            pass

    class _LiveBuf(io.TextIOBase):
        def __init__(self, on_line, prefix):
            self._buf = io.StringIO()
            self._pending = ""
            self._on_line = on_line
            self._prefix = prefix

        def write(self, s):
            self._buf.write(s)
            self._pending += s
            self._drain()
            return len(s)

        def _drain(self):
            while "\n" in self._pending:
                line, self._pending = self._pending.split("\n", 1)
                if line.strip():
                    self._on_line(f"{self._prefix}{line.rstrip()}")

        def flush_pending(self):
            if self._pending.strip():
                self._on_line(f"{self._prefix}{self._pending.strip()}")
            self._pending = ""

        def getvalue(self):
            return self._buf.getvalue()

    proxy = _TLStdout(sys.stdout)

    plat_lock = threading.Lock()
    plat_fetched = {}
    plat_cooldown = {}
    rate_on = rate_count > 0 and rate_interval > 0

    def _throttle_before(platform: str):
        if not rate_on:
            return
        while True:
            if cancel_event is not None and cancel_event.is_set():
                return
            if pause_event is not None:
                pause_event.wait()
            with plat_lock:
                until = plat_cooldown.get(platform, 0.0)
            remain = until - time.time()
            if remain <= 0:
                return
            time.sleep(min(remain, 1.0))

    def _throttle_after(platform: str, n_items: int) -> bool:
        if not rate_on or n_items <= 0:
            return False
        with plat_lock:
            plat_fetched[platform] = plat_fetched.get(platform, 0) + n_items
            if plat_fetched[platform] >= rate_count:
                plat_fetched[platform] = 0
                plat_cooldown[platform] = time.time() + rate_interval
                return True
        return False

    def _save_and_reveal(items):
        if not items:
            return
        if insert_items is not None:
            insert_items(items)
        if reveal_items is not None:
            reveal_items(items, round_id)

    def _fetch_one(job):
        i, a = job
        if pause_event is not None:
            pause_event.wait()
        if cancel_event is not None and cancel_event.is_set():
            return i, a, "", [], "__cancelled__"
        platform = URLRecognizer.recognize(a.url)
        name = a.name or (a.url[:36] if a.url else "未命名")
        if not platform:
            return i, a, "", [], "无法识别链接"
        parser = get_parser(platform)
        if not parser or not hasattr(parser, "fetch_user_posts"):
            return i, a, platform, [], "不支持时间线"
        _throttle_before(platform)
        account_kind = getattr(a, "kind", "") or infer_kind_from_url(a.url)
        kwargs = {}
        if platform == "bilibili" and account_kind in ("video", "dynamic", "all"):
            kwargs["kind"] = account_kind
        if platform == "douyin":
            kwargs["on_items"] = _save_and_reveal
        if rate_on and platform in ("douyin", "weibo", "bilibili"):
            kwargs["pause_every"] = rate_count
            kwargs["pause_secs"] = rate_interval
        if platform in ("douyin", "weibo", "bilibili", "kuaishou") and cancel_event is not None:
            kwargs["stop_check"] = cancel_event.is_set
        name_s = (name[:16] + "…") if len(name) > 16 else name
        buf = _LiveBuf(emit, f"    [{name_s}] ")
        proxy._local.buf = buf
        try:
            posts = parser.fetch_user_posts(a.url, count=fetch_count, **kwargs)
        except Exception as e:  # noqa: BLE001
            return i, a, platform, [], str(e)
        finally:
            proxy._local.buf = None
            buf.flush_pending()
        if _throttle_after(platform, len(posts)):
            emit(f"    [{name_s}] ⏸ 限流：{platform} 已抓满 {rate_count} 条，冷却 {rate_interval}s")
        return i, a, platform, posts, ""

    old_stdout = sys.stdout
    sys.stdout = proxy
    per_platform = {}
    done_order = []
    results = {}
    total_added = {}
    total_files = 0
    try:
        workers = min(4, max(1, n))
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_fetch_one, job) for job in enumerate(accounts, 1)]
            for fut in as_completed(futs):
                i, a, platform, posts, error = fut.result()
                results[i] = (a, platform, posts, error)
                done_order.append(i)
                done_cnt = len(done_order)
                name = a.name or (a.url[:36] if a.url else "未命名")
                if on_event is not None:
                    on_event({"type": "progress", "done": done_cnt, "total": n,
                              "label": f"[{done_cnt}/{n}] {platform} · {name}"})
                if error == "__cancelled__":
                    emit(f"[{i}/{n}] ⊘ 已取消，跳过")
                    continue
                if error == "无法识别链接":
                    emit(f"[{i}/{n}] 无法识别: {a.url[:50]}")
                    continue
                if error == "不支持时间线":
                    emit(f"[{i}/{n}] {platform} 不支持时间线")
                    continue
                if error:
                    emit(f"[{i}/{n}] {platform} 失败: {error}")
                    continue
                if date_lo is not None or date_hi is not None:
                    before_n = len(posts)
                    posts = _filter_posts_by_date(posts, date_lo, date_hi)
                    if len(posts) != before_n:
                        emit(f"[{i}/{n}] 🗓 日期段过滤: {before_n} → {len(posts)} 条（范围外 {before_n - len(posts)} 条）")
                    results[i] = (a, platform, posts, error)
                emit(f"[{i}/{n}] ⏳ {platform} · {name} → 获取到 {len(posts)} 条")
                if insert_items is not None:
                    insert_items(posts)
                if reveal_items is not None:
                    reveal_items(posts, round_id)
                saved = {}
                if export_batch is not None:
                    save_msg, saved = export_batch(posts, incremental)
                    if save_msg:
                        emit(f"[{i}/{n}] {save_msg}")
                if incremental and isinstance(saved, dict):
                    for p, cnt in saved.items():
                        total_added[p] = total_added.get(p, 0) + cnt
                elif not incremental and isinstance(saved, int):
                    total_files += saved
                all_posts.extend(posts)
                total += len(posts)
                per_platform[platform] = per_platform.get(platform, 0) + len(posts)
                dyns = sum(1 for p in posts if getattr(p, "kind", "") == "dynamic")
                videos = len(posts) - dyns
                kind_tag = {"video": "[作品] ", "dynamic": "[动态] "}.get(
                    getattr(a, "kind", "") or infer_kind_from_url(a.url), "")
                if dyns and videos:
                    emit(f"[{i}/{n}] ✅ {kind_tag}{platform} 获取到 {len(posts)} 条（作品 {videos} / 动态 {dyns}）")
                elif dyns:
                    emit(f"[{i}/{n}] ✅ {kind_tag}{platform} 获取到 {len(posts)} 条（动态 {dyns}）")
                else:
                    emit(f"[{i}/{n}] ✅ {kind_tag}{platform} 获取到 {len(posts)} 条（作品 {videos}）")
                for p in posts[:8]:
                    t = (getattr(p, "title", "") or "").strip()
                    d = (getattr(p, "publish_time", "") or "").strip()
                    line = f"      · {d} {t}" if d else f"      · {t}"
                    emit(line[:120] if line.strip() != "·" else "      ·（无标题）")
                if len(posts) > 8:
                    emit(f"      …等共 {len(posts)} 条")
    finally:
        sys.stdout = old_stdout
    all_posts = [p for i in sorted(results) for p in results[i][2]]
    if all_posts:
        if incremental:
            from toolbox import increment_db
            if any(total_added.values()):
                parts = "、".join(f"{p} +{v}" for p, v in total_added.items() if v)
                emit(f"💾 已增量写入数据库: {increment_db.db_path()}（{parts}）")
            else:
                emit(f"💾 无新增（已全部在库中）: {increment_db.db_path()}")
        else:
            from toolbox.config_manager import get_export_subdir
            emit(f"💾 已单独导出 {total_files} 个文件到 {get_export_subdir('video_data')}")
    if on_event is not None:
        cancelled = cancel_event is not None and cancel_event.is_set()
        on_event({"type": "progress", "done": n, "total": n,
                  "label": "✕ 已取消" if cancelled else "✅ 全部完成"})
    return total, log, all_posts
