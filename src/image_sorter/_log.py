"""日志兼容层：保持源项目 logger 接口（log_operation / log_exception 等），
底层走标准 logging（app_logger.setup_logging 统一接管，GUI 控制台静默）。
"""

import logging
import sys

_log = logging.getLogger("image_sorter")


class _CompatLogger:
    """源项目 logger 的等价接口，core 层直迁文件无需改动调用点。"""

    def debug(self, msg, *args):
        _log.debug(msg, *args)

    def info(self, msg, *args):
        _log.info(msg, *args)

    def warning(self, msg, *args):
        _log.warning(msg, *args)

    def error(self, msg, *args):
        _log.error(msg, *args)

    def log_operation(self, action, detail=""):
        if detail:
            _log.info("[操作] %s: %s", action, detail)
        else:
            _log.info("[操作] %s", action)

    def log_exception(self, e, context=""):
        # 在 except 块内调用时带完整 traceback，否则只记异常摘要
        if sys.exc_info()[0] is not None:
            _log.exception("%s", context or "异常")
        else:
            _log.error("%s: %r", context or "异常", e)

    def log_permission_error(self, path, action=""):
        _log.error("[权限] %s: %s", action or "访问被拒绝", path)

    def log_file_corrupted(self, path, detail=""):
        if detail:
            _log.warning("[损坏] %s (%s)", path, detail)
        else:
            _log.warning("[损坏] %s", path)


logger = _CompatLogger()
