"""账号名单总览（只读聚合）。

把三个账号中心的主数据读成一份统一视图，供「账号名单」页展示：
- 信息获取：`config/accounts.txt`（accounts_manager，唯一写入方）
- 网媒追踪：`tracker/accounts.json`（tracker.accounts，唯一写入方）
- 音乐追踪：`music_accounts.json`（music_account.accounts，唯一写入方）

本模块只读，不写回任何账号文件，也不读写凭据。
唯一键：platform + 规范化 URL；id 由二者哈希得到，供后续注册表迁移复用。
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

SOURCE_NAMES = {"info": "信息获取", "tracker": "网媒追踪", "music": "音乐追踪"}

_PLATFORM_RULES = (
    ("space.bilibili.com", "bilibili"), ("bilibili.com", "bilibili"),
    ("douyin.com", "douyin"), ("weibo.com", "weibo"), ("weibo.cn", "weibo"),
    ("kuaishou.com", "kuaishou"), ("xiaohongshu.com", "xiaohongshu"),
    ("music.163.com", "netease"), ("y.qq.com", "qq"), ("kugou.com", "kugou"),
    ("kuwo.cn", "kuwo"), ("5sing.kugou.com", "fivesing"), ("5sing.com", "fivesing"),
)


def infer_platform(url: str) -> str:
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except Exception:
        return ""
    for suffix, name in _PLATFORM_RULES:
        if host == suffix or host.endswith("." + suffix):
            return name
    return ""


def normalize_url(url: str) -> str:
    try:
        parts = urlsplit(url or "")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                           parts.path.rstrip("/"), "", ""))
    except Exception:
        return (url or "").rstrip("/")


def account_id(platform: str, norm_url: str) -> str:
    return "acc_" + hashlib.sha1(f"{platform}|{norm_url}".encode("utf-8")).hexdigest()[:12]


def canonical_url(platform: str, url: str) -> str:
    """按平台收敛到账号身份链接。

    B 站 `/video` 与裸空间链接是同一个账号（视频采集方式），
    但 `/dynamic`（动态）是独立账号身份，保留后缀不合并；
    网易云的 artist id 在 fragment 里，不能直接丢弃。
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except Exception:
        return normalize_url(raw)
    host = (parts.hostname or "").lower()
    path = parts.path.rstrip("/")

    if platform == "bilibili":
        match = re.search(r"/space\.bilibili\.com/(\d+)", f"/{host}{path}")
        if match:
            suffix = "/dynamic" if path.rstrip("/").endswith("/dynamic") else ""
            return f"https://space.bilibili.com/{match.group(1)}{suffix}"
    if platform == "weibo":
        match = re.search(r"/u/(\d+)", path) or re.match(r"/(\d+)", path)
        if match:
            return f"https://weibo.com/u/{match.group(1)}"
    if platform == "douyin":
        match = re.search(r"/user/([^/]+)", path)
        if match:
            return f"https://www.douyin.com/user/{match.group(1)}"
    if platform == "kuaishou":
        match = re.search(r"/profile/([^/]+)", path)
        if match:
            return f"https://www.kuaishou.com/profile/{match.group(1)}"
    if platform == "xiaohongshu":
        match = re.search(r"/user/profile/([^/]+)", path)
        if match:
            return f"https://www.xiaohongshu.com/user/profile/{match.group(1)}"
    if platform == "netease":
        fragment = parts.fragment or ""
        target = fragment if "id=" in fragment else path
        match = re.search(r"id=(\d+)", target)
        if match:
            return f"https://music.163.com/#/artist?id={match.group(1)}"
    if platform == "qq":
        match = re.search(r"/singer/([^/]+)", path)
        if match:
            return f"https://y.qq.com/n/ryqq/singer/{match.group(1)}"
    if platform == "kugou":
        match = re.search(r"/singer/home/(\d+)", path)
        if match:
            return f"https://www.kugou.com/yy/singer/home/{match.group(1)}.html"
    if platform == "kuwo":
        match = re.search(r"/singer_detail/(\d+)", path)
        if match:
            return f"https://www.kuwo.cn/singer_detail/{match.group(1)}"
    return normalize_url(raw)


def _platform_name(platform: str) -> str:
    from toolbox.platform_names import MEDIA_PLATFORM_NAMES, MUSIC_PLATFORM_NAMES
    return (MEDIA_PLATFORM_NAMES.get(platform)
            or MUSIC_PLATFORM_NAMES.get(platform) or platform or "未知")


