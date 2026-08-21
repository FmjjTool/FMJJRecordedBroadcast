"""B站资源 · 装扮/收藏集页：商城搜索 → 装扮套装详情 / 收藏集活动详情 → 批量下载。

复刻自 BiliResourceDownloader 的 search/garb.vue + suit/[id].vue + lottery 页，
字段与目录结构逐一对齐原版 generateDownloadTask：
装扮 8 类：空间背景/粉丝牌/评论背景/表情包/加载动画/进度条/皮肤/点赞动画/头像框。
收藏集：act/basic(勋章+活动视觉+动画+UP头像) + asset_bag(隐藏收藏集+兑换) +
lottery_home_detail(卡牌图/卡牌视频镜像/兑换内容/权益资源)。
"""

import json
from datetime import datetime

from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from bili import api
from bili.download import DownloadTask
from toolbox.config_manager import get_export_dir
from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.pages.bili_emoji import render_files

# 皮肤的固定资源字段（key → 中文名），与原版 skinProps 一致
SKIN_PROPS = [
    ("head_bg", "顶部背景图"),
    ("head_tab_bg", "首页顶部标签页背景"),
    ("tail_bg", "底部背景图"),
    ("tail_icon_main", "首页按钮图标"),
    ("tail_icon_channel", "频道按钮图标"),
    ("tail_icon_dynamic", "动态按钮图标"),
    ("tail_icon_pub_btn_bg", "发布按钮图标"),
    ("tail_icon_shop", "会员购按钮图标"),
    ("tail_icon_myself", "“我的”按钮图标"),
    ("tail_icon_selected_main", "首页按钮选中图标"),
    ("tail_icon_selected_channel", "频道按钮选中图标"),
    ("tail_icon_selected_dynamic", "动态按钮选中图标"),
    ("tail_icon_selected_pub_btn_bg", "发布按钮选中图标"),
    ("tail_icon_selected_shop", "会员购按钮选中图标"),
    ("tail_icon_selected_myself", "“我的”按钮选中图标"),
    ("head_myself_squared_bg", "“我的”界面背景图"),
]


def _sfx(name: str, suffix: str) -> str:
    return name if name.endswith(suffix) else name + suffix


def _emoji_key(raw: str) -> str:
    """装扮表情名 [XXX_xxx] → xxx。"""
    parts = raw.split("_")
    if len(parts) > 1:
        return parts[1].rstrip("]").rstrip(")")
    return raw


def _strip_crop(url: str) -> str:
    """剥离B站图片裁剪后缀（@...）取原图。"""
    return (url or "").split("@")[0]


def _emoji_urls(props: dict) -> list[str]:
    """表情全部可用格式地址（gif/webp/png 去重）。"""
    out = []
    for u in (props.get("image_gif"), props.get("image_webp"), props.get("image")):
        if u and u not in out:
            out.append(u)
    return out


def _space_bg_images(p: dict) -> tuple[list, list, list]:
    """空间背景按 image{n}_landscape 循环取横/竖图与背景视频。"""
    landscapes, portraits, videos = [], [], []
    i = 1
    while f"image{i}_landscape" in p:
        landscapes.append(p.get(f"image{i}_landscape", ""))
        portraits.append(p.get(f"image{i}_portrait", ""))
        for v in (p.get(f"space{i}_mp4_horizontal", ""), p.get(f"space{i}_mp4_vertical", "")):
            if v:
                videos.append(v)
        i += 1
    return landscapes, portraits, videos


