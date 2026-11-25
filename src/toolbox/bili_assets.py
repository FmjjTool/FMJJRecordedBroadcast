"""B站资源下载清单（Qt-free）：装扮详情与 UP 主空间 → 文件清单。

从 `gui_qt/pages/bili_garb.py` 与 `bili_info.py` 抽出的纯函数，桌面页与 WebUI
共用同一份字段映射，避免两套逻辑漂移。
"""

from __future__ import annotations

import json

from bili import api

SKIN_PROPS = [
    ("head_bg", "顶部背景图"),
    ("head_tab_bg", "首页顶部标签页背景"),
    ("tail_bg", "底部背景图"),
    ("tail_icon_main", "首页按钮图标"),
    ("tail_icon_channel", "频道按钮图标"),
    ("tail_icon_dynamic", "动态按钮图标"),
    ("tail_icon_pub_btn_bg", "发布按钮图标"),
    ("tail_icon_shop", "会员购按钮图标"),
    ("tail_icon_myself", "“我的”按钮图标"),
    ("tail_icon_selected_main", "首页按钮选中图标"),
    ("tail_icon_selected_channel", "频道按钮选中图标"),
    ("tail_icon_selected_dynamic", "动态按钮选中图标"),
    ("tail_icon_selected_pub_btn_bg", "发布按钮选中图标"),
    ("tail_icon_selected_shop", "会员购按钮选中图标"),
    ("tail_icon_selected_myself", "“我的”按钮选中图标"),
    ("head_myself_squared_bg", "“我的”界面背景图"),
]


def sanitize(name) -> str:
    return api.sanitize(name)


def _sfx(name: str, suffix: str) -> str:
    return name if name.endswith(suffix) else name + suffix


def _emoji_key(raw: str) -> str:
    """装扮表情名 [XXX_xxx] → xxx。"""
    parts = raw.split("_")
    if len(parts) > 1:
        return parts[1].rstrip("]").rstrip(")")
    return raw


def _emoji_urls(props: dict) -> list[str]:
    """表情全部可用格式地址（gif/webp/png 去重）。"""
    out = []
    for url in (props.get("image_gif"), props.get("image_webp"),
                props.get("image")):
        if url and url not in out:
            out.append(url)
    return out


def _space_bg_images(props: dict) -> tuple[list, list, list]:
    """空间背景按 image{n}_landscape 循环取横/竖图与背景视频。"""
    landscapes, portraits, videos = [], [], []
    index = 1
    while f"image{index}_landscape" in props:
        landscapes.append(props.get(f"image{index}_landscape", ""))
        portraits.append(props.get(f"image{index}_portrait", ""))
        for video in (props.get(f"space{index}_mp4_horizontal", ""),
                      props.get(f"space{index}_mp4_vertical", "")):
            if video:
                videos.append(video)
        index += 1
    return landscapes, portraits, videos


