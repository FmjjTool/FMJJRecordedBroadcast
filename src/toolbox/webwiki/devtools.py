"""微信开发者工具启动适配：打开本地小程序工程，不执行上传或发布。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


class DevToolsError(RuntimeError):
    """微信开发者工具未安装或本地工程无法打开。"""


def _cli_candidates() -> list[Path]:
    candidates: list[Path] = []
    for key in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(key)
        if not base:
            continue
        suffixes = (
            Path("Tencent") / "微信web开发者工具" / "cli.bat",
            Path("Programs") / "微信开发者工具" / "cli.bat",
            Path("微信开发者工具") / "cli.bat",
        )
        candidates.extend(Path(base) / suffix for suffix in suffixes)
    return candidates


def find_cli() -> Path:
    """按 PATH 与常见安装位置查找官方 CLI。"""
    for name in ("cli.bat", "cli.exe", "cli"):
        found = shutil.which(name)
        if found:
            return Path(found)
    for candidate in _cli_candidates():
        if candidate.is_file():
            return candidate
    raise DevToolsError(
        "未找到微信开发者工具命令行。请安装微信开发者工具，或将 cli.bat 所在目录加入 PATH。")


def open_project(project_dir: str | Path, *, cli_path: str | Path | None = None,
                 startfile=None) -> str:
    """请求微信开发者工具打开已同步的小程序工程。

    只打开本地工程，不构建发布包、不上传代码，也不操作账户登录状态。
    """
    project = Path(project_dir).expanduser().resolve()
    config_path = project / "project.config.json"
    if not project.is_dir() or not config_path.is_file():
        raise DevToolsError(
            "小程序工程尚未同步到资料站目录，请先更新「微信小程序主程序」仓库。")

    try:
        with config_path.open("r", encoding="utf-8-sig") as source:
            project_config = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise DevToolsError("小程序工程配置无法读取，请检查本地项目文件后重试。") from exc
    if not isinstance(project_config, dict) or project_config.get("compileType") != "miniprogram":
        raise DevToolsError("所选目录不是有效的微信小程序工程。")

    cli = Path(cli_path).expanduser() if cli_path else find_cli()
    if not cli.is_file():
        raise DevToolsError(
            "未找到微信开发者工具命令行。请安装微信开发者工具，或将 cli.bat 所在目录加入 PATH。")

    project_text = str(project)
    if any(char in project_text for char in '"%&|<>^!\r\n'):
        raise DevToolsError(
            "小程序工程路径含有微信开发者工具命令行不支持的字符，请更换资料站目录后重试。")

    command = [str(cli.resolve()), "open", "--project", project_text]
    if cli.suffix.lower() == ".bat":
        # ShellExecute passes a parameter string to the batch file association.
        # Refuse cmd metacharacters so project paths cannot become commands.
        if any(char in project_text for char in '"%&|<>^!\r\n'):
            raise DevToolsError(
                "小程序工程路径含有微信开发者工具命令行不支持的字符，请更换资料站目录后重试。")
        launch = startfile or getattr(os, "startfile", None)
        if launch is None:
            raise DevToolsError("当前系统不支持直接打开微信开发者工具命令行。")
        arguments = f'open --project "{project_text}"'
        kwargs = {"operation": "open", "arguments": arguments,
                  "cwd": str(project)}
        if os.name == "nt":
            kwargs["show_cmd"] = 1
    else:
        launch = subprocess.Popen
        kwargs = {"cwd": str(project), "shell": False,
                  "stdout": subprocess.DEVNULL,
                  "stderr": subprocess.DEVNULL}
    try:
        if cli.suffix.lower() == ".bat":
            launch(str(cli.resolve()), **kwargs)
        else:
            launch(command, **kwargs)
    except OSError as exc:
        raise DevToolsError(
            "启动微信开发者工具失败，请检查工具安装和本地工程后重试。") from exc
    return "已请求微信开发者工具打开小程序工程（未上传或发布）"
