"""采集内容去重键（collected.db / increment.db 共用）。

键优先级：video_id（稳定平台 ID）→ video_url → raw_url。
抖音 CDN 直链带签名/过期参数，不能当键；用户主页链接（raw_url）也不能当键。
"""

from __future__ import annotations


def collect_key(info) -> str:
    """返回采集条目的稳定去重键；支持 dataclass 对象与字典。"""
    if isinstance(info, dict):
        return str(info.get("video_id") or info.get("video_url")
                   or info.get("raw_url") or "").strip()
    return str(getattr(info, "video_id", "") or getattr(info, "video_url", "")
               or getattr(info, "raw_url", "") or "").strip()
