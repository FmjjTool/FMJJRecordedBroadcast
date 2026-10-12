"""音乐下载器模块

提供网易云音乐下载功能，包括：
- 音乐信息获取
- 文件下载到本地
- 内存下载
- 音乐标签写入
- 异步下载支持
"""

import os
import re
import time
from typing import Optional
from pathlib import Path
from dataclasses import dataclass
from enum import Enum

import requests
from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TRCK, APIC
from mutagen.mp4 import MP4

from .music_api import NeteaseAPI, APIException, HTTPClient
from .cookie_manager import CookieManager


class AudioFormat(Enum):
    """音频格式枚举"""
    MP3 = "mp3"
    FLAC = "flac"
    M4A = "m4a"




@dataclass
class MusicInfo:
    """音乐信息数据类"""
    id: int
    name: str
    artists: str
    album: str
    pic_url: str
    duration: int
    track_number: int
    download_url: str
    file_type: str
    file_size: int
    quality: str
    lyric: str = ""
    tlyric: str = ""


@dataclass
class DownloadResult:
    """下载结果数据类"""
    success: bool
    file_path: Optional[str] = None
    file_size: int = 0
    error_message: str = ""
    music_info: Optional[MusicInfo] = None


class DownloadException(Exception):
    """下载异常类"""
    pass


