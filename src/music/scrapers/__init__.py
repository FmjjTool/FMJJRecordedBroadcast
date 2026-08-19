"""
平台抓取器模块
"""

from .qq_music import QQMusicScraper
from .netease import NeteaseScraper
from .kugou import KugouScraper
from .kuwo import KuwoScraper
from .fivesing import FiveSingScraper

__all__ = [
    "QQMusicScraper",
    "NeteaseScraper",
    "KugouScraper",
    "KuwoScraper",
    "FiveSingScraper",
]