def suit_files(d: dict) -> list[dict]:
    """装扮详情 → 下载文件清单（与原版 suit/[id].vue generateDownloadTask 逐项对齐）。"""
    files: list[dict] = []

    def add(rel, url, ext=None):
        if url and isinstance(url, str) and url.startswith("http"):
            files.append({"path": rel, "url": url, **({"ext": ext} if ext else {})})

    title = api.sanitize(d.get("name", "装扮"))
    items = d.get("suit_items", {}) or {}
    props = d.get("properties", {}) or {}

    # 装扮主体：背景图与分享背景图
    add(f"{title}/装扮背景图", props.get("image_cover"))
    add(f"{title}/装扮分享背景图", props.get("fan_share_image"))

    # 空间背景图/视频
    for bg in items.get("space_bg", []) or []:
        p = bg.get("properties", {}) or {}
        landscapes, portraits, videos = _space_bg_images(p)
        base = api.sanitize(_sfx(bg.get("name", ""), "背景"))
        for i, u in enumerate(landscapes):
            add(f"{title}/{base}/背景{i + 1}", u)
        for i, u in enumerate(portraits):
            base_p = api.sanitize(_sfx(bg.get("name", ""), "肖像"))
            add(f"{title}/{base_p}/肖像{i + 1}", u)
        for i, u in enumerate(videos):
            add(f"{title}/{base}/背景视频{i + 1}", u)

    # 表情包（item_emoji_list JSON 优先，suit_items.emoji 兜底）
    for pkg in items.get("emoji_package", []) or []:
        pkg_dir = api.sanitize(_sfx(pkg.get("name", ""), "表情包"))
        p = pkg.get("properties", {}) or {}
        emojis = []
        raw_list = p.get("item_emoji_list")
        if isinstance(raw_list, str) and raw_list.startswith("["):
            try:
                emojis = json.loads(raw_list)
            except Exception:
                emojis = []
        elif isinstance(raw_list, list):
            emojis = raw_list
        if not emojis:
            for e in items.get("emoji", []) or []:
                emojis.append({"name": e.get("name", ""),
                               "properties": e.get("properties", {}) or {}})
        for e in emojis:
            ep = e.get("properties", {}) or {}
            name = api.sanitize(_emoji_key(e.get("name", "表情")))
            for u in _emoji_urls(ep):
                add(f"{title}/{pkg_dir}/{name}", u)

    # 进度条（Lottie 动画版 / 图片版）
    for icon in items.get("play_icon", []) or []:
        p = icon.get("properties", {}) or {}
        base = api.sanitize(_sfx(icon.get("name", ""), "进度条"))
        if p.get("drag_icon"):
            add(f"{title}/{base}/进度条拖动动画", p.get("drag_icon"))
            add(f"{title}/{base}/进度条松手动画", p.get("icon"))
        else:
            add(f"{title}/{base}/进度条左划", p.get("drag_left_png"))
            add(f"{title}/{base}/进度条右划", p.get("drag_right_png"))
            add(f"{title}/{base}/进度条指示", p.get("middle_png"))
        add(f"{title}/{base}/方形演示图", p.get("squared_image"))
        add(f"{title}/{base}/图标", p.get("static_icon_image"))

    # 皮肤（17 个固定字段 + “我的”背景视频/视频列表 + 封面/预览/资源包）
    for skin in items.get("skin", []) or []:
        p = skin.get("properties", {}) or {}
        base = api.sanitize(_sfx(skin.get("name", ""), "皮肤"))
        for key, label in SKIN_PROPS:
            u = p.get(key)
            if isinstance(u, str) and u:
                add(f"{title}/{base}/{label}", u)
        mp4 = p.get("head_myself_mp4_bg")
        if isinstance(mp4, str) and mp4.startswith("http"):
            add(f"{title}/{base}/“我的”界面背景视频", mp4)
        else:
            raw = p.get("head_myself_mp4_bg_list")
            if isinstance(raw, str) and raw.startswith("["):
                try:
                    for i, u in enumerate(json.loads(raw)):
                        if u:
                            add(f"{title}/{base}/“我的”界面背景视频{i + 1}", u)
                except Exception:
                    pass
        add(f"{title}/{base}/封面图", p.get("image_cover"))
        add(f"{title}/{base}/预览图", p.get("image_preview"))
        add(f"{title}/{base}/皮肤资源包", p.get("package_url"))

    # 头像框（image + 效果演示图）
    for pdt in items.get("pendant", []) or []:
        p = pdt.get("properties", {}) or {}
        base = api.sanitize(_sfx(title, "") + "头像框")
        add(f"{title}/{base}/{api.sanitize(pdt.get('name', '头像框'))}", p.get("image"))
        add(f"{title}/{base}/{api.sanitize(pdt.get('name', '头像框'))}(效果演示)", p.get("garb_avatar"))

    # 粉丝牌背景（image + fans_image + 预览小图）
    for card in items.get("card", []) or []:
        p = card.get("properties", {}) or {}
        base = api.sanitize(_sfx(card.get("name", ""), "粉丝牌背景"))
        add(f"{title}/{base}", p.get("image"))
        add(f"{title}/{base}(2)", p.get("fans_image"))
        add(f"{title}/{base}(预览)", p.get("image_preview_small"))

    # 评论背景
    for cbg in items.get("card_bg", []) or []:
        p = cbg.get("properties", {}) or {}
        base = api.sanitize(_sfx(cbg.get("name", ""), "评论背景"))
        add(f"{title}/{base}", p.get("image"))
        add(f"{title}/{base}(预览)", p.get("image_preview_small"))

    # 加载动画（本体 + 序列帧 + 预览）
    for ld in items.get("loading", []) or []:
        p = ld.get("properties", {}) or {}
        base = api.sanitize(_sfx(ld.get("name", ""), "加载动画"))
        add(f"{title}/{base}", p.get("loading_url"))
        add(f"{title}/{base}(序列帧)", p.get("loading_frame_url"))
        add(f"{title}/{base}(预览)", p.get("image_preview_small"))

    # 点赞动画（SVGA + 裁剪版 + 预览图）
    for tu in items.get("thumbup", []) or []:
        p = tu.get("properties", {}) or {}
        base = api.sanitize(_sfx(tu.get("name", ""), "点赞动画"))
        add(f"{title}/{base}", p.get("image_ani"), ext="svga")
        add(f"{title}/{base}(裁剪版)", p.get("image_ani_cut"), ext="svga")
        add(f"{title}/{base}预览图", p.get("image_preview"))

    # 按 URL 去重
    seen, out = set(), []
    for f in files:
        if f["url"] not in seen:
            seen.add(f["url"])
            out.append(f)
    return out


