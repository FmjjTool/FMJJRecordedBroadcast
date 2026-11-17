"""站点访问统计 · 定时任务入口（Windows 计划任务调起，无界面）。

pythonw / 打包 --noconsole 下 sys.stdout/stderr 为 None，print 会直接崩，
这里先补路径与空流，再调 analytics.collect() 采集一次并写日志。
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

from toolbox.webwiki import analytics  # noqa: E402  (依赖上面的 sys.path 注入)


def main() -> int:
    def log(text):
        analytics.append_job_log(str(text))

    log("定时采集开始")
    try:
        row = analytics.collect(log=log)
    except analytics.AnalyticsError as error:
        log(f"采集失败：{error}")
        return 1
    except Exception as error:  # noqa: BLE001 - 定时任务不弹窗，只写日志
        log(f"采集异常：{error.__class__.__name__}: {error}")
        return 1
    log(f"采集完成：{row['date']} 访客 {row['visitors']}"
        f" · 打开 {row['sessions']} · 浏览量 {row['pageviews']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
