"""
导出模块 - 支持 JSON / XLSX / MD 格式
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime
from typing import Callable, Optional

def _sanitize_filename(name: str, max_len: int = 60) -> str:
    """安全文件名（统一实现见 toolbox.utils.sanitize_filename）"""
    from toolbox.utils import sanitize_filename
    return sanitize_filename(name, max_len, "untitled")

def _atomic_write(path: str, write: Callable) -> None:
    """先写同目录唯一临时文件再 os.replace：并行导出同名文件时永不交错写坏。"""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            write(f)
        os.replace(tmp, path)
    except Exception:
        try: os.remove(tmp)
        except OSError: pass
        raise

def _existing_video_id(path: str) -> Optional[str]:
    """读取已导出 json 的视频ID。文件缺失/损坏（旧版并发写坏）/无ID 返回 None。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            data = data[0] if data else {}
        return str(data.get("视频ID") or "") or None
    except Exception:
        return None


def _fmt_duration(sec: int) -> str:
    if not sec: return ""
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"


def _build_stem(info) -> str:
    date_str = ""
    raw_date = info.publish_time or info.fetched_at or ""
    if raw_date: date_str = re.sub(r'[^0-9]', '', raw_date[:10])
    if not date_str or len(date_str) < 8: date_str = datetime.now().strftime("%Y%m%d")
    title_part = _sanitize_filename(info.title) if info.title else "untitled"
    platform_part = info.platform or "unknown"
    return f"{date_str}_{platform_part}_{title_part}"


def _prepare_data(info_list: list) -> list[dict]:
    return [info.to_dict() for info in info_list]


# ========== 微博评论（帖子导出附带聊天记录） ==========

def _comment_rows(comments: list) -> list[dict]:
    """原始评论 dict → 导出用精简行（中文字段）。"""
    from toolbox import weibo_comments as wc
    rows = []
    for c in comments or []:
        if not isinstance(c, dict):
            continue
        rows.append({
            "用户": wc.comment_user(c),
            "内容": wc.comment_text(c),
            "点赞": wc.comment_likes(c),
            "时间": wc.comment_time(c),
            "图片数": len(wc.image_urls(c)),
        })
    return rows


def _comments_by_id(info_list: list, comments_by_id: Optional[dict]) -> Optional[dict]:
    """按条目筛出评论（键为视频ID），无评论返回 None。"""
    if not comments_by_id:
        return None
    out = {}
    for info in info_list:
        cid = str(getattr(info, "video_id", "") or "")
        comments = comments_by_id.get(cid)
        if comments:
            out[cid] = _comment_rows(comments)
    return out or None


# ========== JSON ==========

def export_json(info_list: list, output_path: Optional[str] = None, pretty: bool = True,
                comments_by_id: Optional[dict] = None) -> str:
    data = _prepare_data(info_list)
    rows_by_id = _comments_by_id(info_list, comments_by_id)
    if rows_by_id:
        for d, info in zip(data, info_list):
            rows = rows_by_id.get(str(getattr(info, "video_id", "") or ""))
            if rows:
                d["评论记录"] = rows
    if not output_path:
        output_path = f"video_info_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    _atomic_write(output_path, lambda f: json.dump(
        data, f, ensure_ascii=False, indent=2 if pretty else None))
    return os.path.abspath(output_path)


# ========== XLSX ==========

