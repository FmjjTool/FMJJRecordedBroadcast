"""网易云音乐 - 搜索 / 详情 / 歌词 / 下载 / 扫码登录（合并自原「Netease」项目）。"""

from .music_api import (
    NeteaseAPI, APIException,
    search_music, search_album, name_v1, url_v1, lyric_v1,
    playlist_detail, album_detail, get_pic_url, qr_login,
    get_song_detail, get_lyric,
    search_artist, artist_detail, artist_songs, artist_albums,
)
from .music_downloader import MusicDownloader, DownloadException
from .cookie_manager import CookieManager, CookieException, parse_cookie_string

__all__ = [
    "NeteaseAPI", "APIException",
    "search_music", "search_album", "name_v1", "url_v1", "lyric_v1",
    "playlist_detail", "album_detail", "get_pic_url", "qr_login",
    "get_song_detail", "get_lyric",
    "search_artist", "artist_detail", "artist_songs", "artist_albums",
    "MusicDownloader", "DownloadException",
    "CookieManager", "CookieException", "parse_cookie_string",
]
