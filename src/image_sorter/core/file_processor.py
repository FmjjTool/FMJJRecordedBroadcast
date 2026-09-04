"""
文件处理模块
负责文件的复制、移动、重命名等操作
"""

import shutil
from pathlib import Path
from typing import Dict, List, Optional
from image_sorter.exceptions import FileOperationException, PermissionException
from image_sorter.settings import is_directory_writable
from image_sorter._log import logger


class FileProcessor:
    """文件操作处理器"""

    @staticmethod
    def copy_file_to_folder(source_file: Path, target_folder: Path,
                           preserve_metadata: bool = True) -> Optional[Path]:
        """
        复制文件到目标文件夹，自动处理重名

        Args:
            source_file: 源文件路径
            target_folder: 目标文件夹路径
            preserve_metadata: 是否保留元数据

        Returns:
            目标文件路径，失败返回None
        """
        try:
            # 检查目标文件夹是否存在
            if not target_folder.exists():
                target_folder.mkdir(parents=True, exist_ok=True)

            # 检查写入权限
            if not FileProcessor._check_writable(target_folder):
                raise PermissionException(str(target_folder))

            # 处理重名
            target_file = FileProcessor._get_unique_filename(
                target_folder, source_file.name
            )

            # 复制文件
            if preserve_metadata:
                shutil.copy2(source_file, target_file)  # 保留元数据
            else:
                shutil.copy(source_file, target_file)

            logger.log_operation(
                "复制文件",
                f"{source_file.name} -> {target_folder.name}"
            )

            return target_file

        except PermissionException:
            raise
        except Exception as e:
            logger.log_exception(e, f"复制文件失败: {source_file}")
            raise FileOperationException("copy", str(source_file), str(target_folder))

    @staticmethod
    def move_file_to_folder(source_file: Path, target_folder: Path) -> Optional[Path]:
        """
        移动文件到目标文件夹，自动处理重名

        Args:
            source_file: 源文件路径
            target_folder: 目标文件夹路径

        Returns:
            目标文件路径，失败返回None
        """
        try:
            if not target_folder.exists():
                target_folder.mkdir(parents=True, exist_ok=True)

            if not FileProcessor._check_writable(target_folder):
                raise PermissionException(str(target_folder))

            target_file = FileProcessor._get_unique_filename(
                target_folder, source_file.name
            )
            moved_file = Path(shutil.move(str(source_file), str(target_file)))

            logger.log_operation(
                "移动文件",
                f"{source_file.name} -> {target_folder.name}"
            )
            return moved_file

        except PermissionException:
            raise
        except Exception as e:
            logger.log_exception(e, f"移动文件失败: {source_file}")
            raise FileOperationException("move", str(source_file), str(target_folder))

    @staticmethod
    def transfer_file_to_folder(
        source_file: Path,
        target_folder: Path,
        mode: str = "copy",
        preserve_metadata: bool = True,
    ) -> Optional[Path]:
        """
        统一文件传输入口（复制/移动）

        Args:
            source_file: 源文件路径
            target_folder: 目标文件夹路径
            mode: copy 或 move
            preserve_metadata: copy 模式下是否保留元数据
        """
        normalized_mode = str(mode).strip().lower()
        if normalized_mode == "move":
            return FileProcessor.move_file_to_folder(source_file, target_folder)
        return FileProcessor.copy_file_to_folder(
            source_file,
            target_folder,
            preserve_metadata=preserve_metadata,
        )

    @staticmethod
    def transfer_files_batch(
        source_files: List[Path],
        target_folder: Path,
        mode: str = "copy",
    ) -> List[Dict[str, Path]]:
        """
        批量传输文件（复制/移动）

        Returns:
            成功传输记录列表，每项包含 source/target
        """
        records: List[Dict[str, Path]] = []
        normalized_mode = str(mode).strip().lower()
        op_name = "批量移动" if normalized_mode == "move" else "批量复制"

        for source_file in source_files:
            try:
                target_file = FileProcessor.transfer_file_to_folder(
                    source_file, target_folder, mode=normalized_mode
                )
                if target_file is None:
                    continue
                records.append({
                    "source": source_file,
                    "target": target_file,
                })
            except Exception as e:
                logger.log_exception(e, f"{op_name}失败: {source_file}")
                continue

        logger.log_operation(
            op_name,
            f"成功 {len(records)}/{len(source_files)} 个文件到 {target_folder.name}"
        )
        return records

    @staticmethod
    def copy_files_batch(source_files: List[Path], target_folder: Path) -> int:
        """
        批量复制文件

        Args:
            source_files: 源文件路径列表
            target_folder: 目标文件夹路径

        Returns:
            成功复制的文件数量
        """
        return len(FileProcessor.transfer_files_batch(source_files, target_folder, mode="copy"))

    @staticmethod
    def _get_unique_filename(folder: Path, filename: str) -> Path:
        """
        获取唯一文件名，避免重名覆盖

        Args:
            folder: 目标文件夹
            filename: 文件名

        Returns:
            唯一的文件路径
        """
        target_path = folder / filename

        # 如果不重名，直接返回
        if not target_path.exists():
            return target_path

        # 处理重名：添加(1), (2), ...后缀
        stem = target_path.stem  # 不含扩展名的文件名
        suffix = target_path.suffix  # 扩展名
        counter = 1

        while counter <= 10000:
            new_name = f"{stem}({counter}){suffix}"
            new_path = folder / new_name

            if not new_path.exists():
                return new_path

            counter += 1

        # 重名次数过多（异常情况），避免死循环
        raise FileOperationException(
            "unique_filename", str(target_path), str(folder),
            message=f"生成唯一文件名失败，重名次数超过上限: {target_path}",
        )

    @staticmethod
    def _check_writable(folder: Path) -> bool:
        """
        检查文件夹是否可写

        Args:
            folder: 文件夹路径

        Returns:
            是否可写
        """
        return is_directory_writable(folder)
