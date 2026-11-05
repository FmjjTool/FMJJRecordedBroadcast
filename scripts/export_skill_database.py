#!/usr/bin/env python3
"""把腾讯文档「封茗囧菌数据汇总库」快照导出到内置 Skill 的 references/database。

用法：
  python scripts/export_skill_database.py

数据来源：
  download/腾讯文档数据/tables.db            智能表格逐行数据（最新同步）

输出（仅 Markdown）：
  src/skills/fengming-livestream-summary/references/database/
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC_KEY = "DRnd3bkhkQUR1aEJr"
DOC_URL = "https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr"
DEFAULT_OUT = ROOT / "src/skills/fengming-livestream-summary/references/database"


def text(value) -> str:
    return "" if value is None else str(value).strip()


def line(value) -> str:
    return " ".join(text(value).split())


def cell(value) -> str:
    return line(value).replace("|", "｜")


def safe_block(value: str) -> str:
    """转义行首 #，避免历史摘要里的 Markdown 标题破坏文档结构。"""
    return "\n".join(
        ("\\" + raw) if raw.startswith("#") else raw
        for raw in value.splitlines()
    )


def fields(*parts: str) -> str:
    return "｜".join(p for p in parts if p)


def song_title(name: str) -> str:
    if not name:
        return ""
    return name if name.startswith("《") else f"《{name}》"


def year_key(date_str: str) -> str:
    value = text(date_str)
    return value[:4] if len(value) >= 4 and value[:4].isdigit() else "未知"


def month_key(date_str: str) -> str:
    value = text(date_str)
    return value[:7] if len(value) >= 7 and value[4] == "-" else year_key(value)


def load_rows(con: sqlite3.Connection, tab_name: str) -> list[dict]:
    row = con.execute(
        "select tab_id from sheets where doc_key=? and tab_name=?",
        (DOC_KEY, tab_name),
    ).fetchone()
    if not row:
        return []
    return [
        json.loads(data)
        for (data,) in con.execute(
            "select data_json from rows where doc_key=? and tab_id=?",
            (DOC_KEY, row[0]),
        )
    ]


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def write_songs(rows: list[dict], out: Path) -> None:
    by_year: dict[str, list[dict]] = defaultdict(list)
    for d in rows:
        by_year[year_key(d.get("首发日期"))].append(d)

    index = [
        f"# 完整歌曲索引（{len(rows)} 首）",
        "",
        "> 来源：腾讯文档「封茗囧菌数据汇总库」·「完整歌曲」表。",
        "> 按首发年份分文件存放；查某首歌先在本索引定位年份，再读 `songs/<年份>.md`。",
        "> 字段：歌曲名｜演唱者｜首发日期｜时长｜作词｜作曲｜出品方｜所属专辑｜作品性质｜音乐类型｜语言｜合唱形式｜MV｜备注。",
        "> 本快照不含歌词、封面图与平台链接。",
        "",
    ]
    for year in sorted(by_year, reverse=True):
        index.append(f"- [{year}（{len(by_year[year])} 首）](songs/{year}.md)")
    write(out / "songs.md", "\n".join(index))

    for year, items in by_year.items():
        lines = [
            f"# 完整歌曲 {year}（{len(items)} 首）",
            "",
            "> 来源：腾讯文档「封茗囧菌数据汇总库」·「完整歌曲」表。",
            "",
        ]
        for d in sorted(
            items,
            key=lambda x: (text(x.get("首发日期")), text(x.get("歌曲名称"))),
            reverse=True,
        ):
            lines.append(
                "- "
                + fields(
                    song_title(cell(d.get("歌曲名称"))),
                    cell(d.get("演唱者")),
                    cell(d.get("首发日期")),
                    cell(d.get("歌曲时长")),
                    f"作词：{cell(d.get('作词人'))}" if text(d.get("作词人")) else "",
                    f"作曲：{cell(d.get('作曲人'))}" if text(d.get("作曲人")) else "",
                    f"出品：{cell(d.get('出品方'))}" if text(d.get("出品方")) else "",
                    f"专辑：{cell(d.get('所属专辑'))}"
                    if text(d.get("所属专辑"))
                    else "",
                    cell(d.get("作品性质")),
                    cell(d.get("音乐类型")),
                    cell(d.get("语言")),
                    cell(d.get("合唱形式")),
                    cell(d.get("MV")),
                    f"备注：{cell(d.get('备注信息'))}"
                    if text(d.get("备注信息"))
                    else "",
                )
            )
        write(out / "songs" / f"{year}.md", "\n".join(lines))


