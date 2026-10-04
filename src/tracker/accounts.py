"""账号追踪：单账号单平台配置 + 采集 + xlsx 长期缓存。

每个账号：{name: 昵称, url: 链接, platform: bilibili/douyin/weibo(可空，采集时自动识别)}。
采集结果追加保存到 tracker/data/<昵称>.xlsx，并支持读取长期缓存。
"""

import json
import re
import sys
import asyncio
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent


def _data_dir() -> Path:
    """账号/分组数据目录：源码运行=模块旁；打包后=exe 旁 config/。

    打包后 __file__ 位于 _internal（Program Files 下通常只读），必须外移。
    """
    if getattr(sys, "frozen", False):
        from toolbox.config_manager import get_config_dir
        return get_config_dir()
    return BASE_DIR


ACCOUNTS_FILE = _data_dir() / "accounts.json"
GROUPS_FILE = _data_dir() / "groups.json"
DATA_DIR = BASE_DIR / "data"

PLATFORM_NAMES = {"bilibili": "B站", "douyin": "抖音", "weibo": "微博",
                  "kuaishou": "快手", "xiaohongshu": "小红书"}

# 组号规范（与「账号获取」一致）：必须为重复数字，如 11、22、33 ... 99
GROUP_ID_PATTERN = re.compile(r"^(\d)\1$")

# xlsx 缓存字段（与采集返回的 record 字段对齐）
_RECORD_FIELDS = [
    "platform", "name", "fans", "following", "likes",
    "video_count", "weibo_count", "sign", "official_title",
    "verified_reason", "verified", "level", "error",
]


def _norm_account(a):
    """补全账号字段默认值（group_ids 组号列表），保证加载后的账号结构统一。

    兼容旧数据：单一 group_id 字符串（含逗号分隔多组）自动转为列表。
    """
    a = dict(a)
    gids = a.get("group_ids")
    if gids is None:
        raw = str(a.pop("group_id", "") or "").replace("，", ",")
        gids = [g.strip() for g in raw.split(",") if g.strip()]
    a["group_ids"] = [str(g) for g in gids if str(g).strip()]
    a.pop("group_id", None)
    return a


def load_accounts():
    """加载追踪账号列表：[{name, url, platform, group_ids: [组号...]}]。

    兼容旧格式（{name, bilibili_mid, douyin_sec_uid, weibo_uid}）：自动拆分为单平台账号。
    """
    if ACCOUNTS_FILE.exists():
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                result = []
                for a in data:
                    # 旧格式：三平台合并 → 拆分为三个单平台账号
                    if "url" not in a and (a.get("bilibili_mid") or a.get("douyin_sec_uid") or a.get("weibo_uid")):
                        name = a.get("name", "封茗囧菌")
                        if a.get("bilibili_mid"):
                            result.append(_norm_account({"name": f"{name}B站", "url": f"https://space.bilibili.com/{a['bilibili_mid']}", "platform": "bilibili"}))
                        if a.get("douyin_sec_uid"):
                            result.append(_norm_account({"name": f"{name}抖音", "url": f"https://www.douyin.com/user/{a['douyin_sec_uid']}", "platform": "douyin"}))
                        if a.get("weibo_uid"):
                            result.append(_norm_account({"name": f"{name}微博", "url": f"https://weibo.com/u/{a['weibo_uid']}", "platform": "weibo"}))
                    else:
                        result.append(_norm_account(a))
                return result
        except Exception:
            pass
    # 首次运行（无 accounts.json）：回退到 config 默认账号，拆分为三个单平台账号
    from . import config
    return [
        _norm_account({"name": "封茗囧菌B站", "url": f"https://space.bilibili.com/{config.BILIBILI_MID}", "platform": "bilibili"}),
        _norm_account({"name": "封茗囧菌抖音", "url": f"https://www.douyin.com/user/{config.DOUYIN_SEC_UID}", "platform": "douyin"}),
        _norm_account({"name": "封茗囧菌微博", "url": f"https://weibo.com/u/{config.WEIBO_UID}", "platform": "weibo"}),
    ]


