# -*- coding: utf-8 -*-
"""资产目录分组聚合（L3）。

把 download 分类目录的文件按「一次导出的产物」聚合成条目，供资产页展示。
不依赖 Qt；页面只传入根目录与分类名，拿回分组列表与是否截断。
"""

from __future__ import annotations

import os
import re
from datetime import datetime

from toolbox.asset_scan import AUDIO_EXTS, IMAGE_EXTS, SCAN_SKIP_DIRS, VIDEO_EXTS

# 分类目录 → (聚合方式, 深度)
#   dir       按相对路径前 N 段目录聚合成一条（账号/文章、平台/视频）
#   stem      每个产物集合一条（目录 + 去附属后缀的文件主干）
#   stem_flat 每个产物集合一条（忽略子目录，腾讯文档报表/表格/正文合并）
#   tcdoc     腾讯文档：忽略子目录按文档聚合，图片/ 下取文档目录名与产物合并
#   danmaku   弹幕：房间号 + 日期一条（同房间同天的 csv/zip/jsonl 合并）
# 未登记的分类保持旧行为：按第一层目录聚合。
GROUP_RULES = {
    "公众号文章": ("dir", 2),
    "评论区": ("dir", 2),
    "B站资源": ("dir", 2),
    "淘宝商品": ("dir", 1),
    "票务演出": ("dir", 1),
    "抖音商品": ("dir", 1),
    "抖音指数": ("dir", 1),
    "音乐信息": ("dir", 1),
    "直播录制": ("stem", 0),
    "歌曲文件": ("stem", 0),
    "音乐账号数据": ("stem", 0),
    "账号追踪数据": ("stem", 0),
    "导出的图片": ("stem", 0),
    "字幕文件": ("stem", 0),
    "腾讯文档数据": ("tcdoc", 0),
    "封面底图": ("stem", 0),
    "套版封面": ("stem", 0),
    "图片裁切": ("stem", 0),
    "弹幕文件": ("danmaku", 0),
    "弹幕文件B站": ("danmaku", 0),
}


def group_stem(fn: str) -> str:
    """文件名 → 产物主干（去掉封面/弹幕/raw/live 等附属后缀）。"""
    s = os.path.splitext(fn or "")[0]
    while True:
        s2 = re.sub(r"_(封面|弹幕|raw)$", "", s, flags=re.I)
        s2 = re.sub(r"_live\d*$", "", s2, flags=re.I)
        if s2 == s:
            break
        s = s2
    return s or fn


