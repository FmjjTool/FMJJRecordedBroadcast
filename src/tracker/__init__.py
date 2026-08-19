"""账号追踪 - B站/抖音/微博 账号数据定时采集（合并自原「tacker」项目）。"""

from .collectors import collect_bilibili, collect_weibo, collect_douyin
from .storage import save

__all__ = ["collect_bilibili", "collect_weibo", "collect_douyin", "save"]
