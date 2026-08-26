"""
平台信息获取 - 基础模块
定义 VideoInfo 数据结构、平台基类、URL 识别和工具函数
"""

from dataclasses import dataclass, field
from typing import Optional
import re
from datetime import datetime


@dataclass
class VideoInfo:
    """统一的视频/帖子信息数据结构"""
    platform: str = ""              # bilibili / douyin / weibo
    kind: str = "video"             # video=主页作品/视频, dynamic=动态
    video_id: str = ""              # 唯一标识
    title: str = ""                 # 标题
    description: str = ""           # 正文/描述（不做截断）
    author: str = ""                # 作者名
    author_id: str = ""             # 作者ID
    author_face: str = ""           # 作者头像URL
    play_count: int = 0             # 播放量/阅览量
    like_count: int = 0             # 点赞数
    comment_count: int = 0          # 评论数
    share_count: int = 0            # 分享/转发数
    favorite_count: int = 0         # 收藏数
    danmaku_count: int = 0          # 弹幕数(B站)
    coin_count: int = 0             # 投币数(B站)
    duration: int = 0               # 视频时长(秒)
    cover_url: str = ""             # 封面图URL
    video_url: str = ""             # 视频/帖子URL
    publish_time: str = ""          # 发布时间 YYYY-MM-DD HH:mm
    tags: list = field(default_factory=list)       # 标签列表
    category: str = ""              # 分类(B站) / 地区(微博)
    dimension: str = ""             # 分辨率 如 1920x1080
    copyright: str = ""             # 版权信息
    music: str = ""                 # 音乐信息(抖音)
    raw_url: str = ""               # 用户输入的原始URL
    raw_data: dict = field(default_factory=dict)   # API完整原始数据
    fetched_at: str = ""            # 数据获取时间
    # 媒体下载字段
    images: list[str] = field(default_factory=list)        # 图片URL列表（原图）
    live_photos: list[str] = field(default_factory=list)   # live图视频URL列表

    def to_dict(self) -> dict:
        """转为字典（用于导出），字段名中文化，raw_data 完整透传"""
        view_label = "阅览量" if self.platform == "weibo" else "播放量"
        kind_label = {"dynamic": "动态", "image": "图文"}.get(self.kind, "视频")
        d = {
            "平台": self.platform,
            "类型": kind_label,
            "视频ID": self.video_id,
            "标题": self.title,
            "正文": self.description,
            "作者": self.author,
            "作者ID": self.author_id,
            "作者头像": self.author_face,
            view_label: self.play_count,
            "点赞数": self.like_count,
            "评论数": self.comment_count,
            "分享数": self.share_count,
            "收藏数": self.favorite_count,
            "弹幕数": self.danmaku_count if self.danmaku_count else 0,
            "投币数": self.coin_count if self.coin_count else 0,
            "时长": "",
            "封面URL": self.cover_url,
            "链接": self.video_url,
            "发布时间": self.publish_time,
            "标签": self.tags,
            "分类": self.category,
            "分辨率": self.dimension,
            "版权": self.copyright,
            "音乐": self.music,
            "原始URL": self.raw_url,
            "原始数据": self.raw_data,
            "获取时间": self.fetched_at,
        }
        if self.duration > 0:
            m, s = divmod(self.duration, 60)
            h, m = divmod(m, 60)
            d["时长"] = f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"
        # 去掉值为0/空的可选字段保持简洁
        for k in ["弹幕数","投币数","收藏数","分类","分辨率","版权","音乐","作者头像"]:
            if not d.get(k):
                del d[k]
        if not d.get("标签"):
            del d["标签"]
        return d

    def summary(self) -> str:
        parts = [f"[{self.platform}]"]
        if self.title: parts.append(self.title[:50])
        if self.author: parts.append(f"@{self.author}")
        if self.play_count: parts.append(f"▶{self._fmt_count(self.play_count)}")
        if self.like_count: parts.append(f"❤{self._fmt_count(self.like_count)}")
        return " ".join(parts)

    @staticmethod
    def _fmt_count(n: int) -> str:
        if n >= 10000: return f"{n/10000:.1f}万"
        return str(n)


class PlatformBase:
    """平台解析器基类"""
    name: str = "base"


    def parse(self, url: str) -> Optional[VideoInfo]:
        """解析URL获取视频信息"""
        raise NotImplementedError

    def extract_id(self, url: str) -> str:
        """从URL中提取视频ID"""
        raise NotImplementedError


