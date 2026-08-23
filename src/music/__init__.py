"""音乐信息获取 - 五大平台综合检索（合并自原「音乐信息获取」项目）。"""

from .models import SongInfo, AlbumInfo, PlaylistInfo, SearchResult
from .scrapers import (
    QQMusicScraper, NeteaseScraper, KugouScraper, KuwoScraper, FiveSingScraper,
)
from .exporter import export_all_formats

ALL_PLATFORMS = [
    ("qq", "QQ音乐", QQMusicScraper),
    ("netease", "网易云音乐", NeteaseScraper),
    ("kugou", "酷狗音乐", KugouScraper),
    ("kuwo", "酷我音乐", KuwoScraper),
    ("5sing", "5sing音乐", FiveSingScraper),
]

__all__ = [
    "SongInfo", "AlbumInfo", "PlaylistInfo", "SearchResult",
    "QQMusicScraper", "NeteaseScraper", "KugouScraper", "KuwoScraper", "FiveSingScraper",
    "export_all_formats", "ALL_PLATFORMS",
]