def export_xlsx(info_list: list, output_path: Optional[str] = None,
                comments_by_id: Optional[dict] = None) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook(); ws = wb.active; ws.title = "视频信息"

    headers = ["序号","平台","视频ID","标题","正文","作者","作者ID",
               "播放/阅览","点赞","评论","分享","收藏",
               "时长","封面URL","链接","发布时间","标签","原始URL","获取时间"]

    # 样式定义
    hf = Font(name="微软雅黑", bold=True, color="FFFFFF", size=11)
    hfill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    ha = Alignment(horizontal="center", vertical="center", wrap_text=True)
    tb = Border(left=Side(style="thin", color="D9D9D9"), right=Side(style="thin", color="D9D9D9"),
                top=Side(style="thin", color="D9D9D9"), bottom=Side(style="thin", color="D9D9D9"))
    even_fill = PatternFill(start_color="F2F7FC", end_color="F2F7FC", fill_type="solid")
    num_fmt = '#,##0'
    center_al = Alignment(horizontal="center", vertical="center")
    left_al = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right_al = Alignment(horizontal="right", vertical="center")

    # 数字列（右对齐+千分位）和链接列
    num_cols = {8, 9, 10, 11, 12}  # 播放/点赞/评论/分享/收藏
    center_cols = {1, 2, 13}  # 序号/平台/时长
    link_cols = {14, 15, 18}  # 封面URL/链接/原始URL

    # 写表头
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = hf; c.fill = hfill; c.alignment = ha; c.border = tb
    ws.row_dimensions[1].height = 28

    # 写数据
    for ri, info in enumerate(info_list, 2):
        vals = [ri-1, info.platform, info.video_id, info.title or "",
                (info.description or "")[:500],
                info.author or "", info.author_id or "",
                info.play_count, info.like_count,
                info.comment_count, info.share_count, info.favorite_count,
                _fmt_duration(info.duration), info.cover_url, info.public_url(),
                info.publish_time or "", ", ".join(info.tags) if info.tags else "",
                info.raw_url or "", info.fetched_at or ""]
        row_fill = even_fill if ri % 2 == 0 else PatternFill()
        for ci, v in enumerate(vals, 1):
            c = ws.cell(row=ri, column=ci, value=v)
            c.border = tb; c.fill = row_fill
            if ci in num_cols:
                c.alignment = right_al
                if isinstance(v, (int, float)) and v > 0:
                    c.number_format = num_fmt
            elif ci in center_cols:
                c.alignment = center_al
            elif ci in link_cols and v and str(v).startswith("http"):
                c.alignment = left_al
                c.hyperlink = str(v)
                c.font = Font(color="0563C1", underline="single", name="微软雅黑")
            else:
                c.alignment = left_al
        ws.row_dimensions[ri].height = 22

    # 列宽
    widths = [5, 8, 16, 36, 30, 14, 14, 12, 10, 10, 10, 10, 7, 30, 30, 16, 18, 30, 16]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    # 微博评论聊天记录（第二个 Sheet）
    rows_by_id = _comments_by_id(info_list, comments_by_id)
    if rows_by_id:
        cs = wb.create_sheet("💬 评论记录")
        cheaders = ["序号", "帖子ID", "用户", "评论内容", "点赞", "时间"]
        for ci, h in enumerate(cheaders, 1):
            c = cs.cell(row=1, column=ci, value=h)
            c.font = hf; c.fill = hfill; c.alignment = ha; c.border = tb
        cs.row_dimensions[1].height = 28
        r = 2
        for info in info_list:
            cid = str(getattr(info, "video_id", "") or "")
            for row in rows_by_id.get(cid, []):
                vals = [r - 1, cid, row["用户"], row["内容"], row["点赞"], row["时间"]]
                for ci, v in enumerate(vals, 1):
                    c = cs.cell(row=r, column=ci, value=v)
                    c.border = tb
                    if ci == 5:
                        c.alignment = right_al
                    elif ci == 1:
                        c.alignment = center_al
                    else:
                        c.alignment = left_al
                cs.row_dimensions[r].height = 22
                r += 1
        for i, w in enumerate([5, 16, 18, 60, 8, 16], 1):
            cs.column_dimensions[get_column_letter(i)].width = w
        cs.freeze_panes = "A2"
        cs.auto_filter.ref = cs.dimensions

    if not output_path:
        output_path = f"video_info_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    d = os.path.dirname(output_path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".xlsx")
    os.close(fd)
    try:
        wb.save(tmp)
        os.replace(tmp, output_path)
    except Exception:
        try: os.remove(tmp)
        except OSError: pass
        raise
    return os.path.abspath(output_path)


# ========== Markdown ==========

