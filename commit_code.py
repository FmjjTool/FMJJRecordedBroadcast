#!/usr/bin/env python3
"""Preview, commit and optionally push project code with standard-library Git tools."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import stat
import sys

# Import only the repository's development checker, never application modules.
sys.dont_write_bytecode = True
from check_git_sync import (CheckError, CONFLICTS, Git, choose_target, fetch,
                            proxy_candidates, safe_text, validate_origin)

BLOCKED_DIRS = {
    ".git", "config", "download", "downloads", "logs", "models",
    "dist", "build", "build_full_vendor", "tests", "docs",
    "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules",
    "profile", "profiles", "browser_profile", "user_data",
}
BLOCKED_FILES = {
    "accounts.json", "groups.json", "music_accounts.json", "music_groups.json",
    "tcdoc_state.json", "credentials.json", "secrets.json",
    "id_rsa", "id_ed25519", "task.md", "skills-lock.json",
}
CODE_SUFFIXES = {
    ".py", ".pyi", ".js", ".mjs", ".ts", ".css", ".html", ".svg",
    ".md", ".txt", ".json", ".toml", ".yml", ".yaml", ".xml",
    ".cmd", ".bat", ".ps1", ".sh", ".vbs", ".ini", ".cfg",
}
MAX_FILE = 4 * 1024 * 1024
SECRET = re.compile(
    rb"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    rb"|sk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}"
    rb"|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
    rb"|(?i:Bearer)[ \t]+[A-Za-z0-9_./+=-]{32,}"
    rb"|(?i:api[_-]?key|access[_-]?token|client[_-]?secret|password)"
    rb"[\"']?[ \t]*[:=][ \t]*[\"'][A-Za-z0-9_./+=-]{24,}[\"'])"
)


def blocked(path: str) -> str:
    parts = Path(path).parts
    lower = [part.lower() for part in parts]
    name = lower[-1] if lower else ""
    if (not parts or Path(path).is_absolute() or ".." in parts
            or any(part in BLOCKED_DIRS or part.startswith(("_", "."))
                   for part in lower[:-1])):
        return "运行数据、隐藏目录或临时目录"
    if (name in BLOCKED_FILES or name.startswith(".env")
            or name.endswith((".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx", ".pyc"))
            or re.search(r"(?:cookie|storage_state|session_state|login_state).*(?:\.json|\.txt|\.dat|\.bak)$", name)):
        return "凭据、用户数据或缓存文件"
    if Path(name).suffix not in CODE_SUFFIXES and name not in {".gitignore", ".gitattributes", "license", "makefile"}:
        return "非源码/文档文件，请用 Git 单独审阅"
    return ""


def changes(git: Git) -> list[tuple[str, str]]:
    # Disable rename folding: each returned name is a literal stageable path.
    raw = git.run("-c", "status.renames=false", "status", "--porcelain=v1",
                  "-z", "--untracked-files=all", "--ignore-submodules=none").out
    result = []
    for entry in raw.split(b"\0"):
        if entry:
            line = entry.decode("utf-8", "strict")
            if line[:2] in CONFLICTS:
                raise CheckError("工作区存在冲突，请先手动处理。")
            result.append((line[:2], line[3:]))
    return result


def ready(git: Git) -> tuple[str, str]:
    root = Path(git.text("rev-parse", "--show-toplevel")).resolve()
    if root != git.repo:
        raise CheckError("--repo 必须是仓库根目录。")
    validate_origin(git)
    branch = git.text("branch", "--show-current")
    if not branch:
        raise CheckError("detached HEAD 不能快捷提交，请先切换到明确分支。")
    target = choose_target(git, branch, None)
    if target != "origin/" + branch:
        raise CheckError("分支与 origin 上游名称不一致，请手动确认映射后使用 Git。")
    for marker in ("index.lock", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD",
                   "rebase-merge", "rebase-apply", "BISECT_LOG"):
        path = Path(git.text("rev-parse", "--git-path", marker))
        if (path if path.is_absolute() else root / path).exists():
            raise CheckError("Git 正在执行其他操作，请先完成后再提交。")
    return branch, target


def head(git: Git) -> str:
    return git.text("rev-parse", "--verify", "HEAD^{commit}")


def compare(git: Git, target: str) -> None:
    fetch(git, target)
    base = git.run("merge-base", "HEAD", target, ok=(0, 1))
    if base.code:
        raise CheckError("无法确认共同祖先，停止快捷操作。")
    ahead, behind = map(int, git.text("rev-list", "--left-right", "--count", f"HEAD...{target}").split())
    print(f"远端检查：本地独有 {ahead} / 云端独有 {behind}")
    if behind:
        raise CheckError("本地与云端分叉，请先审阅并决定合并方案。" if ahead else
                         "云端领先，请先保存工作并手动快进更新。")


def snapshot(git: Git, paths: list[str]) -> dict[str, str]:
    result = {}
    for name in paths:
        reason = blocked(name)
        if reason:
            raise CheckError(f"拒绝提交 {safe_text(name)}：{reason}。")
        path = git.repo / name
        if not path.resolve().is_relative_to(git.repo):
            raise CheckError("路径解析后越出仓库：" + safe_text(name))
        for part in [path, *path.parents]:
            if part == git.repo:
                break
            info = part.lstat() if part.exists() or part.is_symlink() else None
            if (part.is_symlink() or info and getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                raise CheckError("拒绝符号链接或 junction：" + safe_text(name))
        if not path.exists():
            result[name] = "<deleted>"
            continue
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            raise CheckError("不是普通文件或超过 4 MiB：" + safe_text(name))
        data = path.read_bytes()
        if b"\0" in data or SECRET.search(data):
            raise CheckError("疑似凭据或二进制内容，未输出正文，停止：" + safe_text(name))
        result[name] = hashlib.sha256(data).hexdigest()
    return result


def file_manifest(git: Git, paths: list[str], commit: str | None = None) -> dict[str, tuple[str, str]]:
    args = ("ls-tree", "-r", "-z", commit) if commit else ("ls-files", "--stage", "-z")
    raw = git.run("--literal-pathspecs", *args, "--", *paths).out
    result = {}
    for record in raw.split(b"\0"):
        if record:
            meta, name = record.split(b"\t", 1)
            fields = meta.decode("ascii").split()
            mode, blob = (fields[0], fields[2]) if commit else (fields[0], fields[1])
            result[name.decode("utf-8")] = mode, blob
    return result


def outgoing_check(git: Git, target: str) -> None:
    raw = git.run("log", "--format=", "--name-only", "-z", "--no-renames", f"{target}..HEAD", "--").out
    for raw_path in raw.split(b"\0"):
        name = raw_path.decode("utf-8", "strict").strip("\n")
        if name and blocked(name):
            raise CheckError("待推送历史含受保护或非源码路径，请人工审阅：" + safe_text(name))
    patch = git.run("log", "-p", "--format=", "--no-ext-diff", "--no-textconv",
                    "--no-renames", f"{target}..HEAD", "--").out
    added = b"\n".join(line[1:] for line in patch.splitlines()
                        if line.startswith(b"+") and not line.startswith(b"+++"))
    if SECRET.search(added):
        raise CheckError("待推送历史疑似含凭据，未输出正文；请人工安全审阅。")


def push(git: Git, branch: str, target: str) -> None:
    compare(git, target)
    expected = head(git)
    outgoing_check(git, target)
    env = {key: value for key, value in git.env.items()
           if key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}}

    def attempt(label: str, proxy: str) -> str:
        if head(git) != expected or git.text("branch", "--show-current") != branch:
            raise CheckError("提交或分支已被其他任务改变，请重新审阅。")
        validate_origin(git)
        print(f"正在推送（{label}）……", flush=True)
        try:
            result = git.run("-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}",
                             "-c", f"http.https://github.com/.proxy={proxy}",
                             "-c", f"remote.origin.proxy={proxy}", "push",
                             "--porcelain", "--no-follow-tags", "--recurse-submodules=no",
                             "origin", f"{expected}:refs/heads/{branch}",
                             env=env, ok=(0, 1, 128))
            if result.code == 0:
                return ""
            return safe_text((result.err or result.out).decode("utf-8", "replace"))[:1600] or f"Git 退出码 {result.code}"
        except CheckError as exc:
            return str(exc)

    error = attempt("直连", "")
    network = ("could not resolve", "failed to connect", "timed out", "timeout",
               "connection reset", "connection was reset", "empty reply",
               "network is unreachable", "tls", "schannel", "超过")
    if error:
        print("  直连失败：" + error)
        if any(term in error.lower() for term in network):
            for label, proxy in proxy_candidates(git):
                error = attempt(label, proxy)
                if not error:
                    break
                print(f"  {label}失败：{error}")
                if not any(term in error.lower() for term in network):
                    break
        if error:
            raise CheckError("推送未确认成功；本地提交仍保留，请重新检查远端再重试。")
    fetch(git, target)
    actual = git.text("rev-parse", "--verify", f"{target}^{{commit}}")
    if actual != expected:
        raise CheckError("推送后远端已发生变化，请重新检查；不会覆盖远端历史。")
    print("云端已核对：" + actual)


def execute(args) -> int:
    git = Git(Path(args.repo).resolve(), args.timeout)
    branch, target = ready(git)
    before = head(git)
    status = changes(git)
    selected, skipped = [], []
    requested = []
    for value in args.paths:
        path = Path(value)
        resolved = (path if path.is_absolute() else git.repo / path).resolve()
        try:
            name = resolved.relative_to(git.repo).as_posix()
        except ValueError as exc:
            raise CheckError("指定路径越出仓库。") from exc
        requested.append(name)
    for state, name in status:
        if requested and not any(name == p or name.startswith(p.rstrip("/") + "/") or p == "." for p in requested):
            continue
        if args.python_only and Path(name).suffix.lower() != ".py":
            continue
        reason = blocked(name)
        if reason:
            skipped.append((name, reason))
        else:
            selected.append(name)
    print(f"仓库：{safe_text(str(git.repo))}\n分支：{safe_text(branch)} → {safe_text(target)}")
    for name, reason in skipped:
        print(f"跳过：{safe_text(name)}（{reason}）")
    print(f"\n待提交 {len(selected)} 个文件（所选文件提交完整工作区版本）：")
    for name in selected:
        print("  " + safe_text(name))
    if args.push_only:
        selected = []
        print("仅推送模式：上述未提交文件均保留。")
    elif any(state[0] not in {" ", "?"} and name not in selected for state, name in status):
        raise CheckError("暂存区还有未选中的文件；为保留原暂存状态，请先用 Git 处理或改为完整范围。")
    saved = snapshot(git, selected)
    if args.dry_run:
        print("仅预览完成，未联网、暂存、提交或推送。")
        return 0
    action = "2" if args.push or args.push_only else "1"
    if not args.yes:
        if args.push or args.push_only:
            action = "2" if input("\n确认推送当前分支全部未推送提交？输入 y 继续：").strip().lower() == "y" else "0"
        else:
            action = input("\n1 = 仅本地提交；2 = 提交并推送；0 = 取消 [1]：").strip() or "1"
        if action not in {"1", "2"}:
            print("已取消。")
            return 0
    message = args.message
    if selected and not message:
        if args.yes:
            raise CheckError("--yes 提交时必须同时提供 -m 提交说明。")
        message = input("提交说明（留空使用时间说明）：").strip()
        message = message or "chore: update code " + datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    if message and SECRET.search(message.encode("utf-8")):
        raise CheckError("提交说明疑似含凭据，停止。")
    if not selected and action != "2":
        print("没有可提交文件。")
        return 0
    compare(git, target)
    if ready(git) != (branch, target) or head(git) != before or changes(git) != status or snapshot(git, selected) != saved:
        raise CheckError("预览后文件、暂存区、分支或提交已变化，请重新运行。")
    if selected:
        whitespace = git.run("--literal-pathspecs", "diff", "--check", "HEAD",
                             "--", *selected, ok=(0, 1, 2))
        if whitespace.code:
            raise CheckError("所选文件的 Git 空白检查未通过；请用 git diff --check 核查后重试。")
        indexed = file_manifest(git, selected)
        # Already-staged deletions (including git mv sources) no longer exist
        # in the index; passing those names to git add would fail.
        to_stage = [name for name in selected if name in indexed or (git.repo / name).exists()]
        for start in range(0, len(to_stage), 40):
            git.run("--literal-pathspecs", "add", "--", *to_stage[start:start + 40])
        if head(git) != before or snapshot(git, selected) != saved:
            raise CheckError("暂存期间内容已变化；暂存内容保留，请重新审阅。")
        staged = file_manifest(git, selected)
        for mode, blob in staged.values():
            if mode not in {"100644", "100755"}:
                raise CheckError("暂存区含符号链接或子模块，停止提交。")
            data = git.run("cat-file", "blob", blob).out
            if b"\0" in data or SECRET.search(data):
                raise CheckError("暂存内容疑似含凭据或二进制，停止提交。")
        # --only prevents unrelated files staged by another task from entering this commit.
        result = git.run("--literal-pathspecs", "commit", "--only", "-m", message, "--", *selected)
        print(safe_text(result.out.decode("utf-8", "replace").strip()))
        committed = head(git)
        if (git.text("rev-parse", committed + "^") != before
                or file_manifest(git, selected, committed) != staged):
            raise CheckError("提交期间有并发改动或钩子改变内容；提交已保留，停止推送，请人工审阅。")
        print("本地提交已保存并核对：" + committed)
    if action == "2":
        push(git, branch, target)
    remaining = changes(git)
    print(f"完成。工作区仍有 {len(remaining)} 个文件未提交。")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="预览并快捷提交本项目代码；可选推送，不改版本或打包。")
    parser.add_argument("paths", nargs="*", help="可选：仓库内文件/目录，按字面路径选择")
    parser.add_argument("-m", "--message", help="提交说明")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent), help="仓库根目录")
    parser.add_argument("--python-only", action="store_true", help="仅选择 .py 文件")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--push", action="store_true", help="提交后推送当前分支全部未推送提交")
    group.add_argument("--push-only", action="store_true", help="仅推送，保留工作区和暂存区")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不联网或写入")
    parser.add_argument("--yes", action="store_true", help="按参数直接执行，跳过交互确认")
    parser.add_argument("--timeout", type=int, default=30, help="每条 Git 命令超时，3–300 秒")
    parser.add_argument("--pause", action="store_true", help="退出前按回车，供双击入口使用")
    args = parser.parse_args(argv)
    if not 3 <= args.timeout <= 300:
        parser.error("--timeout 必须在 3–300 秒之间。")
    try:
        code = execute(args)
    except (CheckError, OSError, ValueError, EOFError) as exc:
        print("\n操作停止：" + safe_text(str(exc)), file=sys.stderr)
        print("已经生成的本地提交/暂存内容会保留，请核对 Git 状态。", file=sys.stderr)
        code = 2
    except KeyboardInterrupt:
        print("\n操作已取消，请核对 Git 状态。", file=sys.stderr)
        code = 130
    if args.pause:
        try:
            input("\n按回车关闭窗口……")
        except (EOFError, KeyboardInterrupt):
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
