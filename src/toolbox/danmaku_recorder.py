# -*- coding: utf-8 -*-
"""录制时同步保存弹幕（对应录播姬的「弹幕录制」）。

- B站：写成录播姬/官方播放器都能读的 XML：
      <?xml version="1.0" encoding="UTF-8"?><i>
      <d p="相对秒,模式,字号,颜色,时间戳ms,弹幕池,用户ID,行ID">内容</d>…
      </i>
- 所有平台都额外写 JSONL（method/timestamp/content/user/gift），逐条 flush、
  每秒同步磁盘；突发断电仍可能损失末尾未同步数据。B站 XML 每次保持闭合。
- 快手/微博/小红书/网易云由 live_chat.BrowserChatFetcher 采集页面消息通道，
  分类与字段对齐抖音/B站（弹幕/礼物/进场/点赞/关注）。

弹幕连接失败绝不影响视频录制：所有异常都在本类内部吞掉并回调日志。
"""

from __future__ import annotations

import os
import threading
import time
from xml.sax.saxutils import escape
from toolbox.durable_io import JsonlJournal

class DanmakuRecorder:
    """一个房间的弹幕录制任务。"""

    def __init__(self, platform, room, out_dir, base_name, on_log=None,
                 on_message=None, save=True):
        self.platform = platform
        self.room = str(room)
        self.out_dir = out_dir
        self.base = base_name
        self.on_log = on_log or (lambda text, level="info": None)
        self.on_message = on_message
        self.save = save
        self._journal = None
        self._stopped = False
        self.path = ""
        self._f = None
        self._lock = threading.RLock()
        self._fetcher = None
        self._count = 0
        self._start_ms = int(time.time() * 1000)

    # ---------- 生命周期 ----------
    def start(self):
        try:
            os.makedirs(self.out_dir, exist_ok=True)
            if self.save:
                self.path = os.path.join(self.out_dir, self.base + "_弹幕.jsonl")
                self._journal = JsonlJournal(self.path)
                if self.platform == "bili":
                    self._f = open(os.path.join(self.out_dir, self.base + ".xml"), "w+", encoding="utf-8")
                    self._f.write('<?xml version="1.0" encoding="UTF-8"?><i>')
                    self._xml_end = self._f.tell()
                    self._f.write("</i>")
                    self._f.flush()
            self._start_ms = int(time.time() * 1000)
        except Exception as e:  # noqa: BLE001
            self.on_log("弹幕文件创建失败：%s" % e, "warn")
            self.stop()
            return False
        try:
            if self.platform == "bili":
                from toolbox.bili_live import BiliLiveFetcher
                self._fetcher = BiliLiveFetcher(
                    self.room, on_message=self._on_msg,
                    on_room_info=None, on_status=self._on_status)
            elif self.platform == "douyin":
                from toolbox.douyin_live import DouyinLiveFetcher
                self._fetcher = DouyinLiveFetcher(
                    self.room, on_message=self._on_msg,
                    on_room_info=None, on_status=self._on_status)
            else:
                from toolbox.live_chat import BrowserChatFetcher
                self._fetcher = BrowserChatFetcher(
                    self.platform, self.room, on_message=self._on_msg,
                    on_room_info=None, on_status=self._on_status)
            self._fetcher.start()
            self.on_log("弹幕接入已开启%s" % (("：" + os.path.basename(self.path)) if self.path else "（实时渲染）"), "ok")
            return True
        except Exception as e:  # noqa: BLE001
            self.on_log("弹幕连接失败（不影响视频录制）：%s" % e, "warn")
            self.stop()
            return False

    def stop(self):
        self._stopped = True
        try:
            if self._fetcher is not None:
                self._fetcher.stop()
        except Exception:
            pass
        self._fetcher = None
        with self._lock:
            if self._journal:
                self._journal.close()
                self._journal = None
            if self._f:
                try:
                    self._f.flush()
                    os.fsync(self._f.fileno())
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
        if self._stopped:
            return
        ts = m.get("ts") or int(time.time() * 1000)
        try:
            rec = {"method": m.get("type") or "", "timestamp": int(ts),
                   "content": m.get("content") or text or "",
                   "user": {"name": m.get("nickname") or ""}}
            if m.get("giftName"):
                rec["gift"] = {"name": m["giftName"], "count": m.get("giftCount") or 1,
                               "price": m.get("giftPrice") or 0, "icon": m.get("giftIcon") or ""}
            if m.get("rtf"):
                rec["rtfContent"] = m["rtf"]
            with self._lock:
                if self._journal:
                    self._journal.write(rec)
            if self._f and name in ("弹幕", "表情弹幕"):
                rel = max(0.0, (int(ts) - self._start_ms) / 1000.0)
                mode = 1
                size = 25
                color = 16777215
                rowid = str(abs(hash((ts, m.get("nickname") or "", m.get("content") or ""))) % 10**12)
                body = escape(str(m.get("content") or text or ""))
                line = ('<d p="%.3f,%d,%d,%d,%d,0,0,%s">%s</d>'
                        % (rel, mode, size, color, int(ts), rowid, body))
                with self._lock:
                    if self._f:
                        self._f.seek(self._xml_end)
                        self._f.write(line)
                        self._xml_end = self._f.tell()
                        self._f.write("</i>")
                        self._f.truncate()
                        self._f.flush()
            with self._lock:
                self._count += 1
        except Exception as e:
            self.on_log("弹幕写入失败：%s" % type(e).__name__, "warn")
        if self.on_message and not self._stopped:
            try:
                self.on_message(name, text, dict(m))
            except Exception:
                pass
