"""本地资料站 · GitHub 仓库拉取（clone / pull）。

私有仓库用登录 Token 通过 Git 的 http.extraheader 传递，Token 只进子进程环境，
不出现在命令行参数和日志里；公开仓库没有 Token 也能拉。
"""

from __future__ import annotations

import base64
from pathlib import Path

from . import config, runner


class GitError(RuntimeError):
    """git 操作失败（未登录 / 网络 / 本地改动阻塞）。"""


def _git_env(token: str = "") -> dict:
    env = config.child_env()
    entries: list[tuple[str, str]] = []
    if token:
        raw = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        entries.append(("http.extraheader", f"Authorization: Basic {raw}"))
    proxy = config.system_proxy()
    if proxy:
        entries.append(("http.proxy", proxy))
    env["GIT_CONFIG_COUNT"] = str(len(entries))
    for index, (key, value) in enumerate(entries):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


def _git(log, args, cwd, token: str = "", check: bool = True, title: str = ""):
    return runner.run_stream(
        [config.find_git(), *args], cwd, log,
        env=_git_env(token), check=check, title=title)


def repo_target(name: str, root: Path | None = None) -> Path:
    if name == "webwiki":
        return config.web_dir(root)
    if name == "webwiki-api":
        return config.api_dir(root)
    raise GitError(f"未知仓库：{name}")


def repo_state(name: str, root: Path | None = None) -> dict:
    """读取仓库状态（不联网）：是否存在、当前提交、是否脏。"""
    target = repo_target(name, root)
    if not (target / ".git").is_dir():
        return {"exists": False, "commit": "", "date": "", "dirty": False, "error": ""}
    git = config.find_git()
    env = _git_env()
    rc, commit = runner.capture([git, "rev-parse", "--short", "HEAD"], target, env)
    if rc:
        return {"exists": True, "commit": "", "date": "", "dirty": False, "error": commit}
    _rc, date = runner.capture([git, "log", "-1", "--format=%ci"], target, env)
    _rc2, dirty = runner.capture([git, "status", "--porcelain"], target, env)
    return {"exists": True, "commit": commit, "date": date,
            "dirty": bool(dirty), "error": ""}


def ensure_repo(name: str, token: str, log, root: Path | None = None,
                force_clone: bool = False) -> Path:
    """仓库不存在则 clone，存在则 fetch + 快进合并。"""
    target = repo_target(name, root)
    url = config.REPO_URLS[name]
    if force_clone and target.exists():
        raise GitError(f"{target} 已存在，拒绝自动覆盖；请手动删除后再重试")
    if not (target / ".git").is_dir():
        if target.exists() and any(target.iterdir()):
            raise GitError(f"{target} 已存在但不是 git 仓库，请先移走该目录")
        target.parent.mkdir(parents=True, exist_ok=True)
        log(f"克隆 {name} → {target}")
        _git(log, ["clone", "--depth", "1", url, str(target)],
             target.parent, token=token, title=f"克隆 {name}")
        return target

    state = repo_state(name, root)
    if state.get("dirty"):
        raise GitError(
            f"{name} 仓库有未提交的本地改动，已停止拉取以免覆盖。\n"
            f"请先处理：{target}")
    log(f"更新 {name}（当前 {state.get('commit') or '?'}）")
    _git(log, ["fetch", "--depth", "1", "origin", "main"], target, token=token,
         title=f"fetch {name}")
    _git(log, ["merge", "--ff-only", "FETCH_HEAD"], target, token=token,
         title=f"merge {name}")
    return target


def pull_all(token: str, log, root: Path | None = None) -> str:
    """依次更新两个仓库，返回简短结果。"""
    ensure_repo("webwiki-api", token, log, root)
    ensure_repo("webwiki", token, log, root)
    parts = []
    for name in ("webwiki-api", "webwiki"):
        state = repo_state(name, root)
        parts.append(f"{name}@{state.get('commit') or '?'}")
    return " · ".join(parts)
