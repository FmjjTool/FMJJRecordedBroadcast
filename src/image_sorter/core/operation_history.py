"""
操作历史管理器
用于记录和撤回文件操作
"""

from pathlib import Path
from typing import List, Dict, Any, Optional
from enum import Enum
import shutil
from image_sorter._log import logger


class OperationType(Enum):
    """操作类型"""
    KEEP = "keep"
    DELETE = "delete"
    FAVORITE = "favorite"
    FOLDER_CREATE = "folder_create"
    FOLDER_DELETE = "folder_delete"
    FOLDER_RENAME = "folder_rename"


class Operation:
    """单个操作记录"""

    def __init__(self, op_type: OperationType, data: Dict[str, Any]):
        self.op_type = op_type
        self.data = data

    def __repr__(self):
        return f"Operation({self.op_type.value}, {self.data})"


class OperationHistory:
    """操作历史管理器"""

    def __init__(self, max_history: int = 50):
        self.history: List[Operation] = []
        self.max_history = max_history

    def add_operation(self, op_type: OperationType, data: Dict[str, Any]):
        """
        添加操作记录

        Args:
            op_type: 操作类型
            data: 操作数据
        """
        operation = Operation(op_type, data)
        self.history.append(operation)

        # 限制历史记录数量
        if len(self.history) > self.max_history:
            self.history.pop(0)

        logger.info(f"记录操作: {operation}")

    def undo(self) -> Optional[Operation]:
        """
        撤回最后一个操作

        Returns:
            被撤回的操作，如果没有可撤回的操作则返回None
        """
        if not self.history:
            logger.warning("没有可撤回的操作")
            return None

        operation = self.history.pop()
        logger.info(f"撤回操作: {operation}")

        try:
            self._revert_operation(operation)
            return operation
        except Exception as e:
            logger.log_exception(e, f"撤回操作失败: {operation}")
            # 如果撤回失败，重新加入历史记录
            self.history.append(operation)
            raise

    def _revert_operation(self, operation: Operation):
        """
        还原操作

        Args:
            operation: 要还原的操作
        """
        if operation.op_type in (OperationType.KEEP, OperationType.DELETE, OperationType.FAVORITE):
            self._revert_file_operation(operation)
        elif operation.op_type == OperationType.FOLDER_CREATE:
            self._revert_folder_create(operation)
        elif operation.op_type == OperationType.FOLDER_DELETE:
            self._revert_folder_delete(operation)
        elif operation.op_type == OperationType.FOLDER_RENAME:
            self._revert_folder_rename(operation)

    def _revert_file_operation(self, operation: Operation):
        """
        还原文件操作（保留/删除/收藏）

        Args:
            operation: 文件操作记录
        """
        mode = self._normalize_operation_mode(operation.data.get('mode', 'copy'))
        transfers = self._extract_transfer_records(operation.data)

        for source_file, target_file in transfers:
            if mode == "move":
                self._revert_move_transfer(source_file, target_file)
            else:
                self._revert_copy_transfer(target_file)

    @staticmethod
    def _normalize_operation_mode(raw_mode: Any) -> str:
        mode = str(raw_mode).strip().lower()
        return mode if mode in ("copy", "move") else "copy"

    @staticmethod
    def _extract_transfer_records(data: Dict[str, Any]) -> List[tuple[Path, Path]]:
        """
        统一提取 source/target 映射，兼容旧版记录格式
        """
        transfer_records: List[tuple[Path, Path]] = []
        transfers = data.get("transfers", [])

        if isinstance(transfers, list) and transfers:
            for item in transfers:
                if not isinstance(item, dict):
                    continue
                source_raw = str(item.get("source", "")).strip()
                target_raw = str(item.get("target", "")).strip()
                if not source_raw or not target_raw:
                    continue
                transfer_records.append((Path(source_raw), Path(target_raw)))
            if transfer_records:
                return transfer_records

        files = data.get("files", [])
        target_folder = Path(str(data.get("target_folder", "")).strip())
        for file_path in files:
            source_file = Path(file_path)
            transfer_records.append((source_file, target_folder / source_file.name))
        return transfer_records

    @staticmethod
    def _revert_copy_transfer(target_file: Path):
        if not target_file.exists():
            return
        try:
            target_file.unlink()
            logger.info(f"已删除: {target_file}")
        except Exception as e:
            logger.error(f"删除文件失败: {target_file}, {e}")

    @staticmethod
    def _revert_move_transfer(source_file: Path, target_file: Path):
        if not target_file.exists():
            logger.warning(f"撤回移动失败，目标文件不存在: {target_file}")
            return
        if source_file.exists():
            raise FileExistsError(f"撤回移动失败，源路径已存在文件: {source_file}")

        source_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target_file), str(source_file))
        logger.info(f"已恢复: {target_file} -> {source_file}")

    def _revert_folder_create(self, operation: Operation):
        """
        还原文件夹创建操作

        Args:
            operation: 文件夹创建操作记录
        """
        folder_path = Path(operation.data.get('folder_path', ''))

        if folder_path.exists():
            try:
                shutil.rmtree(folder_path)
                logger.info(f"已删除文件夹: {folder_path}")
            except Exception as e:
                logger.error(f"删除文件夹失败: {folder_path}, {e}")

    def _revert_folder_delete(self, operation: Operation):
        """
        还原文件夹删除操作（重新创建）

        Args:
            operation: 文件夹删除操作记录
        """
        folder_path = Path(operation.data.get('folder_path', ''))

        if not folder_path.exists():
            try:
                folder_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"已恢复文件夹: {folder_path}")
            except Exception as e:
                logger.error(f"恢复文件夹失败: {folder_path}, {e}")

    def _revert_folder_rename(self, operation: Operation):
        """
        还原文件夹重命名操作

        Args:
            operation: 文件夹重命名操作记录
        """
        old_path = Path(operation.data.get('old_path', ''))
        new_path = Path(operation.data.get('new_path', ''))

        if new_path.exists():
            try:
                new_path.rename(old_path)
                logger.info(f"已还原文件夹名称: {new_path} -> {old_path}")
            except Exception as e:
                logger.error(f"还原文件夹名称失败: {e}")

    def clear(self):
        """清空历史记录"""
        self.history.clear()
        logger.info("已清空操作历史")
