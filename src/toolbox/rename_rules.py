"""批量改名规则引擎（Qt-free）。

桌面「批量改名」页与 WebUI 预览共用这一份实现：拆分 / 合并文件名，
按「整体 → 替换 → 添加 → 删除 → 序号 → 模板」顺序应用规则。
"""

from __future__ import annotations

import random
import re
from datetime import datetime

_SPECIAL_RE = re.compile(r"[!-/:-@\[-`{-~\s]")
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")


def split_name(filename: str, ignore_ext: bool) -> tuple[str, str]:
    """按需拆分 主名.扩展名。"""
    if not ignore_ext:
        return filename, ""
    dot = filename.rfind(".")
    if dot <= 0:
        return filename, ""
    return filename[:dot], filename[dot + 1:]


def join_name(stem: str, ext: str) -> str:
    return f"{stem}.{ext}" if ext else stem


def apply_rules(stem: str, ext: str, cfg: dict, index: int,
                now: datetime) -> tuple[str, str]:
    """按标签顺序应用全部规则，返回 (新主名, 新扩展名)。"""
    # ---------- 整体 ----------
    whole = cfg.get("whole", {})
    case = whole.get("case", "")
    if case == "upper":
        stem = stem.upper()
    elif case == "lower":
        stem = stem.lower()
    elif case == "capital":
        stem = stem[:1].upper() + stem[1:]
    num_mode = whole.get("num", "")
    if num_mode == "random":
        stem = f"{stem}_{random.randint(1000, 9999)}"
    conv = whole.get("convert", "")
    if conv == "full2half":
        stem = "".join(
            chr(ord(ch) - 0xFEE0) if 0xFF01 <= ord(ch) <= 0xFF5E else ch
            for ch in stem)
    elif conv == "half2full":
        stem = "".join(
            chr(ord(ch) + 0xFEE0) if 33 <= ord(ch) <= 126 else ch
            for ch in stem)
    ext_case = whole.get("ext_case", "")
    if ext_case == "upper" and ext:
        ext = ext.upper()
    elif ext_case == "lower" and ext:
        ext = ext.lower()
    elif ext_case == "delete":
        ext = ""
    new_ext = whole.get("new_ext", "")
    if new_ext:
        ext = new_ext.lstrip(".")

    # ---------- 替换 ----------
    replace = cfg.get("replace", {})
    find, to = replace.get("find", ""), replace.get("to", "")
    if find:
        count = 0 if replace.get("all", True) else 1
        if replace.get("regex", False):
            try:
                stem = re.sub(find, to, stem, count=count)
            except re.error:
                pass
        else:
            if not replace.get("case", False):
                stem = re.sub(re.escape(find), to.replace("\\", "\\\\"), stem,
                              count=count, flags=re.IGNORECASE)
            else:
                stem = (stem.replace(find, to, count) if count
                        else stem.replace(find, to))

    # ---------- 添加 ----------
    add = cfg.get("add", {})
    stem = add.get("prefix", "") + stem + add.get("suffix", "")
    pos_text = add.get("pos_text", "")
    pos_idx = add.get("pos_idx", 0)
    if pos_text:
        if add.get("from_right", False):
            idx = max(len(stem) - pos_idx, 0)
        else:
            idx = min(pos_idx, len(stem))
        stem = stem[:idx] + pos_text + stem[idx:]
    kw = add.get("kw", "")
    if kw:
        after = add.get("kw_after", True)
        found = stem.find(kw)
        if found >= 0:
            cut = found + len(kw) if after else found
            stem = stem[:cut] + add.get("kw_text", "") + stem[cut:]

    # ---------- 删除 ----------
    dele = cfg.get("del", {})
    types = dele.get("types", [])
    if types:
        def _keep(ch: str) -> bool:
            if "space" in types and ch.isspace():
                return False
            if "digit" in types and ch.isdigit():
                return False
            if "lower" in types and ch.islower() and ch.isascii():
                return False
            if "upper" in types and ch.isupper() and ch.isascii():
                return False
            if "chinese" in types and _CHINESE_RE.match(ch):
                return False
            if "special" in types and _SPECIAL_RE.match(ch):
                return False
            return True

        stem = "".join(ch for ch in stem if _keep(ch))
    d_pos = dele.get("pos", 0)
    d_cnt = dele.get("cnt", 0)
    if d_cnt > 0:
        if dele.get("from_right", False):
            stem = (stem[:max(len(stem) - d_pos - d_cnt, 0)]
                    + stem[max(len(stem) - d_pos, 0):])
        else:
            stem = stem[:d_pos] + stem[d_pos + d_cnt:]
    d_kw = dele.get("kw", "")
    if d_kw:
        found = stem.find(d_kw)
        if found >= 0:
            right = dele.get("dir_right", True)
            d_kw_cnt = dele.get("kw_cnt", 0)
            include = dele.get("incl_kw", False)
            if right:
                start = found + len(d_kw) if not include else found
                length = d_kw_cnt + (len(d_kw) if include else 0)
                stem = stem[:start] + stem[start + length:]
            else:
                end = found + len(d_kw) if include else found
                start = max(end - d_kw_cnt, 0)
                stem = stem[:start] + stem[end:]

    # ---------- 序号 ----------
    numbering = cfg.get("num", {})
    if numbering.get("on", False):
        start = numbering.get("start", 1)
        step = numbering.get("step", 1)
        width = numbering.get("width", 1)
        no = start + index * step
        if numbering.get("mode", "suffix") == "suffix":
            stem = f"{stem}{no:0{width}d}"
        else:
            stem = f"{no:0{width}d}_{stem}"

    # ---------- 模板 ----------
    template = cfg.get("template", "")
    if template:
        rep = {
            "{名}": stem, "{name}": stem,
            "{扩展}": ext, "{ext}": ext,
            "{序号}": f"{index + 1:03d}", "{num}": f"{index + 1:03d}",
            "{日期}": now.strftime("%Y-%m-%d"),
            "{date}": now.strftime("%Y-%m-%d"),
            "{时间}": now.strftime("%H%M%S"),
            "{年}": now.strftime("%Y"), "{月}": now.strftime("%m"),
            "{日}": now.strftime("%d"),
        }
        for key, value in rep.items():
            template = template.replace(key, value)
        if ("{ext}" in cfg["template"]) or ("{扩展}" in cfg["template"]):
            ext = ""
        stem = template

    return stem, ext
