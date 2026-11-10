"""腾讯文档监控引擎（浏览量 / 文档成员数 / 浏览人明细 + 智能表格数据同步）。

模块划分：
- config    : 文档清单（支持自定义增删改）、导出路径、登录态路径
- storage   : SQLite 存档（每次采集一行 + 浏览人按 uid 增量）+ 旧版数据导入
- http_api  : 纯 HTTP 直连采集（表格数据 + 浏览量/成员数，不启动浏览器）
- browse    : 浏览记录采集（已查看浏览人昵称 + 最近浏览时间，全量翻页）
- collector : Playwright 浏览器采集（正文预览 / 登录 / 诊断；旧版实时指标保留）
- report    : 每个文档一份 Excel 报表（每日记录 / 采集明细 / 浏览人）
- tables    : 智能表格数据同步（解码 → 结构化 → 本地 SQLite 增量 → Excel）
- images    : 智能表格图片字段增量采集（登记 URL → 下载到 图片/）
- archive   : 正文预览存档（逐页正文 → 本地 SQLite 增量 + Markdown 快照）
- job       : 采集编排（采集 → 落库 → 报表 → 日志），GUI 与定时任务共用
- task      : Windows 计划任务安装 / 删除 / 查询
"""

from . import (archive, browse, collector, config, http_api, images, job, report,
               storage, tables, task)  # noqa: F401

__all__ = ["archive", "browse", "collector", "config", "http_api", "images", "job",
           "report", "storage", "tables", "task"]
