# -*- coding: utf-8 -*-
"""夸克网盘备份 · 无界面定时执行（`main.py --quark-backup` 调用）。

与 GUI「立即备份」共用 `backup.run_all`，只是把日志写进
`config/quark_backup_task.log`：计划任务由 pythonw / 无控制台 exe 调起时
stdout 不存在，只能靠文件留痕。

退出码：0 = 全部成功或无事可做；1 = 存在失败（含未登录）。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

LOG_MAX_BYTES = 2 * 1024 * 1024


def _log_path() -> Path:
    from toolbox.config_manager import get_config_dir
    return Path(get_config_dir()) / "quark_backup_task.log"


def log(message: str) -> None:
    """追加一行备份日志（无控制台环境下也能留痕）。"""
    try:
        path = _log_path()
        if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
            backup = path.with_suffix(".1.log")
            try:
                if backup.exists():
                    backup.unlink()
                path.rename(backup)
            except OSError:
                pass
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except Exception:  # noqa: BLE001 - 日志失败不能打断备份
        pass


def main() -> int:
    from toolbox.quark import backup, config as qcfg
    from toolbox.quark.backup import QuarkAuthError

    mappings = [m for m in qcfg.load_mappings() if m.enabled and m.local_dir]
    if not mappings:
        log("没有启用的备份目录，跳过本次")
        return 0

    log(f"开始备份：{len(mappings)} 组映射")
    try:
        results = backup.run_all(mappings, log=log)
    except QuarkAuthError as exc:
        log(f"未登录：{exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - 计划任务里不能抛栈
        log(f"备份异常终止：{exc}")
        return 1

    failed = [r for r in results if not r.ok]
    for result in results:
        log(f"[{result.remote_dir or '-'}] {result.summary()}"
            f"{'' if result.ok else '  错误：' + result.message}")
    log(f"结束：成功 {len(results) - len(failed)} 组，失败 {len(failed)} 组")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