def suit_files(detail: dict) -> list[dict]:
    """装扮详情 → 下载文件清单（与桌面「装扮 / 收藏集」页逐项一致）。"""
    files: list[dict] = []

    def add(rel, url, ext=None):
        if url and isinstance(url, str) and url.startswith("http"):
            files.append({"path": rel, "url": url,
                          **({"ext": ext} if ext else {})})

    title = sanitize(detail.get("name", "装扮"))
    items = detail.get("suit_items", {}) or {}
    props = detail.get("properties", {}) or {}

    add(f"{title}/装扮背景图", props.get("image_cover"))
    add(f"{title}/装扮分享背景图", props.get("fan_share_image"))

    for bg in items.get("space_bg", []) or []:
        p = bg.get("properties", {}) or {}
        landscapes, portraits, videos = _space_bg_images(p)
        base = sanitize(_sfx(bg.get("name", ""), "背景"))
        for index, url in enumerate(landscapes):
            add(f"{title}/{base}/背景{index + 1}", url)
        for index, url in enumerate(portraits):
            base_p = sanitize(_sfx(bg.get("name", ""), "肖像"))
            add(f"{title}/{base_p}/肖像{index + 1}", url)
        for index, url in enumerate(videos):
            add(f"{title}/{base}/背景视频{index + 1}", url)

    for pkg in items.get("emoji_package", []) or []:
        pkg_dir = sanitize(_sfx(pkg.get("name", ""), "表情包"))
        p = pkg.get("properties", {}) or {}
        emojis = []
        raw_list = p.get("item_emoji_list")
        if isinstance(raw_list, str) and raw_list.startswith("["):
            try:
                emojis = json.loads(raw_list)
            except Exception:  # noqa: BLE001 - 非法 JSON 走兜底
                emojis = []
        elif isinstance(raw_list, list):
            emojis = raw_list
        if not emojis:
            for item in items.get("emoji", []) or []:
                emojis.append({"name": item.get("name", ""),
                               "properties": item.get("properties", {}) or {}})
        for emoji in emojis:
            ep = emoji.get("properties", {}) or {}
            name = sanitize(_emoji_key(emoji.get("name", "表情")))
            for url in _emoji_urls(ep):
                add(f"{title}/{pkg_dir}/{name}", url)

    for icon in items.get("play_icon", []) or []:
        p = icon.get("properties", {}) or {}
        base = sanitize(_sfx(icon.get("name", ""), "进度条"))
        if p.get("drag_icon"):
            add(f"{title}/{base}/进度条拖动动画", p.get("drag_icon"))
            add(f"{title}/{base}/进度条松手动画", p.get("icon"))
        else:
            add(f"{title}/{base}/进度条左划", p.get("drag_left_png"))
            add(f"{title}/{base}/进度条右划", p.get("drag_right_png"))
            add(f"{title}/{base}/进度条指示", p.get("middle_png"))
        add(f"{title}/{base}/方形演示图", p.get("squared_image"))
        add(f"{title}/{base}/图标", p.get("static_icon_image"))

    for skin in items.get("skin", []) or []:
        p = skin.get("properties", {}) or {}
        base = sanitize(_sfx(skin.get("name", ""), "皮肤"))
        for key, label in SKIN_PROPS:
            url = p.get(key)
            if isinstance(url, str) and url:
                add(f"{title}/{base}/{label}", url)
        mp4 = p.get("head_myself_mp4_bg")
        if isinstance(mp4, str) and mp4.startswith("http"):
            add(f"{title}/{base}/“我的”界面背景视频", mp4)
        else:
            raw = p.get("head_myself_mp4_bg_list")
            if isinstance(raw, str) and raw.startswith("["):
                try:
                    for index, url in enumerate(json.loads(raw)):
                        if url:
                            add(f"{title}/{base}/“我的”界面背景视频{index + 1}", url)
                except Exception:  # noqa: BLE001
                    pass
        add(f"{title}/{base}/封面图", p.get("image_cover"))
        add(f"{title}/{base}/预览图", p.get("image_preview"))
        add(f"{title}/{base}/皮肤资源包", p.get("package_url"))

    for pendant in items.get("pendant", []) or []:
        p = pendant.get("properties", {}) or {}
        base = sanitize(_sfx(title, "") + "头像框")
        add(f"{title}/{base}/{sanitize(pendant.get('name', '头像框'))}",
            p.get("image"))
        add(f"{title}/{base}/{sanitize(pendant.get('name', '头像框'))}(效果演示)",
            p.get("garb_avatar"))

    for card in items.get("card", []) or []:
        p = card.get("properties", {}) or {}
        base = sanitize(_sfx(card.get("name", ""), "粉丝牌背景"))
        add(f"{title}/{base}", p.get("image"))
        add(f"{title}/{base}(2)", p.get("fans_image"))
        add(f"{title}/{base}(预览)", p.get("image_preview_small"))

    for cbg in items.get("card_bg", []) or []:
        p = cbg.get("properties", {}) or {}
        base = sanitize(_sfx(cbg.get("name", ""), "评论背景"))
        add(f"{title}/{base}", p.get("image"))
        add(f"{title}/{base}(预览)", p.get("image_preview_small"))

    for loading in items.get("loading", []) or []:
        p = loading.get("properties", {}) or {}
        base = sanitize(_sfx(loading.get("name", ""), "加载动画"))
        add(f"{title}/{base}", p.get("loading_url"))
        add(f"{title}/{base}(序列帧)", p.get("loading_frame_url"))
        add(f"{title}/{base}(预览)", p.get("image_preview_small"))

    for thumbup in items.get("thumbup", []) or []:
        p = thumbup.get("properties", {}) or {}
        base = sanitize(_sfx(thumbup.get("name", ""), "点赞动画"))
        add(f"{title}/{base}", p.get("image_ani"), ext="svga")
        add(f"{title}/{base}(裁剪版)", p.get("image_ani_cut"), ext="svga")
        add(f"{title}/{base}预览图", p.get("image_preview"))

    seen, out = set(), []
    for item in files:
        if item["url"] not in seen:
            seen.add(item["url"])
            out.append(item)
    return out


def space_files(card: dict, appinfo: dict, emotes: list) -> list[dict]:
    """UP 主空间资源 → 下载文件清单（与桌面「UP 主信息」页一致）。"""
    files = [{"path": "头像", "url": card.get("card", {}).get("face", "")}]
    space = card.get("space", {}) or {}
    if space.get("l_img"):
        files.append({"path": "网页端空间背景图", "url": space["l_img"]})
    if space.get("s_img") and space["s_img"] != space.get("l_img"):
        files.append({"path": "网页端空间背景图(小图)", "url": space["s_img"]})
    pendant = card.get("card", {}).get("pendant", {}) or {}
    pen_name = sanitize(pendant.get("name", "头像框"))
    enhanced = pendant.get("image_enhance", "")
    if enhanced:
        files.append({"path": f"头像框 - {pen_name}", "url": enhanced})
    if pendant.get("image") and pendant["image"] != enhanced:
        files.append({"path": f"头像框 - {pen_name}(普通版)",
                      "url": pendant["image"]})
    if appinfo.get("card", {}).get("pendant_url"):
        files.append({"path": f"头像框 - {pen_name}(App端)",
                      "url": appinfo["card"]["pendant_url"]})
    images = appinfo.get("images", {}) or {}
    if images.get("imgUrl"):
        files.append({"path": "App端空间背景图", "url": images["imgUrl"]})
    if images.get("night_imgurl"):
        files.append({"path": "App端空间背景图（夜间模式）",
                      "url": images["night_imgurl"]})
    for item in (appinfo.get("archive", {}) or {}).get("item", []) or []:
        if item.get("cover"):
            files.append({"path": f"投稿封面/{sanitize(item.get('title', '投稿'))}",
                          "url": item["cover"]})
    for emote in emotes:
        if emote.get("icon"):
            files.append({
                "path": f"充电表情/{sanitize(emote.get('name', emote.get('emoji', '表情')))}",
                "url": emote["icon"]})
    top = ((images.get("collection_top_simple", {}) or {}).get("top", {})
           or {}).get("result", []) or []
    for row in top:
        if row.get("cover"):
            title = sanitize(
                f"{(row.get('title', {}) or {}).get('title', '')} "
                f"{(row.get('title', {}) or {}).get('sub_title', '')}".strip()
                or "收藏集")
            files.append({"path": f"收藏集封面/{title}", "url": row["cover"]})
    return [item for item in files if item["url"]]
