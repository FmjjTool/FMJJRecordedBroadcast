"""网易云音乐 - 搜索 / 详情 / 歌词 / 下载 / 扫码登录（合并自原「Netease」项目）。"""

import os

from .music_api import (
    NeteaseAPI, APIException,
    search_music, search_album, name_v1, url_v1, lyric_v1,
    playlist_detail, album_detail, get_pic_url, qr_login,
    get_song_detail, get_lyric,
)
from .music_downloader import MusicDownloader, DownloadException
from .cookie_manager import CookieManager, CookieException

# cookie 文件固定放在 netease 包目录下，避免受启动目录影响
COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookie.txt")

__all__ = [
    "NeteaseAPI", "APIException",
    "search_music", "search_album", "name_v1", "url_v1", "lyric_v1",
    "playlist_detail", "album_detail", "get_pic_url", "qr_login",
    "get_song_detail", "get_lyric",
    "MusicDownloader", "DownloadException",
    "CookieManager", "CookieException", "COOKIE_FILE",
]
