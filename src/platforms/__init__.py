"""
平台解析器模块
"""

from .base import PlatformBase, URLRecognizer
from .bilibili import BilibiliParser
from .douyin import DouyinParser
from .weibo import WeiboParser
from .kuaishou import KuaishouParser
from .xiaohongshu import XiaohongshuParser

# 注册所有解析器
PARSERS = {
    "bilibili": BilibiliParser(),
    "douyin": DouyinParser(),
    "weibo": WeiboParser(),
    "kuaishou": KuaishouParser(),
    "xiaohongshu": XiaohongshuParser(),
}


def get_parser(platform: str) -> PlatformBase | None:
    """根据平台名获取解析器"""
    return PARSERS.get(platform)
