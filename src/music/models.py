"""
======================================================================
  数据模型定义 - SongInfo / AlbumInfo / SearchResult
  包含: 歌曲全字段 + 专辑全字段 + 跨平台搜索结果
======================================================================
"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class FileInfo(BaseModel):
    """各音质文件信息"""
    quality: str = Field(default="", description="音质标签: 128k/192k/320k/FLAC/APE等")
    size: int = Field(default=0, description="文件大小(bytes)")
    extension: str = Field(default="", description="文件扩展名")
    bitrate: int = Field(default=0, description="比特率(bps)")
    sr: int = Field(default=0, description="采样率(Hz)")
    url: str = Field(default="", description="文件直链(如可获取)")


class MVInfo(BaseModel):
    """MV 信息"""
    id: str = Field(default="", description="MV ID")
    vid: str = Field(default="", description="MV 视频ID")
    name: str = Field(default="", description="MV 名称")
    title: str = Field(default="", description="MV 标题")


class PayInfo(BaseModel):
    """付费/版权信息"""
    fee: int = Field(default=0, description="付费类型: 0免费/1VIP/8付费单曲等")
    pay_play: int = Field(default=0, description="试听付费")
    pay_down: int = Field(default=0, description="下载付费")
    price_track: int = Field(default=0, description="单曲价格(分)")
    price_album: int = Field(default=0, description="专辑价格(分)")


class SongInfo(BaseModel):
    """单首歌曲 - 尽可能保留API全部数据"""
    # ---- 核心标识 ----
    song_id: str = Field(default="", description="平台内歌曲ID")
    song_name: str = Field(default="", description="歌曲名称")
    title: str = Field(default="", description="完整标题")
    subtitle: str = Field(default="", description="副标题")
    alias: list[str] = Field(default_factory=list, description="别名/译名")

    # ---- 艺人 ----
    singer: str = Field(default="", description="演唱歌手(拼接)")
    singer_id: str = Field(default="", description="歌手ID")
    singer_mid: str = Field(default="", description="歌手MID")

    # ---- 专辑 ----
    album_name: str = Field(default="", description="所属专辑名称")
    album_id: str = Field(default="", description="专辑ID")
    track_no: int = Field(default=0, description="专辑内曲目序号")
    disc_no: str = Field(default="", description="碟片号")

    # ---- 音频属性 ----
    duration: int = Field(default=0, description="时长(秒)")
    interval: int = Field(default=0, description="时长(原始值)")
    bpm: int = Field(default=0, description="BPM 节拍数")
    genre: str = Field(default="", description="流派/曲风")
    language: str = Field(default="", description="语种")

    # ---- 歌词 ----
    lyrics: str = Field(default="", description="完整歌词")

    # ---- 封面/媒体 ----
    cover_url: str = Field(default="", description="歌曲封面图片URL")
    mv: MVInfo = Field(default_factory=MVInfo, description="MV信息")

    # ---- 音质文件 ----
    files: list[FileInfo] = Field(default_factory=list, description="各音质文件信息列表")
    size128: int = Field(default=0, description="128kbps文件大小")
    size320: int = Field(default=0, description="320kbps文件大小")
    sizeflac: int = Field(default=0, description="FLAC文件大小")

    # ---- 播放 ----
    play_url: str = Field(default="", description="播放直链URL")
    preview_begin: int = Field(default=0, description="试听起始(ms)")
    preview_end: int = Field(default=0, description="试听结束(ms)")

    # ---- 付费/版权 ----
    pay: PayInfo = Field(default_factory=PayInfo, description="付费信息")

    # ---- 时间 ----
    release_date: str = Field(default="", description="发行日期")
    publish_time: int = Field(default=0, description="发布时间戳(ms)")

    # ---- 热度 ----
    popularity: float = Field(default=0.0, description="热度/流行度")

    # ---- 平台 ----
    platform: str = Field(default="", description="来源平台标识")
    platform_name: str = Field(default="", description="来源平台中文名")
    url: str = Field(default="", description="歌曲页面URL")

    # ---- 5sing专属 ----
    mode: str = Field(default="", description="5sing模式: 原唱/翻唱/伴奏")

    # ---- 采集元数据 ----
    collected_at: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        description="采集时间"
    )

    # ---- API原始数据(完整) ----
    extra: dict = Field(default_factory=dict, description="API返回的其他未映射字段")


class AlbumInfo(BaseModel):
    """专辑信息"""
    album_id: str = Field(default="", description="专辑ID")
    album_name: str = Field(default="", description="专辑名称")
    title: str = Field(default="", description="完整标题")
    subtitle: str = Field(default="", description="副标题")
    singer: str = Field(default="", description="专辑歌手")
    singer_id: str = Field(default="", description="歌手ID")
    release_date: str = Field(default="", description="发行日期")
    publish_time: int = Field(default=0, description="发布时间戳(ms)")
    description: str = Field(default="", description="专辑简介")
    album_type: str = Field(default="", description="专辑类型: 录音室专辑/EP/单曲/现场等")
    language: str = Field(default="", description="语种")
    genre: str = Field(default="", description="流派")
    company: str = Field(default="", description="发行公司")
    cover_url: str = Field(default="", description="专辑封面URL")
    song_count: int = Field(default=0, description="专辑内歌曲数")
    songs: list[SongInfo] = Field(default_factory=list, description="专辑内歌曲列表")
    platform: str = Field(default="", description="来源平台标识")
    platform_name: str = Field(default="", description="来源平台中文名")
    url: str = Field(default="", description="专辑页面URL")
    collected_at: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        description="采集时间"
    )
    extra: dict = Field(default_factory=dict, description="API返回的其他未映射字段")


class PlaylistInfo(BaseModel):
    """歌单信息"""
    playlist_id: str = Field(default="", description="歌单ID")
    name: str = Field(default="", description="歌单名称")
    creator: str = Field(default="", description="创建者昵称")
    cover_url: str = Field(default="", description="歌单封面URL")
    description: str = Field(default="", description="歌单简介")
    song_count: int = Field(default=0, description="歌曲总数")
    play_count: int = Field(default=0, description="播放次数")
    tags: str = Field(default="", description="标签(逗号分隔)")
    songs: list[SongInfo] = Field(default_factory=list, description="歌单内歌曲列表")
    platform: str = Field(default="", description="来源平台标识")
    platform_name: str = Field(default="", description="来源平台中文名")
    url: str = Field(default="", description="歌单页面URL")
    collected_at: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        description="采集时间"
    )
    extra: dict = Field(default_factory=dict, description="API返回的其他未映射字段")


class SearchResult(BaseModel):
    """跨平台搜索结果"""
    keyword: str = Field(default="", description="搜索关键词")
    platforms_available: list[str] = Field(default_factory=list, description="歌曲上线的所有平台")
    total_songs: int = Field(default=0, description="歌曲总数")
    total_albums: int = Field(default=0, description="专辑总数")
    songs: list[SongInfo] = Field(default_factory=list, description="各平台匹配的歌曲")
    albums: list[AlbumInfo] = Field(default_factory=list, description="关联的专辑信息")
    fivesing_results: dict[str, list[SongInfo]] = Field(
        default_factory=dict,
        description="5sing三种模式结果: {原唱: [...], 翻唱: [...], 伴奏: [...]}"
    )
    collected_at: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        description="采集时间"
    )