class MusicDownloader:
    """音乐下载器主类"""
    
    def __init__(self, download_dir: str = "download", max_concurrent: int = 3, cookie_file: str = None):
        """
        初始化音乐下载器
        
        Args:
            download_dir: 下载目录
            max_concurrent: 最大并发下载数
            cookie_file: Cookie 文件路径（默认 netease/cookie.txt）
        """
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(exist_ok=True)
        self.max_concurrent = max_concurrent
        
        # 初始化依赖
        self.cookie_manager = CookieManager(cookie_file) if cookie_file else CookieManager()
        self.api = NeteaseAPI()
        
        # 支持的文件格式
        self.supported_formats = {
            'mp3': AudioFormat.MP3,
            'flac': AudioFormat.FLAC,
            'm4a': AudioFormat.M4A
        }
    
    def _sanitize_filename(self, filename: str) -> str:
        """清理文件名，移除非法字符
        
        Args:
            filename: 原始文件名
            
        Returns:
            清理后的安全文件名
        """
        # 移除或替换非法字符
        illegal_chars = r'[<>:"/\\|?*]'
        filename = re.sub(illegal_chars, '_', filename)
        
        # 移除前后空格和点
        filename = filename.strip(' .')
        
        # 限制长度
        if len(filename) > 200:
            filename = filename[:200]
        
        return filename or "unknown"
    
    def _determine_file_extension(self, url: str, content_type: str = "") -> str:
        """根据URL和Content-Type确定文件扩展名
        
        Args:
            url: 下载URL
            content_type: HTTP Content-Type头
            
        Returns:
            文件扩展名
        """
        # 首先尝试从URL获取
        if '.flac' in url.lower():
            return '.flac'
        elif '.mp3' in url.lower():
            return '.mp3'
        elif '.m4a' in url.lower():
            return '.m4a'
        
        # 从Content-Type获取
        content_type = content_type.lower()
        if 'flac' in content_type:
            return '.flac'
        elif 'mpeg' in content_type or 'mp3' in content_type:
            return '.mp3'
        elif 'mp4' in content_type or 'm4a' in content_type:
            return '.m4a'
        
        return '.mp3'  # 默认
    
    _QUALITY_FALLBACK = {
        "standard": [],
        "exhigh": ["standard"],
        "lossless": ["exhigh", "standard"],
        "hires": ["lossless", "exhigh", "standard"],
        "jyeffect": ["hires", "lossless", "exhigh", "standard"],
        "jymaster": ["jyeffect", "hires", "lossless", "exhigh", "standard"],
        "sky": ["jymaster", "jyeffect", "hires", "lossless", "exhigh", "standard"],
    }

    def get_music_info(self, music_id: int, quality: str = "standard") -> MusicInfo:
        """获取音乐详细信息（音质无下载链接时自动降级重试）。"""
        chain = [quality] + self._QUALITY_FALLBACK.get(quality, [])
        last_err = ""
        for q in chain:
            try:
                return self._get_music_info_once(music_id, q)
            except DownloadException as e:
                last_err = str(e)
                # 只有「无链接」类错误才降级；VIP/付费/其他错误直接抛出
                is_no_link = ("下载链接" in last_err or "播放链接" in last_err)
                is_fee_related = ("VIP" in last_err or "付费" in last_err)
                if not is_no_link or is_fee_related:
                    raise
        raise DownloadException(last_err or f"音乐ID {music_id} 无可用的下载链接")

    def _get_music_info_once(self, music_id: int, quality: str) -> MusicInfo:
        """获取音乐详细信息（单次尝试，不降级）。"""
        try:
            # 获取cookies
            cookies = self.cookie_manager.parse_cookies()
            
            # 获取音乐URL信息
            url_result = self.api.get_song_url(music_id, quality, cookies)
            if not url_result.get('data') or not url_result['data']:
                raise DownloadException(f"无法获取音乐ID {music_id} 的播放链接")
            
            song_data = url_result['data'][0]
            download_url = song_data.get('url', '')
            if not download_url:
                fee = song_data.get('fee', 0)
                if fee == 1:
                    raise DownloadException(f"音乐ID {music_id} 是 VIP 付费歌曲，需登录 VIP 账号")
                if fee in (4, 8):
                    raise DownloadException(f"音乐ID {music_id} 是付费歌曲，需购买或登录")
                raise DownloadException(f"音乐ID {music_id} 无可用的下载链接")
            
            # 获取音乐详情
            song_detail = self.api.get_song_detail(music_id)
            if not song_detail or not song_detail.get('name'):
                raise DownloadException(f"无法获取音乐ID {music_id} 的详细信息")

            # 获取歌词
            lyric_result = self.api.get_lyric(music_id, cookies)
            lyric = lyric_result.get('lrc', '') if lyric_result else ''
            tlyric = lyric_result.get('tlyric', '') if lyric_result else ''
            
            # 构建艺术家字符串
            artists = '/'.join(artist['name'] for artist in song_detail.get('ar', []))
            
            # 创建MusicInfo对象
            music_info = MusicInfo(
                id=music_id,
                name=song_detail.get('name', '未知歌曲'),
                artists=artists or '未知艺术家',
                album=song_detail.get('al', {}).get('name', '未知专辑'),
                pic_url=song_detail.get('al', {}).get('picUrl', ''),
                duration=song_detail.get('dt', 0) // 1000,  # 转换为秒
                track_number=song_detail.get('no', 0),
                download_url=download_url,
                file_type=song_data.get('type', 'mp3').lower(),
                file_size=song_data.get('size', 0),
                quality=quality,
                lyric=lyric,
                tlyric=tlyric
            )
            
            return music_info
            
        except APIException as e:
            raise DownloadException(f"API调用失败: {e}")
        except DownloadException:
            raise
        except Exception as e:
            raise DownloadException(f"获取音乐信息时发生错误: {e}")
    
    def download_music_file(self, music_id: int, quality: str = "standard") -> DownloadResult:
        """下载音乐文件到本地
        
        Args:
            music_id: 音乐ID
            quality: 音质等级
            
        Returns:
            下载结果对象
        """
        try:
            # 获取音乐信息
            music_info = self.get_music_info(music_id, quality)
            
            # 生成文件名
            filename = f"{music_info.artists} - {music_info.name}"
            safe_filename = self._sanitize_filename(filename)
            
            # 确定文件扩展名
            file_ext = self._determine_file_extension(music_info.download_url)
            file_path = self.download_dir / f"{safe_filename}{file_ext}"

            # 检查文件是否已存在：不小于 API 返回的大小才算完整
            # （写入标签/封面后文件只会变大；半截文件必然更小 → 自动重下）
            if file_path.exists():
                actual_size = file_path.stat().st_size
                if not music_info.file_size or actual_size >= music_info.file_size:
                    self._ensure_cover_file(file_path, music_info)
                    return DownloadResult(
                        success=True,
                        file_path=str(file_path),
                        file_size=actual_size,
                        music_info=music_info
                    )

            # 下载到 .part 临时文件，校验完整后再落成正式文件。
            # 网易 CDN 会随机中途掐断传输（留下半截文件），失败自动重试
            part_path = file_path.with_name(file_path.name + ".part")
            last_err: Optional[Exception] = None
            for attempt in range(4):
                try:
                    response = HTTPClient.get_session().get(
                        music_info.download_url, stream=True, timeout=30)
                    response.raise_for_status()

                    with open(part_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                f.write(chunk)

                    actual_size = part_path.stat().st_size
                    if music_info.file_size and actual_size != music_info.file_size:
                        raise DownloadException(
                            f"下载不完整（{actual_size}/{music_info.file_size} 字节）")
                    part_path.replace(file_path)
                    break
                except (requests.RequestException, DownloadException) as e:
                    part_path.unlink(missing_ok=True)
                    last_err = e
                    if attempt < 3:
                        time.sleep(2 ** attempt)
            else:
                raise DownloadException(f"下载失败（服务器多次中断连接）: {last_err}")

            # 封面字节只下载一次：内嵌标签与独立封面文件共用
            cover_bytes = self._fetch_cover_bytes(music_info.pic_url)

            # 写入音乐标签
            self._write_music_tags(file_path, music_info, cover_bytes)
            self._ensure_cover_file(file_path, music_info, cover_bytes)
            
            return DownloadResult(
                success=True,
                file_path=str(file_path),
                file_size=file_path.stat().st_size,
                music_info=music_info
            )
            
        except DownloadException:
            raise
        except requests.RequestException as e:
            return DownloadResult(
                success=False,
                error_message=f"下载请求失败: {e}"
            )
        except Exception as e:
            return DownloadResult(
                success=False,
                error_message=f"下载过程中发生错误: {e}"
            )
    
    
    
    
    @staticmethod
    def _fetch_cover_bytes(pic_url: str) -> Optional[bytes]:
        """下载封面图片字节；失败返回 None（不影响主流程）。"""
        if not pic_url:
            return None
        try:
            r = requests.get(pic_url, timeout=10)
            r.raise_for_status()
            return r.content or None
        except Exception:
            return None

    @staticmethod
    def _ensure_cover_file(file_path: Path, music_info: MusicInfo,
                           cover_bytes: Optional[bytes] = None) -> None:
        """把封面另存为与音频同名的图片文件（已存在则跳过，失败静默）。"""
        if not music_info.pic_url:
            return
        from urllib.parse import urlparse
        ext = os.path.splitext(urlparse(music_info.pic_url).path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            ext = ".jpg"
        cover_path = file_path.with_name(file_path.stem + ext)
        if cover_path.exists():
            return
        if cover_bytes is None:
            cover_bytes = MusicDownloader._fetch_cover_bytes(music_info.pic_url)
        if not cover_bytes:
            return
        try:
            cover_path.write_bytes(cover_bytes)
        except Exception:
            pass

    def _write_music_tags(self, file_path: Path, music_info: MusicInfo,
                          cover_bytes: Optional[bytes] = None) -> None:
        """写入音乐标签信息

        Args:
            file_path: 音乐文件路径
            music_info: 音乐信息
            cover_bytes: 封面图片字节（None 则不内嵌封面）
        """
        try:
            file_ext = file_path.suffix.lower()

            if file_ext == '.mp3':
                self._write_mp3_tags(file_path, music_info, cover_bytes)
            elif file_ext == '.flac':
                self._write_flac_tags(file_path, music_info, cover_bytes)
            elif file_ext == '.m4a':
                self._write_m4a_tags(file_path, music_info, cover_bytes)

        except Exception as e:
            print(f"写入音乐标签失败: {e}")

    def _write_mp3_tags(self, file_path: Path, music_info: MusicInfo,
                        cover_bytes: Optional[bytes] = None) -> None:
        """写入MP3标签"""
        try:
            audio = MP3(str(file_path), ID3=ID3)

            # 添加ID3标签
            audio.tags.add(TIT2(encoding=3, text=music_info.name))
            audio.tags.add(TPE1(encoding=3, text=music_info.artists))
            audio.tags.add(TALB(encoding=3, text=music_info.album))

            if music_info.track_number > 0:
                audio.tags.add(TRCK(encoding=3, text=str(music_info.track_number)))

            # 添加封面
            if cover_bytes:
                audio.tags.add(APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,
                    desc='Cover',
                    data=cover_bytes
                ))

            audio.save()
        except Exception as e:
            print(f"写入MP3标签失败: {e}")

    def _write_flac_tags(self, file_path: Path, music_info: MusicInfo,
                         cover_bytes: Optional[bytes] = None) -> None:
        """写入FLAC标签"""
        try:
            audio = FLAC(str(file_path))

            audio['TITLE'] = music_info.name
            audio['ARTIST'] = music_info.artists
            audio['ALBUM'] = music_info.album

            if music_info.track_number > 0:
                audio['TRACKNUMBER'] = str(music_info.track_number)

            # 添加封面
            if cover_bytes:
                from mutagen.flac import Picture
                picture = Picture()
                picture.type = 3  # Cover (front)
                picture.mime = 'image/jpeg'
                picture.desc = 'Cover'
                picture.data = cover_bytes
                audio.add_picture(picture)

            audio.save()
        except Exception as e:
            print(f"写入FLAC标签失败: {e}")

    def _write_m4a_tags(self, file_path: Path, music_info: MusicInfo,
                        cover_bytes: Optional[bytes] = None) -> None:
        """写入M4A标签"""
        try:
            audio = MP4(str(file_path))

            audio['\xa9nam'] = music_info.name
            audio['\xa9ART'] = music_info.artists
            audio['\xa9alb'] = music_info.album

            if music_info.track_number > 0:
                audio['trkn'] = [(music_info.track_number, 0)]

            # 添加封面
            if cover_bytes:
                audio['covr'] = [cover_bytes]

            audio.save()
        except Exception as e:
            print(f"写入M4A标签失败: {e}")
    


if __name__ == "__main__":
    # 测试代码
    downloader = MusicDownloader()
    print("音乐下载器模块")
    print("支持的功能:")
    print("- 同步下载")
    print("- 异步下载")
    print("- 批量下载")
    print("- 内存下载")
    print("- 音乐标签写入")
    print("- 下载进度跟踪")
