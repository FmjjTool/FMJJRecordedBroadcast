"""统一账号注册表：三个账号中心的读写适配层。

三份主数据保持各自的唯一写入方，不新增第四份账号文件：
- 信息获取：`config/accounts.txt`（accounts_manager）
- 网媒追踪：`tracker/accounts.json`（tracker.accounts）
- 音乐追踪：`music_accounts.json`（music_account.accounts）

本模块把「账号是否出现在某个主数据里」当作「该功能是否可以使用这个账号」：
勾选某功能 → 写入该功能的主数据；取消勾选 → 从该主数据移除。
同一平台+规范化链接被视为同一账号，分组按功能各自保存（写入时保持同步）。
"""

from __future__ import annotations

import re

MODULES = ("info", "tracker", "music")
MODULE_NAMES = {"info": "信息获取", "tracker": "网媒追踪", "music": "音乐追踪"}

MEDIA_PLATFORMS = ("bilibili", "douyin", "weibo", "kuaishou", "xiaohongshu")
MUSIC_PLATFORMS = ("netease", "qq", "kugou", "kuwo", "fivesing")

# 组号规范（与三个账号中心一致）：必须是重复数字，如 11、22、33 ... 99
GROUP_ID_PATTERN = re.compile(r"^(\d)\1$")


# ==================== 平台与链接 ====================

def platform_of(url: str) -> str:
    from toolbox.accounts_overview import infer_platform
    return infer_platform(url)


def platform_name(platform: str) -> str:
    if not platform:
        return "未知"
    from toolbox.platform_names import ALL_PLATFORM_NAMES
    return ALL_PLATFORM_NAMES.get(platform, platform)


def allowed_modules(platform: str) -> tuple:
    """该平台账号可以被哪些功能使用（视频号 → 信息获取/网媒追踪，音乐号 → 音乐追踪）。"""
    if platform in MEDIA_PLATFORMS:
        return ("info", "tracker")
    if platform in MUSIC_PLATFORMS:
        return ("music",)
    return ()


def _canonical(platform: str, url: str) -> str:
    from toolbox.accounts_overview import canonical_url
    return canonical_url(platform, url) or (url or "").strip()


def _account_id(platform: str, url: str) -> str:
    from toolbox.accounts_overview import account_id
    return account_id(platform, _canonical(platform, url))