def write_song_clips(rows: list[dict], out: Path) -> None:
    lines = [
        f"# 歌曲片段（{len(rows)} 条）",
        "",
        "> 来源：腾讯文档「封茗囧菌数据汇总库」·「歌曲片段」表。",
        "> 字段：作品名｜参与者｜类型｜片段时长｜发布时间｜平台类型｜原唱｜备注。",
        "",
    ]
    for d in sorted(
        rows,
        key=lambda x: (text(x.get("发布时间")), text(x.get("作品名称"))),
        reverse=True,
    ):
        lines.append(
            "- "
            + fields(
                song_title(cell(d.get("作品名称"))),
                cell(d.get("参与者")),
                cell(d.get("作品类型")),
                cell(d.get("片段时长")),
                cell(d.get("发布时间")),
                cell(d.get("平台类型")),
                f"原唱：{cell(d.get('原作艺人'))}" if text(d.get("原作艺人")) else "",
                f"备注：{cell(d.get('备注信息'))}" if text(d.get("备注信息")) else "",
            )
        )
    write(out / "song-clips.md", "\n".join(lines))


def write_live(rows: list[dict], out: Path) -> None:
    by_month: dict[str, list[dict]] = defaultdict(list)
    for d in rows:
        by_month[month_key(d.get("开始时间"))].append(d)

    index = [
        f"# 直播记录索引（{len(rows)} 场）",
        "",
        "> 来源：腾讯文档「封茗囧菌数据汇总库」·「直播记录」表。",
        "> 按「年-月」分文件存放；每场包含时间、平台、标签、回放链接与库内 AI 摘要。",
        "> 查某场直播：先在本索引定位月份，再读 `live/<年-月>.md`（单文件通常几十 KB，避免整年读取）。",
        "",
    ]
    current_year = ""
    for key in sorted(by_month, reverse=True):
        year = key[:4]
        if year != current_year:
            index.extend(["", f"## {year} 年"])
            current_year = year
        index.append(f"- [{key}（{len(by_month[key])} 场）](live/{key}.md)")
    write(out / "live-records.md", "\n".join(index))

    for key, items in by_month.items():
        lines = [
            f"# 直播记录 {key}（{len(items)} 场）",
            "",
            "> 来源：腾讯文档「封茗囧菌数据汇总库」·「直播记录」表。",
            "> 本文件是历史存档；生成新总结时不得把旧场次内容混入。",
            "",
        ]
        for d in sorted(items, key=lambda x: text(x.get("开始时间")), reverse=True):
            start = cell(d.get("开始时间")) or "日期未记录"
            end = cell(d.get("结束时间"))
            title = cell(d.get("直播标题")) or "（无标题）"
            lines.append(f"## {start}｜{title}")
            lines.append(
                "- "
                + fields(
                    f"平台：{cell(d.get('直播平台'))}"
                    if text(d.get("直播平台"))
                    else "",
                    f"标签：{cell(d.get('直播标签'))}"
                    if text(d.get("直播标签"))
                    else "",
                    f"时段：{start}" + (f" 至 {end}" if end and end != start else ""),
                    f"时长：{cell(d.get('直播时长'))}"
                    if text(d.get("直播时长"))
                    else "",
                )
            )
            links = [
                text(d.get(k))
                for k in ("直播链接1", "直播链接2", "直播链接3")
                if text(d.get(k))
            ]
            if links:
                lines.append("- 回放：" + " ".join(links))
            summary = text(d.get("AI 摘要"))
            if summary:
                lines.extend(["- AI 摘要：", "", safe_block(summary), ""])
            note = line(d.get("备注信息"))
            if note:
                lines.append(f"- 备注：{note}")
            lines.append("")
        write(out / "live" / f"{key}.md", "\n".join(lines))


