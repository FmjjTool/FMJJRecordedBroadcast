# -*- coding: utf-8 -*-
"""直播总结 · 大模型流水线。

分块提取信息点 → 合并去重 → 按 skill 规则生成终稿 → 程序化校验（失败重试一次）。
所有对模型的调用都经 `ChatClient`，可用 `threading.Event` 取消。
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from toolbox import livestream_summary_rule as rule
from toolbox.livestream_summary.client import CancelledError, ChatClient
from toolbox.livestream_summary.rules import load_rules

ProgressCb = Callable[[str, int, str], None]
CancelCb = threading.Event

_SENTENCE = re.compile(r"(?<=[。！？!?；;])")


def _noop(_stage: str, _percent: int, _message: str) -> None:
    pass


def _check_cancel(cancel: CancelCb | None) -> None:
    if cancel is not None and cancel.is_set():
        raise CancelledError("已停止")


@dataclass
class PipelineResult:
    text: str
    stats: dict = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def split_chunks(text: str, chunk_chars: int = 6000) -> list[str]:
    """按行切块，不跨块断行；单行超长时按句末标点再切。"""
    chunk_chars = max(1, int(chunk_chars))
    pieces: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(line) <= chunk_chars:
            pieces.append(line)
            continue
        current = ""
        for sentence in _SENTENCE.split(line):
            if not sentence:
                continue
            if current and len(current) + len(sentence) > chunk_chars:
                pieces.append(current)
                current = ""
            while len(sentence) > chunk_chars:
                pieces.append(sentence[:chunk_chars])
                sentence = sentence[chunk_chars:]
            current += sentence
        if current:
            pieces.append(current)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) > chunk_chars:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def _extract_user(chunk: str, index: int, total: int, date_text: str | None) -> str:
    when = date_text or "未识别"
    return (
        f"直播日期：{when}\n"
        f"这是第 {index}/{total} 块转录（字符数 {len(chunk)}）。\n"
        f"----- 转录开始 -----\n{chunk}\n----- 转录结束 -----\n"
        "请先纠错，再提取本块全部有效信息点；每条保留具体细节并标注候选维度 1—7。"
    )


def _merge_user(notes: str) -> str:
    return (
        "下面是同一场直播按分块提取出的信息点清单。请合并去重：\n"
        "1. 同一件事只保留信息最完整的一条，不丢具体数字、名称、步骤、人名、时间；\n"
        "2. 按 1—7 级归类，保留候选维度标注；\n"
        "3. 不新增原文没有的信息，不写总结正文。\n"
        f"----- 清单开始 -----\n{notes}\n----- 清单结束 -----"
    )


def _final_user(notes: str, date_text: str | None, filename: str) -> str:
    when = date_text or "未识别（按规则使用「暂未公开（直播日期）」）"
    return (
        f"直播日期：{when}\n转录文件名：{filename}\n"
        "以下是从全文提取并合并后的信息点清单。"
        "请严格按主指令、1—7 级维度顺序和输出模板生成最终总结。\n"
        f"----- 信息点开始 -----\n{notes}\n----- 信息点结束 -----"
    )


def _repair_user(output: str, problems: list[str], date_text: str | None) -> str:
    items = "\n".join(f"- {problem}" for problem in problems)
    return (
        f"上一版总结未通过格式校验：\n{items}\n\n"
        f"直播日期：{date_text or '未识别'}\n"
        "请在保持事实不变的前提下修正上述问题，重新输出完整总结。"
        "只输出纯文本，不要解释，不要 Markdown。\n"
        f"----- 上一版开始 -----\n{output}\n----- 上一版结束 -----"
    )


def validate_output(output: str, date_text: str | None) -> list[str]:
    """按 template.md 做程序化校验，返回问题列表（空列表表示通过）。"""
    problems: list[str] = []
    lines = [line.strip() for line in output.strip().splitlines()]
    if not lines:
        return ["输出为空"]

    expected_title = rule.build_title(date_text)
    if lines[0] != expected_title:
        problems.append(f"首行标题应为「{expected_title}」，实际是「{lines[0]}」")
    if len(lines) < 4:
        problems.append("行数不足，缺少固定标注或维度")
        return problems
    if lines[1] != rule.FIXED_LINE or lines[2] != rule.FIXED_LINK:
        problems.append("第 2、3 行必须是腾讯文档固定标注")
    if lines[-2] != rule.FIXED_LINE or lines[-1] != rule.FIXED_LINK:
        problems.append("末尾两行必须是腾讯文档固定标注")

    positions: list[int] = []
    for level in range(1, 8):
        title = rule.DIMENSION_TITLES[level]
        found = next((i for i, line in enumerate(lines)
                      if line == title or line.startswith(title + "（未提及）")), -1)
        if found < 0:
            problems.append(f"缺少维度：{title}")
        positions.append(found)
    if all(pos >= 0 for pos in positions) and positions != sorted(positions):
        problems.append("1—7 级维度顺序不正确")

    for marker in ("**", "##", "```", "~~"):
        if marker in output:
            problems.append(f"出现 Markdown 标记「{marker}」，必须改为纯文本")
    for placeholder in ("[分类名称]", "[实际分类名称]"):
        if placeholder in output:
            problems.append(f"仍保留占位符「{placeholder}」")
    if re.search(r"^\s*#{1,6}\s", output, re.MULTILINE):
        problems.append("出现 Markdown 标题行")
    if re.search(r"^\s*\|.*\|\s*$", output, re.MULTILINE):
        problems.append("出现 Markdown 表格")

    for level in (2, 3, 4, 5):
        title = rule.DIMENSION_TITLES[level]
        start = next((i for i, line in enumerate(lines) if line.startswith(title)), -1)
        if start < 0 or lines[start].endswith("（未提及）"):
            continue
        end = next((i for i in range(start + 1, len(lines))
                    if lines[i].startswith(f"{level + 1} 级：")), len(lines))
        for line in lines[start + 1:end]:
            if line and not (line.startswith("[") and "]：" in line):
                problems.append(f"{level} 级条目格式应为「[分类名称]：详情」：{line[:40]}")
                break
    return problems


def _merge_notes(notes: list[str], client: ChatClient, rules,
                 *, merge_chars: int, max_tokens: int,
                 cancel: CancelCb | None, progress: ProgressCb) -> str:
    merged = "\n\n".join(note for note in notes if note.strip())
    rounds = 0
    while len(merged) > merge_chars and len(notes) > 1 and rounds < 2:
        groups = split_chunks(merged, merge_chars)
        parts: list[str] = []
        for index, group in enumerate(groups, start=1):
            _check_cancel(cancel)
            progress("merge", 70 + min(8, index), f"合并信息点 {index}/{len(groups)}")
            parts.append(client.chat(rules.extract, _merge_user(group),
                                     temperature=0.2, max_tokens=max_tokens,
                                     cancel=cancel))
        merged = "\n\n".join(parts)
        notes = parts
        rounds += 1
    return merged


def run(text: str, filename: str, client: ChatClient, *,
        progress: ProgressCb | None = None,
        cancel: CancelCb | None = None,
        chunk_chars: int = 6000,
        merge_chars: int = 12000,
        max_tokens: int = 8192) -> PipelineResult:
    progress = progress or _noop
    _check_cancel(cancel)
    progress("read", 2, "读取转录")

    rules = load_rules()
    body = rule.strip_srt(text)
    if not body.strip():
        raise ValueError("转录内容为空")
    date_text, date_source = rule.extract_date(filename, body)
    corrected = rule.apply_corrections(body, rule.load_corrections())
    chunks = split_chunks(corrected, chunk_chars)
    if not chunks:
        raise ValueError("清洗后没有可用文本")

    progress("extract", 5, f"开始分块提取（共 {len(chunks)} 块）")
    notes: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        _check_cancel(cancel)
        notes.append(client.chat(
            rules.extract, _extract_user(chunk, index, len(chunks), date_text),
            temperature=0.2, max_tokens=max_tokens, cancel=cancel,
            on_wait=lambda message: progress("extract", 5, message)))
        percent = 5 + int(65 * index / len(chunks))
        progress("extract", percent, f"已提取 {index}/{len(chunks)} 块")

    merged = _merge_notes(notes, client, rules, merge_chars=merge_chars,
                          max_tokens=max_tokens, cancel=cancel, progress=progress)

    _check_cancel(cancel)
    progress("final", 80, "生成终稿")
    output = client.chat(rules.final, _final_user(merged, date_text, filename),
                         temperature=0.3, max_tokens=max_tokens, cancel=cancel)

    progress("validate", 92, "校验格式")
    problems = validate_output(output, date_text)
    if problems:
        _check_cancel(cancel)
        progress("repair", 94, f"修正 {len(problems)} 处格式问题")
        output = client.chat(rules.final, _repair_user(output, problems, date_text),
                             temperature=0.2, max_tokens=max_tokens, cancel=cancel)
        problems = validate_output(output, date_text)

    stats = {
        "chars": len(corrected),
        "chunks": len(chunks),
        "requests": client.requests_made,
        "date": date_text or "",
        "date_source": date_source,
        "problems": len(problems),
    }
    progress("done", 100, "完成")
    return PipelineResult(text=output, stats=stats, problems=problems)


def output_filename(text: str) -> str:
    title = text.strip().splitlines()[0] if text.strip() else "直播总结"
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip() + ".txt"


def default_output_path(text: str, source: Path, output_dir: str = "") -> Path:
    target_dir = Path(output_dir).expanduser() if output_dir else source.parent
    return target_dir / output_filename(text)
