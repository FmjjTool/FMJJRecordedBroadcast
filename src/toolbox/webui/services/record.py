"""录制页 WebUI 适配（L2）：只调用 L3 只读概览，不直接读配置文件。"""

from __future__ import annotations

from toolbox import live_record_status


class RecordService:
    def overview(self) -> dict:
        return live_record_status.overview()