class URLRecognizer:
    """自动识别URL所属平台"""

    PATTERNS = {
        "bilibili": [
            r"bilibili\.com/video/(BV[a-zA-Z0-9]+)",
            r"bilibili\.com/video/av(\d+)",
            r"space\.bilibili\.com/\d+",
            r"t\.bilibili\.com/\d+",
            r"bilibili\.com/opus/\d+",
            r"b23\.tv/([a-zA-Z0-9]+)",
            r"bili(?:22|23|33)\.cn/([a-zA-Z0-9]+)",
        ],
        "douyin": [
            r"douyin\.com/video/(\d+)",
            r"douyin\.com/note/(\d+)",
            r"douyin\.com/user/",
            r"v\.douyin\.com/([a-zA-Z0-9]+)",
            r"iesdouyin\.com/share/video/(\d+)",
        ],
        "weibo": [
            r"weibo\.com/(?:\d+/)?.+",
            r"weibo\.cn/.+",
            r"m\.weibo\.cn/.+",
            r"video\.weibo\.com/.+",
        ],
        "kuaishou": [
            r"kuaishou\.com/f/([a-zA-Z0-9_-]+)",
            r"kuaishou\.com/short-video/([a-zA-Z0-9]+)",
            r"kuaishou\.com/profile/([a-zA-Z0-9]+)",
        ],
        "xiaohongshu": [
            r"xiaohongshu\.com/(?:discovery/item|explore)/([a-f0-9]+)",
            r"xiaohongshu\.com/user/profile/([a-f0-9]+)",
        ],
    }

    @classmethod
    def recognize(cls, url: str) -> Optional[str]:
        """识别URL所属平台，返回平台名称"""
        url = url.strip()
        for platform, patterns in cls.PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, url, re.IGNORECASE):
                    return platform
        return None








def now_str() -> str:
    """当前时间字符串"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_datetime(raw) -> str:
    """
    将各种时间格式统一为 YYYY-MM-DD HH:mm。
    支持: Unix时间戳、微博格式 "Thu Jul 02 00:00:01 +0800 2026"、ISO格式等。
    """
    if not raw:
        return ""

    # Unix 时间戳 (整数或浮点)
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw).strftime("%Y-%m-%d %H:%M")
        except Exception:
            return str(raw)

    raw_str = str(raw).strip()

    # 已经符合格式的直接返回
    if re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$', raw_str):
        return raw_str
    if re.match(r'^\d{4}-\d{2}-\d{2}$', raw_str):
        return raw_str + " 00:00"

    # 微博格式: "Thu Jul 02 00:00:01 +0800 2026"
    # 或其他类似格式
    formats_to_try = [
        "%a %b %d %H:%M:%S %z %Y",   # Thu Jul 02 00:00:01 +0800 2026
        "%a %b %d %H:%M:%S %Y",       # Thu Jul 02 00:00:01 2026
        "%Y-%m-%dT%H:%M:%S%z",        # 2026-07-02T00:00:01+08:00
        "%Y-%m-%dT%H:%M:%S",          # 2026-07-02T00:00:01
        "%Y-%m-%d %H:%M:%S",          # 2026-07-02 00:00:01
        "%Y/%m/%d %H:%M:%S",          # 2026/07/02 00:00:01
        "%Y年%m月%d日 %H:%M",         # 2026年07月02日 00:00
        "%b %d, %Y %H:%M:%S",         # Jul 02, 2026 00:00:01
        "%d %b %Y %H:%M:%S",          # 02 Jul 2026 00:00:01
    ]

    for fmt in formats_to_try:
        try:
            dt = datetime.strptime(raw_str, fmt)
            # 如果有时区信息，保持原样
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue

    # 最后尝试：直接看能不能提取日期数字
    date_match = re.search(r'(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})', raw_str)
    time_match = re.search(r'(\d{1,2}):(\d{2})(?::(\d{2}))?', raw_str)
    if date_match:
        y, m, d = date_match.groups()
        if time_match:
            h, mi = time_match.groups()[:2]
            return f"{y}-{int(m):02d}-{int(d):02d} {int(h):02d}:{int(mi):02d}"
        return f"{y}-{int(m):02d}-{int(d):02d} 00:00"

    return raw_str