def lottery_files(act_id: str, act: dict, bag: dict, lotteries: list[dict]) -> list[dict]:
    """收藏集活动 → 下载文件清单（与原版 lottery.vue generateDownloadTask 逐项对齐）。"""
    files: list[dict] = []

    def add(rel, url, ext=None, mirrors=None):
        if url and isinstance(url, str) and url.startswith("http"):
            files.append({"path": rel, "url": url, **({"ext": ext} if ext else {}),
                          **({"mirrors": mirrors} if mirrors else {})})

    def add_raw(rel, url):
        add(rel, _strip_crop(url) if url else url)

    title = api.sanitize(act.get("act_title", f"收藏集{act_id}"))

    # 收藏集勋章（collector_medal_info JSON：等级主图 + 各场景图）
    raw_medals = act.get("collector_medal_info")
    if isinstance(raw_medals, str):
        try:
            raw_medals = json.loads(raw_medals)
        except Exception:
            raw_medals = []
    for medal in raw_medals or []:
        level = f"{medal.get('level', '?')}级"
        add(f"{title}/收藏集勋章/{level}", medal.get("image"))
        for k, u in sorted((medal.get("scene_image", {}) or {}).items(),
                           key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
            add(f"{title}/收藏集勋章/类型{k}/{level}", u)

    # 活动视觉资源（主视觉/方形图/App展示/卡牌光效阴影，剥裁剪后缀）
    add_raw(f"{title}/活动视觉/活动主视觉图", act.get("act_y_img"))
    add_raw(f"{title}/活动视觉/活动方形图", act.get("act_square_img"))
    add_raw(f"{title}/活动视觉/App端头图展示", act.get("app_head_show"))
    add_raw(f"{title}/活动视觉/App端空间展示", act.get("app_space_show"))
    add_raw(f"{title}/活动视觉/横版卡牌光效图", act.get("horizontal_card_light_url"))
    add_raw(f"{title}/活动视觉/横版卡牌阴影图", act.get("horizontal_card_shadow_url"))
    add_raw(f"{title}/活动视觉/竖版卡牌光效图", act.get("vertical_card_light_url"))
    add_raw(f"{title}/活动视觉/竖版卡牌阴影图", act.get("vertical_card_shadow_url"))

    # 抽卡/入场动画视频（key 为帧率）
    anim = act.get("animation_info", {}) or {}
    for fps, u in (anim.get("animation_draw_url", {}) or {}).items():
        add_raw(f"{title}/活动视觉/抽卡动画{fps}fps", u)
    for fps, u in (anim.get("animation_entry_url", {}) or {}).items():
        add_raw(f"{title}/活动视觉/入场动画{fps}fps", u)

    # 相关UP头像
    infos = act.get("related_user_infos", {}) or {}
    for mid in act.get("related_mids", []) or []:
        info = infos.get(str(mid)) or infos.get(mid) or {}
        if info.get("avatar"):
            add_raw(f"{title}/活动视觉/相关UP头像 - {api.sanitize(info.get('nickname', mid))}", info["avatar"])

    # 各收藏集卡池详情
    for lot in lotteries:
        lid = str(lot.get("lottery_id", ""))
        ltitle = api.sanitize(lot.get("lottery_name") or lot.get("name") or f"收藏集{lid}")
        try:
            detail = api.lottery_home_detail(act_id, lid)
        except api.BiliApiError:
            detail = {}
        if detail.get("name"):
            ltitle = api.sanitize(detail["name"])

        # 卡牌图片（官方下载版优先）
        for it in detail.get("item_list", []) or []:
            ci = it.get("card_info", {}) or {}
            if not ci.get("card_name"):
                continue
            cname = api.sanitize(ci["card_name"])
            add(f"{ltitle}/卡池图片/{cname}", ci.get("card_img_download") or ci.get("card_img"))
            # 卡牌视频（官方下载版优先，其余为镜像备用）
            urls = list(dict.fromkeys([*(ci.get("video_list_download") or []), *(ci.get("video_list") or [])]))
            urls = [u for u in urls if u]
            if urls:
                add(f"{ltitle}/卡池视频/{cname}", urls[0], mirrors=urls[1:])

        # 兑换内容（钻石头像背景/勋章/典藏卡/头像框/出框头像/表情包等，不限类型）
        cl = detail.get("collect_list", {}) or {}
        combined = list(cl.get("collect_chain", []) or []) + list(cl.get("collect_infos", []) or [])
        for r in combined:
            rname = api.sanitize(r.get("redeem_item_name", "兑换项"))
            add(f"{ltitle}/其他/{rname}", r.get("redeem_item_image"))
            add(f"{ltitle}/其他/{rname}(下载版)", r.get("redeem_item_image_download"))
            add(f"{ltitle}/其他/{rname}(详情图)", r.get("redeem_detail_image"))
            if r.get("redeem_item_type") == 1:
                cti = ((r.get("card_item", {}) or {}).get("card_type_info", {}) or {})
                add(f"{ltitle}/其他/{rname}(概览图)", cti.get("overview_image"))
                anims = list(dict.fromkeys(
                    ((cti.get("content", {}) or {}).get("animation", {}) or {}).get("animation_video_urls", []) or []))
                for i, u in enumerate(anims):
                    add(f"{ltitle}/其他/{rname}(视频{i + 1 if len(anims) > 1 else ''})", u)

        # 权益资源（剥裁剪后缀去重）
        for i, r in enumerate(detail.get("act_rights_infos", []) or []):
            add_raw(f"{ltitle}/权益资源/权益{i + 1}", r.get("resource"))

    # 各收藏集封面
    for lot in act.get("lottery_list", []) or []:
        ltitle = api.sanitize(lot.get("lottery_name", f"收藏集{lot.get('lottery_id', '')}"))
        add(f"{ltitle}/其他/封面", lot.get("lottery_image"))
        add(f"{ltitle}/其他/方形封面", lot.get("lottery_square_img"))

    # asset_bag 兑换列表（不同等级勋章兑换图等）
    for r in bag.get("collect_list", []) or []:
        if r.get("redeem_item_image"):
            add(f"{title}/兑换内容/{api.sanitize(r.get('redeem_item_name', '兑换项'))}", r["redeem_item_image"])

    # 按 URL 去重
    seen, out = set(), []
    for f in files:
        if f["url"] not in seen:
            seen.add(f["url"])
            out.append(f)
    return out


class BiliGarbPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._current = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_search_tab(), "🔍 装扮/收藏集搜索")
        self.tabs.addTab(self._build_suit_tab(), "👕 装扮详情（按ID）")
        self.tabs.addTab(self._build_lottery_tab(), "🎁 收藏集活动（按ID）")

    # ---------- 搜索 ----------
    def _build_search_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText("装扮/收藏集关键词（如 白菜、UP主名），回车搜索")
        self.search_entry.returnPressed.connect(self._search)
        row.addWidget(self.search_entry, 1)
        btn = QPushButton("搜索")
        btn.setStyleSheet("background:#1976d2;color:white;padding:5px 16px;")
        btn.clicked.connect(self._search)
        row.addWidget(btn)
        v.addLayout(row)

        self.search_table = QTableWidget(0, 5)
        self.search_table.setHorizontalHeaderLabels(["类型", "名称", "item_id", "简介", "查看"])
        self.search_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.search_table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.search_table, 1)
        self.search_info = QLabel("无需登录。type=ip 是装扮（套装详情），type=dlc_act 是收藏集（活动详情）")
        self.search_info.setStyleSheet("color:#888;")
        v.addWidget(self.search_info)
        return w

    def _search(self):
        kw = self.search_entry.text().strip()
        if not kw:
            return
        self.search_info.setText("搜索中……")

        def job():
            out, seen, pn = [], set(), 1
            while pn <= 3:
                d = api.garb_search(kw, pn)
                lst = d.get("list", [])
                if not lst:
                    break
                for it in lst:
                    p = it.get("properties", {})
                    # 收藏集的 item_id 无效，必须用 properties.dlc_act_id 跳转
                    key = str(p.get("dlc_act_id") or it.get("item_id", ""))
                    jk = f"{it.get('jump_link', '')}"
                    if key in seen or jk in seen:
                        continue
                    seen.add(key)
                    seen.add(jk)
                    out.append({
                        "type": p.get("type", ""),
                        "name": it.get("name", ""),
                        "item_id": key,
                        "desc": p.get("desc", "") or "",
                        "sale": it.get("sale_count_desc", ""),
                    })
                total = d.get("total", 0)
                if len(seen) >= total * 2 or len(lst) < 20:
                    break
                pn += 1
            return out

        def done(result, err):
            if err:
                self.search_info.setText(f"搜索失败：{err}")
                return
            self.search_table.setRowCount(0)
            for i, d in enumerate(result):
                self.search_table.insertRow(i)
                tp = "收藏集" if d["type"] == "dlc_act" else "装扮"
                self.search_table.setItem(i, 0, QTableWidgetItem(tp))
                self.search_table.setItem(i, 1, QTableWidgetItem(d["name"]))
                self.search_table.setItem(i, 2, QTableWidgetItem(d["item_id"]))
                sale = str(d.get("sale") or "")
                self.search_table.setItem(i, 3, QTableWidgetItem(f"销量 {sale}" if sale else ""))
                btn = QPushButton("详情")
                btn.clicked.connect(lambda _, iid=d["item_id"], t=d["type"]: self._open_detail(iid, t))
                self.search_table.setCellWidget(i, 4, btn)
            self.search_info.setText(f"共 {len(result)} 个结果（已翻最多 3 页）")

        run_bg(job, done)

    def _open_detail(self, iid, t):
        if t == "dlc_act":
            self.tabs.setCurrentIndex(2)
            self.lottery_entry.setText(str(iid))
            self._load_lottery()
        else:
            self.tabs.setCurrentIndex(1)
            self.suit_entry.setText(str(iid))
            self._load_suit()

    # ---------- 装扮详情 ----------
    def _build_suit_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        row.addWidget(QLabel("装扮 item_id："))
        self.suit_entry = QLineEdit()
        self.suit_entry.setPlaceholderText("装扮 item_id 或装扮页链接（如 336202401），多个逗号分隔")
        self.suit_entry.returnPressed.connect(self._load_suit)
        row.addWidget(self.suit_entry, 1)
        btn = QPushButton("查看")
        btn.setStyleSheet("background:#1976d2;color:white;padding:5px 14px;")
        btn.clicked.connect(self._load_suit)
        row.addWidget(btn)
        v.addLayout(row)

        self.suit_info = QLabel("加载装扮全部资源：空间背景/粉丝牌/评论背景/表情包/加载动画/进度条/皮肤/点赞动画")
        self.suit_info.setStyleSheet("color:#888;")
        v.addWidget(self.suit_info)
        self.suit_table = QTableWidget(0, 2)
        self.suit_table.setHorizontalHeaderLabels(["文件", "目录"])
        self.suit_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.suit_table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.suit_table, 1)
        self._suit_bar = self._dl_bar("装扮")
        v.addWidget(self._suit_bar)
        return w

    # 跨页入口：从其他页面跳转加载（先 show_page 再调用）
    def open_act(self, act_id: str):
        self.tabs.setCurrentIndex(2)
        self.lottery_entry.setText(str(act_id))
        self._load_lottery()

    def open_suit(self, iid: str):
        self.tabs.setCurrentIndex(1)
        self.suit_entry.setText(str(iid))
        self._load_suit()

    def _load_suit(self):
        from bili.links import parse_link
        raw = self.suit_entry.text().strip()
        if "http" in raw.lower():
            parsed = parse_link(raw)
            if parsed:
                typ, oid = parsed
                if typ == "lottery":  # 收藏集链接输到装扮页 → 自动跳收藏集 Tab
                    self.suit_info.setText(f"识别到收藏集链接（act_id={oid}），已跳转收藏集活动页")
                    self.open_act(oid)
                    return
                if typ == "suit":
                    self.suit_entry.setText(oid)
                    raw = oid
        if not raw:
            return
        ids = [x.strip() for x in raw.replace("，", ",").replace("&", ",").split(",") if x.strip()]
        self.suit_info.setText("加载中……")

        def job():
            files, title, acts = [], "", []
            for iid in ids:
                try:
                    d = api.garb_item(iid)
                except api.BiliApiError as e:
                    # 装扮接口 data=null（失效/下架）→ 尝试按收藏集活动解析
                    try:
                        act = api.act_basic(iid)
                        acts.append((iid, act.get("act_title", iid)))
                        continue
                    except api.BiliApiError:
                        raise e
                title = d.get("name", iid)
                files.extend(suit_files(d))
            return {"title": title, "files": files, "acts": acts}

        def done(result, err):
            if err:
                self.suit_info.setText(f"加载失败：{err}")
                return
            if result["acts"]:  # 命中收藏集 → 自动跳转
                iid, t = result["acts"][0]
                self.suit_info.setText(f"{iid} 是收藏集活动「{t}」（装扮接口无数据），已自动跳转收藏集页")
                self.open_act(iid)
                return
            self._current = result
            render_files(self.suit_table, self.suit_info, result)

        run_bg(job, done)

    # ---------- 收藏集 ----------
    def _build_lottery_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        row.addWidget(QLabel("活动 act_id："))
        self.lottery_entry = QLineEdit()
        self.lottery_entry.setPlaceholderText("收藏集 act_id 或收藏集页链接（如 107423），搜索页可跳转")
        self.lottery_entry.returnPressed.connect(self._load_lottery)
        row.addWidget(self.lottery_entry, 1)
        btn = QPushButton("查看")
        btn.setStyleSheet("background:#1976d2;color:white;padding:5px 14px;")
        btn.clicked.connect(self._load_lottery)
        row.addWidget(btn)
        v.addLayout(row)

        self.lottery_info = QLabel("加载活动视觉/勋章/全部收藏集卡牌图与卡牌视频")
        self.lottery_info.setStyleSheet("color:#888;")
        v.addWidget(self.lottery_info)
        self.lottery_table = QTableWidget(0, 2)
        self.lottery_table.setHorizontalHeaderLabels(["文件", "目录"])
        self.lottery_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.lottery_table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.lottery_table, 1)
        self._lottery_bar = self._dl_bar("收藏集")
        v.addWidget(self._lottery_bar)
        return w

    def _load_lottery(self):
        from bili.links import parse_link
        act_id = self.lottery_entry.text().strip()
        if "http" in act_id.lower():
            parsed = parse_link(act_id)
            if parsed:
                typ, oid = parsed
                if typ == "lottery":
                    self.lottery_entry.setText(oid)
                    act_id = oid
                else:
                    self.lottery_info.setText(f"该链接是「{typ}」类型，非收藏集链接")
                    return
        if not act_id:
            return
        self.lottery_info.setText("加载中……")

        def job():
            act = api.act_basic(act_id)
            title = act.get("act_title", act_id)
            bag = api.asset_bag(act_id)
            lots = act.get("lottery_list", []) or []
            # 资产包里可能有隐藏收藏集
            known = {str(x.get("lottery_id", "")) for x in lots}
            for ls in bag.get("lottery_simple_list", []) or []:
                if str(ls.get("lottery_id", "")) not in known:
                    lots.append(ls)
            files = lottery_files(act_id, act, bag, lots)
            return {"title": f"{title}", "files": files}

        def done(result, err):
            if err:
                self.lottery_info.setText(f"加载失败：{err}")
                return
            self._current = result
            render_files(self.lottery_table, self.lottery_info, result)

        run_bg(job, done)

    # ---------- 下载条 ----------
    def _dl_bar(self, sub_dir):
        from gui_qt.pages.bili_emoji import _DlBar
        return _DlBar(self, lambda: self._current, sub_dir, show_fmt=False)
