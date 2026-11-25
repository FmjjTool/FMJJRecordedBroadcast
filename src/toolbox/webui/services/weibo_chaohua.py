"""微博超话 WebUI 适配（L2）：本地只读概览与分析。"""

from __future__ import annotations

from toolbox import webui_weibo_chaohua


class WeiboChaohuaService:
    def overview(self) -> dict:
        return webui_weibo_chaohua.overview()

    def analysis(self, topic_id: str) -> dict:
        return webui_weibo_chaohua.analysis(topic_id)
