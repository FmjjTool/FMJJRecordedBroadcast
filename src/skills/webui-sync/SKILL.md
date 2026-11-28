---
name: webui-sync
description: "桌面到 WebUI 同步兼容入口。桌面用户能力新增/变化或 WebUI 缺失/不一致时，转交 layering-contract 的同步参考；保留四层同批接入、字段表、桌面专属白名单和三层验收。"
---

# 桌面到 WebUI 同步（兼容入口）

先阅读 [项目总约定](../convention-skill/SKILL.md) 与 [架构总入口](../layering-contract/SKILL.md)，再完整阅读 [桌面到 WebUI 同步规范正文](../layering-contract/references/webui-sync.md)。涉及中间层/进程边界时同时阅读 [分层详细规则的 WebUI 中间层隔离](../layering-contract/references/architecture.md#webui-中间层隔离强制)。

本入口保留旧 skill 名、目录与下载 ID；四层接入、参数字段表、死控件检查、导航对齐、桌面专属判定、同批提交及三层验收只在上述参考文档维护。

单独导出本兼容目录不包含规范正文；交给其他工具使用时，须同时导出 `layering-contract` 到同一父目录，保留上述相对路径。

业务页面经 L3，桌面与 Web 共用规则和数据、隔离页面与进程状态；验证遵守隔离测试及数据保护要求。
