---
name: data-lifecycle
description: "数据生命周期兼容入口。新增/修改数据库、存储、去重、迁移、备份或保留清理时，转交 layering-contract 的数据生命周期参考；不授权真实凭据或腾讯文档写入。"
---

# 数据生命周期（兼容入口）

先阅读 [项目总约定](../convention-skill/SKILL.md) 与 [架构总入口](../layering-contract/SKILL.md)，再完整阅读 [数据生命周期规范正文](../layering-contract/references/data-lifecycle.md)。

本入口保留旧 skill 名、目录与下载 ID；owner、schema、迁移、去重、primary/derived、事务、保留、备份恢复及导入导出规则只在上述参考文档维护。涉及跨层、平台或 WebUI 的部分，按架构总入口的触发表补读对应参考。

单独导出本兼容目录不包含规范正文；交给其他工具使用时，须同时导出 `layering-contract` 到同一父目录，保留上述相对路径。

真实凭据、核心模块和腾讯文档范围的保护不因转交而改变；本入口不执行迁移、覆盖、清理或其他数据操作。
