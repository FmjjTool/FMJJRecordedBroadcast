"""本地资料站：从 GitHub 拉取 webwiki / webwiki-api，同步腾讯文档并启动本地服务。

模块划分：
- config : 目录 / 端口 / GitHub 登录态 / 系统代理 / 解释器探测
- auth   : GitHub OAuth 设备码登录（扫码打开授权页 + 输入设备码）
- gitops : git clone / pull（私有仓库用登录 Token）
- deploy : Python 虚拟环境、依赖安装、腾讯文档同步、前端构建
- server : uvicorn 后端 + 静态站反代（本地浏览器访问）
"""

from __future__ import annotations
