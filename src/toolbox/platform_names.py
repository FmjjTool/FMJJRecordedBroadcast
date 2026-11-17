"""平台英文标识 → 中文名的单一来源。"""

from __future__ import annotations

MEDIA_PLATFORM_NAMES = {
    "bilibili": "B站",
    "douyin": "抖音",
    "weibo": "微博",
    "kuaishou": "快手",
    "xiaohongshu": "小红书",
}

MUSIC_PLATFORM_NAMES = {
    "netease": "网易云",
    "qq": "QQ音乐",
    "kugou": "酷狗",
    "kuwo": "酷我",
    "fivesing": "5sing",
}

DIRECTORY_PLATFORM_NAMES = {
    **MEDIA_PLATFORM_NAMES,
    "haokan": "好看视频",
    "weishi": "微视",
    "pear": "梨视频",
    "pipigx": "皮皮搞笑",
}

ALL_PLATFORM_NAMES = {**MEDIA_PLATFORM_NAMES, **MUSIC_PLATFORM_NAMES}
