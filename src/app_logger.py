"""应用日志：每次运行生成独立 log 文件（logs/），自动轮转清理旧文件，捕获未处理异常。

用途：溯源、复现问题。程序启动时调用 setup_logging() 即可。
- 日志文件命名 app_YYYYMMDD_HHMMSS.log，存项目根 logs/ 目录
- 超出 max_files（默认 50）自动删除最旧的
- 未处理异常（含 traceback）自动写入日志
- stdout/stderr 以 Tee 方式同时写控制台和日志文件（文件侧过滤 ANSI 颜色码）
"""

import atexit
import logging
import re
import sys
import time
import traceback
from pathlib import Path

# logs 目录 = 应用根/logs（源码=项目根，打包后=exe 旁）
try:
    from toolbox.config_manager import get_app_root
    LOG_DIR = get_app_root() / "logs"
except Exception:  # 兜底：按 __file__ 推导
    LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

_setup_done = False
_original_stdout = None
_original_stderr = None
_log_file_obj = None


class _Tee:
    """同时写控制台和日志文件；写文件时过滤 ANSI 颜色码（rich 进度条等）。

    console_stream=None 时只写日志文件（GUI 模式：控制台不再刷下载进度/警告，
    全部输出仍进 logs/app_*.log 供溯源）。
    """

    def __init__(self, console_stream, file_obj):
        self._console = console_stream
        self._file = file_obj

    def write(self, data):
        if self._console is not None:
            try:
                self._console.write(data)
            except Exception:
                pass
        try:
            self._file.write(_ANSI_RE.sub("", data))
            self._file.flush()  # 实时落盘，崩溃时也能溯源到最近的输出
        except Exception:
            pass

    def flush(self):
        for s in (self._console, self._file):
            if s is None:
                continue
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def fileno(self):
        if self._console is None:
            return -1
        try:
            return self._console.fileno()
        except Exception:
            return -1


def _rotate_logs(max_files: int):
    """删除最旧的日志，保留最近 max_files 个（含即将新建的）。"""
    if max_files <= 0:
        return
    if not LOG_DIR.is_dir():
        return
    logs = sorted(LOG_DIR.glob("app_*.log"), key=lambda p: p.name)
    # 即将新建 1 个，所以最多保留 max_files - 1 个旧的
    while len(logs) >= max_files:
        oldest = logs.pop(0)
        try:
            oldest.unlink()
        except OSError:
            pass


def _excepthook(exc_type, exc_value, exc_tb):
    """未处理异常 → 写日志 + 回显控制台。"""
    tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        logging.error("未处理异常:\n%s", tb_text)
    except Exception:
        pass
    if _original_stderr is not None:
        try:
            _original_stderr.write(tb_text)
        except Exception:
            pass


def _flush_on_exit():
    try:
        logging.shutdown()
    except Exception:
        pass
    if _log_file_obj is not None:
        try:
            _log_file_obj.flush()
            _log_file_obj.close()
        except Exception:
            pass


def setup_logging(max_files: int = 50, console: bool = True) -> str:
    """初始化日志系统，返回日志文件路径。可重复调用（幂等）。

    console=False：stdout/stderr 只写日志文件不回显控制台（GUI 启动用，
    控制台不再被下载进度/Qt 警告刷屏；未处理异常仍回显真实 stderr）。
    """
    global _setup_done, _original_stdout, _original_stderr, _log_file_obj
    if _setup_done:
        return ""

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _rotate_logs(max_files)

    now = time.time()
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
    ms = int((now % 1) * 1000)
    log_path = LOG_DIR / f"app_{ts}_{ms:03d}.log"
    # 极端情况下（同毫秒）仍撞名，加序号兜底
    i = 2
    while log_path.exists():
        log_path = LOG_DIR / f"app_{ts}_{ms:03d}_{i}.log"
        i += 1
    _log_file_obj = open(log_path, "w", encoding="utf-8")

    # 配置根 logger（写入文件；控制台仍靠原 stdout）
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # 捕获未处理异常
    sys.excepthook = _excepthook

    # Tee stdout/stderr 到日志文件（console=False 时控制台侧静默，文件侧过滤 ANSI）
    _original_stdout = sys.stdout
    _original_stderr = sys.stderr
    sys.stdout = _Tee(_original_stdout if console else None, _log_file_obj)
    sys.stderr = _Tee(_original_stderr if console else None, _log_file_obj)

    atexit.register(_flush_on_exit)

    logging.info("=" * 60)
    logging.info("应用启动，日志文件: %s", log_path)
    logging.info("=" * 60)

    _setup_done = True
    return str(log_path)