def write_offline_events(rows: list[dict], out: Path) -> None:
    by_year: dict[str, list[dict]] = defaultdict(list)
    for d in rows:
        by_year[year_key(d.get("活动日期"))].append(d)

    index = [
        f"# 线下活动索引（{len(rows)} 场）",
        "",
        "> 来源：腾讯文档「封茗囧菌数据汇总库」·「线下活动」表。",
        "> 按活动年份分文件存放；查某场活动先定位年份，再读 `offline-events/<年份>.md`。",
        "",
    ]
    for year in sorted(by_year, reverse=True):
        index.append(f"- [{year}（{len(by_year[year])} 场）](offline-events/{year}.md)")
    write(out / "offline-events.md", "\n".join(index))

    for year, items in by_year.items():
        lines = [
            f"# 线下活动 {year}（{len(items)} 场）",
            "",
            "> 来源：腾讯文档「封茗囧菌数据汇总库」·「线下活动」表。",
            "",
        ]
        for d in sorted(
            items,
            key=lambda x: (text(x.get("活动日期")), text(x.get("活动名称"))),
            reverse=True,
        ):
            date = cell(d.get("活动日期")) or "日期未记录"
            name = cell(d.get("活动名称")) or "（无名称）"
            lines.append(f"## {date}｜{name}")
            lines.append(
                "- "
                + fields(
                    f"城市：{cell(d.get('省份'))} {cell(d.get('城市'))}".strip(),
                    f"场馆：{cell(d.get('场馆名称'))}"
                    if text(d.get("场馆名称"))
                    else "",
                    f"地址：{cell(d.get('场馆地址'))}"
                    if text(d.get("场馆地址"))
                    else "",
                )
            )
            lines.append(
                "- "
                + fields(
                    f"类型：{cell(d.get('活动类型'))}"
                    if text(d.get("活动类型"))
                    else "",
                    f"状态：{cell(d.get('活动状态'))}"
                    if text(d.get("活动状态"))
                    else "",
                    f"舞台：{cell(d.get('舞台环节'))}"
                    if text(d.get("舞台环节"))
                    else "",
                    f"签售：{cell(d.get('签售环节'))}"
                    if text(d.get("签售环节"))
                    else "",
                )
            )
            for label, key in (
                ("演唱曲目", "演唱曲目"),
                ("标准化曲目", "标准化曲目"),
                ("活动歌单", "活动歌单"),
                ("特邀嘉宾", "特邀嘉宾"),
                ("嘉宾参与内容", "嘉宾参与内容"),
                ("票档价格", "票档价格"),
                ("票档数量", "票档数量"),
                ("关联周边", "关联周边"),
                ("附加活动", "附加活动"),
                ("备注", "备注信息"),
                ("官方群", "官方群"),
            ):
                value = line(d.get(key))
                if value:
                    lines.append(f"- {label}：{value}")
            links = [
                text(d.get(k))
                for k in ("购票链接 1", "购票链接 2", "购票链接 3")
                if text(d.get(k))
            ]
            if links:
                lines.append("- 购票链接：" + " ".join(links))
            for key in ("现场LIVE 1", "现场LIVE 2", "现场LIVE 3"):
                value = text(d.get(key))
                if value:
                    lines.append(f"- {key.replace('LIVE', '现场 LIVE')}：{value}")
            lines.append("")
        write(out / "offline-events" / f"{year}.md", "\n".join(lines))


def write_merch(rows: list[dict], out: Path) -> None:
    lines = [
        f"# 周边图鉴（{len(rows)} 条）",
        "",
        "> 来源：腾讯文档「封茗囧菌数据汇总库」·「周边图鉴」表。",
        "> 字段：首发日期｜周边名称｜数量｜获取方式｜定价｜关联活动｜来源渠道｜备注。",
        "",
    ]
    for d in sorted(
        rows,
        key=lambda x: (text(x.get("首发日期")), text(x.get("周边名称"))),
        reverse=True,
    ):
        lines.append(
            "- "
            + fields(
                cell(d.get("首发日期")),
                cell(d.get("周边名称")),
                f"{cell(d.get('周边数量'))} 件" if text(d.get("周边数量")) else "",
                f"获取：{cell(d.get('获取方式'))}"
                if text(d.get("获取方式"))
                else "",
                f"定价：{cell(d.get('定价信息'))}"
                if text(d.get("定价信息"))
                else "",
                f"关联：{cell(d.get('关联活动'))}"
                if text(d.get("关联活动"))
                else "",
                f"来源：{cell(d.get('来源渠道'))}"
                if text(d.get("来源渠道"))
                else "",
                f"备注：{cell(d.get('备注信息'))}"
                if text(d.get("备注信息"))
                else "",
            )
        )
    write(out / "merch.md", "\n".join(lines))


