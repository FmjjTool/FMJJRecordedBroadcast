# -*- coding: utf-8 -*-
"""「已存账号 / 录播用户」选择器：从本机已有文件里直接挑直播间。

弹幕姬房间号旁边点 📇 就打开这个窗口，不用再手打房间号。

读取来源（都是本机已有数据，不联网也能列出来）：
  1. 直播录制列表 —— config/live_record.json 里两个平台的 anchors（房间号 + 名称）
  2. 抖音房间缓存 —— config/douyin_live_rooms.json（连过的房间 + 主播/标题）
  3. 账号获取 —— config/accounts.txt（支持直播间链接、B站空间、抖音主页）
  4. 最近连接 —— config/douyin_live.json / bili_live.json 的 last_room

需要联网的两种解析：
  · B站空间链接（space.bilibili.com/UID）→ 直播间号：游客接口 getRoomInfoOld
  · 抖音主页链接（douyin.com/user/sec_uid）→ 直播间号：Playwright 页面上下文
    请求 profile/other（纯 httpx 会被 Argus 拦成空响应；未开播时取不到房间号）
"""

from __future__ import annotations

import json
import os
import re
import threading

import httpx
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")

PLATFORM_LABEL = {"douyin": "抖音", "bili": "B站"}


class RoomEntry:
    """一条候选直播间。room 为空表示还要解析（uid / sec_uid）。"""

    def __init__(self, source, platform, name="", room="", note="",
                 uid="", sec_uid="", live_url=""):
        self.source = source
        self.platform = platform
        self.name = name or ""
        self.room = str(room or "")
        self.note = note or ""
        self.uid = uid or ""
        self.sec_uid = sec_uid or ""
        self.live_url = live_url or ""

    @property
    def ready(self):
        return bool(self.room)


def _config_dir():
    from pathlib import Path
    try:
        from toolbox.config_manager import get_config_dir
        return get_config_dir()
    except Exception:
        return Path(__file__).resolve().parent.parent.parent / "config"


def parse_account_url(url):
    """账号 URL → (platform, room, uid, sec_uid)。识别不了返回 ("", "", "", "")。"""
    u = (url or "").strip()
    if not u:
        return "", "", "", ""
    m = re.search(r"live\.douyin\.com/(\d+)", u)
    if m:
        return "douyin", m.group(1), "", ""
    m = re.search(r"live\.bilibili\.com/(\d+)", u)
    if m:
        return "bili", m.group(1), "", ""
    m = re.search(r"space\.bilibili\.com/(\d+)", u)
    if m:
        return "bili", "", m.group(1), ""
    m = re.search(r"douyin\.com/user/([A-Za-z0-9_\-]+)", u)
    if m:
        return "douyin", "", "", m.group(1)
    return "", "", "", ""


def bili_uid_to_room(uid, timeout=8):
    """B站 UID → 直播间号（游客接口，绝大多数主播 roomid≠uid 时也能取到）。"""
    try:
        r = httpx.get("https://api.live.bilibili.com/room/v1/Room/getRoomInfoOld",
                      params={"mid": str(uid)},
                      headers={"User-Agent": UA, "Referer": "https://live.bilibili.com/"},
                      timeout=timeout)
        d = r.json()
        if d.get("code") == 0:
            rid = (d.get("data") or {}).get("roomid")
            if rid:
                return str(rid)
    except Exception:
        pass
    return ""


def resolve_douyin_sec(sec_uid, timeout_ms=30000):
    """抖音主页 sec_uid → (昵称, web_rid, 是否直播中)。

    纯 httpx 请求 profile/other 会返回 200 + 空体（Argus 校验浏览器指纹），
    必须在 Playwright 页面上下文里 fetch 才拿得到数据（与下载器的兜底同源）。
    未开播时 room_data 为空 → web_rid 为空，只能拿到昵称。
    """
    import urllib.parse
    try:
        from playwright.sync_api import sync_playwright
        from toolbox.browser import launch as pw_launch
    except Exception:
        return "", "", False
    qs = urllib.parse.urlencode({
        "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
        "sec_user_id": sec_uid, "publish_video_strategy_type": "2",
        "version_code": "170400", "version_name": "17.4.0",
        "cookie_enabled": "true", "platform": "PC", "downlink": "10",
    })
    api = "https://www.douyin.com/aweme/v1/web/user/profile/other/?" + qs
    try:
        with sync_playwright() as p:
            browser = pw_launch(p, headless=True,
                                args=["--disable-blink-features=AutomationControlled",
                                      "--no-sandbox", "--disable-dev-shm-usage"])
            try:
                ctx = browser.new_context(user_agent=UA, viewport={"width": 1920,
                                                                  "height": 1080},
                                          locale="zh-CN")
                ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>false});")
                page = ctx.new_page()
                page.goto("https://www.douyin.com/", wait_until="domcontentloaded",
                          timeout=timeout_ms)
                page.wait_for_timeout(2500)
                txt = page.evaluate(
                    """async (u) => {
                        const r = await fetch(u, {credentials: 'include'});
                        return await r.text();
                    }""", api)
            finally:
                browser.close()
    except Exception:
        return "", "", False
    if not txt or not txt.strip().startswith("{"):
        return "", "", False
    try:
        d = json.loads(txt)
    except Exception:
        return "", "", False
    u = d.get("user") or {}
    nick = u.get("nickname") or ""
    live = bool(int(u.get("live_status") or 0))
    rd = u.get("room_data")
    if isinstance(rd, str) and rd.strip():
        try:
            rd = json.loads(rd)
        except Exception:
            rd = None
    web_rid = ((rd or {}).get("owner") or {}).get("web_rid") or ""
    return nick, str(web_rid), live


