"""
账号管理模块 - 读取/写入 accounts.txt，支持分组、备注

文件格式 (accounts.txt):
  # 注释行（跳过）
  https://example.com                          → 纯 URL（兼容旧格式）
  11|游戏主播|抖音大号|https://www.douyin.com/xxx  → 组号|组名|备注|URL

组号规范: 必须为重复数字，如 11、22、33 ... 99
"""

import os
import re
import json
from dataclasses import dataclass, field

ACCOUNT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "accounts.txt")
GROUPS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "groups.json")

# 组号必须是 11, 22, 33, ..., 99 这样的重复双位数
GROUP_ID_PATTERN = re.compile(r"^(\d)\1$")  # 匹配 11, 22, 33, ...


@dataclass
class Account:
    url: str
    name: str = ""           # 账号名
    group_ids: list = field(default_factory=list)  # 多个组号，如 ["11", "22"]
    group_name: str = ""     # 组名（主组名，向后兼容）
    note: str = ""           # 备注
    kind: str = ""           # 采集类型: ""=自动(按URL) / "video"=只视频 / "dynamic"=只动态 / "all"=全部

    @property
    def group_id(self) -> str:
        """兼容旧代码：返回第一个组号"""
        return self.group_ids[0] if self.group_ids else ""

    def to_line(self) -> str:
        """序列化为文件中的一行（组号|组名|账号名|URL[|备注]）。"""
        if self.group_ids:
            gids = ",".join(self.group_ids)
            line = f"{gids}|{self.group_name}|{self.name}|{self.url}"
            if self.note:
                line += f"|{self.note}"
            return line
        if self.name:
            return f"{self.name}|{self.url}"
        return self.url

    @staticmethod
    def from_line(line: str) -> "Account":
        """从文件中的一行解析。兼容旧格式（组号|组名|备注|URL → 第三段当作账号名）。

        第3/4段自动纠偏：若账号名与 URL 位置填反（账号名是 http 开头），自动对调。
        """
        line = line.strip()
        parts = line.split("|")
        # pipe 格式: 组号(逗号分隔可多组)|组名|账号名|URL[|备注]
        if len(parts) >= 4 and re.match(r'^[\d,]+$', parts[0]):
            gids = [g for g in parts[0].split(",") if g]
            name, url = parts[2], parts[3]
            # 账号名/URL 位置填反时自动纠偏
            if url and not re.match(r'^https?://', url) and re.match(r'^https?://', name or ""):
                name, url = url, name
            return Account(
                group_ids=gids,
                group_name=parts[1],
                name=name,
                url=url,
                note=parts[4] if len(parts) > 4 else "",
            )
        # 账号名|URL
        if len(parts) == 2 and not re.match(r'^https?://', parts[0]):
            return Account(name=parts[0], url=parts[1])
        # 纯 URL
        return Account(url=line)


# ========== 文件读写 ==========

def load_accounts() -> list[Account]:
    """加载所有账号"""
    if not os.path.exists(ACCOUNT_FILE):
        return []
    accounts = []
    with open(ACCOUNT_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                accounts.append(Account.from_line(line))
    return accounts


def save_accounts(accounts: list[Account]):
    """保存账号列表"""
    with open(ACCOUNT_FILE, "w", encoding="utf-8") as f:
        for a in accounts:
            f.write(a.to_line() + "\n")


# ========== CRUD ==========

def _parse_group_ids(group_id: str) -> list:
    """解析组号字符串（逗号分隔，支持中文逗号），过滤非法组号。"""
    if not group_id:
        return []
    result = []
    for g in str(group_id).replace("，", ",").split(","):
        g = g.strip()
        if g and GROUP_ID_PATTERN.match(g):
            result.append(g)
    return result


def infer_kind_from_url(url: str) -> str:
    """根据 URL 推断 B站账号采集类型。/dynamic → 动态、/video → 视频、其他 → 自动。"""
    if "space.bilibili.com" in url:
        if url.rstrip("/").endswith("/dynamic"):
            return "dynamic"
        if url.rstrip("/").endswith("/video"):
            return "video"
    return ""


def add_account(url: str, name: str = "", group_id: str = "", group_name: str = "", note: str = "", kind: str = "") -> bool:
    """添加账号。kind: ""=按URL自动推断 / "video"/"dynamic"/"all"=手动指定。"""
    name = name.replace("|", " ")
    group_name = group_name.replace("|", " ")
    note = note.replace("|", " ")
    # 账号名/URL 输入框填反时自动纠偏
    if name and url and not re.match(r'^https?://', url) and re.match(r'^https?://', name):
        url, name = name, url
    accounts = load_accounts()
    for a in accounts:
        if a.url == url:
            return False
    accounts.append(Account(
        url=url, name=name,
        group_ids=_parse_group_ids(group_id), group_name=group_name, note=note,
        kind=kind or infer_kind_from_url(url),
    ))
    save_accounts(accounts)
    return True


def update_account(index: int, name=None, group_id: str = "", group_name: str = "", note=None, kind=None) -> bool:
    """更新指定账号的账号名/分组/备注/采集类型。传 None 表示不修改。"""
    group_name = (group_name or "").replace("|", " ")
    accounts = load_accounts()
    if 1 <= index <= len(accounts):
        acc = accounts[index - 1]
        if name is not None:
            acc.name = name.replace("|", " ")
        if note is not None:
            acc.note = note.replace("|", " ")
        if kind is not None:
            acc.kind = kind
        acc.group_ids = _parse_group_ids(group_id)
        acc.group_name = group_name
        save_accounts(accounts)
        return True
    return False


def remove_account(index: int) -> bool:
    accounts = load_accounts()
    if 1 <= index <= len(accounts):
        accounts.pop(index - 1)
        save_accounts(accounts)
        return True
    return False


# ========== 分组查询 ==========

def get_groups() -> dict[str, str]:
    """返回 {组号: 组名} 的映射。优先读 groups.json，兜底从账号汇总（兼容旧数据）。"""
    groups: dict[str, str] = {}
    if os.path.exists(GROUPS_FILE):
        try:
            with open(GROUPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                groups = {str(k): str(v) for k, v in data.items() if str(k)}
        except Exception:
            pass
    # 兜底：从账号汇总（兼容旧数据，组名存在账号行里）
    for a in load_accounts():
        for g in a.group_ids:
            if g and g not in groups:
                groups[g] = a.group_name
    return groups


def save_group(group_id: str, group_name: str) -> bool:
    """新建/更新一个分组（组号 + 组名），写入 groups.json。"""
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


def delete_group(group_id: str) -> None:
    """删除分组，并清空归属该组的账号绑定。"""
    groups = get_groups()
    if group_id in groups:
        groups.pop(group_id)
        try:
            with open(GROUPS_FILE, "w", encoding="utf-8") as f:
                json.dump(groups, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    accounts = load_accounts()
    changed = False
    for a in accounts:
        if group_id in a.group_ids:
            a.group_ids = [g for g in a.group_ids if g != group_id]
            if not a.group_ids:
                a.group_name = ""
            changed = True
    if changed:
        save_accounts(accounts)


def get_accounts_by_group(group_id: str) -> list[Account]:
    """获取指定组号的所有账号"""
    return [a for a in load_accounts() if group_id in a.group_ids]
