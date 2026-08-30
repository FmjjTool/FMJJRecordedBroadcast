"""音乐账号追踪：单账号单平台配置 + 采集 + xlsx 长期缓存。

每个账号：{name: 昵称, url: 链接, platform: netease/qq/kugou/kuwo/fivesing,
          kind: user/artist/singer（可空，采集时从链接识别）}。
采集结果追加保存到 导出目录/音乐账号数据/<昵称>.xlsx。
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent

PLATFORM_NAMES = {"netease": "网易云", "qq": "QQ音乐", "kugou": "酷狗",
                  "kuwo": "酷我", "fivesing": "5sing"}

# 组号规范（与「账号追踪」一致）：必须为重复数字，如 11、22、33 ... 99
GROUP_ID_PATTERN = re.compile(r"^(\d)\1$")

# xlsx 缓存字段（与采集返回的 record 字段对齐）
_RECORD_FIELDS = [
    "platform", "kind", "name", "fans", "following", "listens",
    "songs", "albums", "mvs", "works", "sign", "region",
    "birthday", "level", "extra", "avatar", "error",
]

_NUM_FIELDS = {"fans", "following", "listens", "songs", "albums", "mvs", "works", "level"}


def _data_dir() -> Path:
    """账号/分组数据目录：源码运行=模块旁；打包后=exe 旁 config/。"""
    if getattr(sys, "frozen", False):
        from toolbox.config_manager import get_config_dir
        return get_config_dir()
    return BASE_DIR


ACCOUNTS_FILE = _data_dir() / "music_accounts.json"
GROUPS_FILE = _data_dir() / "music_groups.json"


def _norm_account(a):
    """补全账号字段默认值，保证加载后的账号结构统一。"""
    a = dict(a)
    a.setdefault("group_id", "")
    a.setdefault("kind", "")
    return a


def load_accounts():
    """加载追踪账号列表：[{name, url, platform, kind, group_id}]。"""
    if ACCOUNTS_FILE.exists():
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [_norm_account(a) for a in data if isinstance(a, dict)]
        except Exception:
            pass
    return []


def save_accounts(accounts):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)


# ==================== 分组 ====================

def get_groups():
    """返回 {组号: 组名} 映射（读 music_groups.json）。"""
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
    groups = get_groups()
    if group_id in groups:
        groups.pop(group_id)
        with open(GROUPS_FILE, "w", encoding="utf-8") as f:
            json.dump(groups, f, ensure_ascii=False, indent=2)
    accounts = load_accounts()
    changed = False
    for a in accounts:
        if a.get("group_id") == group_id:
            a["group_id"] = ""
            changed = True
    if changed:
        save_accounts(accounts)


def get_accounts_by_group(group_id):
    return [a for a in load_accounts() if a.get("group_id") == group_id]


# ==================== 链接解析 ====================

def parse_url(url):
    """从账号链接解析 (platform, pid, kind)。识别失败返回 (None, None, None)。

    kind：user=用户主页，artist/singer=歌手主页。
    """
    url = (url or "").strip()
    if not url:
        return None, None, None
    # 网易云：music.163.com/#/user/home?id=xxx 或 /user/home?id=xxx（用户）
    m = re.search(r'music\.163\.com/(?:#/)?user/home\?id=(\d+)', url)
    if m:
        return "netease", m.group(1), "user"
    m = re.search(r'music\.163\.com/(?:#/)?home\?id=(\d+)', url)
    if m:
        return "netease", m.group(1), "user"
    # 网易云歌手：music.163.com/#/artist?id=xxx
    m = re.search(r'music\.163\.com/(?:#/)?artist\?id=(\d+)', url)
    if m:
        return "netease", m.group(1), "artist"
    # QQ音乐歌手：y.qq.com/n/ryqq/singer/{mid}
    m = re.search(r'y\.qq\.com/n/ryqq/singer/([A-Za-z0-9]+)', url)
    if m:
        return "qq", m.group(1), "singer"
    m = re.search(r'[?&]mid=([A-Za-z0-9]{10,20})', url) and "y.qq.com" in url
    if m and re.search(r'type=singer', url):
        return "qq", re.search(r'[?&]mid=([A-Za-z0-9]{10,20})', url).group(1), "singer"
    # 酷狗歌手：kugou.com/yy/singer/home/{id}.html 或 m.kugou.com/singer/{id}
    m = re.search(r'kugou\.com/yy/singer/home/(\d+)', url)
    if m:
        return "kugou", m.group(1), "singer"
    m = re.search(r'm\.kugou\.com/singer/(\d+)', url)
    if m:
        return "kugou", m.group(1), "singer"
    m = re.search(r'kugou\.com/singer/(\d+)', url)
    if m:
        return "kugou", m.group(1), "singer"
    # 酷我歌手：kuwo.cn/singer_detail/{rid}
    m = re.search(r'kuwo\.cn/singer_detail/(\d+)', url)
    if m:
        return "kuwo", m.group(1), "singer"
    # 5sing 用户：5sing.kugou.com/{uid}/default.html 或 /m/space/{uid}.html
    m = re.search(r'5sing\.kugou\.com/(?:m/space/)?(\d+)(?:/default\.html|\.html)?', url)
    if m:
        return "fivesing", m.group(1), "user"
    return None, None, None


def collect_account(account):
    """采集单个账号：解析链接平台→调用对应采集器，返回 record dict。"""
    platform = (account.get("platform") or "").strip()
    pid = (account.get("platform_id") or "").strip()
    kind = (account.get("kind") or "").strip()
    if not (platform and pid):
        platform, pid, kind = parse_url(account.get("url", ""))
    if not platform or not pid:
        return {"platform": "unknown", "error": "无法识别链接平台/ID"}

    from .collectors import collect
    return collect(platform, pid, kind)


# ==================== xlsx 长期缓存 ====================

def _xlsx_path(name):
    safe = re.sub(r'[\\/:*?"<>|]', "_", name)
    try:
        from toolbox.config_manager import get_export_subdir
        data_dir = get_export_subdir("music_tracker")
    except Exception:
        data_dir = BASE_DIR / "data"
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
    """读取账号历史采集数据（xlsx），返回记录列表（最新在前，每条含 _time）。"""
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
        if not any(v not in (None, "") for v in row):
            continue
        rec = dict(zip(headers, row))
        time_str = rec.pop("time", "") or ""
        rec["_time"] = time_str
        for k in _NUM_FIELDS:
            if rec.get(k) not in (None, ""):
                try:
                    rec[k] = int(rec[k])
                except (ValueError, TypeError):
                    pass
        records.append(rec)
    records.reverse()  # 最新在前
    return records