def load_local_entries():
    """汇总本机所有可用的直播间来源（去重，直播录制列表优先）。"""
    out = []
    cfg = _config_dir()

    # 1) 直播录制列表
    try:
        st = json.loads((cfg / "live_record.json").read_text(encoding="utf-8"))
        for plat, d in (st.get("platforms") or {}).items():
            for a in (d.get("anchors") or []):
                room = str(a.get("room") or "")
                if room:
                    out.append(RoomEntry("直播录制", plat, a.get("name") or "",
                                         room, a.get("remark") or a.get("title") or ""))
    except Exception:
        pass

    # 2) 抖音房间缓存（连过的房间）
    try:
        cache = json.loads((cfg / "douyin_live_rooms.json").read_text(encoding="utf-8"))
        for rid, d in (cache or {}).items():
            d = d or {}
            out.append(RoomEntry("抖音缓存", "douyin", d.get("anchor") or "",
                                 str(rid), d.get("title") or ""))
    except Exception:
        pass

    # 3) 账号获取（accounts.txt）
    try:
        from accounts_manager import load_accounts
        for acc in load_accounts():
            plat, room, uid, sec = parse_account_url(acc.url)
            if not plat:
                continue
            note = " / ".join(x for x in (acc.group_name, acc.note) if x)
            out.append(RoomEntry("账号获取", plat, acc.name or "", room, note,
                                 uid=uid, sec_uid=sec, live_url=acc.url))
    except Exception:
        pass

    # 4) 最近连接
    for fname, plat in (("douyin_live.json", "douyin"), ("bili_live.json", "bili")):
        try:
            s = json.loads((cfg / fname).read_text(encoding="utf-8"))
            room = str(s.get("last_room") or "")
            if room:
                out.append(RoomEntry("最近连接", plat, "", room, ""))
        except Exception:
            pass

    # 去重：同平台同房间只留第一条（来源顺序即优先级）
    seen = set()
    uniq = []
    for e in out:
        if e.room:
            k = (e.platform, e.room)
            if k in seen:
                continue
            seen.add(k)
        uniq.append(e)
    return uniq


class _Bridge(QObject):
    resolved = Signal(int, str, str)   # row, room, name


