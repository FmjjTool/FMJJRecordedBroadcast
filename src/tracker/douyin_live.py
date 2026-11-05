# -*- coding: utf-8 -*-
"""抖音直播历史（直播动态）采集与缓存。

数据链路（2026-11-04 实测，见 _smoke/_smoke_dy_live_history.py）：
- 接口：GET webcast.amemv.com/webcast/moment/audience_show/
  ?aid=6383&app_name=douyin_web&max_time={游标}&anchor_id={数字uid}
  纯游客可用（无需 Cookie/a_bogus），每页固定 5 条，has_more/max_time 链式翻页。
- uid 解析：抖音号直解直播间页 HTML anchor 块；sec_uid 走 iesdouyin 分享页
  SSR（直播动态卡 goto_url 带 anchor_id），该页被 JSVM 风控壳保护时回退到
  profile 采集器三级链路。解析出的 uid 记回账号配置 douyin_uid，下次免解析；
  profile 采集返回的 record.uid 优先级最高。
- 回放：web 端拿不到官方回放（App 专属），本模块只存直播场次记录。
"""
import re
import time
from datetime import datetime

import requests

from .accounts import _xlsx_path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")
API = "https://webcast.amemv.com/webcast/moment/audience_show/"

SEC_UID_RE = re.compile(r"MS4wLjABAAAA[\w-]+")

# 直播历史 sheet 列（room_id 必须按文本写入，纯数字会被 Excel 转浮点丢精度）
_LIVE_HEADERS = ["开播时间", "结束时间", "时长(分钟)", "标题", "房间号", "隐藏"]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Referer": "https://live.douyin.com/"})
    return s


def resolve_by_sec_uid(sec_uid: str) -> str:
    """sec_uid → 数字 uid。首选 iesdouyin 分享页 SSR（快）；
    该页被 JSVM 风控壳保护时（2026-11 起常见），回退到已有 profile 采集器的
    三级链路（HTTP a_bogus → Playwright → Cookie）拿 uid。"""
    r = _session().get(f"https://www.iesdouyin.com/share/user/{sec_uid}", timeout=20)
    m = re.search(r"anchor_id%3D(\d+)", r.text) or re.search(r'"anchor_id":"?(\d+)', r.text)
    if m:
        return m.group(1)
    from .collectors.douyin import _sync_collect
    rec = _sync_collect(sec_uid)
    if rec.get("uid"):
        return str(rec["uid"])
    raise RuntimeError(f"分享页与 profile 接口均未解析到 uid：{(rec.get('error') or '')[:60]}")


def resolve_by_douyin_id(douyin_id: str) -> str:
    """抖音号 → 数字 uid（直播间页 HTML anchor 块直解，游客稳定；room/enter 兜底）。"""
    s = _session()
    html = s.get(f"https://live.douyin.com/{douyin_id}", timeout=15).text
    norm = html.replace('\\"', '"')  # SSR 有转义/非转义两种变体，先归一
    m = re.search(rf'"web_rid":"{re.escape(douyin_id)}"[\s\S]{{0,800}}?'
                  r'"anchor":\{"id_str":"(\d+)","sec_uid":"([^"]+)"'
                  r'\s*,"nickname":"([^"]*)"', norm)
    if m:
        return m.group(1)
    r = s.get("https://live.douyin.com/webcast/room/web/enter/", params={
        "aid": "6383", "app_name": "douyin_web", "live_id": "1",
        "device_platform": "web", "language": "zh-CN", "web_rid": douyin_id,
    }, timeout=20)
    if not r.text:
        raise RuntimeError("直播间页与 room/enter 均未取到用户（可能被风控，稍后重试）")
    d = r.json()
    user = (d.get("data") or {}).get("user") or {}
    if not user.get("id_str"):
        raise RuntimeError(f"room/enter 未取到用户：{d.get('status_code')}")
    return user["id_str"]


def resolve_uid_from_url(url: str) -> str:
    """账号链接 → 数字 uid：sec_uid 走分享页 SSR，抖音号直解直播间页，纯数字直接用。"""
    url = (url or "").strip()
    if not url:
        raise RuntimeError("链接为空，无法解析抖音 uid")
    if "v.douyin.com" in url:  # 短链先 302 展开再识别
        try:
            url = _session().get(url, timeout=15).url
        except Exception:
            pass
    m = SEC_UID_RE.search(url)
    if m:
        return resolve_by_sec_uid(m.group(0))
    m = re.search(r"douyin\.com/([A-Za-z0-9_-]+)", url)
    if m and not m.group(1).isdigit() and m.group(1) not in ("user", "share"):
        return resolve_by_douyin_id(m.group(1))
    m = re.search(r"(\d{5,})", url)
    if m:
        return m.group(1)
    raise RuntimeError("链接无法解析出抖音 uid")


