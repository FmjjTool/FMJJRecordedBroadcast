"""自定义异常类（直迁自源项目 utils/exceptions.py）。"""


class AppException(Exception):
    """应用程序基础异常类"""
    pass


class PermissionException(AppException):
    """文件夹权限异常"""
    def __init__(self, path, message="无操作权限"):
        self.path = path
        self.message = f"{message}: {path}"
        super().__init__(self.message)


class CorruptedFileException(AppException):
    """媒体文件损坏异常"""
    def __init__(self, file_path, message="文件已损坏"):
        self.file_path = file_path
        self.message = f"{message}: {file_path}"
        super().__init__(self.message)


class UnsupportedFormatException(AppException):
    """不支持的文件格式异常"""
    def __init__(self, file_path, format_):
        self.file_path = file_path
        self.format = format_
        self.message = f"不支持的格式 {format_}: {file_path}"
        super().__init__(self.message)


class WorkDirException(AppException):
    """工作目录异常"""
    def __init__(self, message="工作目录配置异常"):
        self.message = message
        super().__init__(self.message)


class FileOperationException(AppException):
    """文件操作异常"""
    def __init__(self, operation, source, target, message=""):
        self.operation = operation
        self.source = source
        self.target = target
        if not message:
            message = f"文件操作失败 ({operation}): {source} -> {target}"
        self.message = message
        super().__init__(self.message)
