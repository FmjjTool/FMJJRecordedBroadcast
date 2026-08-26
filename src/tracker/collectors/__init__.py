from .bilibili import collect as collect_bilibili
from .weibo import collect as collect_weibo
from .douyin import collect as collect_douyin
from .kuaishou import collect as collect_kuaishou
from .xiaohongshu import collect as collect_xiaohongshu

__all__ = ["collect_bilibili", "collect_weibo", "collect_douyin",
           "collect_kuaishou", "collect_xiaohongshu"]
