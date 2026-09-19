"""三大视频平台评论区共用层：筛选 / 保存 / 长图导出。

被 toolbox/{douyin,bili,kuaishou}_comments 复用（duck-typing 后端），
后端需提供字段提取器与常量：
  image_urls / avatar_url / comment_text / comment_user / comment_likes /
  comment_time / comment_ts / comment_extra / IMG_HEADERS
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

# 浅色主题（仿抖音 App 评论区）
_C_BG = "#ffffff"
_C_HEAD = "#f7f8fa"
_C_TEXT = "#232323"
_C_NICK = "#61666d"
_C_TIME = "#b1b4bb"
_C_ACCENT = "#161823"
_C_LIKE = "#f52e56"
_C_BORDER = "#eef0f3"

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]
_font_cache = {}


def _font(size: int):
    key = size
    if key not in _font_cache:
        f = None
        for path in _FONT_CANDIDATES:
            try:
                from PIL import ImageFont
                f = ImageFont.truetype(path, size)
                break
            except Exception:
                continue
        if f is None:
            from PIL import ImageFont
            f = ImageFont.load_default()
        _font_cache[key] = f
    return _font_cache[key]


def _wrap(text: str, font, max_w: int) -> list:
    """中文按字符宽度换行（PIL 无自动换行）。"""
    lines, line = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(line)
            line = ""
            continue
        t = line + ch
        if font.getlength(t) > max_w and line:
            lines.append(line)
            line = ch
        else:
            line = t
    if line:
        lines.append(line)
    return lines or [""]


def _http_img(be, url: str):
    """下载并打开图片；失败返回 None。"""
    try:
        r = requests.get(url, headers=be.IMG_HEADERS, timeout=20)
        if r.status_code == 200 and len(r.content) > 500:
            from PIL import Image
            import io
            return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        pass
    return None


# ==================== 搜索 / 筛选 ====================

def filter_comments(be, comments: list, keyword: str = "", user: str = "",
                    image_mode: str = "all", min_likes: int = 0,
                    sort_by: str = "default") -> list:
    """搜索筛选评论。

    keyword：评论文本包含（不区分大小写）；user：昵称包含；
    image_mode：all 全部 / has 带图 / text 纯文字；
    min_likes：最低点赞；sort_by：default 原序 / likes 点赞 / time 时间。
    """
    kw = (keyword or "").strip().lower()
    us = (user or "").strip().lower()
    out = []
    for c in comments:
        if kw and kw not in be.comment_text(c).lower():
            continue
        if us and us not in be.comment_user(c).lower():
            continue
        n_img = len(be.image_urls(c))
        if image_mode == "has" and n_img == 0:
            continue
        if image_mode == "text" and n_img > 0:
            continue
        if min_likes and be.comment_likes(c) < min_likes:
            continue
        out.append(c)
    if sort_by == "likes":
        out.sort(key=be.comment_likes, reverse=True)
    elif sort_by == "time":
        out.sort(key=be.comment_ts, reverse=True)
    return out


# ==================== 保存原始数据 ====================

def save_raw(be, comments: list, out_dir: Path, progress=None) -> dict:
    """保存原始数据：comments.json / comments.txt / images/。返回统计。"""
    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    (out_dir / "comments.json").write_text(
        json.dumps(comments, ensure_ascii=False, indent=2), encoding="utf-8")

    lines, n_img, n_dl = [], 0, 0
    for c in comments:
        user = be.comment_user(c)
        text = be.comment_text(c).replace("\n", " ")
        lines.append(f"[{user}] {text}")
        for u in be.image_urls(c):
            n_img += 1
            ext = ".jpg"
            m = re.search(r"\.(jpe?g|png|webp|gif)(?:[?~]|$)", u, re.I)
            if m:
                ext = "." + m.group(1).lower()
            path = out_dir / "images" / f"img_{n_img:03d}{ext}"
            try:
                r = requests.get(u, headers=be.IMG_HEADERS, timeout=30)
                if r.status_code == 200 and len(r.content) > 1000:
                    path.write_bytes(r.content)
                    n_dl += 1
            except Exception:
                pass
            if progress and n_img % 5 == 0:
                progress(f"图片 {n_dl}/{n_img}", n_img, max(n_img, 1))
    (out_dir / "comments.txt").write_text(
        "\n".join(lines) + f"\n\n共 {len(comments)} 条评论，{n_img} 张图片，成功下载 {n_dl} 张",
        encoding="utf-8")
    return {"comments": len(comments), "images": n_img, "downloaded": n_dl}


# ==================== 导出长图（PIL） ====================

def export_comments_png(be, comments: list, meta: dict, out_path: Path,
                        progress=None) -> Path:
    """评论列表导出为长图。

    meta: {platform, video_desc, filter_desc, stats}（页头信息）。
    每条评论绘制：头像 + 昵称 + 时间/IP + 点赞 + 文本 + 评论图片（等比缩放内嵌）。
    """
    from PIL import Image, ImageDraw

    W = 860
    AVATAR = 44
    PAD = 24
    CONTENT_X = PAD + AVATAR + 12
    CONTENT_W = W - CONTENT_X - PAD
    IMG_MAX_W = min(560, CONTENT_W)
    IMG_MAX_H = 640          # 单张图片最大高度（长截图等比缩到该高度内）
    IMG_GAP = 6
    COL_W = (IMG_MAX_W - IMG_GAP) // 2   # 两列网格的单列宽

    def _fit(im, max_w: int, max_h: int):
        """等比缩放到 (max_w, max_h) 内，不放大。"""
        iw, ih = im.size
        sc = min(max_w / iw, max_h / ih, 1.0)
        if sc < 1.0:
            im = im.resize((max(1, int(iw * sc)), max(1, int(ih * sc))))
        return im

    # ---- 先下载并排版（量高度），再统一绘制 ----
    rows = []
    for idx, c in enumerate(comments):
        text = be.comment_text(c)
        t_lines = _wrap(text, _font(16), CONTENT_W)
        h_text = len(t_lines) * 24
        imgs = [im for u in be.image_urls(c)[:9]
                 if (im := _http_img(be, u)) is not None]
        h_imgs = 0
        if len(imgs) == 1:
            imgs[0] = _fit(imgs[0], IMG_MAX_W, IMG_MAX_H)
            h_imgs = imgs[0].size[1] + 8
        elif imgs:
            imgs = [_fit(im, COL_W, IMG_MAX_H) for im in imgs]
            # 与绘制一致：贪心装短列
            ys = [0, 0]
            for im in imgs:
                k = 0 if ys[0] <= ys[1] else 1
                ys[k] += im.size[1] + IMG_GAP
            h_imgs = max(ys) + 6
        rows.append({"c": c, "text_lines": t_lines, "imgs": imgs,
                     "h": 34 + h_text + h_imgs + 18})
        if progress and (idx + 1) % 5 == 0:
            progress(f"渲染 {idx + 1}/{len(comments)}", idx + 1, len(comments))

    stats = meta.get("stats") or {}
    filter_desc = meta.get("filter_desc") or ""
    head_lines = [
        f"视频：{(meta.get('video_desc') or '未知')[:36]}",
        f"共导出 {len(comments)} 条评论"
        + (f"（原始 {stats.get('total', '')} 条）" if stats.get("total") else ""),
    ]
    if filter_desc:
        head_lines.append(f"筛选：{filter_desc}")
    head_lines.append(f"导出时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")

    head_h = 40 + len(head_lines) * 26 + 14
    total_h = head_h + sum(r["h"] for r in rows) + 60

    img = Image.new("RGB", (W, total_h), _C_BG)
    dr = ImageDraw.Draw(img)

    # ---- 页头 ----
    dr.rectangle([0, 0, W, head_h], fill=_C_HEAD)
    dr.text((PAD, 16), f"💬 {meta.get('platform') or ''}评论区", font=_font(22), fill=_C_ACCENT)
    y = 52
    for line in head_lines:
        dr.text((PAD, y), line, font=_font(14), fill=_C_NICK)
        y += 26

    # ---- 评论 ----
    y = head_h + 10
    for r in rows:
        c = r["c"]
        # 头像（圆形裁切）
        av = _http_img(be, be.avatar_url(c))
        if av is not None:
            av = av.resize((AVATAR, AVATAR))
            mask = Image.new("L", (AVATAR, AVATAR), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, AVATAR, AVATAR], fill=255)
            img.paste(av, (PAD, y + 4), mask)
        else:
            dr.ellipse([PAD, y + 4, PAD + AVATAR, y + 4 + AVATAR],
                       outline=_C_TIME, width=1)
        # 昵称 + 点赞
        dr.text((CONTENT_X, y + 4), be.comment_user(c)[:24], font=_font(14), fill=_C_NICK)
        likes = be.comment_likes(c)
        like_txt = f"♥ {likes}" if likes else "♥"
        lw = _font(13).getlength(like_txt)
        dr.text((W - PAD - lw, y + 4), like_txt, font=_font(13), fill=_C_LIKE)
        # 文本
        ty = y + 26
        for ln in r["text_lines"]:
            dr.text((CONTENT_X, ty), ln, font=_font(16), fill=_C_TEXT)
            ty += 24
        # 时间 / IP
        extra = be.comment_extra(c)
        if extra:
            dr.text((CONTENT_X, ty), extra, font=_font(12), fill=_C_TIME)
            ty += 18
        # 图片
        if r["imgs"]:
            ty += 8
            if len(r["imgs"]) == 1:
                img.paste(r["imgs"][0], (CONTENT_X, ty))
                ty += r["imgs"][0].size[1] + 8
            else:
                xs = [CONTENT_X, CONTENT_X + COL_W + IMG_GAP]
                ys = [ty, ty]
                for im in r["imgs"]:
                    k = 0 if ys[0] <= ys[1] else 1
                    img.paste(im, (xs[k], ys[k]))
                    ys[k] += im.size[1] + IMG_GAP
                ty = max(ys) + 6
        # 分隔线
        y += r["h"]
        dr.line([PAD, y - 6, W - PAD, y - 6], fill=_C_BORDER, width=1)

    # ---- 页脚 ----
    dr.text((PAD, total_h - 34), "· 由 封茗工具箱 导出 ·",
            font=_font(12), fill=_C_TIME)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    if progress:
        progress("导出完成", 1, 1)
    return out_path
