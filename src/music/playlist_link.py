"""线下活动歌单链接登记（L3，Qt-free）。

歌单写入平台后，把各平台歌单链接回写到腾讯文档「线下活动」对应列：

    活动歌单_网易云 / 活动歌单_酷狗 / 活动歌单_QQ

列名由用户在腾讯文档侧建立，程序不加列、不改表结构；表里没有该列就跳过并报原因。
链接按各平台公开的歌单页格式构造，与仓库里读取歌单时使用的格式一致
（见 `music/scrapers/*.py`、`gui_qt/pages/netease.py` 的示例）。

酷狗自建云歌单的公开页是 `/yy/special/single/collection_3_<userid>_<listid>_0.html`
（2026-11-26 实测可直接打开，不再需要 specialid），链接由 `KugouPlaylistWriter.
playlist_url()` 生成；写入前仍做一次只读网页探测，探测不通过就不写，避免把
打不开的链接写进真实表格。
"""

from __future__ import annotations

from urllib.parse import quote

# 平台 → 腾讯文档「线下活动」列名（用户 2026-11-26 新建）
EVENT_COLUMNS = {
    "netease": "活动歌单_网易云",
    "kugou": "活动歌单_酷狗",
    "qq": "活动歌单_QQ",
}

# 平台 → 歌单网页地址模板；{id} 为创建接口返回的歌单 ID
_URL_TEMPLATES = {
    "netease": "https://music.163.com/playlist?id={id}",
    "qq": "https://y.qq.com/n/ryqq_v2/playlist/{id}",
    "kugou": "https://www.kugou.com/yy/special/single/{id}.html",
}

# 需要先探测网页可访问的平台：酷狗云歌单只有分配 specialid 才有网页页，
# 探测不通过就不写死链。QQ 的链接由官方账号接口解析（tid），不再额外探测。
VERIFY_BEFORE_WRITE = frozenset({"kugou"})


def column_of(platform: str) -> str:
    """平台 → 目标列名；不支持的平台返回空串。"""
    return EVENT_COLUMNS.get(str(platform or "").strip().lower(), "")


def supports(platform: str) -> bool:
    return bool(column_of(platform))


def url_of(platform: str, playlist_id: str) -> str:
    """平台歌单 ID → 网页链接；不支持的平台或空 ID 返回空串。"""
    template = _URL_TEMPLATES.get(str(platform or "").strip().lower())
    ident = str(playlist_id or "").strip()
    if not template or not ident:
        return ""
    return template.format(id=quote(ident, safe=""))


def build_links(results) -> list[dict]:
    """写入结果 → 待回写清单。

    优先用平台自己解析出的公开链接（QQ 的 tid、酷狗 global_collection_id）；
    酷狗解析不出链接（歌单被删/账号身份异常）时保留一条空链接记录，供回执说明原因。
    返回 [{platform, column, playlist_id, url, playlist_name, verify}, …]
    """
    links = []
    for item in results or ():
        if not isinstance(item, dict):
            continue
        platform = str(item.get("platform") or "").strip().lower()
        column = column_of(platform)
        playlist_id = str(item.get("playlist_id") or "").strip()
        if not column or not playlist_id:
            continue
        url = str(item.get("playlist_url") or "").strip()
        if not url:
            if platform == "kugou":
                # 解析不出 global_collection_id（歌单被删/身份异常）时不拿 listid 硬拼
                url = ""
            else:
                url = url_of(platform, playlist_id)
        links.append({
            "platform": platform,
            "column": column,
            "playlist_id": playlist_id,
            "url": url,
            "playlist_name": str(item.get("playlist_name") or ""),
            "verify": bool(url) and platform in VERIFY_BEFORE_WRITE,
        })
    return [link for link in links if link["column"]]
