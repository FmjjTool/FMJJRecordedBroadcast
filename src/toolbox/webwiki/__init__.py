"""本地资料站：分别管理微信小程序主程序与 webwiki / webwiki-api 两个项目。

模块划分：
- config : 目录 / 端口 / GitHub 登录态 / 系统代理 / 解释器探测 / 仓库分层
- auth   : GitHub OAuth 设备码登录（扫码打开授权页 + 输入设备码）
- gitops : git clone / pull（私有仓库用登录 Token）
- deploy : Python 虚拟环境、依赖安装、本地数据重建、前端构建
- server : uvicorn 后端 + 静态站反代（本地浏览器访问）
"""

from __future__ import annotations
