"""三平台公开粉丝群查询：抖音 / 微博 / B站（均需登录 Cookie）。

抖音（2026-11 实测）：
  GET https://www.douyin.com/aweme/v1/web/im_group_api/list/other/
      ?device_platform=webapp&aid=6383&channel=channel_pc_web
      &sec_owner_uid=<sec_uid>&optional_group_types=["13","18"]
      &ext={"im_group_list_other_sort":"2"}&...&a_bogus=<签名>
  - 游客态返回 blocked，必须带登录 Cookie；a_bogus 由 toolbox/abogus_cli.js 生成
  - 返回 group_id / group_name / group_member_count / entry_limit / group_tags

微博（2026-11 实测）：
  GET https://weibo.com/ajax/profile/sidedetail?uid=<uid>
  - 需登录 Cookie；data.fansgroup.affiliated_groups 里是全部粉丝群
  - 返回 id / no（群号） / name / member_count / max_member / group_url

B站（2026-11 实测）：
  GET https://api.vc.bilibili.com/link_group/v1/member/my_groups?up_mid=<mid>&...
  - 需登录 Cookie；只返回「当前账号已加入」的应援团
  - 网页端没有 UP 全部群的公开列表；已知 group_id 时可查详情/成员

群头像：三平台列表接口都直接返回头像 URL（group_icon / avatar / group_cover），
无需额外请求；查询后自动下载到 <群目录>/头像/，文件名 序号_群名_群ID.ext。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import requests

PLATFORM_LABELS = {"douyin": "抖音", "weibo": "微博", "bilibili": "B站"}

UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

DOUYIN_API = "https://www.douyin.com/aweme/v1/web/im_group_api/list/other/"
WEIBO_API = "https://weibo.com/ajax/profile/sidedetail"
BILI_API = "https://api.vc.bilibili.com/link_group/v1/member/my_groups"

_DY_URL_RE = re.compile(r"douyin\.com/user/([A-Za-z0-9_-]{20,})", re.I)
_DY_SEC_RE = re.compile(r"^\s*(MS4wLjABAAAA[\w\-]{10,})\s*$")
_WB_URL_RE = re.compile(r"weibo\.(?:com|cn)/(?:u/)?(\d{5,})", re.I)
_WB_UID_RE = re.compile(r"^\s*(\d{5,})\s*$")
_BILI_URL_RE = re.compile(r"space\.bilibili\.com/(\d+)", re.I)
_BILI_MID_RE = re.compile(r"^\s*(\d{2,})\s*$")

_WEIBO_JOIN = {1: "申请加入", 2: "邀请加入", 3: "付费加入"}


@dataclass
class FanGroup:
    platform: str = ""
    group_id: str = ""
    name: str = ""
    member_count: int | None = None
    member_limit: int | None = None
    group_no: str = ""
    owner_uid: str = ""
    avatar: str = ""
    avatar_path: str = ""
    entry_limit: str = ""
    tags: str = ""
    notice: str = ""
    url: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def member_text(self) -> str:
        if self.member_count is None:
            return "-"
        if self.member_limit:
            return f"{self.member_count}/{self.member_limit}"
        return str(self.member_count)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["platform_label"] = PLATFORM_LABELS.get(self.platform, self.platform)
        return d


# ==================== 输入解析 ====================

def extract_douyin_sec_uid(text: str) -> str:
    raw = (text or "").strip()
    m = _DY_URL_RE.search(raw)
    if m:
        return m.group(1)
    m = _DY_SEC_RE.match(raw)
    if m:
        return m.group(1)
    raise ValueError("没识别到抖音 sec_uid（请粘贴用户主页链接）")


def extract_weibo_uid(text: str) -> str:
    raw = (text or "").strip()
    m = _WB_URL_RE.search(raw)
    if m:
        return m.group(1)
    m = _WB_UID_RE.match(raw)
    if m:
        return m.group(1)
    raise ValueError("没识别到微博 UID（请粘贴 weibo.com/u/<UID> 或直接输入 UID）")


def extract_bili_mid(text: str) -> str:
    raw = (text or "").strip()
    m = _BILI_URL_RE.search(raw)
    if m:
        return m.group(1)
    m = _BILI_MID_RE.match(raw)
    if m:
        return m.group(1)
    raise ValueError("没识别到 B站 UID（请粘贴 space.bilibili.com/<UID> 或直接输入 UID）")


def extract_owner_id(platform: str, text: str) -> str:
    if platform == "douyin":
        return extract_douyin_sec_uid(text)
    if platform == "weibo":
        return extract_weibo_uid(text)
    if platform == "bilibili":
        return extract_bili_mid(text)
    raise ValueError(f"不支持的平台：{platform}")


# ==================== Cookie ====================

def _cookie(platform: str) -> str:
    try:
        from auth import get_cookie, is_cookie_enabled, load_cookies
        if not is_cookie_enabled():
            return ""
        return get_cookie(platform) or load_cookies().get(platform, "")
    except Exception:
        return ""


def _require_cookie(platform: str) -> str:
    ck = _cookie(platform)
    if not ck:
        raise ValueError(f"需要{PLATFORM_LABELS[platform]}登录态："
                         f"请到「Cookie 管理」登录{PLATFORM_LABELS[platform]}后重试")
    return ck


# ==================== 抖音 ====================

def _douyin_params(sec_uid: str) -> dict:
    return {
        "device_platform": "webapp", "aid": "6383", "channel": "channel_pc_web",
        "sec_owner_uid": sec_uid,
        "optional_group_types": '["13","18"]',
        "ext": '{"im_group_list_other_sort":"2"}',
        "update_version_code": "170400", "pc_client_type": "1",
        "pc_libra_divert": "Windows", "support_h265": "1", "support_dash": "1",
        "cpu_core_num": "16", "version_code": "170400", "version_name": "17.4.0",
        "cookie_enabled": "true", "screen_width": "1440", "screen_height": "1000",
        "browser_language": "zh-CN", "browser_platform": "Win32",
        "browser_name": "Chrome", "browser_version": "131.0.0.0",
        "browser_online": "true", "engine_name": "Blink",
        "engine_version": "131.0.0.0", "os_name": "Windows", "os_version": "10",
        "device_memory": "32", "platform": "PC", "downlink": "10",
        "effective_type": "4g", "round_trip_time": "50",
    }


def _url_of(v) -> str:
    """头像字段兼容字符串 / {url_list} / 列表三种形态。"""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        for k in ("url_list", "url", "uri"):
            u = _url_of(v.get(k))
            if u:
                return u
    elif isinstance(v, (list, tuple)):
        for x in v:
            u = _url_of(x)
            if u:
                return u
    return ""


def fetch_douyin(sec_uid: str, timeout: float = 20.0) -> list[FanGroup]:
    from urllib.parse import urlencode
    from multi_downloader import _douyin_abogus

    ck = _require_cookie("douyin")
    query = urlencode(_douyin_params(sec_uid))
    abogus = _douyin_abogus(query, UA_PC)
    if not abogus:
        raise ValueError("抖音 a_bogus 签名失败（node 异常）")

    r = requests.get(f"{DOUYIN_API}?{query}&a_bogus={abogus}",
                     headers={"User-Agent": UA_PC, "Cookie": ck,
                              "Referer": f"https://www.douyin.com/user/{sec_uid}"},
                     timeout=timeout)
    body = (r.text or "").strip()
    if body == "blocked":
        raise ValueError("抖音返回 blocked：登录态失效或游客被风控，"
                         "请到「Cookie 管理」重新登录抖音")
    if not body:
        raise ValueError("抖音接口返回空响应（可能被风控，请稍后重试）")
    d = r.json()
    if d.get("status_code") != 0:
        raise ValueError(f"抖音接口错误 code={d.get('status_code')} "
                         f"{d.get('status_msg') or ''}".strip())

    out: list[FanGroup] = []
    for g in d.get("group_list") or []:
        tags = g.get("group_tags") or []
        out.append(FanGroup(
            platform="douyin",
            group_id=str(g.get("group_id") or ""),
            name=str(g.get("group_name") or ""),
            member_count=_int_or_none(g.get("group_member_count")),
            group_no="",
            owner_uid=str(g.get("group_owner_uid") or ""),
            avatar=_url_of(g.get("group_icon")),
            entry_limit=str(g.get("entry_limit") or ""),
            tags="、".join(str(t) for t in tags if t),
            notice=str(g.get("group_desc") or ""),
            url="",
            raw=g,
        ))
    return out


def _int_or_none(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ==================== 微博 ====================

def fetch_weibo(uid: str, timeout: float = 25.0) -> list[FanGroup]:
    ck = _require_cookie("weibo")
    r = requests.get(WEIBO_API, params={"uid": uid},
                     headers={"User-Agent": UA_PC, "Cookie": ck,
                              "Referer": f"https://weibo.com/u/{uid}"},
                     timeout=timeout)
    if r.status_code != 200:
        raise ValueError(f"微博接口 HTTP {r.status_code}（登录态可能已失效）")
    try:
        data = (r.json() or {}).get("data") or {}
    except Exception:
        raise ValueError("微博接口返回的不是 JSON（登录态可能已失效）")
    fg = data.get("fansgroup") or {}
    groups = fg.get("affiliated_groups") or []
    if not groups:
        raise ValueError("没有查到粉丝群（该账号未创建粉丝群，或粉丝群未公开）")

    out: list[FanGroup] = []
    for g in groups:
        jt = g.get("affi_join_type")
        tags = [str(t.get("title") or "") for t in (g.get("tags") or []) if t.get("title")]
        out.append(FanGroup(
            platform="weibo",
            group_id=str(g.get("id") or ""),
            name=str(g.get("name") or g.get("system_name") or ""),
            member_count=_int_or_none(g.get("member_count")),
            member_limit=_int_or_none(g.get("max_member")),
            group_no=str(g.get("no") or ""),
            owner_uid=str(g.get("owner") or ""),
            avatar=_url_of(g.get("avatar")) or _url_of(g.get("round_avatar")),
            entry_limit=_WEIBO_JOIN.get(jt, "暂不可加入" if jt is None else f"类型 {jt}"),
            tags="、".join(tags),
            notice=str(g.get("summary") or ""),
            url=str(g.get("group_url") or ""),
            raw=g,
        ))
    return out


# ==================== B站 ====================

def fetch_bili(mid: str, timeout: float = 20.0) -> list[FanGroup]:
    ck = _require_cookie("bilibili")
    r = requests.get(BILI_API,
                     params={"up_mid": mid, "build": "0",
                             "mobi_app": "web", "platform": "web"},
                     headers={"User-Agent": UA_PC, "Cookie": ck,
                              "Referer": f"https://space.bilibili.com/{mid}",
                              "Origin": "https://space.bilibili.com"},
                     timeout=timeout)
    try:
        d = r.json()
    except Exception:
        raise ValueError("B站接口返回的不是 JSON（登录态可能已失效）")
    if d.get("code") == -101:
        raise ValueError("B站未登录或登录态失效：请到「Cookie 管理」重新登录 B站")
    if d.get("code") != 0:
        raise ValueError(f"B站接口错误 code={d.get('code')} {d.get('message') or ''}".strip())

    out: list[FanGroup] = []
    for g in (d.get("data") or {}).get("list") or []:
        out.append(FanGroup(
            platform="bilibili",
            group_id=str(g.get("group_id") or ""),
            name=str(g.get("group_name") or ""),
            member_count=None,          # my_groups 不返回人数
            group_no="",
            owner_uid=str(g.get("owner_uid") or ""),
            avatar=_url_of(g.get("group_cover")),
            entry_limit="",
            tags="",
            notice=str(g.get("group_notice") or ""),
            url=f"https://space.bilibili.com/{mid}/fansgroup",
            raw=g,
        ))
    if not out:
        raise ValueError("没有查到应援团：网页端只显示当前账号已加入的群，"
                         "可先在 B站 App/网页加入后再查")
    return out


FETCHERS = {"douyin": fetch_douyin, "weibo": fetch_weibo, "bilibili": fetch_bili}


def fetch_groups(platform: str, text: str, progress=None) -> tuple[str, list[FanGroup]]:
    """输入主页链接/ID → (owner_id, 群列表)。"""
    owner_id = extract_owner_id(platform, text)
    if progress:
        progress(f"{PLATFORM_LABELS[platform]} ID：{owner_id}，正在查询...")
    return owner_id, FETCHERS[platform](owner_id)


# ==================== 快照 / 变化 ====================

def _safe_name(name: str, max_len: int = 60) -> str:
    from toolbox.utils import sanitize_filename
    # sec_uid 里含连续下划线，不能折叠，否则目录名会和真实 ID 对不上
    return sanitize_filename(str(name or ""), max_len, "unknown", collapse_ws=False)


def group_dir(platform: str, owner_id: str, create: bool = True) -> Path:
    from toolbox.config_manager import get_export_subdir
    d = get_export_subdir("fan_groups") / PLATFORM_LABELS.get(platform, platform) / _safe_name(owner_id)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def load_latest(platform: str, owner_id: str) -> dict | None:
    """读取上一次快照（用于人数对比）；没有则返回 None。"""
    p = group_dir(platform, owner_id, create=False) / "最新.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def diff_members(groups: list[FanGroup], previous: dict | None) -> dict[str, int | None]:
    """当前群列表 vs 上次快照 → {group_id: 人数变化}（新群为 None）。"""
    if not previous:
        return {}
    prev = {str(g.get("group_id")): g.get("member_count")
            for g in previous.get("groups") or []}
    out: dict[str, int | None] = {}
    for g in groups:
        if g.group_id not in prev:
            out[g.group_id] = None
            continue
        old = prev.get(g.group_id)
        if isinstance(old, int) and isinstance(g.member_count, int):
            out[g.group_id] = g.member_count - old
        else:
            out[g.group_id] = 0
    return out


def _snapshot_payload(platform: str, owner_id: str, groups: list[FanGroup],
                      note: str = "") -> dict:
    return {
        "platform": platform,
        "platform_label": PLATFORM_LABELS.get(platform, platform),
        "owner_id": owner_id,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "note": note,
        "groups": [g.to_dict() for g in groups],
    }


def save_snapshot(platform: str, owner_id: str, groups: list[FanGroup],
                  note: str = "") -> Path:
    d = group_dir(platform, owner_id)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    text = json.dumps(_snapshot_payload(platform, owner_id, groups, note),
                      ensure_ascii=False, indent=2)
    path = d / f"快照_{ts}.json"
    path.write_text(text, encoding="utf-8")
    (d / "最新.json").write_text(text, encoding="utf-8")
    return path


def update_latest(platform: str, owner_id: str, groups: list[FanGroup],
                  note: str = "") -> None:
    """只刷新 最新.json（头像刷新等不新增带时间戳的快照）。"""
    d = group_dir(platform, owner_id)
    (d / "最新.json").write_text(
        json.dumps(_snapshot_payload(platform, owner_id, groups, note),
                   ensure_ascii=False, indent=2),
        encoding="utf-8")


# ==================== 群头像 ====================

_AVATAR_REFERER = {
    "douyin": "https://www.douyin.com/",
    "weibo": "https://weibo.com/",
    "bilibili": "https://space.bilibili.com/",
}
_IMG_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif)(?:[?~!]|$)", re.I)


def avatar_dir(platform: str, owner_id: str, create: bool = True) -> Path:
    d = group_dir(platform, owner_id, create=create) / "头像"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _avatar_ext(url: str, data: bytes) -> str:
    m = _IMG_EXT_RE.search(url or "")
    if m:
        return "." + m.group(1).lower().replace("jpeg", "jpg")
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"GIF8":
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def _fetch_avatar(url: str, platform: str, timeout: float = 15.0) -> tuple[bytes, str]:
    url = (url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    headers = {"User-Agent": UA_PC}
    referer = _AVATAR_REFERER.get(platform)
    if referer:
        headers["Referer"] = referer
    candidates = [url]
    if url.startswith("http://"):
        candidates.append("https://" + url[len("http://"):])
    last = ""
    for u in candidates:
        try:
            r = requests.get(u, headers=headers, timeout=timeout)
            if r.status_code == 200 and len(r.content) > 100:
                return r.content, u
            last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = str(e)
    raise RuntimeError(last or "下载失败")


def _find_avatars(d: Path, group_id: str) -> list[Path]:
    """按文件名后缀 _<group_id> 找已下载头像（重复查询时复用）。"""
    if not group_id or not d.exists():
        return []
    suffix = "_" + _safe_name(group_id, 40)
    return [p for p in d.iterdir() if p.is_file() and p.stem.endswith(suffix)]


def download_avatars(platform: str, owner_id: str, groups: list[FanGroup],
                     progress=None, force: bool = False) -> tuple[int, int]:
    """下载群头像到 <群目录>/头像/，返回 (成功数, 有头像的群数)。

    默认复用已下载文件（按群 ID 匹配）；force=True 重新下载并覆盖。
    """
    d = avatar_dir(platform, owner_id)
    ok = total = 0
    for idx, g in enumerate(groups, 1):
        if not g.avatar:
            g.avatar_path = ""
            continue
        total += 1
        olds = _find_avatars(d, g.group_id)
        if olds and not force:
            g.avatar_path = str(olds[0])
            ok += 1
            continue
        if progress:
            progress(f"下载群头像 {idx}/{len(groups)}：{g.name or g.group_id}")
        try:
            data, real_url = _fetch_avatar(g.avatar, platform)
            for old in olds:
                try:
                    old.unlink()
                except OSError:
                    pass
            name = _safe_name(g.name, 30) or "群"
            gid = _safe_name(g.group_id, 40) or f"{idx:02d}"
            path = d / f"{idx:02d}_{name}_{gid}{_avatar_ext(real_url, data)}"
            path.write_bytes(data)
            g.avatar_path = str(path)
            ok += 1
        except Exception as e:  # noqa: BLE001
            g.avatar_path = ""
            if progress:
                progress(f"群头像下载失败（{g.name or g.group_id}）：{str(e)[:50]}")
    return ok, total


# ==================== Excel ====================

def export_xlsx(platform: str, owner_id: str, groups: list[FanGroup],
                changes: dict[str, int | None] | None = None,
                path: Path | None = None) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    if path is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = group_dir(platform, owner_id) / f"粉丝群_{ts}.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    changes = changes or {}

    wb = Workbook()
    ws = wb.active
    ws.title = "粉丝群"
    headers = ["序号", "群名", "群ID", "群号", "人数", "人数变化", "人数上限",
               "群主UID", "进群条件", "标签", "公告/简介", "群链接", "头像文件", "采集时间"]
    ws.append(headers)
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="C2185B")
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = head_font
        cell.fill = head_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")

    widths = [6, 30, 22, 12, 12, 10, 10, 16, 16, 18, 46, 44, 34, 18]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for idx, g in enumerate(groups, 1):
        delta = changes.get(g.group_id)
        delta_text = "" if delta is None else (f"+{delta}" if delta > 0 else str(delta))
        avatar_file = f"头像/{Path(g.avatar_path).name}" if g.avatar_path else ""
        ws.append([idx, g.name, g.group_id, g.group_no, g.member_count,
                   delta_text, g.member_limit, g.owner_uid, g.entry_limit,
                   g.tags, g.notice, g.url, avatar_file, now])
    wb.save(str(path))
    return path
