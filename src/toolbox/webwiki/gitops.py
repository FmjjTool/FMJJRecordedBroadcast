"""本地资料站 · GitHub 仓库拉取（clone / pull）。

私有仓库用登录 Token 通过 Git 的 http.extraheader 传递，Token 只进子进程环境，
不出现在命令行参数和日志里；公开仓库没有 Token 也能拉。

拉取失败时按约定 15 逐档尝试：直连 → 指定 / 环境变量代理 → 系统代理 →
本机常见端口（先探活）→ git 配置，全部失败才报错，并在错误里列出每档原文。
仓库是浅克隆（--depth 1），且一律以云端为准：远端历史被重写（force push /
重建仓库）后本地与 FETCH_HEAD 没有共同祖先，git merge 会拒绝；这种情况不停下，
而是把旧提交留成备份分支，再把工作区对齐远端最新提交。本地有未提交改动时仍然
拒绝覆盖（先让用户处理），因为那不是「以云端为准」能解释掉的。
"""

from __future__ import annotations

import base64
import shutil
from datetime import datetime
from pathlib import Path

from . import config, runner


class GitError(RuntimeError):
    """git 操作失败（未登录 / 网络 / 本地改动阻塞）。"""


def _git_env(token: str = "", proxy: str | None = None) -> dict:
    """构造 git 子进程环境；proxy=None 用系统代理，proxy="" 表示强制直连。"""
    env = config.child_env()
    entries: list[tuple[str, str]] = []
    if token:
        raw = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
        entries.append(("http.extraheader", f"Authorization: Basic {raw}"))
    chosen = config.system_proxy() if proxy is None else proxy
    # 直连时也要显式留空 http.proxy，否则仓库/全局配置里的代理照样生效
    entries.append(("http.proxy", chosen))
    env["GIT_CONFIG_COUNT"] = str(len(entries))
    for index, (key, value) in enumerate(entries):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if chosen:
            env[name] = chosen
        else:
            env.pop(name, None)
    # 取不到凭证就立刻失败，不要卡在命令行等待输入
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    # 通道卡死时（TLS 握手 / 无响应）及时放弃，好切下一档
    env["GIT_HTTP_LOW_SPEED_LIMIT"] = "1"
    env["GIT_HTTP_LOW_SPEED_TIME"] = "30"
    return env


def _git(log, args, cwd, token: str = "", check: bool = True, title: str = ""):
    return runner.run_stream(
        [config.find_git(), *args], cwd, log,
        env=_git_env(token), check=check, title=title)


def repo_target(name: str, root: Path | None = None) -> Path:
    if name == "mandisa-miniprogram":
        return config.miniapp_dir(root)
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
    rc, commit = runner.capture([git, "rev-parse", "--short", "HEAD"], target, env,
                                merge_stderr=False)
    if rc:
        return {"exists": True, "commit": "", "date": "", "dirty": False, "error": commit}
    _rc, date = runner.capture([git, "log", "-1", "--format=%ci"], target, env,
                               merge_stderr=False)
    _rc2, dirty = runner.capture([git, "status", "--porcelain"], target, env,
                                merge_stderr=False)
    return {"exists": True, "commit": commit, "date": date,
            "dirty": bool(dirty), "error": ""}


def _run_on_all_channels(log, args: list[str], cwd, token: str,
                         title: str) -> tuple[int, list[str], list[str]]:
    """同一命令按约定 15 逐档换通道，返回首个成功结果或全部失败信息。"""
    failures: list[str] = []
    last: list[str] = []
    for proxy in config.proxy_candidates():
        label = f"代理 {proxy}" if proxy else "直连"
        rc, lines = runner.run_stream(
            [config.find_git(), *args], cwd, log,
            env=_git_env(token, proxy), check=False,
            title=f"{title}（{label}）")
        if rc == 0:
            return rc, lines, failures
        last = lines
        failures.append(f"{label}：{lines[-1] if lines else '无输出'}")
        log(f"{label} 未成功，换下一档通道")
    return 1, last, failures


