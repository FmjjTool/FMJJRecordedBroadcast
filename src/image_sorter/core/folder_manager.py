"""
文件夹管理模块
负责工作目录的设置、内置文件夹创建与目录枚举。
（文件夹的创建/重命名/删除操作由 UI 层 folder_panel 直接执行）
"""

from pathlib import Path
from typing import List, Tuple
from image_sorter.constants import BUILTIN_FOLDERS
from image_sorter.exceptions import PermissionException, WorkDirException
from image_sorter.settings import is_directory_writable
from image_sorter._log import logger


class FolderManager:
    """文件夹管理器"""

    def __init__(self, work_dir: str = ""):
        """
        初始化文件夹管理器

        Args:
            work_dir: 工作目录路径
        """
        self._work_dir = Path(work_dir) if work_dir else None
        if self._work_dir:
            self._ensure_builtin_folders()

    @property
    def work_dir(self) -> Path:
        """获取工作目录"""
        if self._work_dir is None:
            raise WorkDirException("未设置工作目录")
        return self._work_dir

    def set_work_dir(self, work_dir: str):
        """
        设置工作目录并创建系统内置文件夹

        Args:
            work_dir: 工作目录路径
        """
        work_path = Path(work_dir)

        # 检查目录是否存在
        if not work_path.exists():
            raise WorkDirException(f"目录不存在: {work_dir}")

        # 检查写入权限（实际写探测，os.access 在 Windows 上不可靠）
        if not is_directory_writable(work_path):
            raise PermissionException(work_dir, "工作目录无写入权限")

        self._work_dir = work_path
        self._ensure_builtin_folders()
        logger.log_operation("设置工作目录", str(work_path))

    def _ensure_builtin_folders(self):
        """确保系统内置文件夹存在，不存在则自动创建"""
        for folder_name in BUILTIN_FOLDERS.values():
            folder_path = self.work_dir / folder_name
            if not folder_path.exists():
                try:
                    folder_path.mkdir(parents=True, exist_ok=True)
                    logger.log_operation("创建系统文件夹", folder_name)
                except Exception as e:
                    logger.log_exception(e, f"创建系统文件夹失败: {folder_name}")

    def get_folders(self, sort_by: str = 'time') -> List[Tuple[str, bool, Path]]:
        """
        获取工作目录下所有文件夹列表

        Args:
            sort_by: 排序方式，'time'按创建时间倒序，'name'按名称正序

        Returns:
            List of (文件夹名, 是否为系统内置, 文件夹路径)
        """
        if self._work_dir is None or not self._work_dir.exists():
            return []

        folders = []
        builtin_names = set(BUILTIN_FOLDERS.values())

        for item in self._work_dir.iterdir():
            if item.is_dir():
                is_builtin = item.name in builtin_names
                folders.append((item.name, is_builtin, item))

        # 排序
        if sort_by == 'time':
            folders.sort(key=lambda x: x[2].stat().st_ctime, reverse=True)
        elif sort_by == 'name':
            folders.sort(key=lambda x: x[0])

        return folders
