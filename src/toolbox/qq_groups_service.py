"""QQ群人数订阅用例。

数据源：
- 免登录：QQ群资料分享页（qun.qq.com/universal-share），每个群需要各自的分享链接。
- 登录兜底：QQ群登录态下按群号读取官方群资料（qinfo），用于批量订阅预置群。

存储：
- 订阅名单复用 config/fan_groups_targets.json，记录 platform=qqgroup、
  input=<分享链接>、name=<群名>，由 toolbox.fan_groups_task 唯一读写。
- 快照复用 platforms.fan_groups 的 download/粉丝群/QQ群/<群号>/，
  含时间戳 JSON、最新.json 与 Excel。

本模块只做用例编排；页面不得直接拼请求或写订阅文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from platforms import fan_groups as fg
from toolbox import fan_groups_task as task


@dataclass
class QQGroupRow:
    no: str
    name: str
    share_url: str = ""     # 订阅输入：分享链接或群号
    source: str = ""        # share=分享链接，login=登录按群号
    member_count: int | None = None
    fetched_at: str = ""
    status: str = ""
    avatar: str = ""
    change: int | None = None
    is_new: bool = False

    @property
    def member_text(self) -> str:
        return str(self.member_count) if self.member_count is not None else "-"

    @property
    def delta_text(self) -> str:
        if self.is_new:
            return "新"
        if self.change is None:
            return "-"
        if self.change > 0:
            return f"+{self.change}"
        return str(self.change)


def _qq_targets() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for item in task.load_targets():
        if str(item.get("platform") or "") != "qqgroup":
            continue
        try:
            group_no = fg.extract_qq_group_no(item.get("input", ""))
        except ValueError:
            continue
        out[group_no] = item
    return out


def _row_from_target(no: str, default_name: str, target: dict | None) -> QQGroupRow:
    share_url = str((target or {}).get("input") or "").strip()
    source = ""
    if share_url:
        source = "share" if fg.is_qq_share_text(share_url) else "login"
    name = str((target or {}).get("name") or "").strip() or default_name or no
    latest = fg.load_latest("qqgroup", no) if share_url else None
    count = None
    fetched_at = ""
    avatar = ""
    if latest:
        groups = latest.get("groups") or []
        if groups:
            item = groups[0] if isinstance(groups[0], dict) else {}
            name = str(item.get("name") or name)
            count = item.get("member_count")
            avatar = str(item.get("avatar") or "")
        fetched_at = str(latest.get("fetched_at") or "")
    if not share_url:
        status = "待配置（分享链接或登录按群号）"
    elif count is None:
        status = "已按群号订阅，待刷新" if source == "login" else "已订阅，待刷新"
    else:
        status = f"已更新 {fetched_at}" if fetched_at else "已更新"
    return QQGroupRow(no=no, name=name, share_url=share_url, source=source,
                      member_count=count if isinstance(count, int) else None,
                      fetched_at=fetched_at, status=status, avatar=avatar)


def catalog() -> list[QQGroupRow]:
    """官方目录 + 已订阅群合并后的展示行；目录顺序固定，自定义群追加在后面。"""
    targets = _qq_targets()
    rows: list[QQGroupRow] = []
    seen: set[str] = set()
    for item in fg.qq_group_catalog():
        no = str(item.get("no") or "")
        if not no or no in seen:
            continue
        rows.append(_row_from_target(no, str(item.get("name") or ""), targets.get(no)))
        seen.add(no)
    for no, target in targets.items():
        if no in seen:
            continue
        rows.append(_row_from_target(no, "", target))
    return rows


def save_link(target_input: str, name: str = "") -> QQGroupRow:
    """读取分享页或登录态群资料并写入/更新订阅；立即保存一次快照。"""
    group = fg.fetch_qqgroup_any(target_input)
    group_no = group.group_id
    title = (name or "").strip() or group.name or group_no
    target = _qq_targets().get(group_no)
    if target:
        task.update_target("qqgroup", str(target.get("input") or ""),
                           target_input, title)
    else:
        task.add_target("qqgroup", target_input, title)
    previous = fg.load_latest("qqgroup", group_no)
    changes = fg.diff_members([group], previous)
    fg.save_snapshot("qqgroup", group_no, [group])
    change = changes.get(group_no)
    return QQGroupRow(
        no=group_no,
        name=title,
        share_url=target_input,
        source="share" if fg.is_qq_share_text(target_input) else "login",
        member_count=group.member_count,
        fetched_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        status="已更新",
        avatar=group.avatar,
        change=change if isinstance(change, int) else None,
        is_new=previous is None or change is None,
    )


def remove(group_no: str) -> bool:
    """取消订阅；快照文件保留，便于重新订阅后继续对比。"""
    target = _qq_targets().get(str(group_no or "").strip())
    if not target:
        return False
    return task.remove_target("qqgroup", str(target.get("input") or ""))


def refresh_all(progress=None, cancel=None) -> dict:
    """刷新全部已订阅群；单个失败不阻断其他群。"""
    targets = _qq_targets()
    total = len(targets)
    results: list[QQGroupRow] = []
    errors: list[tuple[str, str]] = []
    cancelled = False
    for index, (group_no, target) in enumerate(targets.items(), 1):
        if cancel is not None and cancel.is_set():
            cancelled = True
            break
        label = str(target.get("name") or "").strip() or group_no
        if progress:
            progress(index, total, label)
        try:
            _, groups = fg.fetch_groups("qqgroup", str(target.get("input") or ""))
            group = groups[0]
            previous = fg.load_latest("qqgroup", group.group_id)
            changes = fg.diff_members(groups, previous)
            fg.save_snapshot("qqgroup", group.group_id, groups)
            change = changes.get(group.group_id)
            results.append(QQGroupRow(
                no=group.group_id,
                name=group.name or label,
                share_url=str(target.get("input") or ""),
                source=("share" if fg.is_qq_share_text(str(target.get("input") or ""))
                        else "login"),
                member_count=group.member_count,
                fetched_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                status="已更新",
                avatar=group.avatar,
                change=change if isinstance(change, int) else None,
                is_new=previous is None or change is None,
            ))
        except Exception as exc:  # noqa: BLE001 - 单个群失败不影响其他群
            errors.append((label, str(exc)))
    return {"total": total, "ok": len(results), "errors": errors,
            "results": results, "cancelled": cancelled}


def subscribe_catalog_by_login(progress=None, cancel=None) -> dict:
    """登录态下按群号订阅官方目录中的群；已有分享链接的群不覆盖。"""
    targets = _qq_targets()
    catalog = fg.qq_group_catalog()
    results: list[QQGroupRow] = []
    errors: list[tuple[str, str]] = []
    cancelled = False
    for index, item in enumerate(catalog, 1):
        if cancel is not None and cancel.is_set():
            cancelled = True
            break
        no = str(item.get("no") or "").strip()
        name = str(item.get("name") or "").strip() or no
        if not no:
            continue
        existing = targets.get(no)
        if existing and fg.is_qq_share_text(str(existing.get("input") or "")):
            continue
        if progress:
            progress(index, len(catalog), name)
        try:
            group = fg.fetch_qqgroup_by_cookie(no)
            title = group.name or name
            if existing:
                task.update_target("qqgroup", str(existing.get("input") or ""),
                                   no, title)
            else:
                task.add_target("qqgroup", no, title)
            previous = fg.load_latest("qqgroup", no)
            changes = fg.diff_members([group], previous)
            fg.save_snapshot("qqgroup", no, [group])
            change = changes.get(no)
            results.append(QQGroupRow(
                no=no, name=title, share_url=no, source="login",
                member_count=group.member_count,
                fetched_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                status="已更新", avatar=group.avatar,
                change=change if isinstance(change, int) else None,
                is_new=previous is None or change is None,
            ))
        except Exception as exc:  # noqa: BLE001 - 单个群失败不影响其他群
            errors.append((name, str(exc)))
    return {"total": len(catalog), "ok": len(results), "errors": errors,
            "results": results, "cancelled": cancelled}


def export_rows(rows: list[QQGroupRow]) -> Path:
    """把当前展示行导出为 Excel（复用粉丝群导出格式）。"""
    groups = []
    changes: dict[str, int | None] = {}
    for row in rows:
        groups.append(fg.FanGroup(
            platform="qqgroup",
            group_id=row.no,
            name=row.name,
            member_count=row.member_count,
            group_no=row.no,
            avatar=row.avatar,
            entry_limit=row.status,
        ))
        if row.is_new:
            changes[row.no] = None
        elif row.change is not None:
            changes[row.no] = row.change
    path = fg.group_dir("qqgroup", "all", create=False) / (
        f"QQ群人数_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    return fg.export_xlsx("qqgroup", "all", groups, changes, path=path)


def export_dir() -> Path:
    return fg.group_dir("qqgroup", "all", create=False)
