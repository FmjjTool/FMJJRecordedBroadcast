#!/usr/bin/env python3
"""Check this repository against GitHub without changing local project files.

Only Python's standard library and Git are required. Online checks fetch one
remote branch; no application modules, settings or credentials are loaded.
Exit codes: 0 = no differences, 1 = differences, 2 = incomplete/failed check.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
from urllib.parse import urlsplit

EXPECTED_ORIGIN = "https://github.com/FmjjTool/FMJJRecordedBroadcast.git"
MAX_OUTPUT = 4 * 1024 * 1024
CONFLICTS = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


class CheckError(Exception):
    """A check could not produce a trustworthy result."""


def safe_text(text: str) -> str:
    """Do not echo transport addresses, tokens or terminal escape sequences."""
    text = re.sub(r"(?:https?|ssh|socks5h?)://[^\s<>\"']+", "[地址已隐藏]", text, flags=re.I)
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)", "[凭据已隐藏]", text)
    text = re.sub(r"(?i)(authorization|proxy-authorization|password|access_token)\s*[:=]\s*[^\r\n]+", r"\1: [已隐藏]", text)
    return "".join(c if c in "\n\t" or ord(c) >= 32 and ord(c) != 127 else "?" for c in text)


@dataclass
class Result:
    code: int
    out: bytes
    err: bytes


class Git:
    def __init__(self, repo: Path, timeout: int = 30):
        self.repo = repo
        self.timeout = timeout
        self.executable = shutil.which("git")
        if not self.executable:
            raise CheckError("未找到 Git，请先安装 Git for Windows 并加入 PATH。")
        inherited = {key: value for key, value in os.environ.items()
                     if not key.upper().startswith("GIT_TRACE") and key.upper() != "GIT_CURL_VERBOSE"}
        self.env = dict(inherited, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never",
                        GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1", LC_ALL="C", LANGUAGE="C")

    def run(self, *args: str, env: dict[str, str] | None = None, ok: tuple[int, ...] = (0,)) -> Result:
        command = [self.executable, "--no-pager", "-c", "color.ui=false", "-c",
                   "core.quotepath=false", "-c", "i18n.logOutputEncoding=utf-8", *args]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.Popen(command, cwd=self.repo, env=env or self.env,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, creationflags=flags,
                                       start_new_session=os.name != "nt")
        except OSError as exc:
            raise CheckError("无法启动 Git：" + safe_text(str(exc))) from exc
        buffers = [bytearray(), bytearray()]
        overflow = threading.Event()

        def read_stream(stream, buffer):
            try:
                while True:
                    block = stream.read(65536)
                    if not block:
                        break
                    available = MAX_OUTPUT - len(buffer)
                    buffer.extend(block[:max(0, available)])
                    if len(block) > available:
                        overflow.set()
            finally:
                stream.close()

        readers = [threading.Thread(target=read_stream, args=(stream, buffer), daemon=True)
                   for stream, buffer in zip((process.stdout, process.stderr), buffers)]
        for reader in readers:
            reader.start()
        try:
            process.wait(timeout=self.timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            # This PID and its descendants were created exclusively by this call.
            cleanup_failed = False
            try:
                if os.name == "nt":
                    killer = shutil.which("taskkill")
                    if killer:
                        try:
                            subprocess.run([killer, "/PID", str(process.pid), "/T", "/F"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           timeout=10, creationflags=flags, check=False)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                cleanup_failed = True
            for reader in readers:
                reader.join(timeout=2)
            if isinstance(exc, KeyboardInterrupt):
                raise
            if cleanup_failed or any(reader.is_alive() for reader in readers):
                raise CheckError("Git 操作超时，且本工具子进程收尾未确认；检查未完成，请人工核查。") from exc
            raise CheckError(f"Git 操作超过 {self.timeout} 秒，检查未完成。") from exc
        for reader in readers:
            reader.join(timeout=2)
        if any(reader.is_alive() for reader in readers):
            raise CheckError("Git 输出未正常结束，检查未完成。")
        if overflow.is_set():
            raise CheckError("Git 输出超过安全上限，检查未完成；请缩小仓库范围后重试。")
        result = Result(process.returncode, bytes(buffers[0]), bytes(buffers[1]))
        if result.code not in ok:
            detail = safe_text(result.err.decode("utf-8", "replace").strip())[:1600]
            raise CheckError(f"Git 操作失败（退出码 {result.code}）：{detail or '未提供错误详情'}")
        return result

    def text(self, *args: str) -> str:
        return self.run(*args).out.decode("utf-8", "replace").strip()


def validate_origin(git: Git) -> None:
    for flag in ((), ("--push",)):
        urls = git.text("remote", "get-url", *flag, "--all", "origin").splitlines()
        if not urls or any(url != EXPECTED_ORIGIN for url in urls):
            raise CheckError("origin 的获取或推送地址不符合本项目约定，已停止；请人工核对，工具不会改写地址。")


def choose_target(git: Git, branch: str, requested: str | None) -> str:
    if requested:
        target = requested
    elif branch == "main":
        target = "origin/main"
    elif branch:
        upstream = git.run("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", ok=(0, 128))
        target = upstream.out.decode("utf-8", "replace").strip() if upstream.code == 0 else ""
    else:
        target = ""
    if not target.startswith("origin/") or len(target) <= len("origin/"):
        raise CheckError("当前分支没有明确的 origin 上游，或处于 detached HEAD；请用 --target origin/分支名明确比较目标。")
    git.run("check-ref-format", "refs/heads/" + target[len("origin/"):])
    return target


def proxy_candidates(git: Git):
    candidates = [("环境代理", os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")),
                  ("环境代理", os.environ.get("ALL_PROXY") or os.environ.get("all_proxy"))]
    candidates += [(f"本机代理端口 {port}", f"http://127.0.0.1:{port}") for port in (7890, 10809, 12450)]
    configured = git.run("config", "--get", "http.proxy", ok=(0, 1))
    candidates.append(("Git 配置代理", configured.out.decode("utf-8", "replace").strip()))
    seen = set()
    for label, value in candidates:
        if not value or value in seen:
            continue
        seen.add(value)
        try:
            parsed = urlsplit(value)
            port = parsed.port or {"http": 80, "https": 443}.get(parsed.scheme)
            if (parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname
                    or not port or parsed.username is not None or parsed.password is not None
                    or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
                print(f"  {label}：格式不支持或包含认证信息，跳过。")
                continue
            with socket.create_connection((parsed.hostname, port), timeout=2):
                pass
        except (ValueError, OSError):
            print(f"  {label}：端口不可达，跳过。")
            continue
        yield label, value


def fetch(git: Git, target: str) -> None:
    direct_env = {key: value for key, value in git.env.items()
                  if key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}}
    remote_branch = target[len("origin/"):]
    refspec = f"+refs/heads/{remote_branch}:refs/remotes/{target}"

    def attempt(label: str, proxy: str) -> str | None:
        print(f"正在获取远端状态（{label}）……", flush=True)
        try:
            result = git.run("-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}",
                             "-c", f"http.https://github.com/.proxy={proxy}",
                             "-c", f"remote.origin.proxy={proxy}",
                             "fetch", "--no-recurse-submodules", "--no-auto-maintenance",
                             "--no-tags", "--no-write-fetch-head", "origin", refspec,
                             env=direct_env, ok=(0, 1, 128))
        except CheckError as exc:
            return str(exc)
        if result.code == 0:
            return None
        return safe_text(result.err.decode("utf-8", "replace").strip())[:1600] or f"Git 退出码 {result.code}"

    network_errors = ("could not resolve", "failed to connect", "timed out", "timeout", "connection reset",
                      "connection was reset", "empty reply", "network is unreachable", "tls", "schannel", "超过")
    error = attempt("直连", "")
    if error is None:
        return
    print("  直连失败：" + error)
    if not any(term in error.lower() for term in network_errors):
        raise CheckError("远端获取失败；请检查认证、分支或 Git 配置。本次没有云端最新结论。")
    for label, proxy in proxy_candidates(git):
        error = attempt(label, proxy)
        if error is None:
            return
        print(f"  {label}失败：{error}")
        if not any(term in error.lower() for term in network_errors):
            break
    raise CheckError("直连和可用代理均未能取得远端状态；本次没有云端最新结论。请处理网络，或用 --offline 明确检查本地缓存。")


def working_changes(git: Git) -> list[tuple[str, str]]:
    fields = git.run("status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignore-submodules=none").out.split(b"\0")
    result = []
    index = 0
    while index < len(fields) and fields[index]:
        item = fields[index].decode("utf-8", "replace")
        state, path = item[:2], item[3:]
        index += 1
        if "R" in state or "C" in state:
            if index >= len(fields) or not fields[index]:
                raise CheckError("Git 重命名状态不完整，检查未完成。")
            path = fields[index].decode("utf-8", "replace") + " → " + path
            index += 1
        result.append((state, path))
    return result


def file_stats(git: Git, old: str, new: str) -> list[tuple[str, str, str]]:
    data = git.run("diff", "--numstat", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", old, new, "--").out
    rows = []
    for item in data.split(b"\0"):
        if item:
            added, removed, path = item.split(b"\t", 2)
            rows.append((path.decode("utf-8", "replace"), added.decode("ascii"), removed.decode("ascii")))
    return rows


def show_rows(title: str, rows: list[str], limit: int) -> None:
    print(f"\n{title}（{len(rows)} 项）")
    for row in rows[:limit] if limit else rows:
        print("  " + safe_text(row))
    if limit and len(rows) > limit:
        print(f"  ……另有 {len(rows) - limit} 项，可用 --limit 0 展示全部。")
    if not rows:
        print("  无")


def show_stats(title: str, rows: list[tuple[str, str, str]], limit: int) -> None:
    show_rows(title, [(f"+{added} / -{removed}  " if added != "-" else "二进制变化  ")
                     + json.dumps(path, ensure_ascii=False) for path, added, removed in rows], limit)


def inspect(args) -> int:
    git = Git(Path(args.repo).resolve(), args.timeout)
    if git.text("rev-parse", "--is-inside-work-tree") != "true":
        raise CheckError("目标不是可检查工作区的 Git 仓库。")
    root = Path(git.text("rev-parse", "--show-toplevel"))
    if root.resolve() != git.repo:
        raise CheckError("--repo 必须指向仓库根目录，避免检查到父目录的其他项目。")
    validate_origin(git)
    branch = git.text("branch", "--show-current")
    target = choose_target(git, branch, args.target)
    head = git.text("rev-parse", "--verify", "HEAD^{commit}")
    print(f"仓库：{safe_text(str(root))}\n本地分支：{safe_text(branch or 'detached HEAD')}\n比较目标：{safe_text(target)}")
    if args.offline:
        print("数据来源：离线，仅本地远端引用缓存，不代表云端最新状态。")
    else:
        fetch(git, target)
        print("数据来源：本次已成功获取远端状态。")
    remote = git.text("rev-parse", "--verify", f"refs/remotes/{target}^{{commit}}")
    base_result = git.run("merge-base", head, remote, ok=(0, 1))
    if base_result.code != 0:
        shallow = git.text("rev-parse", "--is-shallow-repository") == "true"
        raise CheckError("浅克隆历史不足，无法确认共同祖先；请人工补全历史后重试。" if shallow else "两端没有共同祖先，无法按正常分支关系比较；请人工核对仓库和目标。")
    base = base_result.out.decode("ascii").strip()
    ahead, behind = map(int, git.text("rev-list", "--left-right", "--count", f"{head}...{remote}").split())
    state = "双方分叉，需人工决定合并方案" if ahead and behind else "本地领先" if ahead else "云端领先" if behind else "提交历史一致"
    changes = working_changes(git)
    files = file_stats(git, remote, head)
    local_files = file_stats(git, base, head) if ahead and behind else []
    remote_files = file_stats(git, base, remote) if ahead and behind else []
    commits = []
    for left, right in ((remote, head), (head, remote)):
        options = [f"--max-count={args.limit}"] if args.limit else []
        commits.append(git.text("log", "--format=%h %s", *options, f"{left}..{right}", "--").splitlines())
    if head != git.text("rev-parse", "--verify", "HEAD^{commit}") or branch != git.text("branch", "--show-current"):
        raise CheckError("检查期间本地分支或 HEAD 已变化，请重新运行以获取一致快照。")
    print("\n检查时间：" + datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S 北京时间"))
    print(f"历史状态：{state}\n独有提交：本地 {ahead} / 云端 {behind}")
    print(f"本地提交：{head}\n云端提交：{remote}\n共同基线：{base}")
    print(f"已提交文件内容：{'存在差异' if files else '一致'}（不含未提交改动）")
    staged = sum(state != "??" and state not in CONFLICTS and state[0] != " " for state, _ in changes)
    unstaged = sum(state != "??" and state not in CONFLICTS and state[1] != " " for state, _ in changes)
    conflicts = sum(state in CONFLICTS for state, _ in changes)
    untracked = sum(state == "??" for state, _ in changes)
    print(f"工作区：{'有未提交改动' if changes else '干净'}；已暂存 {staged} / 未暂存 {unstaged} / 未跟踪 {untracked} / 冲突 {conflicts}")
    show_rows("工作区文件（XY 为 Git 暂存区/工作区状态）",
              [f"{state}  {json.dumps(path, ensure_ascii=False)}" for state, path in changes], args.limit)
    for label, count, rows in zip(("本地", "云端"), (ahead, behind), commits):
        show_rows(f"{label}独有提交，共 {count} 个；以下为最近提交", rows, 0)
        if len(rows) < count:
            print(f"  ……另有 {count - len(rows)} 个，可用 --limit 0 展示全部。")
    show_stats("已提交文件差异：云端 → 本地（增删行以本地为准；重命名按删/增列出）", files, args.limit)
    if ahead and behind:
        show_stats("共同基线 → 本地", local_files, args.limit)
        show_stats("共同基线 → 云端", remote_files, args.limit)
        overlap = sorted({row[0] for row in local_files} & {row[0] for row in remote_files})
        show_rows("双方改动的相同文件（重叠待审，不等于已发生文本冲突）",
                  [json.dumps(path, ensure_ascii=False) for path in overlap], args.limit)
    print("\n建议：" + ("先审阅并解决未提交冲突。" if conflicts else
          "先保存并审阅工作区改动，再决定同步。" if changes else
          "审阅双方差异后决定合并方案。" if ahead and behind else
          "可在需要时明确执行推送。" if ahead else
          "可在确认后仅快进更新。" if behind else "当前比较快照没有差异。"))
    print("本工具仅检查；在线时只更新 Git 对象与远端跟踪引用，不同步工作文件。")
    return 1 if ahead or behind or changes or files else 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="一键检查本项目本地与 GitHub 差异；仅依赖 Python 标准库和 Git。")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent), help="仓库根目录，默认脚本所在目录")
    parser.add_argument("--target", help="明确远端目标，如 origin/main；非 main 默认使用其 origin 上游")
    parser.add_argument("--offline", action="store_true", help="不联网，仅检查本地缓存；不代表云端最新状态")
    parser.add_argument("--limit", type=int, default=20, help="每组最多展示多少项，0 展示全部，默认 20")
    parser.add_argument("--timeout", type=int, default=30, help="每条 Git 命令超时秒数，默认 30")
    parser.add_argument("--pause", action="store_true", help="退出前按回车，供双击入口使用")
    args = parser.parse_args(argv)
    if args.limit < 0 or not 3 <= args.timeout <= 300:
        parser.error("--limit 必须大于等于 0；--timeout 必须在 3 到 300 秒之间。")
    try:
        code = inspect(args)
    except (CheckError, OSError, ValueError) as exc:
        print("\n检查失败：" + safe_text(str(exc)), file=sys.stderr)
        code = 2
    except KeyboardInterrupt:
        print("\n检查已取消。", file=sys.stderr)
        code = 130
    if args.pause:
        try:
            input("\n按回车关闭窗口……")
        except (EOFError, KeyboardInterrupt):
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