def write_awards(rows: list[dict], out: Path) -> None:
    lines = [
        f"# 奖讯热榜（{len(rows)} 条）",
        "",
        "> 来源：腾讯文档「封茗囧菌数据汇总库」·「奖讯热榜」表。",
        "",
    ]
    for d in sorted(rows, key=lambda x: text(x.get("获奖日期")), reverse=True):
        lines.append(
            f"## {cell(d.get('获奖日期'))}｜{cell(d.get('赛事名称'))}"
            f"｜{cell(d.get('二级分类'))}｜{cell(d.get('获奖等级'))}"
        )
        for label, key in (
            ("同属榜单", "同属榜单"),
            ("原文引用", "原文引用"),
        ):
            value = line(d.get(key))
            if value:
                lines.append(f"- {label}：{value}")
        for label, key in (("原地址", "原地址"), ("获奖链接", "获奖链接")):
            value = text(d.get(key))
            if value:
                lines.append(f"- {label}：{value}")
        lines.append("")
    write(out / "awards.md", "\n".join(lines))


def write_updates(rows: list[dict], out: Path) -> None:
    lines = [
        f"# 更新日志（{len(rows)} 条）",
        "",
        "> 来源：腾讯文档「封茗囧菌数据汇总库」·「更新日志」表。",
        "",
    ]
    for d in sorted(rows, key=lambda x: text(x.get("更新日期")), reverse=True):
        lines.append(
            "- "
            + fields(
                cell(d.get("更新日期")),
                f"v{cell(d.get('版本号'))}" if text(d.get("版本号")) else "",
                f"模块：{cell(d.get('更新模块'))}"
                if text(d.get("更新模块"))
                else "",
                cell(d.get("更新摘要")),
            )
        )
    write(out / "updates.md", "\n".join(lines))


def write_official(rows: list[dict], out: Path) -> None:
    by_month: dict[str, list[dict]] = defaultdict(list)
    for d in rows:
        by_month[month_key(d.get("发布时间"))].append(d)

    for key, items in by_month.items():
        lines = [
            f"# 官方动态 {key}（{len(items)} 条）",
            "",
            "> 来源：腾讯文档「封茗囧菌数据汇总库」·「官方动态」表。",
            "> 字段：日期｜平台｜账号｜类型｜标题｜内容｜链接。",
            "",
        ]
        for d in sorted(items, key=lambda x: text(x.get("发布时间")), reverse=True):
            content = line(d.get("作品内容"))
            title = cell(d.get("作品标题"))
            if content and content != title:
                body = f"{title}｜内容：{content}" if title else f"内容：{content}"
            else:
                body = title or content
            link = text(d.get("作品链接")) or text(d.get("动态链接(b站)"))
            lines.append(
                "- "
                + fields(
                    cell(d.get("发布时间")) or "日期未记录",
                    cell(d.get("发布平台")),
                    cell(d.get("发布账号")),
                    cell(d.get("动态类型")),
                    body,
                    link,
                )
            )
        write(out / "official-posts" / f"{key}.md", "\n".join(lines))

    index = [
        f"# 官方动态索引（{len(rows)} 条）",
        "",
        "> 来源：腾讯文档「封茗囧菌数据汇总库」·「官方动态」表。",
        "> 按「年-月」分文件存放；用于核对官宣、新歌、联动、活动等公开信息。",
        "> 查某段时间的动态：先在本索引定位月份，再读 `official-posts/<年-月>.md`。",
        "",
    ]
    keys = [k for k in sorted(by_month, reverse=True) if k != "未知"]
    if "未知" in by_month:
        keys.append("未知")
    current_year = ""
    for key in keys:
        year = key[:4]
        heading = "日期未知" if year == "未知" else f"{year} 年"
        if heading != current_year:
            index.extend(["", f"## {heading}"])
            current_year = heading
        index.append(f"- [{key}（{len(by_month[key])} 条）](official-posts/{key}.md)")
    write(out / "official-posts.md", "\n".join(index))