def _info_items():
    from accounts_manager import load_accounts
    for acc in load_accounts():
        if not acc.url:
            continue
        yield {
            "name": acc.name, "url": acc.url, "note": acc.note,
            "group_ids": list(acc.group_ids), "kind": acc.kind,
        }


def _tracker_items():
    from tracker import accounts as tracker_accounts
    for acc in tracker_accounts.load_accounts():
        url = acc.get("url") or ""
        if not url:
            continue
        yield {
            "name": acc.get("name", ""), "url": url, "note": acc.get("note", ""),
            "group_ids": [str(g) for g in acc.get("group_ids") or []],
            "kind": "", "platform": acc.get("platform", ""),
        }


def _music_items():
    from music_account import accounts as music_accounts
    for acc in music_accounts.load_accounts():
        url = acc.get("url") or ""
        if not url:
            continue
        group_id = str(acc.get("group_id") or "")
        yield {
            "name": acc.get("name", ""), "url": url, "note": acc.get("note", ""),
            "group_ids": [group_id] if group_id else [],
            "kind": acc.get("kind", ""), "platform": acc.get("platform", ""),
        }


def _group_maps() -> dict:
    from accounts_manager import get_groups as info_groups
    from music_account.accounts import get_groups as music_groups
    from tracker.accounts import get_groups as tracker_groups
    return {
        "info": info_groups(),
        "tracker": tracker_groups(),
        "music": music_groups(),
    }


def iter_entries():
    """逐条产出三个账号中心的原始条目（含来源、平台、身份链接与 id）。"""
    for source, iterator in (("info", _info_items()),
                             ("tracker", _tracker_items()),
                             ("music", _music_items())):
        for item in iterator:
            platform = item.get("platform") or infer_platform(item["url"])
            canonical = canonical_url(platform, item["url"])
            yield {
                "source": source, "platform": platform,
                "url": item["url"], "canonical": canonical,
                "id": account_id(platform, canonical),
                "name": item["name"], "group_ids": item["group_ids"],
                "kind": item.get("kind", ""),
            }


def load_overview() -> dict:
    """返回去重后的账号视图与统计；不写任何文件。"""
    groups = _group_maps()
    merged: dict[str, dict] = {}
    raw_counts = {"info": 0, "tracker": 0, "music": 0}

    for entry in iter_entries():
        source = entry["source"]
        raw_counts[source] += 1
        platform = entry["platform"]
        norm = entry["canonical"]
        key = entry["id"]
        item = entry
        account = merged.get(key)
        if account is None:
            account = {
                "id": key, "platform": platform,
                "platform_name": _platform_name(platform),
                "name": item["name"], "url": norm, "norm_url": norm,
                "sources": [], "kinds": {}, "group_ids": [], "groups": [],
                "names": [],
            }
            merged[key] = account
        if source not in account["sources"]:
            account["sources"].append(source)
        if item["name"] and item["name"] not in account["names"]:
            account["names"].append(item["name"])
        if not account["name"] and item["name"]:
            account["name"] = item["name"]
        if item.get("kind"):
            kinds = account["kinds"].setdefault(source, [])
            if item["kind"] not in kinds:
                kinds.append(item["kind"])
        for gid in item["group_ids"]:
            if gid and gid not in account["group_ids"]:
                account["group_ids"].append(gid)
            gname = groups.get(source, {}).get(gid, "")
            label = f"{gid} {gname}".strip()
            if gid and label not in account["groups"]:
                account["groups"].append(label)

    accounts = list(merged.values())
    for account in accounts:
        account["sources_text"] = "、".join(
            SOURCE_NAMES[s] for s in ("info", "tracker", "music")
            if s in account["sources"])
        kinds = [f"{SOURCE_NAMES[s]}:{'/'.join(v)}" for s, v in account["kinds"].items() if v]
        account["kinds_text"] = " / ".join(kinds)
        account["groups_text"] = "、".join(account["groups"])

    by_platform: dict[str, int] = {}
    for account in accounts:
        by_platform[account["platform"] or "unknown"] = (
            by_platform.get(account["platform"] or "unknown", 0) + 1)

    return {
        "accounts": sorted(accounts, key=lambda a: (a["platform"], a["name"] or a["url"])),
        "stats": {
            "raw": sum(raw_counts.values()),
            "unique": len(accounts),
            "by_source": raw_counts,
            "by_platform": dict(sorted(by_platform.items())),
            "duplicates": sum(1 for a in accounts if len(a["sources"]) > 1),
        },
    }