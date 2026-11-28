# -*- coding: utf-8 -*-
"""直播总结 · 大模型方案（独立于纯规则保底的增强链路）。

- `config`：模型地址、模型名、分块字数与 API Key 的本机加密存储
- `rules`：读取内置 skill 的规则文件并拼装提示词
- `client`：OpenAI Chat Completions、OpenAI Responses 与 Anthropic Messages 客户端
- `pipeline`：分块提取 → 合并 → 终稿 → 输出校验

本包不依赖 PySide6，页面与命令行都可以调用 `pipeline.run()`。
"""