def write_readme(out: Path, counts: dict[str, int], synced_at: str) -> None:
    lines = [
        "# 封茗囧菌数据汇总库（内置快照）",
        "",
        f"- 数据来源：腾讯文档「封茗囧菌数据汇总库」 {DOC_URL}",
        f"- 内容同步：{synced_at}",
        "- 本快照只包含 Markdown 分表，不含采集人数统计、封面图与平台链接。",
        "- 检索顺序：先读本索引 → 再按需读分表；禁止整库通读。",
        "",
        "## 文件索引",
        "",
        f"- `songs.md`：完整歌曲索引 {counts['songs']} 首（按首发年份分文件，正文在 `songs/<年份>.md`）",
        f"- `song-clips.md`：歌曲片段 {counts['clips']} 条（单文件）",
        f"- `live-records.md`：直播记录索引 {counts['live']} 场（按年-月分文件，正文在 `live/<年-月>.md`）",
        f"- `offline-events.md`：线下活动索引 {counts['events']} 场（按年份分文件，正文在 `offline-events/<年份>.md`）",
        f"- `merch.md`：周边图鉴 {counts['merch']} 条（单文件）",
        f"- `awards.md`：奖讯热榜 {counts['awards']} 条（单文件）",
        f"- `updates.md`：数据库更新日志 {counts['updates']} 条（单文件）",
        f"- `official-posts.md`：官方动态索引 {counts['posts']} 条（按年-月分文件，正文在 `official-posts/<年-月>.md`）",
        "",
        "## 使用建议",
        "",
        "1. 纠错/核对歌名、词曲、专辑、首发日期 → `songs.md` 定位年份，再读 `songs/<年份>.md`；歌曲片段 → `song-clips.md`。",
        "2. 查某场历史直播的日期、标题、平台、回放与库内 AI 摘要 → `live-records.md` 定位年-月，再读 `live/<年-月>.md`。",
        "3. 查线下活动、演唱会、签售、演唱曲目 → `offline-events.md` 定位年份，再读 `offline-events/<年份>.md`。",
        "4. 查周边、定价、获取方式 → `merch.md`。",
        "5. 核对官宣、新歌、联动、活动预告 → `official-posts.md` 定位年-月，再读 `official-posts/<年-月>.md`。",
        "6. 查奖项、榜单 → `awards.md`；查数据库版本变更 → `updates.md`。",
        "",
        "## 快照边界",
        "",
        "- 「囧菌百科」在腾讯文档中是 grid 页，当前采集器不支持该类型，本快照不含该表；个人基础档案以用户上传文件、直播转录和官方数据源为准。",
        "- 本快照不含歌词、封面图、B站/QQ音乐/网易云等平台链接；需要这些信息时请用户上传库文件或联网核验。",
        "- 本快照是离线副本，可能晚于腾讯文档在线库；若用户另行上传了更新的库文件，以用户文件为准。",
        "- 库内 AI 摘要为历史总结存档，可作为格式与既有信息的参考，但不得把旧场次内容混入本次总结。",
    ]
    write(out / "README.md", "\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "download/腾讯文档数据/tables.db"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    db_path = Path(args.db)
    out = Path(args.out)
    if not db_path.exists():
        raise SystemExit(f"表格数据库不存在: {db_path}")

    con = sqlite3.connect(db_path)
    sheets = dict(
        con.execute(
            "select tab_name, fetched_at from sheets where doc_key=?", (DOC_KEY,)
        )
    )
    songs = load_rows(con, "完整歌曲")
    clips = load_rows(con, "歌曲片段")
    live = load_rows(con, "直播记录")
    events = load_rows(con, "线下活动")
    merch = load_rows(con, "周边图鉴")
    awards = load_rows(con, "奖讯热榜")
    updates = load_rows(con, "更新日志")
    posts = load_rows(con, "官方动态")
    con.close()

    out.mkdir(parents=True, exist_ok=True)
    for name in ("songs", "live", "offline-events", "official-posts"):
        shutil.rmtree(out / name, ignore_errors=True)
    for name in (
        "README.md",
        "songs.md",
        "song-clips.md",
        "live-records.md",
        "offline-events.md",
        "merch.md",
        "awards.md",
        "updates.md",
        "official-posts.md",
        "collection-stats.md",
    ):
        (out / name).unlink(missing_ok=True)
    for path in out.rglob("*"):
        if path.is_file() and path.suffix.lower() != ".md":
            path.unlink()

    write_songs(songs, out)
    write_song_clips(clips, out)
    write_live(live, out)
    write_offline_events(events, out)
    write_merch(merch, out)
    write_awards(awards, out)
    write_updates(updates, out)
    write_official(posts, out)

    synced_at = max(sheets.values()) if sheets else "未知"
    counts = {
        "songs": len(songs),
        "clips": len(clips),
        "live": len(live),
        "events": len(events),
        "merch": len(merch),
        "awards": len(awards),
        "updates": len(updates),
        "posts": len(posts),
    }
    write_readme(out, counts, synced_at)
    print(f"已导出到 {out}")
    for key, value in counts.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
