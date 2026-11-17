# -*- coding: utf-8 -*-
"""微博超话数据采集（访客态免登录）。

核心接口（2026-11 实测）：
  GET https://weibo.com/ajax_proxy/chaohua/page?flowId=<超话ID><频道后缀>
  - flowId 不带后缀：返回超话头部 + 频道列表
  - 游标翻页：下一页参数取返回体 moreInfo.params，since_id 必须原样回传
  - 访客 cookie：passport.weibo.com/visitor 两步换取；请求头同时带
    Referer + X-XSRF-TOKEN，否则返回 303403

已知边界：
  - 阅读量 / 影响力 / 榜单分数在网页端不存在（仅 App 超话详情页 / 主持人后台可见）
  - 帖子总数、粉丝数只有「4.8万 / 21万」这类缩略文案，无精确值
  - 「最新」频道翻页不是严格时间倒序，深翻会混入零星老帖
"""

from __future__ import annotations

import html
import json
import re
import statistics
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import requests

API_PAGE = "https://weibo.com/ajax_proxy/chaohua/page"
PAGE_URL = "https://weibo.com/p/%s"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36")
DATE_FMT = "%a %b %d %H:%M:%S %z %Y"  # Sun Nov 15 19:35:04 +0800 2026
PAGE_INTERVAL = 1.0  # 每页间隔秒数，太快会触发 303403

_TOPIC_ID_RE = re.compile(r"(10080[0-9a-fA-F]{10,})")
# header.channelInfo 缺失时的兜底频道（flowId 后缀按 2026-11 实测）
FALLBACK_CHANNELS = [
    ("热门", "_-_recommend", 5),
    ("最新", "_-_feed", 70),
    ("精华", "_-_soul", 30),
    ("水帖专区", "__10010001_-_tag_comment_sort", 30),
]
DEFAULT_MAX_PAGES = {name: pages for name, _suffix, pages in FALLBACK_CHANNELS}

WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
POST_COLUMNS = [
    ("id", "帖子ID"), ("mblogid", "短链ID"), ("author", "作者"), ("uid", "UID"),
    ("author_followers", "作者粉丝数"), ("author_verified", "是否认证"),
    ("dt_str", "发布时间"), ("text", "正文"), ("source", "发布设备"),
    ("region", "地区"), ("reposts", "转发"), ("comments", "评论"),
    ("likes", "点赞"), ("pics", "图片数"), ("is_long", "是否长文"), ("url", "链接"),
]


class ChaohuaError(RuntimeError):
    """超话接口 / 会话异常。"""


class ChaohuaStopped(ChaohuaError):
    """用户停止采集。"""


# ==================== 基础工具 ====================

def extract_topic_id(raw: str) -> str:
    """从超话链接或纯 ID 中提取 10080... 超话 ID。"""
    m = _TOPIC_ID_RE.search(raw or "")
    if not m:
        raise ValueError("未识别到超话 ID（应形如 10080857b3593b49a1cef3b2585f65295e0419）")
    return m.group(1)


def page_url(topic_id: str) -> str:
    return PAGE_URL % topic_id


