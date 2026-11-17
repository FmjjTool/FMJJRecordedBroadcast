# -*- coding: utf-8 -*-
"""直播总结 · 规则加载（直接复用内置 skill，不另写一套规则）。

`extract_rules` 用于分块提取阶段（纠错表 + 分类库）；
`final_rules` 用于终稿阶段（主指令 + 纠错 + 分类 + 模板 + 数据库索引）。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


class RuleError(RuntimeError):
    """规则文件缺失或不可读。"""


RULE_DIR = "fengming-livestream-summary"


@dataclass(frozen=True)
class RuleBundle:
    extract: str
    final: str


def rules_dir() -> Path:
    from toolbox.skills import skills_root
    return skills_root() / RULE_DIR


def _read(path: Path, required: bool = True) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        if required:
            raise RuleError(f"缺少直播总结规则文件：{path}") from exc
        return ""


def _block(title: str, content: str) -> str:
    return f"===== {title} =====\n{content}" if content else ""


@lru_cache(maxsize=1)
def load_rules() -> RuleBundle:
    root = rules_dir()
    skill = _read(root / "SKILL.md")
    correction = _read(root / "references" / "correction-map.md")
    classification = _read(root / "references" / "classification.md")
    template = _read(root / "references" / "template.md")
    db_index = _read(root / "references" / "database" / "README.md", required=False)
    live_records = _read(root / "references" / "database" / "live-records.md", required=False)

    extract = "\n\n".join(filter(None, (
        "你是「封茗囧菌直播总结」的信息提取器。严格按下面的纠错规则与归类规则工作："
        "先把本段转录中的误识词纠正为规范表述，再逐条提取信息点；"
        "每条保留原句里的具体细节（数字、名称、工具、步骤、人名、时间），"
        "禁止概括成空泛描述；每条标注候选维度 1—7；不要输出总结正文、不要写寒暄。",
        _block("纠错映射表", correction),
        _block("归类与分类名库", classification),
    )))

    final = "\n\n".join(filter(None, (
        "你是「封茗囧菌直播总结」的终稿生成器。必须完整遵守下面的规则文件，"
        "只输出纯文本总结，不输出任何解释、寒暄或 Markdown 标记。",
        _block("主指令", skill),
        _block("纠错映射表", correction),
        _block("归类与分类名库", classification),
        _block("输出模板", template),
        _block("数据库索引", db_index),
        _block("直播记录（仅核对日期与历史信息，禁止混入本次内容）", live_records),
    )))

    return RuleBundle(extract=extract, final=final)
