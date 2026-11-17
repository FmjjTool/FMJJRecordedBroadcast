# -*- coding: utf-8 -*-
"""大模型直播总结的分块、校验、流水线与配置隔离回归；不联网、不调用真实接口。"""

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from toolbox import livestream_summary_rule as rule
from toolbox.livestream_summary import config as ls_config
from toolbox.livestream_summary import pipeline
from toolbox.livestream_summary.client import ChatClient, ChatError, CancelledError
from toolbox.livestream_summary.rules import load_rules


def valid_output() -> str:
    text, _ = rule.summarize_text("下周我要发布一首新歌。我觉得恋爱最重要的是沟通。",
                                  "20260817_直播.txt")
    return text


class ChunkTest(unittest.TestCase):
    def test_lines_not_split(self):
        chunks = pipeline.split_chunks("第一行的内容\n第二行的内容\n第三行的内容", 10)
        self.assertTrue(all(len(chunk) <= 10 for chunk in chunks))
        self.assertEqual(len(chunks), 3)

    def test_long_line_split_by_sentence(self):
        chunks = pipeline.split_chunks("第一句话。第二句话。第三句话。", 8)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 8 for chunk in chunks))

    def test_empty(self):
        self.assertEqual(pipeline.split_chunks("   \n\n ", 100), [])


class ValidateTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(pipeline.validate_output(valid_output(), "20260817"), [])

    def test_wrong_title(self):
        output = valid_output().replace("封茗囧菌 20260817 直播总结", "总结", 1)
        self.assertTrue(any("标题" in item for item in
                            pipeline.validate_output(output, "20260817")))

    def test_missing_dimension(self):
        output = valid_output().replace("4 级：直播中提及的「学校相关事件」（未提及）\n", "")
        self.assertTrue(any("缺少维度" in item for item in
                            pipeline.validate_output(output, "20260817")))

    def test_markdown_and_placeholder(self):
        output = valid_output() + "\n**加粗**\n[分类名称]：测试\n"
        problems = pipeline.validate_output(output, "20260817")
        self.assertTrue(any("Markdown" in item for item in problems))
        self.assertTrue(any("占位符" in item for item in problems))

    def test_missing_tail(self):
        lines = valid_output().splitlines()[:-2]
        problems = pipeline.validate_output("\n".join(lines), "20260817")
        self.assertTrue(any("末尾" in item for item in problems))


class PipelineTest(unittest.TestCase):
    class FakeClient:
        def __init__(self, final_text):
            self.final_text = final_text
            self.calls = []
            self.requests_made = 0

        def chat(self, system, user, **kwargs):
            self.calls.append(system)
            self.requests_made += 1
            if "信息提取器" in system:
                return "候选维度 1：下周发布新歌。"
            return self.final_text

    def test_run_with_two_chunks(self):
        client = self.FakeClient(valid_output())
        text = "下周我要发布一首新歌，叫《夏天的风》。\n我觉得恋爱最重要的是沟通。"
        result = pipeline.run(text, "20260817_直播.txt", client, chunk_chars=20)
        self.assertEqual(result.text, valid_output())
        self.assertEqual(result.problems, [])
        self.assertGreaterEqual(result.stats["chunks"], 2)
        self.assertEqual(result.stats["chunks"] + 1, len(client.calls))
        self.assertEqual(result.stats["date"], "20260817")

    def test_cancel(self):
        client = self.FakeClient(valid_output())
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            pipeline.run("一些内容。", "直播.txt", client, cancel=cancel)

    def test_repair_called_on_bad_output(self):
        bad = "不合格的输出"
        client = self.FakeClient(bad)
        result = pipeline.run("下周我要发布新歌。", "直播.txt", client, chunk_chars=100)
        self.assertTrue(result.problems)
        # 提取 1 次 + 终稿 1 次 + 修正 1 次
        self.assertEqual(len(client.calls), 3)

    def test_run_rule_local(self):
        result = pipeline.run_rule("下周我要发布一首新歌。", "20260817_直播.txt")
        self.assertEqual(result.mode, "rule")
        self.assertEqual(result.stats["requests"], 0)
        self.assertTrue(result.text.startswith("封茗囧菌 20260817 直播总结"))
        self.assertEqual(result.problems, [])


class RuleTest(unittest.TestCase):
    def test_rule_bundle(self):
        bundle = load_rules()
        self.assertIn("封茗囧菌", bundle.final)
        self.assertIn("1 级", bundle.final)
        self.assertIn("[实际分类名称]", bundle.final)
        self.assertIn("纠错映射表", bundle.extract)