def _clone(log, name: str, target: Path, token: str) -> None:
    url = config.REPO_URLS[name]
    log(f"克隆 {name} → {target}")
    rc, _lines, failures = _run_on_all_channels(
        log, ["clone", "--depth", "1", url, str(target)],
        target.parent, token, f"克隆 {name}")
    if rc == 0:
        return
    # 半成品目录由本次克隆创建，清掉再报错，避免下次被判定为"已存在但不是 git 仓库"
    if target.exists() and (target / ".git").exists():
        shutil.rmtree(target, ignore_errors=True)
    raise GitError("克隆失败，已尝试全部网络通道：\n" + "\n".join(failures))


def ensure_repo(name: str, token: str, log, root: Path | None = None,
                force_clone: bool = False) -> Path:
    """仓库不存在则 clone，存在则 fetch + 更新到远端最新提交。"""
    target = repo_target(name, root)
    if force_clone and target.exists():
        raise GitError(f"{target} 已存在，拒绝自动覆盖；请手动删除后再重试")
    if not (target / ".git").is_dir():
        if target.exists() and any(target.iterdir()):
            raise GitError(f"{target} 已存在但不是 git 仓库，请先移走该目录")
        target.parent.mkdir(parents=True, exist_ok=True)
        _clone(log, name, target, token)
        return target

    state = repo_state(name, root)
    if state.get("dirty"):
        raise GitError(
            f"{name} 仓库有未提交的本地改动，已停止拉取以免覆盖。\n"
            f"请先处理：{target}")
    log(f"更新 {name}（当前 {state.get('commit') or '?'}）")
    branch = config.REPO_BRANCHES.get(name, "main")
    rc, _lines, failures = _run_on_all_channels(
        log, ["fetch", "--depth", "1", "origin", branch],
        target, token, f"fetch {name}")
    if rc:
        raise GitError("拉取失败，已尝试全部网络通道：\n" + "\n".join(failures))
    _advance(log, name, target, token, state.get("commit") or "?")
    return target


_REWRITTEN_MARKS = ("unrelated histories",
                    "Not possible to fast-forward",
                    "not possible to fast-forward")


def _history_rewritten(tail: str) -> bool:
    """merge 失败是不是「远端历史被重写」这一类（与网络无关）。"""
    return any(mark in tail for mark in _REWRITTEN_MARKS)


def _advance(log, name: str, target: Path, token: str, old_commit: str) -> None:
    """把本地 main 推进到 FETCH_HEAD；云端优先，本地跟不上的旧提交留备份分支。"""
    rc, lines = runner.run_stream(
        [config.find_git(), "merge", "--ff-only", "FETCH_HEAD"],
        target, log, env=_git_env(token), check=False, title=f"merge {name}")
    if rc == 0:
        return
    tail = lines[-1] if lines else "无输出"
    if not _history_rewritten(tail):
        raise GitError(f"merge {name} 失败（退出码 {rc}）：{tail}")

    backup = f"pre-sync-{datetime.now():%Y%m%d-%H%M%S}"
    runner.capture([config.find_git(), "branch", backup, "HEAD"],
                   target, _git_env())
    log(f"远端历史已重写（{tail}）；仓库以云端为准")
    log(f"旧提交 {old_commit} 已保留为分支 {backup}，现在对齐远端最新提交")
    _git(log, ["reset", "--hard", "FETCH_HEAD"], target, token=token,
         title=f"同步 {name}")


def pull_all(token: str, log, root: Path | None = None) -> str:
    """依次更新主源仓库与 Web 投影仓库；某仓库失败不影响另一个。"""
    errors: list[str] = []
    for name in ("mandisa-miniprogram", "webwiki-api", "webwiki"):
        try:
            ensure_repo(name, token, log, root)
        except GitError as exc:
            errors.append(str(exc))
            log(f"{name} 未更新，继续下一个仓库")
    if errors:
        raise GitError("\n\n".join(errors))
    parts = []
    for name in ("mandisa-miniprogram", "webwiki-api", "webwiki"):
        state = repo_state(name, root)
        parts.append(f"{name}@{state.get('commit') or '?'}")
    return " · ".join(parts)
