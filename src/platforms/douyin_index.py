"""抖音指数（创作者中心 · 达人详情）API 层：搜索 + 作者/作品/粉丝分析全量抓取。

接口（2026-09-27 实测抓包，creator.douyin.com/api/v2/daren/*，POST JSON，需抖音登录 Cookie）：
- get_sug_great_user_list   搜索达人（名称/抖音号 → 指数系统 uid）
- get_author_info           达人信息（头像/简介/粉丝/获赞/作品/涨粉里程碑/主页链接）
- get_great_user_mile_Info  核心指标（昨日/近7天/近15天/近30天 新增点赞/净增粉丝/分享/评论/作品
                            + 各期环比 + 每日趋势序列）
- get_great_item_mile_Info 稿均指标（近7天/近30天 稿均点赞/涨粉/分享/评论 + 环比）
- get_great_user_top_video  达人视频（时间范围内作品：点赞/评论/分享/收藏/封面/链接/发布日期）
- item_index_exist         视频指数（单视频指数数值）
- get_great_user_fans_info 粉丝画像（省份/城市/城市级别/年龄/性别/手机品牌/手机价格/兴趣标签 + TGI）
- get_similar_users        相似达人

游客态一律 401 Unauthorized；接口无需 a_bogus / msToken / csrf-token，
仅需登录 Cookie + referer + appsource 头。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

_BASE = "https://creator.douyin.com"
_REFERER = ("https://creator.douyin.com/creator-micro/creator-count/"
            "arithmetic-index/daren/detail")
UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# 粉丝画像键 → 中文标签（*_Tgi 后缀由解析层统一追加 TGI）
FANS_KEYS = {
    "Province": "省份分布",
    "City": "城市分布",
    "CityLabel": "城市级别",
    "Age": "年龄分布",
    "Gender": "性别分布",
    "DeviceBrand": "手机品牌",
    "DevicePrice": "手机价格",
    "FirstTag": "兴趣标签",
    "SecondTag": "兴趣二级标签",
}

_GENDER_CN = {"male": "男性", "female": "女性"}


def _cookie() -> str:
    from auth import get_cookie
    return get_cookie("douyin") or ""


def _parse_cookie(s: str) -> dict:
    out = {}
    for part in s.split(";"):
        part = part.strip()
        if part and "=" in part:
            k, _, v = part.partition("=")
            out[k] = v
    return out


def make_client() -> httpx.Client:
    """带抖音登录态的 HTTP 客户端（无 Cookie 时抛清晰错误）。"""
    cookie = _cookie()
    if not cookie:
        raise RuntimeError("未配置抖音 Cookie：请到「Cookie 管理」→ 抖音 → 浏览器登录"
                           "（抖音指数接口需要登录态）")
    return httpx.Client(
        headers={
            "user-agent": UA_PC,
            "accept": "application/json, text/plain, */*",
            "appsource": "PC",
            "referer": _REFERER,
        },
        cookies=_parse_cookie(cookie),
        timeout=20,
    )


def _post(client: httpx.Client, path: str, payload: dict) -> dict:
    r = client.post(_BASE + path, json=payload,
                    headers={"content-type": "application/json"})
    if r.status_code == 401:
        raise RuntimeError("抖音 Cookie 已失效（401 未登录）：请到「Cookie 管理」重新登录抖音")
    r.raise_for_status()
    try:
        d = r.json()
    except Exception:
        raise RuntimeError(f"接口返回非 JSON（{path}，HTTP {r.status_code}）") from None
    if d.get("status") not in (0,):
        raise RuntimeError(f"接口返回错误：{d.get('msg') or d.get('status')}（{path}）")
    return d.get("data") or {}


# ==================== 单接口 ====================

def search_daren(keyword: str, total: int = 20,
                 client: httpx.Client | None = None) -> list[dict]:
    """按名称/抖音号搜索达人 → 候选列表（含指数 uid）。"""
    own = client is None
    c = client or make_client()
    try:
        data = _post(c, "/api/v2/daren/get_sug_great_user_list",
                     {"keyword": keyword, "total": str(total)})
        return data.get("userlist") or []
    finally:
        if own:
            c.close()


def get_author_info(uid: str, client: httpx.Client | None = None) -> dict:
    own = client is None
    c = client or make_client()
    try:
        return _post(c, "/api/v2/daren/get_author_info", {"user_id": uid})
    finally:
        if own:
            c.close()


def get_core_metrics(uid: str, client: httpx.Client | None = None) -> dict:
    """核心指标：昨日/近7天/近15天/近30天 新增点赞/净增粉丝/分享/评论/作品 + 环比 + 每日序列。"""
    own = client is None
    c = client or make_client()
    try:
        return _post(c, "/api/v2/daren/get_great_user_mile_Info", {"user_id": uid})
    finally:
        if own:
            c.close()


def get_item_metrics(uid: str, client: httpx.Client | None = None) -> dict:
    """稿均指标：近7天/近30天 稿均点赞/涨粉/分享/评论 + 环比。"""
    own = client is None
    c = client or make_client()
    try:
        return _post(c, "/api/v2/daren/get_great_item_mile_Info", {"user_id": uid})
    finally:
        if own:
            c.close()


def get_top_videos(uid: str, start_date: str, end_date: str,
                   client: httpx.Client | None = None) -> list[dict]:
    """达人视频列表（按点赞排名，含点赞/评论/分享/收藏计数）。"""
    own = client is None
    c = client or make_client()
    try:
        data = _post(c, "/api/v2/daren/get_great_user_top_video",
                     {"user_id": uid, "start_date": start_date, "end_date": end_date})
        return data.get("like_list") or []
    finally:
        if own:
            c.close()


def get_item_index(item_id: str, start_date: str, end_date: str,
                   client: httpx.Client | None = None) -> str:
    """单视频的视频指数数值（无指数数据返回空串）。"""
    own = client is None
    c = client or make_client()
    try:
        data = _post(c, "/api/v2/daren/item_index_exist",
                     {"itemId": str(item_id), "startDate": start_date, "endDate": end_date})
        return str(data.get("item_index") or "") if data.get("item_statue") == "succeed" else ""
    finally:
        if own:
            c.close()


def get_fans_profile(uid: str, client: httpx.Client | None = None) -> dict:
    """粉丝画像：各维度分布占比 + TGI（原接口各键为二次 JSON 字符串，这里解析成列表）。"""
    own = client is None
    c = client or make_client()
    try:
        data = _post(c, "/api/v2/daren/get_great_user_fans_info", {"user_id": uid})
    finally:
        if own:
            c.close()
    out = {}
    for key, label in FANS_KEYS.items():
        items = _parse_dist(data.get(key))
        for it in items:
            if key == "Gender":
                it["name"] = _GENDER_CN.get(it.get("name"), it.get("name"))
        if items:
            out[label] = items
        tgi = _parse_dist(data.get(key + "_Tgi"))
        for it in tgi:
            if key == "Gender":
                it["name"] = _GENDER_CN.get(it.get("name"), it.get("name"))
        if tgi:
            out[label + "TGI"] = tgi
    return out


def get_similar_users(uid: str, client: httpx.Client | None = None) -> list[dict]:
    own = client is None
    c = client or make_client()
    try:
        data = _post(c, "/api/v2/daren/get_similar_users", {"user_id": uid})
        return data.get("similar_user_list") or []
    finally:
        if own:
            c.close()


def _parse_dist(raw) -> list[dict]:
    """粉丝画像字段为 JSON 字符串（"[{\"name\":..,\"value\":..}]"），解析失败返回空。"""
    if not raw:
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    try:
        items = json.loads(raw)
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    except Exception:
        return []


# ==================== 全量抓取 ====================

def date_range(days: int = 30) -> tuple[str, str]:
    """视频统计时间范围：[今天-days+1, 今天]，格式 YYYYMMDD。"""
    end = datetime.now()
    start = end - timedelta(days=days - 1)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def fetch_daren_all(uid: str, days: int = 30, progress=None) -> dict:
    """抓取一个达人的全部抖音指数数据（作者分析 + 作品分析 + 粉丝分析）。

    progress(msg) 可选回调（后台线程内调用）。
    """
    say = progress or (lambda msg: None)
    start_date, end_date = date_range(days)
    client = make_client()
    try:
        say("获取达人信息...")
        author = get_author_info(uid, client)
        time.sleep(0.4)
        say("获取核心指标（昨日/近7天/近15天/近30天）...")
        core = get_core_metrics(uid, client)
        time.sleep(0.4)
        say("获取稿均指标...")
        item_metrics = get_item_metrics(uid, client)
        time.sleep(0.4)
        say("获取达人视频列表...")
        videos = get_top_videos(uid, start_date, end_date, client)
        for i, v in enumerate(videos):
            time.sleep(0.4)
            say(f"获取视频指数 {i + 1}/{len(videos)}...")
            v["video_index"] = get_item_index(v.get("item_id", ""), start_date, end_date, client)
        time.sleep(0.4)
        say("获取粉丝画像（地域/年龄/性别/手机/兴趣）...")
        fans = get_fans_profile(uid, client)
        time.sleep(0.4)
        say("获取相似达人...")
        similar = get_similar_users(uid, client)
    finally:
        client.close()

    return {
        "meta": {
            "platform": "douyin_index",
            "uid": uid,
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "days": days,
            "start_date": start_date,
            "end_date": end_date,
        },
        "author": author,
        "core_metrics": core,
        "item_metrics": item_metrics,
        "videos": videos,
        "fans_profile": fans,
        "similar_users": similar,
    }


# ==================== 本地保存 ====================

def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", str(name or "").strip()) or "未知达人"


def index_dir() -> Path:
    """数据保存根目录：download/抖音指数/。"""
    from toolbox.config_manager import get_export_subdir
    return get_export_subdir("douyin_index")


def save_snapshot(data: dict) -> Path:
    """全量快照落盘：download/抖音指数/<达人名>/抖音指数_时间戳.json（+ latest.json 副本）。"""
    name = _safe_name((data.get("author") or {}).get("user_name"))
    d = index_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = d / f"抖音指数_{ts}.json"
    text = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    (d / "latest.json").write_text(text, encoding="utf-8")
    return path


# ==================== 展示格式化 ====================

def fmt_count(v) -> str:
    """计数 → 中文紧凑格式：4518751 → 451.9万；34594 → 3.5万；387 → 387。"""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return str(v or 0)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 100000000:
        return f"{sign}{n / 100000000:.2f}亿"
    if n >= 10000:
        return f"{sign}{n / 10000:.1f}万"
    return f"{sign}{n}"


def fmt_pct(v) -> str:
    """环比小数（0.9915）→ 百分比文本（+99.15%）。"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "-"
    return f"{'+' if x > 0 else ''}{x * 100:.2f}%"