def fetch_live_history(uid: str, max_pages: int = 20) -> list:
    """audience_show 链式翻页（每页 5 条，最多 100 场）。返回 [{start,end,dur,title,room_id,hide}]，最新在前。"""
    s = _session()
    rows, cursor, page = [], 0, 0
    while page < max_pages:
        r = s.get(API, params={"aid": "6383", "app_name": "douyin_web",
                               "max_time": str(cursor), "anchor_id": uid}, timeout=20)
        d = r.json()
        if d.get("status_code") != 0:
            raise RuntimeError(f"audience_show status_code={d.get('status_code')} "
                               f"{((d.get('data') or {}).get('prompts') or '')[:60]}")
        data = d.get("data") or {}
        for h in data.get("history_data") or []:
            st, et = int(h.get("start_time") or 0), int(h.get("end_time") or 0)
            rows.append({
                "start": datetime.fromtimestamp(st).strftime("%Y-%m-%d %H:%M") if st else "",
                "end": datetime.fromtimestamp(et).strftime("%Y-%m-%d %H:%M") if et else "",
                "dur": (et - st) // 60 if st and et else "",
                "title": h.get("title", ""),
                "room_id": str(h.get("room_id_str") or h.get("room_id") or ""),
                "hide": "是" if h.get("is_hide") else "否",
            })
        page += 1
        if not data.get("has_more"):
            break
        cursor = int(data.get("max_time") or 0)
        time.sleep(0.3)
    return rows


def save_live_history(name: str, rows: list) -> int:
    """合并直播场次到账号 xlsx 的「直播历史」sheet（按房间号去重），返回新增条数。"""
    import openpyxl
    path = _xlsx_path(name)
    existing = set()
    if path.exists():
        wb = openpyxl.load_workbook(path)
        if "直播历史" in wb.sheetnames:
            for row in wb["直播历史"].iter_rows(min_row=2, values_only=True):
                if row and (row[4] or row[0]):
                    rid = str(row[4] or "")
                    existing.add(rid or f"{row[0]}|{row[3]}")
    else:
        wb = openpyxl.Workbook()
        ws0 = wb.active
        ws0.title = "指标数据"
        from .accounts import _RECORD_FIELDS
        ws0.append(["time"] + _RECORD_FIELDS)  # 与 save_history 同结构，保证表头先落盘
    if "直播历史" not in wb.sheetnames:
        ws = wb.create_sheet("直播历史")
        ws.append(_LIVE_HEADERS)
    else:
        ws = wb["直播历史"]
    new = 0
    for r in rows:  # 接口返回最新在前，直接追加后整表仍是新→旧
        rid = str(r.get("room_id") or "")
        # 折叠组等场次可能无 room_id，退化用「开播时间+标题」作合并键
        key = rid or f"{r.get('start', '')}|{r.get('title', '')}"
        if key in existing:
            continue
        existing.add(key)
        ws.append([r.get("start", ""), r.get("end", ""), r.get("dur", ""),
                   r.get("title", ""), rid, r.get("hide", "否")])
        new += 1
    if new:
        wb.save(path)
    return new


def load_live_history(name: str) -> list:
    """读取账号「直播历史」sheet，返回 [{start,end,dur,title,room_id,hide}]（最新在前）。无缓存返回 []。"""
    import openpyxl
    path = _xlsx_path(name)
    if not path.exists():
        return []
    try:
        wb = openpyxl.load_workbook(path)
        if "直播历史" not in wb.sheetnames:
            return []
        rows = list(wb["直播历史"].iter_rows(min_row=2, values_only=True))
    except Exception:
        return []
    out = []
    for row in rows:
        if not row or (not row[4] and not row[0]):
            continue
        out.append({"start": row[0] or "", "end": row[1] or "",
                    "dur": row[2] if row[2] not in (None, "") else "",
                    "title": row[3] or "", "room_id": str(row[4]),
                    "hide": row[5] or "否"})
    return out


def collect_and_save(acc: dict, uid: str = None) -> dict:
    """采集直播历史并合并进账号缓存（追踪页采集入口）。返回 {total, new, uid, error}。

    uid 优先级：profile 采集返回的 record.uid → 账号配置缓存的 douyin_uid → 链接解析。
    """
    try:
        uid = (uid or "").strip() or (acc.get("douyin_uid") or "").strip()
        if not uid:
            uid = resolve_uid_from_url(acc.get("url", ""))
        rows = fetch_live_history(uid)
        new = save_live_history(acc.get("name", "未命名"), rows)
        _persist_uid(acc, uid)
        return {"total": len(rows), "new": new, "uid": uid, "error": ""}
    except Exception as e:
        return {"total": None, "new": 0, "uid": None, "error": f"{type(e).__name__}: {str(e)[:80]}"}


def _persist_uid(acc: dict, uid: str):
    """把解析出的数字 uid 记回账号配置（douyin_uid），下次采集免解析。"""
    if acc.get("douyin_uid") == uid or not (acc.get("url") or "").strip():
        return
    try:
        from .accounts import load_accounts, save_accounts
        accounts = load_accounts()
        for a in accounts:
            if a.get("url") == acc.get("url"):
                a["douyin_uid"] = uid
                save_accounts(accounts)
                break
    except Exception:
        pass
