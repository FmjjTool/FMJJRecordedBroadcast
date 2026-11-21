"""版本形态：standard（当前发布形态）/ full（内置全部外部依赖的开箱即用版）。

判定顺序：
1. 环境变量 ``FMJJ_EDITION``（仅开发与测试用，取值 standard / full）；
2. 程序目录下的 ``FULL_EDITION`` 标记文件（由 build.py --edition full 放入）；
3. 都没有 → standard。

两种形态共用同一份代码、同一个版本号、同一个安装 AppId；edition 只影响
「内置资源去哪找」和「更新时挑哪个安装包」，不改变任何业务逻辑。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FULL_MARKER = "FULL_EDITION"
ENV_VAR = "FMJJ_EDITION"
VALID = ("standard", "full")


def app_dir() -> Path:
    """当前运行目录：打包后是 exe 所在目录，源码运行是项目根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def edition() -> str:
    env = (os.environ.get(ENV_VAR) or "").strip().lower()
    if env in VALID:
        return env
    try:
        if (app_dir() / FULL_MARKER).is_file():
            return "full"
    except OSError:
        pass
    return "standard"


def is_full() -> bool:
    return edition() == "full"


def bundled_dir(name: str) -> Path:
    """包内资源目录（不存在也返回路径，由调用方判断）。"""
    return app_dir() / name