def _same_url(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left.strip() == right.strip():
        return True
    plat = platform_of(left) or platform_of(right)
    return _canonical(plat, left) == _canonical(plat, right)


def _merge_kind(left: str, right: str) -> str:
    """同一账号的多条采集记录合并成一个采集类型。"""
    values = {left or "", right or ""}
    if "all" in values or {"video", "dynamic"} <= values:
        return "all"
    return left or right


# ==================== 读取 ====================

def _info_items():
    from accounts_manager import load_accounts
    return load_accounts()


def _tracker_items():
    from tracker import accounts as tracker_accounts
    return tracker_accounts.load_accounts()


def _music_items():
    from music_account import accounts as music_accounts
    return music_accounts.load_accounts()


def list_accounts() -> list[dict]:
    """所有账号去重后的统一视图，按「信息获取 → 网媒 → 音乐」首次出现顺序排列。"""
    merged: dict[str, dict] = {}
    order: list[str] = []

    def add(module, name, url, group_ids, kind="", platform=""):
        url = (url or "").strip()
        if not url:
            return
        plat = platform or platform_of(url)
        key = _account_id(plat, url)
        record = merged.get(key)
        if record is None:
            record = {"id": key, "platform": plat, "url": _canonical(plat, url),
                      "modules": {}, "names": {}}
            merged[key] = record
            order.append(key)
        previous = record["modules"].get(module)
        if previous is not None:
            # 同一账号在信息获取里可能有 /video、/dynamic 两条记录：合并为「全部」
            kind = _merge_kind(previous.get("kind", ""), kind)
        record["modules"][module] = {
            "url": url,
            "name": name or "",
            "group_ids": [str(g) for g in group_ids if str(g).strip()],
            "kind": kind or "",
        }
        if name:
            record["names"][module] = name

    for acc in _info_items():
        add("info", acc.name, acc.url, acc.group_ids, acc.kind)
    for acc in _tracker_items():
        add("tracker", acc.get("name", ""), acc.get("url", ""),
            acc.get("group_ids") or [], "", acc.get("platform", ""))
    for acc in _music_items():
        gid = str(acc.get("group_id") or "")
        add("music", acc.get("name", ""), acc.get("url", ""),
            [gid] if gid else [], acc.get("kind", ""), acc.get("platform", ""))

    accounts = []
    for key in order:
        record = merged[key]
        record["name"] = next(
            (record["names"][m] for m in MODULES if record["names"].get(m)), "")
        gids: list[str] = []
        for module in MODULES:
            for gid in record["modules"].get(module, {}).get("group_ids", []):
                if gid not in gids:
                    gids.append(gid)
        record["group_ids"] = gids
        record["platform_name"] = platform_name(record["platform"])
        record["allowed"] = allowed_modules(record["platform"])
        accounts.append(record)
    return accounts


def get_account(account_id: str) -> dict | None:
    return next((a for a in list_accounts() if a["id"] == account_id), None)


def _raw_url(account: dict) -> str:
    """用于写入时的链接：优先用已有主数据里的原始链接，避免规范化丢参数。"""
    for module in MODULES:
        item = account["modules"].get(module)
        if item and item.get("url"):
            return item["url"]
    return account["url"]


# ==================== 写入：账号 ====================

def _as_group_ids(value) -> list[str]:
    """组号入参统一成列表：支持 "11,22" 或 ["11", "22"]。"""
    if isinstance(value, (list, tuple, set)):
        raw = [str(item).strip() for item in value]
    else:
        raw = [item.strip() for item in str(value or "").replace("，", ",").split(",")]
    return [gid for gid in raw if gid and GROUP_ID_PATTERN.match(gid)]


def add_account(name: str, url: str, modules, group_id="",
                kind: str = "") -> tuple[bool, str]:
    """把账号写入所选功能的主数据。返回 (是否新增, 提示文本)。

    group_id 可以是单个组号或组号列表（信息获取/网媒追踪支持多组）。
    """
    url = (url or "").strip()
    name = (name or "").strip()
    if not url:
        return False, "请填写账号链接"
    platform = platform_of(url)
    if not platform:
        return False, "无法识别链接所属平台"
    usable = [m for m in modules if m in allowed_modules(platform)]
    if not usable:
        return False, f"{platform_name(platform)}账号不能用于所选功能"

    gids = _as_group_ids(group_id)
    group_map = groups()
    for gid in gids:
        if gid not in group_map:
            group_map[gid] = f"组{gid}"
            _save_group_everywhere(gid, group_map[gid])
    group_name = group_map.get(gids[0], "") if gids else ""

    added = []
    if "info" in usable:
        from accounts_manager import add_account as info_add
        if info_add(url, name=name, group_id=",".join(gids), group_name=group_name, kind=kind):
            added.append("info")
    if "tracker" in usable:
        from tracker import accounts as tracker_accounts
        accounts = tracker_accounts.load_accounts()
        if not any(_same_url(acc.get("url", ""), url) for acc in accounts):
            accounts.append({"name": name, "url": url, "platform": platform,
                             "group_ids": list(gids)})
            tracker_accounts.save_accounts(accounts)
            added.append("tracker")
    if "music" in usable:
        from music_account import accounts as music_accounts
        accounts = music_accounts.load_accounts()
        if not any(_same_url(acc.get("url", ""), url) for acc in accounts):
            _plat, _pid, music_kind = music_accounts.parse_url(url)
            accounts.append({"name": name, "url": url, "platform": platform,
                             "kind": music_kind or "", "group_id": gids[0] if gids else ""})
            music_accounts.save_accounts(accounts)
            added.append("music")

    if not added:
        return False, "该账号已存在于所选功能"
    return True, "已添加：" + "、".join(MODULE_NAMES[m] for m in added)


def update_account(account_id: str, name=None, url=None, kind=None) -> tuple[bool, str]:
    """修改账号名/链接/采集类型；改动会写回该账号所在的每个功能。"""
    account = get_account(account_id)
    if account is None:
        return False, "账号不存在（可能已被删除）"
    old_name = account["name"]
    new_name = old_name if name is None else str(name).strip()
    new_url = None if url is None else str(url).strip()
    if new_url is not None:
        if not new_url:
            return False, "链接不能为空"
        platform = platform_of(new_url)
        if not platform:
            return False, "无法识别新链接的平台"
        if _account_id(platform, new_url) != account_id:
            if any(_account_id(platform, new_url) == a["id"] for a in list_accounts()):
                return False, "该链接已存在（链接是账号唯一标识）"
    if new_url is None and name is None and kind is None:
        return False, "没有修改"

    raw = _raw_url(account)
    for module in account["modules"]:
        if module == "info":
            _update_info(account, raw, new_name, new_url, kind)
        elif module == "tracker":
            _update_tracker(account, raw, new_name, new_url)
        else:
            _update_music(account, raw, new_name, new_url)
    return True, "已保存账号修改"


def _update_info(account, raw, new_name, new_url, kind):
    from accounts_manager import infer_kind_from_url, load_accounts, save_accounts
    accounts = load_accounts()
    matches = [a for a in accounts if _same_url(a.url, raw)]
    if not matches:
        return
    target = matches[0]
    target.name = new_name
    if new_url and new_url != target.url:
        target.url = new_url
        if kind is None:
            target.kind = infer_kind_from_url(new_url)
        # 链接变了：同一账号的其它采集行合并到这一行，避免重复指向同一主页
        accounts = [a for a in accounts if a is target or not _same_url(a.url, raw)]
    if kind is not None:
        target.kind = kind
        # 采集类型改为单值时，同一账号的多条采集行合并为一条
        accounts = [a for a in accounts if a is target or not _same_url(a.url, target.url)]
    save_accounts(accounts)


def _update_tracker(account, raw, new_name, new_url):
    from tracker import accounts as tracker_accounts
    accounts = tracker_accounts.load_accounts()
    matches = [a for a in accounts if _same_url(a.get("url", ""), raw)]
    if not matches:
        return
    target = matches[0]
    target["name"] = new_name
    if new_url and new_url != target.get("url"):
        target["url"] = new_url
        platform, _pid = tracker_accounts.parse_url(new_url)
        if platform:
            target["platform"] = platform
        accounts = [a for a in accounts if a is target or not _same_url(a.get("url", ""), raw)]
    tracker_accounts.save_accounts(accounts)


def _update_music(account, raw, new_name, new_url):
    from music_account import accounts as music_accounts
    accounts = music_accounts.load_accounts()
    matches = [a for a in accounts if _same_url(a.get("url", ""), raw)]
    if not matches:
        return
    target = matches[0]
    target["name"] = new_name
    if new_url and new_url != target.get("url"):
        target["url"] = new_url
        platform, _pid, music_kind = music_accounts.parse_url(new_url)
        if platform:
            target["platform"] = platform
            target["kind"] = music_kind or ""
        accounts = [a for a in accounts if a is target or not _same_url(a.get("url", ""), raw)]
    music_accounts.save_accounts(accounts)


def set_module(account_id: str, module: str, enabled: bool,
               group_id: str = "") -> bool:
    """勾选/取消某个功能对该账号的使用权。"""
    account = get_account(account_id)
    if account is None or module not in MODULES:
        return False
    if module not in allowed_modules(account["platform"]):
        return False
    present = module in account["modules"]
    if enabled == present:
        return False
    name = account["name"]
    gids = [group_id] if (group_id or "").strip() else list(account["group_ids"])
    if enabled:
        # 新增到另一个功能时用规范化主页链接（信息获取里可能是 /video 之类的采集链接）
        ok, _msg = add_account(name, account["url"], [module], gids,
                               account["modules"].get("info", {}).get("kind", ""))
        return ok
    return _remove_from_module(account, module, _raw_url(account))


def _remove_from_module(account, module, raw) -> bool:
    if module == "info":
        from accounts_manager import load_accounts, save_accounts
        accounts = load_accounts()
        keep = [a for a in accounts if not _same_url(a.url, raw)]
        if len(keep) == len(accounts):
            return False
        save_accounts(keep)
        return True
    if module == "tracker":
        from tracker import accounts as tracker_accounts
        accounts = tracker_accounts.load_accounts()
        keep = [a for a in accounts if not _same_url(a.get("url", ""), raw)]
        if len(keep) == len(accounts):
            return False
        tracker_accounts.save_accounts(keep)
        return True
    from music_account import accounts as music_accounts
    accounts = music_accounts.load_accounts()
    keep = [a for a in accounts if not _same_url(a.get("url", ""), raw)]
    if len(keep) == len(accounts):
        return False
    music_accounts.save_accounts(keep)
    return True


def remove_account(account_id: str) -> bool:
    """从所有功能中删除该账号。"""
    account = get_account(account_id)
    if account is None:
        return False
    raw = _raw_url(account)
    changed = False
    for module in list(account["modules"]):
        changed = _remove_from_module(account, module, raw) or changed
    return changed


def set_group(account_id: str, group_ids) -> bool:
    """把该账号在所有已启用功能里的分组改成同一组（音乐功能只保存第一个组号）。"""
    account = get_account(account_id)
    if account is None:
        return False
    gids = [str(g).strip() for g in group_ids if str(g).strip()]
    for gid in gids:
        if not GROUP_ID_PATTERN.match(gid):
            return False
    raw = _raw_url(account)
    group_map = groups()
    gname = group_map.get(gids[0], "") if gids else ""
    for module in account["modules"]:
        if module == "info":
            from accounts_manager import load_accounts, save_accounts
            accounts = load_accounts()
            changed = False
            for row in accounts:
                if _same_url(row.url, raw):
                    row.group_ids = gids
                    row.group_name = gname
                    changed = True
            if changed:
                save_accounts(accounts)
        elif module == "tracker":
            from tracker import accounts as tracker_accounts
            accounts = tracker_accounts.load_accounts()
            changed = False
            for row in accounts:
                if _same_url(row.get("url", ""), raw):
                    row["group_ids"] = gids
                    changed = True
            if changed:
                tracker_accounts.save_accounts(accounts)
        else:
            from music_account import accounts as music_accounts
            accounts = music_accounts.load_accounts()
            changed = False
            for row in accounts:
                if _same_url(row.get("url", ""), raw):
                    row["group_id"] = gids[0] if gids else ""
                    changed = True
            if changed:
                music_accounts.save_accounts(accounts)
    return True


def move_account(account_id: str, delta: int) -> bool:
    """在账号所在的每个功能里上移/下移一位（顺序即采集顺序）。"""
    account = get_account(account_id)
    if account is None or delta == 0:
        return False
    raw = _raw_url(account)
    moved = False
    for module in account["modules"]:
        if module == "info":
            from accounts_manager import move_account as info_move
            target = next((a for a in _info_items() if _same_url(a.url, raw)), None)
            if target is not None:
                moved = info_move(target.url, delta) or moved
        elif module == "tracker":
            from tracker import accounts as tracker_accounts
            accounts = tracker_accounts.load_accounts()
            index = next((i for i, a in enumerate(accounts)
                          if _same_url(a.get("url", ""), raw)), -1)
            if index >= 0:
                moved = tracker_accounts.move_account(index, delta) or moved
        else:
            from music_account import accounts as music_accounts
            accounts = music_accounts.load_accounts()
            index = next((i for i, a in enumerate(accounts)
                          if _same_url(a.get("url", ""), raw)), -1)
            if index >= 0:
                moved = music_accounts.move_account(index, delta) or moved
    return moved


# ==================== 写入：分组 ====================

def groups() -> dict[str, str]:
    """三个功能的分组并集：{组号: 组名}，组名优先取信息获取。"""
    from accounts_manager import get_groups as info_groups
    from music_account.accounts import get_groups as music_groups
    from tracker.accounts import get_groups as tracker_groups
    merged: dict[str, str] = {}
    for source in (info_groups(), tracker_groups(), music_groups()):
        for gid, gname in source.items():
            if gid and gid not in merged:
                merged[gid] = gname
    return merged


def _save_group_everywhere(gid: str, gname: str) -> None:
    from accounts_manager import save_group as info_save
    from music_account.accounts import save_group as music_save
    from tracker.accounts import save_group as tracker_save
    for save in (info_save, tracker_save, music_save):
        try:
            save(gid, gname)
        except Exception:
            pass


def add_group(gid: str, gname: str) -> tuple[bool, str]:
    gid = (gid or "").strip()
    gname = (gname or "").strip()
    if not GROUP_ID_PATTERN.match(gid):
        return False, "组编号必须为重复数字，如 11、22、33"
    if not gname:
        return False, "请输入组名"
    _save_group_everywhere(gid, gname)
    return True, f"已保存分组：{gid} {gname}"


def delete_group(gid: str) -> None:
    """删除分组：三个功能里同步解绑（账号本身保留）。"""
    from accounts_manager import delete_group as info_delete
    from music_account.accounts import delete_group as music_delete
    from tracker.accounts import delete_group as tracker_delete
    for delete in (info_delete, tracker_delete, music_delete):
        try:
            delete(gid)
        except Exception:
            pass


def move_group(gid: str, delta: int) -> bool:
    from accounts_manager import move_group as info_move
    from music_account.accounts import move_group as music_move
    from tracker.accounts import move_group as tracker_move
    moved = False
    for move in (info_move, tracker_move, music_move):
        try:
            moved = move(gid, delta) or moved
        except Exception:
            pass
    return moved