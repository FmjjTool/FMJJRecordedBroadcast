"""工作台 Web 用例：复用本地只读统计，不持有桌面页面状态。"""

from __future__ import annotations

from toolbox.dashboard_stats import load_dashboard_data


def overview() -> dict:
    """返回共享数据总览；不触发采集、同步或平台写入。"""
    return load_dashboard_data()
