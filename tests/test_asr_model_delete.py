# -*- coding: utf-8 -*-
"""本地 Whisper 模型删除：只操作临时目录，不触碰真实 models 目录。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from toolbox import asr_engine


class AsrModelDeleteTest(unittest.TestCase):
    def test_delete_ready_and_partial_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(asr_engine, "get_models_dir", return_value=root):
                ready = root / "faster-whisper-base"
                ready.mkdir()
                (ready / "model.bin").write_bytes(b"x")
                (ready / ".ready.json").write_text("{}", encoding="utf-8")
                partial = root / "faster-whisper-small"
                partial.mkdir()
                (partial / "model.bin.part").write_bytes(b"x")

                self.assertTrue(asr_engine.local_model_ready("base"))
                self.assertTrue(asr_engine.local_model_present("base"))
                self.assertFalse(asr_engine.local_model_ready("small"))
                self.assertTrue(asr_engine.local_model_present("small"))

                asr_engine.delete_model("base")
                asr_engine.delete_model("small")
                self.assertFalse(ready.exists())
                self.assertFalse(partial.exists())
                asr_engine.delete_model("base")

    def test_unknown_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(asr_engine, "get_models_dir",
                              return_value=Path(tmp)):
                with self.assertRaises(asr_engine.AsrError):
                    asr_engine.delete_model("../config")


if __name__ == "__main__":
    unittest.main()
