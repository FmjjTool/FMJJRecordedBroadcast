"""腾讯文档监控引擎（浏览量 / 文档成员数 + 智能表格数据同步）。

模块划分：
- config    : 文档清单（支持自定义增删改）、导出路径、登录态路径
- storage   : SQLite 存档（每次采集一行）+ 增量计算 + 旧版数据导入
- collector : Playwright 浏览器采集（接口优先，界面兜底）
- report    : 每个文档一份 Excel 报表（每日记录 / 采集明细）
- tables    : 智能表格数据同步（解码 → 结构化 → 本地 SQLite 增量 → Excel）
- job       : 采集编排（采集 → 落库 → 报表 → 日志），GUI 与定时任务共用
- task      : Windows 计划任务安装 / 删除 / 查询
"""

from . import collector, config, job, report, storage, tables, task  # noqa: F401

__all__ = ["collector", "config", "job", "report", "storage", "tables", "task"]
