"""operation_history 单元测试（随迁自源项目 tests/test_operation_history.py）"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from image_sorter.core.operation_history import OperationHistory, OperationType


class TestOperationHistoryUndo(unittest.TestCase):
    def test_undo_move_restores_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src_dir = tmp / "src"
            dst_dir = tmp / "dst"
            src_dir.mkdir()
            dst_dir.mkdir()
            src_file = src_dir / "a.jpg"
            src_file.write_bytes(b"data")
            dst_file = dst_dir / "a.jpg"

            import shutil
            shutil.move(str(src_file), str(dst_file))
            self.assertFalse(src_file.exists())

            history = OperationHistory()
            history.add_operation(OperationType.KEEP, {
                "mode": "move",
                "transfers": [{"source": str(src_file), "target": str(dst_file)}],
            })
            history.undo()
            self.assertTrue(src_file.exists())
            self.assertFalse(dst_file.exists())

    def test_undo_copy_deletes_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src_dir = tmp / "src"
            dst_dir = tmp / "dst"
            src_dir.mkdir()
            dst_dir.mkdir()
            src_file = src_dir / "a.jpg"
            src_file.write_bytes(b"data")
            dst_file = dst_dir / "a.jpg"

            import shutil
            shutil.copy2(str(src_file), str(dst_file))

            history = OperationHistory()
            history.add_operation(OperationType.KEEP, {
                "mode": "copy",
                "transfers": [{"source": str(src_file), "target": str(dst_file)}],
            })
            history.undo()
            # 复制模式撤回：删除目标文件，源文件不受影响
            self.assertTrue(src_file.exists())
            self.assertFalse(dst_file.exists())

    def test_undo_empty_history_returns_none(self):
        history = OperationHistory()
        self.assertIsNone(history.undo())


if __name__ == "__main__":
    unittest.main()