def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)


def move_account(index: int, delta: int) -> bool:
    """上移/下移账号（index 为当前行号，delta=-1/+1），调整后立即保存。"""
    accounts = load_accounts()
    new = index + delta
    if not (0 <= index < len(accounts)) or not (0 <= new < len(accounts)):
        return False
    accounts[index], accounts[new] = accounts[new], accounts[index]
    save_accounts(accounts)
    return True


def update_account_at(index: int, name: str = "", url: str = "") -> bool:
    """按下标更新账号的名称/链接（platform 按新链接重推）。
    url 为空或与其他账号重复时失败；历史数据按旧 URL 关联，不随链接变更迁移。"""
    accounts = load_accounts()
    if not (0 <= index < len(accounts)):
        return False
    url = (url or "").strip()
    if not url:
        return False
    if any(a.get("url") == url for i, a in enumerate(accounts) if i != index):
        return False
    if url != accounts[index].get("url"):
        plat, _pid = parse_url(url)
        accounts[index]["url"] = url
        if plat:
            accounts[index]["platform"] = plat
    accounts[index]["name"] = (name or "").strip()
    save_accounts(accounts)
    return True


# ==================== 分组 ====================

def get_groups():
    """返回 {组号: 组名} 映射（读 groups.json）。"""
    if GROUPS_FILE.exists():
        try:
            with open(GROUPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items() if str(k)}
        except Exception:
            pass
    return {}