class RoomPickerDialog(QDialog):
    """从本机已存账号/录播用户里挑一个直播间。"""

    def __init__(self, parent, platform=""):
        super().__init__(parent)
        self.setWindowTitle("选择已存账号 / 录播用户")
        self.resize(860, 540)
        self.platform = platform
        self.entries = load_local_entries()
        self.bridge = _Bridge(self)
        self.bridge.resolved.connect(self._on_resolved,
                                     Qt.ConnectionType.QueuedConnection)

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 搜索名称 / 房间号 / 备注")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refill)
        top.addWidget(self.search, 1)
        self.cb_platform = QComboBox()
        self.cb_platform.addItem("全部平台", "")
        self.cb_platform.addItem("抖音", "douyin")
        self.cb_platform.addItem("B站", "bili")
        if platform:
            i = self.cb_platform.findData(platform)
            if i >= 0:
                self.cb_platform.setCurrentIndex(i)
        self.cb_platform.currentIndexChanged.connect(self._refill)
        top.addWidget(self.cb_platform)
        self.ck_ready = QCheckBox("只看能直接用的")
        self.ck_ready.setChecked(True)
        self.ck_ready.toggled.connect(self._refill)
        top.addWidget(self.ck_ready)
        lay.addLayout(top)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["来源", "平台", "名称", "房间号", "备注"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda _i: self._accept())
        lay.addWidget(self.table, 1)

        bar = QHBoxLayout()
        self.btn_bili = QPushButton("解析B站空间→房间号")
        self.btn_bili.setToolTip("对选中的「账号获取 → B站空间」条目查询直播间号")
        self.btn_bili.clicked.connect(self._resolve_bili)
        bar.addWidget(self.btn_bili)
        self.btn_dy = QPushButton("解析抖音主页（浏览器）")
        self.btn_dy.setToolTip("抖音主页链接要在浏览器里请求才拿得到直播间号；"
                               "未开播时只能取到昵称")
        self.btn_dy.clicked.connect(self._resolve_douyin)
        bar.addWidget(self.btn_dy)
        self.status = QLabel("")
        self.status.setStyleSheet("color:#666;")
        bar.addWidget(self.status, 1)
        lay.addLayout(bar)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                | QDialogButtonBox.StandardButton.Cancel)
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("填入房间号")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        self._refill()
        # B站空间条目自动解析（最多 12 条，后台并发）
        self._auto_resolve_bili()

    # ---------- 列表 ----------
    def _visible(self):
        kw = self.search.text().strip().lower()
        plat = self.cb_platform.currentData()
        only_ready = self.ck_ready.isChecked()
        out = []
        for e in self.entries:
            if plat and e.platform != plat:
                continue
            if only_ready and not e.ready:
                continue
            if kw:
                hay = " ".join((e.name, e.room, e.note, e.source,
                                PLATFORM_LABEL.get(e.platform, ""))).lower()
                if kw not in hay:
                    continue
            out.append(e)
        return out

    def _refill(self):
        rows = self._visible()
        # 解析后同平台同房间会重复（如一个账号的 video/dynamic 两条空间链接）→ 去重
        seen, uniq = set(), []
        for e in rows:
            if e.room:
                k = (e.platform, e.room)
                if k in seen:
                    continue
                seen.add(k)
            uniq.append(e)
        rows = uniq
        self.table.setRowCount(0)
        for e in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = [e.source, PLATFORM_LABEL.get(e.platform, e.platform),
                    e.name or "-", e.room or "（待解析）", e.note]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if c in (1, 3):
                    it.setTextAlignment(Qt.AlignCenter)
                it.setData(Qt.UserRole, id(e))
                self.table.setItem(r, c, it)
        n_ready = sum(1 for e in rows if e.ready)
        self.status.setText("共 %d 条（可直接用 %d 条）" % (len(rows), n_ready))
        self._rows = rows

    def _selected_entry(self):
        r = self.table.currentRow()
        if 0 <= r < len(getattr(self, "_rows", [])):
            return self._rows[r]
        return None

    # ---------- 解析 ----------
    def _auto_resolve_bili(self):
        todo = [e for e in self.entries if e.platform == "bili" and e.uid and not e.room]
        todo = todo[:12]
        if not todo:
            return
        self.status.setText("正在解析 %d 个 B站空间…" % len(todo))

        def work():
            for e in todo:
                rid = bili_uid_to_room(e.uid)
                if rid:
                    e.room = rid
                    self.bridge.resolved.emit(-1, rid, e.name)

        threading.Thread(target=work, daemon=True).start()

    def _resolve_bili(self):
        e = self._selected_entry()
        if not e or e.platform != "bili" or not e.uid:
            self.status.setText("请先选中一条「账号获取 → B站空间」条目")
            return
        self.status.setText("正在解析 B站空间 %s …" % e.uid)

        def work():
            rid = bili_uid_to_room(e.uid)
            if rid:
                e.room = rid
                self.bridge.resolved.emit(-1, rid, e.name)
            else:
                self.bridge.resolved.emit(-2, "", "解析失败：该账号可能没有直播间")

        threading.Thread(target=work, daemon=True).start()

    def _resolve_douyin(self):
        e = self._selected_entry()
        if not e or e.platform != "douyin" or not e.sec_uid:
            self.status.setText("请先选中一条「账号获取 → 抖音主页」条目")
            return
        self.status.setText("正在用浏览器解析抖音主页（约 10 秒）…")
        self.btn_dy.setEnabled(False)

        def work():
            nick, rid, live = resolve_douyin_sec(e.sec_uid)
            if nick:
                e.name = e.name or nick
            if rid:
                e.room = rid
                self.bridge.resolved.emit(-1, rid, e.name)
            else:
                self.bridge.resolved.emit(
                    -2, "", "已取到昵称「%s」，当前未开播/无直播间号" % (nick or "?"))

        threading.Thread(target=work, daemon=True).start()

    def _on_resolved(self, row, room, name):
        self.btn_dy.setEnabled(True)
        if row == -2:
            self.status.setText(room or name)
            self._refill()
            return
        self.status.setText("解析完成：房间号 %s" % room)
        self.ck_ready.setChecked(True)
        self._refill()
        # 选中解析出来的那一行
        for i, e in enumerate(getattr(self, "_rows", [])):
            if e.room == room and (not name or e.name == name):
                self.table.selectRow(i)
                break

    # ---------- 返回 ----------
    def _accept(self):
        e = self._selected_entry()
        if not e:
            self.status.setText("请先选择一个直播间")
            return
        if not e.room:
            self.status.setText("该条目还没有房间号：先点上面的「解析」按钮")
            return
        self.entry = e
        self.accept()

    def exec_get(self):
        """返回选中的 RoomEntry（取消返回 None）。"""
        self.entry = None
        return self.entry if self.exec() == QDialog.DialogCode.Accepted else None