def extract_date(text) -> str:
    """从标题/文件名提取 YYYYMMDD（兼容 20260923 与 2026-09-23）。

    只认独立成段且月/日合法的日期：商品 ID、视频 ID 这类长数字里
    随机出现的 20xxxxxx 不再被当成日期（否则排序和分组都会错）。
    """
    t = text or ""

    def _ok(y, m, d):
        return 1 <= int(m) <= 12 and 1 <= int(d) <= 31

    m = re.match(r"^(20\d{2})(\d{2})(\d{2})(?!\d)", t)
    if m and _ok(*m.groups()):
        return "".join(m.groups())
    m = re.search(r"(?<!\d)(20\d{2})-(\d{2})-(\d{2})(?!\d)", t)
    if m and _ok(*m.groups()):
        return "".join(m.groups())
    m = re.search(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", t)
    if m and _ok(*m.groups()):
        return "".join(m.groups())
    return ""


def load_dir_groups(root, limit_files: int = 20000, cat: str = "",
                    on_progress=None) -> tuple[list, bool]:
    """扫描 root 并按分类规则聚合，返回 (groups, truncated)。

    limit_files 兜底防误选超大目录把内存打满；truncated=True 表示已截断。
    on_progress(count) 每扫描若干文件回调一次，回调抛异常即中止扫描
    （供后台任务展示进度与响应取消）。
    """
    root = str(root)
    cat = cat or os.path.basename(root.rstrip("\\/"))
    files_scan, top_dirs = [], {}
    total = 0
    if on_progress is not None:
        on_progress(0)

    def scan_error(error):
        raise error

    try:
        os.stat(root)
    except FileNotFoundError:
        return [], False
    for dirpath, dirnames, filenames in os.walk(root, onerror=scan_error):
        dirnames[:] = sorted(d for d in dirnames if d.lower() not in SCAN_SKIP_DIRS)
        rel = os.path.relpath(dirpath, root)
        rel = "" if rel in (".", "") else rel.replace(os.sep, "/")
        if rel:
            head = rel.split("/")[0]
            top_dirs.setdefault(head.lower(), head)
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue  # .stem_owners.json 等内部文件不参与聚合
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except FileNotFoundError:
                continue
            ext = os.path.splitext(fn)[1].lower()
            kind = ("image" if ext in IMAGE_EXTS else
                    "video" if ext in VIDEO_EXTS else
                    "audio" if ext in AUDIO_EXTS else "other")
            files_scan.append((rel, fn, full, st, kind))
            total += 1
            if on_progress is not None and total % 200 == 0:
                on_progress(total)
            if limit_files and total >= limit_files:
                break
        if limit_files and total >= limit_files:
            break

    truncated = bool(limit_files and total >= limit_files)

    rule_kind, rule_depth = GROUP_RULES.get(cat, ("dir", 1))
    buckets, order = {}, []
    for rel, fn, full, st, kind in files_scan:
        title = ""
        group_root = root
        if rule_kind in ("stem", "stem_flat", "tcdoc"):
            if rule_kind == "tcdoc" and rel.startswith("图片/"):
                # 图片按文档目录聚合：图片/<文档目录>/<工作表>/<字段>/<文件>
                parts = rel.split("/")
                stem = parts[1] if len(parts) > 1 and parts[1] else os.path.splitext(fn)[0]
            else:
                stem = group_stem(fn)
            # stem_flat/tcdoc：忽略子目录合并（腾讯文档的报表/表格/正文/图片同文档）
            key = f"{rel}/{stem}" if rel and rule_kind == "stem" else stem
            title = stem
            group_root = os.path.dirname(full)
        elif rule_kind == "danmaku":
            stem = os.path.splitext(fn)[0]
            date = extract_date(stem) or extract_date(rel)
            room = re.search(r"\d{6,}", stem)
            key = f"{room.group(0)}_{date}" if room and date else stem
            title = key
            group_root = os.path.dirname(full)
        elif rel:
            parts = rel.split("/")
            take = min(rule_depth, len(parts))
            key = "/".join(parts[:take])
            title = parts[take - 1]
            group_root = os.path.join(root, key)
        else:
            # 根目录下的单文件：与同层产物目录同名才合并（旧版公众号把
            # docx/html/pdf 放分类根、md/图片在目录内），否则各自成条
            stem = os.path.splitext(fn)[0]
            key = top_dirs.get(stem.lower()) or stem
            title = key
            group_root = os.path.join(root, key)
        g = buckets.get(key)
        if g is None:
            g = buckets[key] = {
                "title": title or key, "files": [], "dirs": set(),
                "n_img": 0, "n_vid": 0, "n_aud": 0, "n_doc": 0,
                "size": 0, "mtime": "",
                "cover": "", "root_dir": group_root,
                "date": extract_date(title or key),
            }
            order.append(key)
        g["files"].append({
            "path": full, "name": fn, "size": st.st_size, "type": kind, "rel_dir": rel,
        })
        g["dirs"].add(rel)
        g["size"] += st.st_size
        mt = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        if mt > g["mtime"]:
            g["mtime"] = mt
        if kind == "image":
            g["n_img"] += 1
            if not g["cover"]:
                g["cover"] = full
        elif kind == "video":
            g["n_vid"] += 1
        elif kind == "audio":
            g["n_aud"] += 1
        else:
            g["n_doc"] += 1

    groups = [buckets[k] for k in order]
    for g in groups:
        g["dirs"] = sorted(g["dirs"])
        if not os.path.isdir(str(g["root_dir"])):
            g["root_dir"] = root      # 没有同名目录 → 打开分类根
        if not g["date"]:
            g["date"] = extract_date(os.path.basename(str(g["root_dir"])))
        if not g["date"]:
            # 账号目录没有日期前缀：从组内文件名取最新日期（公众号新归档结构）
            dates = [extract_date(f.get("name") or "") for f in g["files"]]
            dates = [d for d in dates if d]
            if dates:
                g["date"] = max(dates)
    # 日期倒序（无日期回退最后修改时间），与条目视图的「时间倒序」口径一致
    groups.sort(key=lambda g: (g["date"] or "", g["mtime"]), reverse=True)
    return groups, truncated