class ConfigTest(unittest.TestCase):
    def test_api_key_roundtrip_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ls_config, "get_config_dir", return_value=Path(tmp)):
                self.assertTrue(ls_config.save_api_key("sk-fictional-123"))
                self.assertEqual(ls_config.load_api_key(), "sk-fictional-123")
                self.assertTrue(ls_config.has_api_key())
                ls_config.save_api_key("")
                self.assertEqual(ls_config.load_api_key(), "")
                self.assertFalse(ls_config.has_api_key())

    def test_settings_clamped(self):
        with patch.object(ls_config, "load_convert_config",
                          return_value={ls_config.CFG_CHUNK_CHARS: "abc"}):
            self.assertEqual(ls_config.load_settings().chunk_chars,
                             ls_config.DEFAULT_CHUNK_CHARS)
        with patch.object(ls_config, "load_convert_config",
                          return_value={ls_config.CFG_CHUNK_CHARS: 100}):
            self.assertEqual(ls_config.load_settings().chunk_chars, 2000)
        with patch.object(ls_config, "load_convert_config",
                          return_value={ls_config.CFG_MODE: "bad"}):
            self.assertEqual(ls_config.load_settings().mode, "ai")

    def test_save_settings_keys(self):
        with patch.object(ls_config, "load_convert_config", return_value={}), \
                patch.object(ls_config, "save_convert_config") as save:
            ls_config.save_settings(ls_config.Settings(
                base_url="https://example.com/v1", model="demo-model",
                chunk_chars=4000, output_dir="D:/out", mode="rule"))
            saved = save.call_args[0][0]
            self.assertEqual(saved[ls_config.CFG_BASE_URL], "https://example.com/v1")
            self.assertEqual(saved[ls_config.CFG_MODEL], "demo-model")
            self.assertEqual(saved[ls_config.CFG_CHUNK_CHARS], 4000)
            self.assertEqual(saved[ls_config.CFG_OUTPUT_DIR], "D:/out")
            self.assertEqual(saved[ls_config.CFG_MODE], "rule")


class ClientTest(unittest.TestCase):
    def test_endpoint(self):
        from toolbox.livestream_summary.client import _endpoint
        self.assertEqual(_endpoint("https://api.openai.com/v1"),
                         "https://api.openai.com/v1/chat/completions")
        self.assertEqual(_endpoint("https://api.deepseek.com/"),
                         "https://api.deepseek.com/chat/completions")
        self.assertEqual(_endpoint("https://x/v1/chat/completions"),
                         "https://x/v1/chat/completions")
        with self.assertRaises(ChatError):
            _endpoint("")

    def test_local_endpoint(self):
        from toolbox.livestream_summary.client import is_local_base_url
        self.assertTrue(is_local_base_url("http://127.0.0.1:11434/v1"))
        self.assertTrue(is_local_base_url("http://localhost:1234/v1"))
        self.assertFalse(is_local_base_url("https://api.openai.com/v1"))

    def test_empty_key_allowed_for_local(self):
        ChatClient("http://127.0.0.1:11434/v1", "", "qwen2.5:7b",
                   allow_empty_key=True)._validate()
        with self.assertRaises(ChatError):
            ChatClient("https://api.openai.com/v1", "", "m")._validate()

    def test_missing_key(self):
        with self.assertRaises(ChatError):
            ChatClient("https://x/v1", "", "m").chat("s", "u")

    def test_content_strip_fence(self):
        from toolbox.livestream_summary.client import _content
        self.assertEqual(
            _content({"choices": [{"message": {"content": "```\n正文\n```"}}]}),
            "正文")
        with self.assertRaises(ChatError):
            _content({"choices": [{"message": {"content": ""}}]})


class LocalModelTest(unittest.TestCase):
    def test_chat_base(self):
        from toolbox.livestream_summary import local_model
        self.assertEqual(local_model.chat_base_url(), "http://127.0.0.1:11434/v1")

    def test_recommended_models(self):
        from toolbox.livestream_summary import local_model
        self.assertTrue(local_model.RECOMMENDED_MODELS)
        for item in local_model.RECOMMENDED_MODELS:
            self.assertTrue(item["name"])
            self.assertTrue(item["label"])
            self.assertTrue(item["size"])

    def test_parse_pull_event(self):
        from toolbox.livestream_summary import local_model
        event = local_model.parse_pull_event(
            '{"status":"downloading","completed":50,"total":100}')
        self.assertEqual(event["percent"], 50)
        self.assertFalse(event["done"])
        self.assertTrue(local_model.parse_pull_event('{"status":"success"}')["done"])
        self.assertIn("error", local_model.parse_pull_event('{"error":"not found"}'))
        self.assertEqual(local_model.parse_pull_event("not json"), {})

    def test_format_size(self):
        from toolbox.livestream_summary import local_model
        self.assertEqual(local_model.format_size(2 * 1024 ** 3), "2.0 GB")
        self.assertEqual(local_model.format_size(512 * 1024 ** 2), "512 MB")

    def test_pull_model_stream(self):
        from toolbox.livestream_summary import local_model
        import requests as requests_lib

        class FakeResponse:
            status_code = 200
            text = ""

            def iter_lines(self, decode_unicode=True):
                return iter([
                    '{"status":"pulling manifest"}',
                    '{"status":"downloading","completed":50,"total":100}',
                    '{"status":"success"}',
                ])

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        seen = []
        with patch.object(local_model.requests, "post",
                          return_value=FakeResponse()):
            local_model.pull_model(
                "qwen2.5:7b", progress=lambda p, m: seen.append((p, m)))
        self.assertEqual(seen[-1][0], 50)
        self.assertEqual(seen[-1][1], "success")

        with patch.object(local_model.requests, "get",
                          side_effect=requests_lib.RequestException("down")):
            self.assertFalse(local_model.is_running())
            with self.assertRaises(local_model.LocalModelError):
                local_model.list_models()


class FileNameTest(unittest.TestCase):
    def test_output_filename(self):
        self.assertEqual(pipeline.output_filename("封茗囧菌 20260817 直播总结"),
                         "封茗囧菌 20260817 直播总结.txt")

    def test_default_path(self):
        path = pipeline.default_output_path("封茗囧菌 20260817 直播总结",
                                            Path("D:/录播/20260817.txt"), "")
        self.assertEqual(path, Path("D:/录播/封茗囧菌 20260817 直播总结.txt"))


if __name__ == "__main__":
    unittest.main()