def export_md(info_list: list, output_path: Optional[str] = None,
              comments_by_id: Optional[dict] = None) -> str:
    if not output_path:
        output_path = f"video_info_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    rows_by_id = _comments_by_id(info_list, comments_by_id) or {}
    lines = ["# 📊 视频信息报告",
             f"\n> 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             f"> 总计: {len(info_list)} 条\n"]
    groups: dict[str, list] = {}
    for info in info_list: groups.setdefault(info.platform, []).append(info)
    lines.append("## 📑 目录\n")
    for plat, items in groups.items():
        emoji = {"bilibili":"📺","douyin":"🎵","weibo":"📢"}.get(plat,"📄")
        lines.append(f"- {emoji} **{plat.upper()}** ({len(items)} 条)")
    lines.append("")
    for plat, items in groups.items():
        emoji = {"bilibili":"📺","douyin":"🎵","weibo":"📢"}.get(plat,"📄")
        lines.append("---\n"); lines.append(f"## {emoji} {plat.upper()} ({len(items)} 条)\n")
        for i, info in enumerate(items, 1):
            view_label = "阅览量" if info.platform == "weibo" else "播放量"
            lines.append(f"### {i}. {info.title or '无标题'}\n")
            lines.append("| 属性 | 值 |"); lines.append("|------|-----|")
            rows = [("平台", info.platform), ("视频ID", info.video_id),
                    ("作者", f"@{info.author} (ID: {info.author_id})")]
            if info.description: rows.append(("正文", info.description[:500]))
            rows.extend([(view_label, _fmt(info.play_count)),
                         ("点赞数", _fmt(info.like_count)),
                         ("评论数", _fmt(info.comment_count)),
                         ("分享数", _fmt(info.share_count)),
                         ("收藏数", _fmt(info.favorite_count))])
            if info.duration: rows.append(("时长", _fmt_duration(info.duration)))
            rows.append(("发布时间", info.publish_time))
            if info.tags: rows.append(("标签", ", ".join(info.tags)))
            if info.category: rows.append(("分类/地区", info.category))
            if info.music: rows.append(("音乐", info.music))
            rows.extend([("封面URL", info.cover_url), ("链接", info.public_url()), ("原始URL", info.raw_url)])
            for k, v in rows: lines.append(f"| {k} | {v} |")
            comments = rows_by_id.get(str(getattr(info, "video_id", "") or "")) or []
            if comments:
                lines.append("")
                lines.append(f"**💬 评论记录（{len(comments)} 条）**")
                lines.append("")
                for ci, c in enumerate(comments, 1):
                    text = (c["内容"] or "").replace("\n", " ")
                    extra = " ".join(x for x in (c["时间"], f"♥{c['点赞']}" if c["点赞"] else "") if x)
                    lines.append(f"{ci}. {c['用户']}：{text}" + (f"（{extra}）" if extra else ""))
            lines.append("")
    content = "\n".join(lines)
    _atomic_write(output_path, lambda f: f.write(content))
    return os.path.abspath(output_path)


def _fmt(n: int) -> str:
    if n >= 10000: return f"{n/10000:.1f}万 ({n:,})"
    return f"{n:,}"


# ========== 批量导出 ==========



# ========== 按视频独立导出 ==========

EXPORT_DIR = "video_data"

def export_per_video(info_list: list, output_dir: Optional[str] = None, formats: Optional[list[str]] = None,
                     skip_existing: bool = True, comments_pages: int = 0,
                     progress: Optional[Callable] = None) -> tuple[dict[str, list[str]], list[str]]:
    """按视频独立导出。skip_existing=True 时已存在文件跳过不覆盖。

    并行导出（ThreadPoolExecutor，上限 8 线程）：单条导出是 xlsx 渲染/磁盘 IO 密集型，
    条目多时串行极慢，并行后总耗时 ≈ 最慢单条。
    comments_pages > 0 时，微博帖子额外抓取前 N 页评论（聊天记录）：
    评论串行抓取（避免并发触发风控），存为 {stem}_comments.json/_comments.txt，
    并写入同一次导出的 JSON/XLSX/MD；首次补评论会强制重写已存在的帖子文件。
    返回 (results, skipped)：results 为新导出的路径分组，skipped 为已存在被跳过的路径。
    文件被移走/删除/改名后（路径不存在），会重新保存。
    """
    if formats is None: formats = ["json","xlsx","md"]
    base_dir = output_dir or EXPORT_DIR
    os.makedirs(base_dir, exist_ok=True)
    from toolbox.utils import platform_cn, DATA_DIR_SUFFIX

    # 批内同名预分配：不同条目撞同一 stem（B站动态与视频同日同标题最典型——
    # 动态正文常与视频标题一致）时，后到者追加视频ID后缀，
    # 否则并行导出会写同一文件互相覆盖/交错损坏
    taken: dict[str, str] = {}   # stem → 首个占用者的视频ID
    jobs: list[dict] = []
    for info in info_list:
        stem = _build_stem(info)
        my_id = str(info.video_id or "")
        owner = taken.get(stem)
        if owner is not None and owner != my_id:
            stem = f"{stem}_{_sanitize_filename(my_id, 24) or 'x'}"
        elif owner is None:
            taken[stem] = my_id
        # 按平台分文件夹: video_data/抖音_文件/, video_data/B站_文件/ ...（媒体在对应 {平台}_媒体/）
        plat_dir = os.path.join(base_dir, platform_cn(info.platform) + DATA_DIR_SUFFIX)
        # 磁盘同名占位：{stem}.json 属于另一条目（视频ID不同）→ 追加自身ID后缀
        # （分批导出时批内预分配管不到的历史文件）
        jpath = os.path.join(plat_dir, f"{stem}.json")
        if os.path.exists(jpath):
            other = _existing_video_id(jpath)
            if other is not None and other != my_id:
                stem = f"{stem}_{_sanitize_filename(my_id, 24) or 'x'}"
                jpath = os.path.join(plat_dir, f"{stem}.json")
        jobs.append({"info": info, "stem": stem, "plat_dir": plat_dir,
                     "jpath": jpath, "comments": None, "force": False})

    if comments_pages > 0:
        _attach_weibo_comments(jobs, comments_pages, skip_existing, progress)

    def _export_one(job):
        info, stem, plat_dir = job["info"], job["stem"], job["plat_dir"]
        paths: list[str] = []
        skipped_local: list[str] = []
        os.makedirs(plat_dir, exist_ok=True)
        if job["force"]:
            paths.extend(p for p in (
                os.path.join(plat_dir, f"{stem}_comments.json"),
                os.path.join(plat_dir, f"{stem}_comments.txt"),
            ) if os.path.exists(p))
        my_id = str(info.video_id or "")
        jpath = job["jpath"]
        # json 健康且同ID才整体跳过；损坏的旧文件（历史并发写坏）覆盖重写自愈
        same_saved = _existing_video_id(jpath) == my_id
        comments_by_id = ({my_id: job["comments"]}
                          if job["comments"] is not None else None)
        for fmt in formats:
            path = os.path.join(plat_dir, f"{stem}.{fmt}")
            if (skip_existing and os.path.exists(path)
                    and (fmt != "json" or same_saved) and not job["force"]):
                skipped_local.append(path)
                continue
            if fmt == "json":
                export_json([info], path, comments_by_id=comments_by_id)
                raw_path = os.path.join(plat_dir, f"{stem}_raw.json")
                _export_raw_json(info, raw_path)
                paths.append(raw_path)
            elif fmt == "xlsx":
                export_xlsx([info], path, comments_by_id=comments_by_id)
            elif fmt == "md":
                export_md([info], path, comments_by_id=comments_by_id)
            paths.append(path)
        return info.platform, paths, skipped_local

    if len(jobs) > 4:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as ex:
            outcomes = list(ex.map(_export_one, jobs))
    else:
        outcomes = [_export_one(job) for job in jobs]

    results: dict[str, list[str]] = {}
    skipped: list[str] = []
    for plat, paths, skipped_local in outcomes:
        results.setdefault(plat, []).extend(paths)
        skipped.extend(skipped_local)
    return results, skipped


def _attach_weibo_comments(jobs: list[dict], pages: int, skip_existing: bool,
                           progress: Optional[Callable] = None) -> None:
    """串行抓取微博评论：已存在评论文件直接复用，否则抓取并落盘。

    评论抓取是网络请求，不能放进 8 线程并行导出里（容易触发微博风控）；
    某帖抓取失败只影响该帖（打印日志 + 落一份错误说明），帖子本身照常导出。
    """
    from toolbox import weibo_comments as wc
    weibo_jobs = [j for j in jobs if getattr(j["info"], "platform", "") == "weibo"]
    for idx, job in enumerate(weibo_jobs):
        info = job["info"]
        os.makedirs(job["plat_dir"], exist_ok=True)
        cpath = os.path.join(job["plat_dir"], f"{job['stem']}_comments.json")
        saved, saved_pages = None, 0
        if skip_existing and os.path.exists(cpath):
            saved, saved_pages = _load_comments_json(cpath, str(info.video_id or ""))
            # 已抓页数够用才复用；用户把页数调大时重新抓取并重写帖子文件
            if saved is not None and saved_pages >= pages:
                job["comments"] = saved
                continue
        if progress:
            progress(f"抓取评论 {idx + 1}/{len(weibo_jobs)}：{(info.title or info.video_id)[:24]}")
        try:
            comments = wc.fetch_comments(str(info.video_id or ""), pages=pages)
        except Exception as e:
            print(f"[导出] 微博评论抓取失败 {info.video_id}: {e}")
            if saved is not None:
                job["comments"] = saved  # 保留已有评论记录，不因补抓失败丢失
            else:
                _write_comments_error(info, job["stem"], job["plat_dir"], str(e))
        else:
            job["comments"] = comments
            job["force"] = True
            _write_weibo_comments(info, comments, job["stem"], job["plat_dir"], pages)
        if idx < len(weibo_jobs) - 1:
            time.sleep(0.4)


def _load_comments_json(path: str, expected_id: str = "") -> tuple[Optional[list], int]:
    """读取评论文件 → (评论列表, 已抓页数)。评论文件损坏/帖子ID不符返回 (None, 0)。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None, 0
    if isinstance(data, list):
        # 旧格式无页数记录，视为已够用（避免每次导出都重抓）
        return data, 10 ** 9
    if isinstance(data, dict):
        meta = data.get("帖子") if isinstance(data.get("帖子"), dict) else {}
        # 同目录同名 stem 可能被另一帖子占用过：帖子ID 对不上就不复用，重新抓
        if expected_id and str(meta.get("帖子ID") or "") not in ("", expected_id):
            return None, 0
        if isinstance(data.get("评论"), list):
            try:
                pages = int(meta.get("抓取页数") or 0)
            except (TypeError, ValueError):
                pages = 0
            return data["评论"], pages
    return None, 0


def _write_weibo_comments(info, comments: list, stem: str, plat_dir: str,
                          pages: int) -> None:
    """评论原始数据 + 聊天记录文本落盘（与帖子同目录）。"""
    from toolbox import weibo_comments as wc
    meta = {
        "帖子ID": str(info.video_id or ""),
        "标题": info.title or "",
        "链接": info.public_url(),
        "作者": info.author or "",
        "抓取时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "抓取页数": int(pages),
        "评论数": len(comments),
    }
    _atomic_write(os.path.join(plat_dir, f"{stem}_comments.json"),
                  lambda f: json.dump({"帖子": meta, "评论": comments},
                                      f, ensure_ascii=False, indent=2))
    lines = [
        "# 微博评论聊天记录",
        f"帖子：{info.title or info.video_id}",
        f"作者：{info.author or '-'}",
        f"链接：{info.public_url()}",
        f"抓取：{meta['抓取时间']} · 共 {len(comments)} 条",
        "",
    ]
    for c in comments:
        if not isinstance(c, dict):
            continue
        text = wc.comment_text(c).replace("\n", " ")
        ts = wc.comment_time(c)
        likes = wc.comment_likes(c)
        prefix = f"[{ts}] " if ts else ""
        suffix = f"（♥{likes}）" if likes else ""
        lines.append(f"{prefix}{wc.comment_user(c)}：{text}{suffix}")
    _atomic_write(os.path.join(plat_dir, f"{stem}_comments.txt"),
                  lambda f: f.write("\n".join(lines) + "\n"))


def _write_comments_error(info, stem: str, plat_dir: str, error: str) -> None:
    """抓取失败留一份说明（不写 _comments.json，下次导出会重试）。"""
    lines = [
        "# 微博评论聊天记录",
        f"帖子：{info.title or info.video_id}",
        f"链接：{info.public_url()}",
        f"状态：抓取失败",
        f"原因：{error}",
        "",
        "可到「Cookie 管理」登录微博后重新导出重试。",
    ]
    _atomic_write(os.path.join(plat_dir, f"{stem}_comments.txt"),
                  lambda f: f.write("\n".join(lines) + "\n"))


def _export_raw_json(info, path: str):
    raw = info.raw_data if isinstance(info.raw_data, dict) else {}
    _atomic_write(path, lambda f: json.dump(raw, f, ensure_ascii=False, indent=2))


def export_per_video_flat(info_list: list, output_dir: Optional[str] = None, formats: Optional[list[str]] = None,
                          skip_existing: bool = True, comments_pages: int = 0,
                          progress: Optional[Callable] = None) -> tuple[list[str], list[str]]:
    """按视频独立导出并展平。返回 (paths, skipped_paths)。"""
    grouped, skipped = export_per_video(info_list, output_dir, formats, skip_existing,
                                        comments_pages=comments_pages, progress=progress)
    flat: list[str] = []
    for paths in grouped.values(): flat.extend(paths)
    return flat, skipped
