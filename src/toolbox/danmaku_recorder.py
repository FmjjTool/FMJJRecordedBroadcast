# -*- coding: utf-8 -*-
"""录制时同步保存弹幕（对应录播姬的「弹幕录制」）。

- B站：写成录播姬/官方播放器都能读的 XML：
      <?xml version="1.0" encoding="UTF-8"?><i>
      <d p="相对秒,模式,字号,颜色,时间戳ms,弹幕池,用户ID,行ID">内容</d>…
      </i>
- 抖音：写成 JSONL（dycast 同构字段：method/timestamp/content/user/gift），
  一条一行、逐条 flush，崩溃也不丢。

弹幕连接失败绝不影响视频录制：所有异常都在本类内部吞掉并回调日志。
"""

from __future__ import annotations

import json
import os
import threading
import time
from xml.sax.saxutils import escape

FLUSH_EVERY = 20      # 每 N 条刷一次盘（录播姬 RecordDanmakuFlushInterval 默认 20）


class DanmakuRecorder:
    """一个房间的弹幕录制任务。"""

    def __init__(self, platform, room, out_dir, base_name, on_log=None):
        self.platform = platform
        self.room = str(room)
        self.out_dir = out_dir
        self.base = base_name
        self.on_log = on_log or (lambda text, level="info": None)
        self.path = ""
        self._f = None
        self._lock = threading.RLock()
        self._fetcher = None
        self._count = 0
        self._buf = 0
        self._start_ms = int(time.time() * 1000)

    # ---------- 生命周期 ----------
    def start(self):
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            ext = ".xml" if self.platform == "bili" else "_弹幕.jsonl"
            self.path = os.path.join(self.out_dir, self.base + ext)
            self._f = open(self.path, "w", encoding="utf-8", buffering=1)
            if self.platform == "bili":
                self._f.write('<?xml version="1.0" encoding="UTF-8"?><i>')
            self._start_ms = int(time.time() * 1000)
        except Exception as e:  # noqa: BLE001
            self.on_log("弹幕文件创建失败：%s" % e, "warn")
            self._f = None
            return False
        try:
            if self.platform == "bili":
                from toolbox.bili_live import BiliLiveFetcher
                self._fetcher = BiliLiveFetcher(
                    self.room, on_message=self._on_msg,
                    on_room_info=None, on_status=self._on_status)
            else:
                from toolbox.douyin_live import DouyinLiveFetcher
                self._fetcher = DouyinLiveFetcher(
                    self.room, on_message=self._on_msg,
                    on_room_info=None, on_status=self._on_status)
            self._fetcher.start()
            self.on_log("弹幕录制已开启：%s" % os.path.basename(self.path), "ok")
            return True
        except Exception as e:  # noqa: BLE001
            self.on_log("弹幕连接失败（不影响视频录制）：%s" % e, "warn")
            return False

    def stop(self):
        try:
            if self._fetcher is not None:
                self._fetcher.stop()
        except Exception:
            pass
        self._fetcher = None
        with self._lock:
            if self._f:
                try:
                    if self.platform == "bili":
                        self._f.write("</i>")
                    self._f.flush()
                    self._f.close()
                except Exception:
                    pass
                self._f = None
        if self.path:
            self.on_log("弹幕录制已结束（%d 条）：%s"
                        % (self._count, os.path.basename(self.path)), "info")
        return self._count

    # ---------- 回调 ----------
    def _on_status(self, status, msg):
        if status in ("error", "not_live"):
            self.on_log("弹幕连接：%s" % msg, "warn")

    def _on_msg(self, name, text, m=None):
        m = m or {}
        if not self._f:
            return
        ts = m.get("ts") or int(time.time() * 1000)
        try:
            if self.platform == "bili":
                rel = max(0.0, (int(ts) - self._start_ms) / 1000.0)
                mode = 1
                size = 25
                color = 16777215
                rowid = str(abs(hash((ts, m.get("nickname") or "", m.get("content") or ""))) % 10**12)
                body = escape(str(m.get("content") or text or ""))
                line = ('<d p="%.3f,%d,%d,%d,%d,0,0,%s">%s</d>'
                        % (rel, mode, size, color, int(ts), rowid, body))
            else:
                rec = {
                    "method": m.get("type") or "",
                    "timestamp": int(ts),
                    "content": m.get("content") or text or "",
                    "user": {"name": m.get("nickname") or ""},
                }
                if m.get("giftName"):
                    rec["gift"] = {"name": m.get("giftName"),
                                   "count": m.get("giftCount") or 1,
                                   "price": m.get("giftPrice") or 0,
                                   "icon": m.get("giftIcon") or ""}
                if m.get("rtf"):
                    rec["rtfContent"] = m["rtf"]
                line = json.dumps(rec, ensure_ascii=False)
            with self._lock:
                self._f.write(line + ("\n" if self.platform != "bili" else ""))
                self._count += 1
                self._buf += 1
                if self._buf >= FLUSH_EVERY:
                    self._buf = 0
                    self._f.flush()
        except Exception:
            pass