def parse_dt(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(value or "", DATE_FMT).replace(tzinfo=None)
    except Exception:
        return None


def _safe_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _clean_text(value: str | None) -> str:
    text = re.sub(r"<br\s*/?>", " ", value or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def normalize_post(raw: dict) -> dict:
    """把微博 status 对象压平成分析用字段（不存在阅读量字段）。"""
    user = raw.get("user") or {}
    dt = parse_dt(raw.get("created_at"))
    uid = str(user.get("idstr") or user.get("id") or "")
    mblogid = str(raw.get("mblogid") or "")
    pics = raw.get("pic_num")
    if pics is None:
        pics = len(raw.get("pic_ids") or [])
    return {
        "id": str(raw.get("idstr") or raw.get("id") or ""),
        "mblogid": mblogid,
        "author": user.get("screen_name") or "",
        "uid": uid,
        "author_followers": _safe_int(user.get("followers_count")),
        "author_verified": bool(user.get("verified")),
        "created_at_raw": raw.get("created_at") or "",
        "dt": dt,
        "dt_str": dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "",
        "text": _clean_text(raw.get("text")),
        "source": _clean_text(raw.get("source")),
        "region": raw.get("region_name") or "",
        "reposts": _safe_int(raw.get("reposts_count")),
        "comments": _safe_int(raw.get("comments_count")),
        "likes": _safe_int(raw.get("attitudes_count")),
        "pics": _safe_int(pics),
        "is_long": bool(raw.get("isLongText")),
        "url": f"https://weibo.com/{uid}/{mblogid}" if uid and mblogid else "",
    }


def dedupe_posts(posts: list[dict]) -> list[dict]:
    seen, unique = set(), []
    for post in posts:
        pid = post.get("id") or ""
        if not pid or pid in seen:
            continue
        seen.add(pid)
        unique.append(post)
    return unique


def merge_posts(data: dict[str, list[dict]]) -> list[dict]:
    """跨频道按帖子 ID 去重，并保留首次出现的频道名。"""
    seen, merged = set(), []
    for channel, posts in data.items():
        for post in posts:
            pid = post.get("id") or ""
            if not pid or pid in seen:
                continue
            seen.add(pid)
            item = dict(post)
            item["channel"] = channel
            merged.append(item)
    return merged


# ==================== 采集客户端 ====================

class ChaohuaClient:
    """访客态超话采集器。progress(msg) 用于回传进度，stop_event 用于停止。"""

    def __init__(self, topic_id: str, progress=None, stop_event: threading.Event | None = None):
        self.topic_id = extract_topic_id(topic_id)
        self.progress = progress
        self.stop_event = stop_event or threading.Event()
        self.session: requests.Session | None = None
        self._xsrf = ""

    # ---------- 会话 ----------
    def _say(self, message: str):
        if self.progress is not None:
            try:
                self.progress(message)
            except Exception:
                pass

    def build_session(self) -> requests.Session:
        """两步换取访客 cookie：genvisitor 拿 tid → incarnate 换 SUB/SUBP。"""
        session = requests.Session()
        session.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        fingerprint = {
            "os": "1", "browser": "Chrome142", "fonts": "undefined",
            "screenInfo": "1920*1080*24", "plugins": "",
        }
        try:
            resp = session.post(
                "https://passport.weibo.com/visitor/genvisitor",
                data={"cb": "gen_callback", "fp": json.dumps(fingerprint)},
                timeout=20,
            )
            match = re.search(r'"tid":"(.*?)"', resp.text)
            if not match:
                raise ChaohuaError("访客身份获取失败（未返回 tid）")
            session.get(
                "https://passport.weibo.com/visitor/visitor",
                params={
                    "a": "incarnate", "t": match.group(1), "w": "2", "c": "095",
                    "gc": "", "cb": "cross_domain", "from": "weibo",
                    "_rand": str(int(time.time() * 1000)),
                },
                timeout=20,
            )
            # 访问目标页：服务端下发 XSRF-TOKEN，并把超话上下文写入 cookie
            session.get(page_url(self.topic_id), timeout=20)
        except requests.RequestException as exc:
            raise ChaohuaError(f"访客身份获取失败：{exc}") from exc
        self.session = session
        self._xsrf = session.cookies.get("XSRF-TOKEN", "")
        return session

    def _headers(self) -> dict:
        return {
            "Referer": page_url(self.topic_id),
            "X-Requested-With": "XMLHttpRequest",
            "X-XSRF-TOKEN": self._xsrf or (self.session.cookies.get("XSRF-TOKEN", "") if self.session else ""),
            "Accept": "application/json, text/plain, */*",
        }

    def _sleep(self, seconds: float):
        end = time.time() + seconds
        while time.time() < end:
            if self.stop_event.is_set():
                return
            time.sleep(min(0.1, max(0.0, end - time.time())))

    def _request(self, params: dict, retries: int = 2) -> dict:
        """请求 page 接口；遇到风控 / 会话过期时重建访客会话重试。"""
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            if self.stop_event.is_set():
                raise ChaohuaStopped("已停止")
            try:
                if self.session is None:
                    self._say("换取访客 Cookie ...")
                    self.build_session()
                resp = self.session.get(API_PAGE, params=params,
                                        headers=self._headers(), timeout=25)
                if resp.status_code != 200:
                    raise ChaohuaError(f"HTTP {resp.status_code}")
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise ChaohuaError(f"接口返回非 JSON（HTTP {resp.status_code}）") from exc
                if not isinstance(data, dict):
                    raise ChaohuaError("接口返回结构异常")
                if "items" in data:
                    return data
                code = data.get("code")
                message = data.get("msg") or data.get("message") or str(data)[:120]
                raise ChaohuaError(f"接口返回异常 code={code} {message}")
            except ChaohuaStopped:
                raise
            except Exception as exc:  # noqa: BLE001 - 统一转成会话重建后重试
                last_error = exc
                self.session = None
                if self.stop_event.is_set():
                    raise ChaohuaStopped("已停止") from exc
                if attempt < retries:
                    self._say(f"{exc}，重建访客会话后重试（{attempt + 1}/{retries}）")
                    self._sleep(2.0)
        raise ChaohuaError(f"请求失败：{last_error}") from last_error

    # ---------- 头部 / 频道 ----------
    def fetch_overview(self) -> dict:
        """返回超话基础信息 + 频道列表（flowId 从接口动态读取）。"""
        data = self._request({"flowId": self.topic_id})
        header = (data.get("header") or {}).get("data") or {}
        desc_more = header.get("desc_more") or []
        stats: dict[str, str] = {}
        for item in desc_more:
            key = (item.get("desc") or "").strip()
            if key:
                stats[key] = item.get("desc_num") or ""
        labels = [x.get("text") for x in (header.get("label_list") or []) if x.get("text")]

        channels = []
        for channel in ((data.get("channelInfo") or {}).get("channels") or []):
            flow_id = (channel.get("flowId") or "").strip()
            if not flow_id:
                continue
            name = (channel.get("title") or channel.get("name") or "").strip()
            if not name:
                name = flow_id.rsplit("_-_", 1)[-1] or "频道"
            channels.append({
                "name": name,
                "flow_id": flow_id,
                "max_pages": DEFAULT_MAX_PAGES.get(name, 10),
            })
        if not channels:
            channels = [
                {"name": name, "flow_id": self.topic_id + suffix, "max_pages": pages}
                for name, suffix, pages in FALLBACK_CHANNELS
            ]

        fans_label, fans_str = "", ""
        for key, value in stats.items():
            if key != "帖子":
                fans_label, fans_str = key, value
                break

        return {
            "nick": header.get("nick") or "",
            "page_title": header.get("page_title") or "",
            "desc": _clean_text(header.get("desc")),
            "labels": labels,
            "post_count_str": stats.get("帖子", ""),
            "fans_label": fans_label,
            "fans_str": fans_str,
            "share_desc": (header.get("share_content") or {}).get("description") or "",
            "topic_id": header.get("oid") or f"1022:{self.topic_id}",
            "portrait": header.get("portrait_hd") or "",
            "channels": channels,
            "url": page_url(self.topic_id),
        }

    # ---------- 频道翻页 ----------
    def crawl_channel(self, name: str, flow_id: str, max_pages: int) -> tuple[list[dict], int]:
        """游标翻页：since_id 原样回传；每页保留 flowId；到底或达到页数上限停止。"""
        params = {"flowId": flow_id}
        posts: list[dict] = []
        pages = 0
        while pages < max_pages and not self.stop_event.is_set():
            try:
                data = self._request(params)
            except ChaohuaStopped:
                break
            feeds = [item.get("data") for item in data.get("items") or []
                     if item.get("category") == "feed" and item.get("data")]
            if not feeds:
                break
            posts.extend(normalize_post(item) for item in feeds)
            pages += 1
            self._say(f"「{name}」第 {pages} 页 +{len(feeds)} 条，累计 {len(posts)} 条")

            next_params = ((data.get("moreInfo") or {}).get("params")) or {}
            if not next_params.get("since_id"):
                break
            params = {"flowId": flow_id, **next_params}
            self._sleep(PAGE_INTERVAL)
        return posts, pages

    def crawl(self, channels: list[dict]) -> dict[str, list[dict]]:
        """依次采集选中频道，返回 {频道名: 去重后帖子列表}。"""
        result: dict[str, list[dict]] = {}
        total = len(channels)
        for index, channel in enumerate(channels, 1):
            if self.stop_event.is_set():
                self._say("已停止采集")
                break
            name = channel.get("name") or "频道"
            flow_id = channel.get("flow_id") or ""
            max_pages = max(1, _safe_int(channel.get("max_pages")) or 10)
            self._say(f"[{index}/{total}] 开始采集「{name}」（最多 {max_pages} 页）")
            posts, pages = self.crawl_channel(name, flow_id, max_pages)
            unique = dedupe_posts(posts)
            result[name] = unique
            self._say(f"[{index}/{total}]「{name}」完成：{pages} 页 / {len(posts)} 条（去重后 {len(unique)}）")
        return result


def fetch_overview(topic_id: str, progress=None, stop_event=None) -> dict:
    """便捷入口：建立访客会话并返回超话头部信息。"""
    return ChaohuaClient(topic_id, progress=progress, stop_event=stop_event).fetch_overview()


# ==================== 统计 ====================

def build_summary(data: dict[str, list[dict]]) -> dict:
    merged = merge_posts(data)
    channel_stats = []
    for channel, posts in data.items():
        likes = [post["likes"] for post in posts]
        dates = [post["dt"] for post in posts if post.get("dt")]
        channel_stats.append({
            "name": channel,
            "count": len(posts),
            "avg_likes": sum(likes) / len(likes) if likes else 0.0,
            "median_likes": statistics.median(likes) if likes else 0,
            "max_likes": max(likes) if likes else 0,
            "pic_rate": 100 * sum(1 for post in posts if post["pics"] > 0) / len(posts) if posts else 0.0,
            "authors": len({post["author"] for post in posts if post["author"]}),
            "start": min(dates).strftime("%Y-%m-%d") if dates else "",
            "end": max(dates).strftime("%Y-%m-%d") if dates else "",
        })

    author_posts = Counter(post["author"] or "（未知）" for post in merged)
    author_likes: Counter = Counter()
    author_max_likes: dict[str, int] = defaultdict(int)
    sources = Counter()
    regions = Counter()
    months: dict[str, dict] = defaultdict(lambda: {"count": 0, "likes": 0, "max": 0})
    hours = [0] * 24
    weekdays = [0] * 7

    for post in merged:
        author = post["author"] or "（未知）"
        author_likes[author] += post["likes"]
        author_max_likes[author] = max(author_max_likes[author], post["likes"])
        sources[post["source"] or "（未知）"] += 1
        regions[post["region"] or "（未知）"] += 1
        dt = post.get("dt")
        if dt is not None:
            month = dt.strftime("%Y-%m")
            months[month]["count"] += 1
            months[month]["likes"] += post["likes"]
            months[month]["max"] = max(months[month]["max"], post["likes"])
            hours[dt.hour] += 1
            weekdays[dt.weekday()] += 1

    return {
        "unique": len(merged),
        "raw_total": sum(len(posts) for posts in data.values()),
        "channels": channel_stats,
        "authors_top": author_posts.most_common(10),
        "likes_top": [(name, likes, author_max_likes[name])
                      for name, likes in author_likes.most_common(10)],
        "months": [(month, months[month]) for month in sorted(months)],
        "sources_top": sources.most_common(10),
        "regions_top": regions.most_common(10),
        "hours": hours,
        "weekdays": weekdays,
    }


# ==================== Excel 导出 ====================

def export_xlsx(info: dict, data: dict[str, list[dict]], path: str | Path | None = None) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    if not data:
        raise ValueError("没有可导出的帖子数据")

    if path is None:
        from toolbox.config_manager import get_export_subdir
        base = get_export_subdir("weibo_chaohua")
        nick = re.sub(r'[\\/:*?"<>|]', "_", info.get("nick") or info.get("topic_id") or "微博超话")
        if not nick.endswith("超话"):
            nick += "超话"
        path = base / f"{nick}_数据采集_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    summary = build_summary(data)
    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="E6A23C")
    head_align = Alignment(horizontal="center", vertical="center")
    used_titles: set[str] = set()

    def sheet_title(base: str) -> str:
        clean = re.sub(r"[\\/*?:\[\]]", "_", str(base))[:31] or "Sheet"
        title, index = clean, 2
        while title in used_titles:
            suffix = f"({index})"
            title = clean[:31 - len(suffix)] + suffix
            index += 1
        used_titles.add(title)
        return title

    def write_sheet(name: str, headers: list[str], rows: list[list],
                    widths: list[int] | None = None):
        ws = wb.create_sheet(sheet_title(name))
        ws.append(headers)
        for column in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=column)
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = head_align
        for row in rows:
            ws.append(row)
        if widths:
            for index, width in enumerate(widths, 1):
                ws.column_dimensions[get_column_letter(index)].width = width
        ws.freeze_panes = "A2"
        if ws.max_row > 1:
            ws.auto_filter.ref = ws.dimensions
        return ws

    # 汇总
    ws = wb.active
    ws.title = sheet_title("汇总")
    used_titles.add(ws.title)
    summary_rows = [
        ("超话名称", info.get("nick") or ""),
        ("超话 ID", info.get("topic_id") or ""),
        ("页面地址", info.get("url") or ""),
        ("简介", info.get("desc") or ""),
        ("分类 / 签到", " / ".join(info.get("labels") or [])),
        ("帖子数（页面文案）", info.get("post_count_str") or ""),
        ("粉丝数（页面文案）",
         f'{info.get("fans_label") or "粉丝"} {info.get("fans_str") or ""}'.strip()),
        ("分享文案", info.get("share_desc") or ""),
        ("采集时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("采集频道", "、".join(data.keys())),
        ("帖子条数（含频道重复）", summary["raw_total"]),
        ("唯一帖子数（跨频道去重）", summary["unique"]),
        ("说明", "阅读量 / 影响力 / 榜单分数网页端不存在；"
                 "「最新」频道深翻会混入零星老帖。"),
    ]
    ws.append(["指标", "值"])
    for cell in ws[1]:
        cell.font = head_font
        cell.fill = head_fill
        cell.alignment = head_align
    for key, value in summary_rows:
        ws.append([key, value])
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 90
    ws.freeze_panes = "A2"
    for row in ws.iter_rows(min_row=2, max_col=2):
        row[1].alignment = Alignment(vertical="top", wrap_text=True)

    # 各频道明细
    detail_widths = [19, 13, 20, 15, 12, 9, 19, 70, 22, 16, 7, 7, 7, 8, 8, 46]
    for channel, posts in data.items():
        rows = [[post.get(key, "") for key, _label in POST_COLUMNS] for post in posts]
        write_sheet(channel, [label for _key, label in POST_COLUMNS],
                    rows, detail_widths)

    # 月度趋势
    write_sheet(
        "月度趋势", ["月份", "帖数", "点赞合计", "均赞", "最高赞"],
        [[month, item["count"], item["likes"],
          round(item["likes"] / item["count"], 1) if item["count"] else 0,
          item["max"]] for month, item in summary["months"]],
        [12, 10, 12, 10, 10],
    )

    # 作者榜（全局去重后统计）
    merged = merge_posts(data)
    author_likes: Counter = Counter()
    for post in merged:
        author_likes[post["author"] or "（未知）"] += post["likes"]
    write_sheet("作者榜-发帖", ["排名", "作者", "帖数", "累计获赞"],
                [[rank, author, count, author_likes[author]]
                 for rank, (author, count) in enumerate(summary["authors_top"], 1)],
                [8, 30, 10, 12])
    write_sheet("作者榜-获赞", ["排名", "作者", "累计获赞", "最高单帖赞"],
                [[rank, author, likes, max_likes]
                 for rank, (author, likes, max_likes) in enumerate(summary["likes_top"], 1)],
                [8, 30, 12, 12])

    # 设备 / 地区 / 时段
    total = max(1, summary["unique"])
    write_sheet("设备分布", ["设备", "帖数", "占比"],
                [[name, count, f"{100 * count / total:.1f}%"]
                 for name, count in Counter(
                     post["source"] or "（未知）" for post in merged).most_common()],
                [30, 10, 10])
    write_sheet("地区分布", ["地区", "帖数", "占比"],
                [[name, count, f"{100 * count / total:.1f}%"]
                 for name, count in Counter(
                     post["region"] or "（未知）" for post in merged).most_common()],
                [20, 10, 10])
    write_sheet("发帖时段", ["时段", "帖数"],
                [[f"{hour:02d}时", count]
                 for hour, count in enumerate(summary["hours"])],
                [10, 10])
    write_sheet("星期分布", ["星期", "帖数"],
                [[WEEKDAY_NAMES[index], count]
                 for index, count in enumerate(summary["weekdays"])],
                [10, 10])

    # 全局 TOP50
    top_posts = sorted(merged, key=lambda post: post["likes"], reverse=True)[:50]
    write_sheet(
        "全局TOP50",
        ["排名", "频道", "日期", "作者", "赞", "评论", "转发", "图片",
         "地区", "设备", "正文", "链接"],
        [[rank, post.get("channel", ""),
          post["dt"].strftime("%Y-%m-%d") if post.get("dt") else "",
          post["author"], post["likes"], post["comments"], post["reposts"],
          post["pics"], post["region"], post["source"],
          post["text"][:120], post["url"]]
         for rank, post in enumerate(top_posts, 1)],
        [6, 10, 12, 20, 8, 8, 8, 8, 14, 24, 70, 44],
    )

    wb.save(str(path))
    return path