def save_group(group_id, group_name):
    """新建/更新一个分组（组号 + 组名）。返回是否成功。"""
    group_id = (group_id or "").strip()
    group_name = (group_name or "").strip()
    if not group_id or not group_name:
        return False
    groups = get_groups()
    groups[group_id] = group_name
    try:
        with open(GROUPS_FILE, "w", encoding="utf-8") as f:
            json.dump(groups, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def delete_group(group_id):
    """删除分组，并从各账号的组号列表中移除该组。"""
    groups = get_groups()
    if group_id in groups:
        groups.pop(group_id)
        with open(GROUPS_FILE, "w", encoding="utf-8") as f:
            json.dump(groups, f, ensure_ascii=False, indent=2)
    accounts = load_accounts()
    changed = False
    for a in accounts:
        if group_id in a.get("group_ids", []):
            a["group_ids"] = [g for g in a["group_ids"] if g != group_id]
            changed = True
    if changed:
        save_accounts(accounts)


def move_group(group_id, delta):
    """上移/下移分组（重排 groups.json 键顺序，列表/下拉显示顺序随之变化）。"""
    groups = get_groups()
    keys = list(groups.keys())
    if group_id not in keys:
        return False
    idx = keys.index(group_id)
    j = idx + (1 if delta > 0 else -1)
    if not (0 <= j < len(keys)):
        return False
    keys[idx], keys[j] = keys[j], keys[idx]
    reordered = {k: groups[k] for k in keys}
    try:
        with open(GROUPS_FILE, "w", encoding="utf-8") as f:
            json.dump(reordered, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def get_accounts_by_group(group_id):
    """返回归属指定组号的所有账号（账号可绑多组，任一命中即返回）。"""
    return [a for a in load_accounts() if group_id in a.get("group_ids", [])]


def parse_url(url):
    """从账号链接解析 (platform, id)。识别失败返回 (None, None)。"""
    url = (url or "").strip()
    if not url:
        return None, None
    # B站：space.bilibili.com/1467772 或 bilibili.com/1467772
    m = re.search(r'bilibili\.com/[\w/]*?(\d{5,})', url)
    if m:
        return "bilibili", m.group(1)
    # 微博：weibo.com/u/3056354293 或 m.weibo.cn/u/3056354293
    m = re.search(r'weibo\.(?:com|cn)/(?:u/|n/)?(\d{6,})', url)
    if m:
        return "weibo", m.group(1)
    # 抖音：douyin.com/user/MS4wLjABAAAA...
    m = re.search(r'douyin\.com/user/([\w\-_]+)', url)
    if m:
        return "douyin", m.group(1)
    # 快手：kuaishou.com/profile/3x2ranvzkq6g6wy
    m = re.search(r'kuaishou\.com/profile/([a-zA-Z0-9]+)', url)
    if m:
        return "kuaishou", m.group(1)
    # 小红书：xiaohongshu.com/user/profile/60ec2add...
    m = re.search(r'xiaohongshu\.com/user/profile/([a-f0-9]+)', url)
    if m:
        return "xiaohongshu", m.group(1)
    return None, None


def collect_account(account):
    """采集单个账号：解析链接平台→调用对应采集器，返回 record dict。"""
    url = account.get("url", "")
    platform = (account.get("platform") or "").strip()
    pid = (account.get("platform_id") or "").strip()
    if not (platform and pid):
        platform, pid = parse_url(url)
    if not platform or not pid:
        return {"platform": "unknown", "error": "无法识别链接平台/ID"}

    from .collectors import (collect_bilibili, collect_weibo, collect_douyin,
                             collect_kuaishou, collect_xiaohongshu)
    fn = {
        "bilibili": collect_bilibili,
        "weibo": collect_weibo,
        "douyin": collect_douyin,
        "kuaishou": collect_kuaishou,
        "xiaohongshu": collect_xiaohongshu,
    }.get(platform)
    if not fn:
        return {"platform": "unknown", "error": f"未知平台 {platform}"}
    return asyncio.run(fn(pid))


# ==================== xlsx 长期缓存 ====================

def _xlsx_path(name):
    safe = re.sub(r'[\\/:*?"<>|]', "_", name)
    try:
        from toolbox.config_manager import get_export_subdir
        data_dir = get_export_subdir("tracker")
    except Exception:
        data_dir = DATA_DIR  # 回退到 tracker/data
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / f"{safe}.xlsx"


def save_history(name, record):
    """把采集结果追加到账号的 xlsx 缓存，返回文件路径。"""
    import openpyxl
    path = _xlsx_path(name)
    headers = ["time"] + _RECORD_FIELDS
    row = [datetime.now().strftime("%Y-%m-%d %H:%M:%S")] + [record.get(f, "") for f in _RECORD_FIELDS]
    if path.exists():
        wb = openpyxl.load_workbook(path)
        ws = wb.active
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
    ws.append(row)
    wb.save(path)
    return str(path)


def load_history(name):
    """读取账号历史采集数据（xlsx），返回记录列表（最新在前，每条含 _time）。无缓存返回 []。"""
    import openpyxl
    path = _xlsx_path(name)
    if not path.exists():
        return []
    try:
        wb = openpyxl.load_workbook(path)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
    except Exception:
        return []
    if len(rows) < 2:
        return []
    headers = rows[0]
    records = []
    for row in rows[1:]:
        rec = dict(zip(headers, row))
        time_str = rec.pop("time", "") or ""
        rec["_time"] = time_str
        for k in ["fans", "following", "likes", "video_count", "weibo_count", "level"]:
            if rec.get(k) not in (None, ""):
                try:
                    rec[k] = int(rec[k])
                except (ValueError, TypeError):
                    pass
        if rec.get("verified") == "False" or rec.get("verified") is False:
            rec["verified"] = False
        records.append(rec)
    records.reverse()  # 最新在前
    return records


# ==================== 从「账号获取」同步 ====================

def plan_sync_from_fm() -> tuple:
    """计算「账号获取」→ 追踪的同步计划（不写盘）。

    按平台+ID 去重（识别失败时按整条 URL 去重），返回 (新增条目列表, 跳过数)。
    """
    from accounts_manager import load_accounts as load_fm_accounts
    src_accounts = [a for a in load_fm_accounts() if getattr(a, "url", "")]

    def _key(url):
        plat, pid = parse_url(url)
        return (plat, pid) if plat and pid else ("__url__", (url or "").strip())

    dst_accounts = load_accounts()
    existing = {_key(a.get("url", "")) for a in dst_accounts}
    new_items, skipped = [], 0
    for acc in src_accounts:
        key = _key(acc.url)
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        gids = [str(g) for g in (acc.group_ids or []) if str(g).strip()]
        new_items.append({"name": acc.name or "", "url": acc.url,
                          "platform": _key(acc.url)[0] or "", "group_ids": gids})
    return new_items, skipped


def apply_sync_from_fm(new_items: list) -> int:
    """写入同步计划（plan_sync_from_fm 的结果）：追加账号 + 缺失分组补建。"""
    if not new_items:
        return 0
    dst_accounts = load_accounts()
    dst_accounts.extend(new_items)
    save_accounts(dst_accounts)
    # 分组带过去：追踪侧缺的组按同组号+组名补建
    from accounts_manager import get_groups as fm_groups
    new_gids = {g for item in new_items for g in item.get("group_ids", [])}
    src_groups = fm_groups()
    dst_groups = get_groups()
    for gid in new_gids:
        if gid not in dst_groups and src_groups.get(gid):
            save_group(gid, src_groups[gid])
    return len(new_items)


# ==================== 反向：同步到「账号获取」 ====================

def plan_sync_to_fm() -> tuple:
    """计算追踪 → 「账号获取」的同步计划（不写盘）。

    按平台+ID 去重（识别失败时按整条 URL 去重），返回 (新增条目列表, 跳过数)。
    条目结构：{name, url, group_ids}。
    """
    from accounts_manager import load_accounts as load_fm_accounts
    src_accounts = [a for a in load_accounts() if (a.get("url") or "").strip()]

    def _key(url):
        plat, pid = parse_url(url)
        return (plat, pid) if plat and pid else ("__url__", (url or "").strip())

    dst_accounts = [a for a in load_fm_accounts() if getattr(a, "url", "")]
    existing = {_key(a.url) for a in dst_accounts}
    new_items, skipped = [], 0
    for acc in src_accounts:
        key = _key(acc.get("url", ""))
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        gids = [str(g) for g in (acc.get("group_ids") or []) if str(g).strip()]
        new_items.append({"name": acc.get("name") or "", "url": acc["url"],
                          "group_ids": gids})
    return new_items, skipped


def apply_sync_to_fm(new_items: list) -> int:
    """写入反向同步计划（plan_sync_to_fm 的结果）：
    追加到 accounts.txt（B站链接自动带 video/dynamic 采集类型）+ 缺失分组补建。"""
    if not new_items:
        return 0
    from accounts_manager import (Account, infer_kind_from_url,
                                  load_accounts as load_fm_accounts,
                                  save_accounts as save_fm_accounts,
                                  get_groups as fm_groups,
                                  save_group as fm_save_group)
    dst_accounts = load_fm_accounts()
    src_groups = get_groups()  # 追踪侧分组 {组号: 组名}
    dst_groups = fm_groups()
    new_gids = set()
    for item in new_items:
        gids = [g for g in item.get("group_ids", []) if GROUP_ID_PATTERN.match(g)]
        gname = src_groups.get(gids[0], "") if gids else ""
        dst_accounts.append(Account(
            url=item["url"], name=item.get("name", ""),
            group_ids=gids, group_name=gname,
            kind=infer_kind_from_url(item["url"]),
        ))
        new_gids.update(gids)
    save_fm_accounts(dst_accounts)
    # 分组带过去：账号获取侧缺的组按同组号+组名补建
    for gid in new_gids:
        if gid not in dst_groups and src_groups.get(gid):
            fm_save_group(gid, src_groups[gid])
    return len(new_items)
