---
name: platform-adapter-policy
description: "平台适配兼容入口。新增/修改平台接口、登录适配、下载或回退策略时，转交 layering-contract 的平台适配参考；保留契约、网络边界、能力矩阵和错误分类要求。"
---

# 平台适配与外部接口（兼容入口）

先阅读 [项目总约定](../convention-skill/SKILL.md) 与 [架构总入口](../layering-contract/SKILL.md)，再完整阅读 [平台适配规范正文](../layering-contract/references/platform-adapter-policy.md)。

本入口保留旧 skill 名、目录与下载 ID；Adapter 契约、能力矩阵、HTTP/浏览器回退、限流、签名、错误与契约测试规则只在上述参考文档维护。涉及持久化或 WebUI 的部分，按架构总入口的触发表补读对应参考。

单独导出本兼容目录不包含规范正文；交给其他工具使用时，须同时导出 `layering-contract` 到同一父目录，保留上述相对路径。

页面仍只经 L3 调用平台能力；本入口不授权真实账户操作或扩大腾讯文档写入范围。
