"""定时任务脚本入口（Windows 计划任务调起，无界面）。

pythonw 下 sys.stdout/stderr 为 None，print 会直接崩，这里先补空流再跑采集。
"""

import os
import sys

_SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from toolbox.tcdoc.job import main  # noqa: E402  (依赖上面的 sys.path 注入)

if __name__ == "__main__":
    sys.exit(main())
