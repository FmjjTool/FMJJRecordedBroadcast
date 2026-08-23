"""
======================================================================
  基础抓取器 - 抽象基类
  定义: search / get_song_detail / get_album / get_play_url / fetch_full_detail
======================================================================
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .models import SongInfo, AlbumInfo, PlaylistInfo


class BaseScraper(ABC):
    platform: str = "base"
    platform_name: str = "Base"

    @abstractmethod
    def search_song(self, keyword: str, page: int = 1, limit: int = 10) -> list[SongInfo]:
        """仅搜索（不含详情），返回基础 SongInfo 列表"""
        ...

    @abstractmethod
    def get_song_detail(self, song_id: str) -> Optional[SongInfo]:
        """获取歌曲详情（歌词 + 所有平台字段）"""
        ...


    def get_album(self, album_id: str) -> Optional[AlbumInfo]:
        return None

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistInfo]:
        """获取歌单信息（含歌曲列表），平台未实现时返回 None"""
        return None

    # ---- 链接/输入解析：从用户输入提取歌曲或歌单 ID ----
    @staticmethod
    def extract_song_id(text: str) -> str:
        raise NotImplementedError

    @staticmethod
    def extract_playlist_id(text: str) -> str:
        raise NotImplementedError

    def get_play_url(self, song_id: str) -> str:
        return ""

    # ---- 按专辑检索 ----
    def search_by_album(self, singer: str, album_name: str, limit: int = 20) -> list[SongInfo]:
        """
        按专辑名检索：先搜索歌手+专辑名，再筛选匹配的专辑。
        返回该专辑下的歌曲列表（基础信息，不含详情）
        """
        keyword = f"{singer} {album_name}"
        candidates = self.search_song(keyword, limit=limit)
        # 模糊匹配专辑名（去空格、大小写不敏感）
        target = album_name.strip().lower()
        matched = []
        for s in candidates:
            cur = (s.album_name or "").strip().lower()
            if not cur:
                continue
            if target == cur:
                matched.append(s)
            elif target in cur:
                matched.append(s)
            elif len(cur) >= max(3, len(target) * 0.6) and cur in target:
                matched.append(s)
        return matched

    # ---- 搜索（不含详情） ----
    def search(self, keyword: str, limit: int = 5) -> list[SongInfo]:
        """搜索歌曲列表（不自动获取详情），供用户选择"""
        songs = self.search_song(keyword, limit=limit)
        for s in songs:
            s.platform_name = self.platform_name
        return songs

    # ---- 获取完整详情（选定后调用） ----
    def fetch_full_detail(self, song: SongInfo) -> Optional[SongInfo]:
        """对已选歌曲获取完整信息：详情 + 歌词 + 专辑 + 播放直链"""
        detail = self.get_song_detail(song.song_id)
        if not detail:
            return song

        detail.platform_name = self.platform_name

        # 将搜索结果的字段作为基础，详情字段填充空白
        _merge(song, detail)

        # 获取专辑
        album_id = song.album_id or detail.album_id
        if album_id and album_id != "0":
            try:
                album = self.get_album(album_id)
                if album:
                    album.platform_name = self.platform_name
                    song.extra["_album"] = album.model_dump()
            except Exception:
                pass

        # 播放直链
        if not song.play_url:
            try:
                song.play_url = self.get_play_url(song.song_id)
            except Exception:
                pass

        return song


# ------------------------------------------------------------------
# 合并：target (搜索结果) 有值则不覆盖，source (详情) 填充空白
_MERGE_FIELDS = (
    "album_id", "album_name", "lyrics", "duration", "interval",
    "release_date", "cover_url", "language", "play_url",
    "track_no", "disc_no", "bpm", "genre", "publish_time",
    "title", "subtitle", "singer_id", "singer_mid",
    "popularity", "size128", "size320", "sizeflac",
)

def _merge(target: SongInfo, source: SongInfo):
    for field in _MERGE_FIELDS:
        src = getattr(source, field, None)
        tgt = getattr(target, field, None)
        if not tgt and src:
            setattr(target, field, src)
    if source.files and not target.files:
        target.files = source.files
    if source.alias and not target.alias:
        target.alias = source.alias
    if source.mv and source.mv.id and (not target.mv or not target.mv.id):
        target.mv = source.mv
    if source.extra:
        for k, v in source.extra.items():
            if k not in target.extra:
                target.extra[k] = v
