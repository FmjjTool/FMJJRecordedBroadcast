#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
启动前依赖自检脚本。

作用：启动 App 前，检查 requirements.txt 里声明的所有 Python 依赖，
凡是【没装上】或【版本不满足约束】（例如 pydantic-core 被无声升级到 2.48、
但 pydantic 2.13.x 只认 2.46.4 这类兼容性破坏），就自动用 pip 重新安装修复。

关键顺序：先做【版本检查 + 修复】，再做【import 测试】。因为 pydantic_core
这类 C 扩展一旦被 import 进内存，pip 替换磁盘文件也不会生效，必须趁它还没
被 import 之前就把版本修对。

用法：
    python check_deps.py             # 检查，缺了/错了就自动装
    python check_deps.py --check     # 只检查报告，不安装
被 app_gui.py 在启动时自动调用（在 import 任何项目模块之前）。
"""

import importlib
import importlib.metadata
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REQUIREMENTS = os.path.join(ROOT, "requirements.txt")
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"

# 发行名 -> 实际 import 名（不一致的在这里补齐；键统一小写）
IMPORT_ALIASES = {
    "pydantic-core": "pydantic_core",
    "pycryptodome": "Crypto",
    "pillow": "PIL",
    "beautifulsoup4": "bs4",
    "python-dateutil": "dateutil",
    "opencv-python": "cv2",
    "pywebview": "webview",
    "tkinterdnd2": "tkinterdnd2",
}

C_OK, C_WARN, C_ERR, C_INFO, C_END = "\033[92m", "\033[93m", "\033[91m", "\033[96m", "\033[0m"


def _log(tag, color, msg):
    print(f"{color}[{tag}]{C_END} {msg}", flush=True)


def ok(msg): _log("√", C_OK, msg)
def warn(msg): _log("!", C_WARN, msg)
def err(msg): _log("×", C_ERR, msg)
def info(msg): _log("i", C_INFO, msg)


def _norm(name: str) -> str:
    """发行名规范化（pip/importlib.metadata 均按 - 与 _ 等价处理）。"""
    return name.lower().replace("_", "-")


def _import_name(dist_name: str) -> str:
    key = dist_name.lower()
    if key in IMPORT_ALIASES:
        return IMPORT_ALIASES[key]
    return dist_name.replace("-", "_")


def parse_requirements(path: str):
    """逐行解析 requirements.txt，返回约束行列表。"""
    reqs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            reqs.append(line)
    return reqs


def _split_name(line: str):
    """从约束行里抽出包名（'pydantic>=2.0.0' -> 'pydantic'）。"""
    for sep in (">=", "<=", "==", "~=", "!=", ">", "<", " ", "["):
        if sep in line:
            return line.split(sep, 1)[0].strip()
    return line.strip()


def installed_version(dist_name: str):
    try:
        return importlib.metadata.version(_norm(dist_name))
    except Exception:
        return None


def version_ok(line: str, installed: str):
    """校验已装版本是否满足约束行。解析失败则视为 OK（交给 import 测试兜底）。"""
    try:
        from packaging.requirements import Requirement
        from packaging.version import Version
    except Exception:
        return True
    try:
        req = Requirement(line)
        if not req.specifier:
            return True
        return req.specifier.contains(Version(installed), prereleases=True)
    except Exception:
        return True


def import_ok(import_name: str):
    """实际 import 测试，返回 (是否成功, 错误信息)。"""
    try:
        importlib.import_module(import_name)
        return True, ""
    except SystemExit:
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_versions():
    """阶段一：只查【是否安装 + 版本是否满足约束】，不做 import。"""
    problems = []
    for line in parse_requirements(REQUIREMENTS):
        dist = _split_name(line)
        ver = installed_version(dist)
        if ver is None:
            problems.append(f"缺失 {dist}（{line}）")
        elif not version_ok(line, ver):
            problems.append(f"版本不符 {dist}：已装 {ver}，要求 {line}")
    return problems


def check_imports():
    """阶段二：实际 import 测试（须在版本修复之后调用）。"""
    problems = []
    for line in parse_requirements(REQUIREMENTS):
        dist = _split_name(line)
        good, why = import_ok(_import_name(dist))
        if not good:
            problems.append(f"导入失败 {dist}：{why}")
    return problems


def _pip(args):
    """调用当前解释器执行 pip，返回 returncode。"""
    cmd = [sys.executable, "-m", "pip", *args, "-i", PIP_MIRROR]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            err((r.stderr or r.stdout or "")[-600:])
        return r.returncode
    except Exception as e:
        err(f"pip 调用失败：{e}")
        return -1


def ensure_dependencies(auto_fix=True):
    """入口：版本检查 → 修复 → import 测试。返回 (是否全部就绪, 问题列表)。"""
    # 打包后的 exe（PyInstaller frozen）依赖已内置，直接放行
    if getattr(sys, "frozen", False):
        return True, []

    info("检查 Python 依赖...")
    problems = check_versions()

    if problems:
        warn(f"发现 {len(problems)} 处依赖版本异常：")
        for p in problems:
            print(f"    - {p}", flush=True)
        if not auto_fix:
            return False, problems
        info("开始自动修复：pip install -r requirements.txt ...")
        if _pip(["install", "-r", REQUIREMENTS]) != 0:
            err("依赖重装失败，请手动运行：python -m pip install -r requirements.txt")
        # 复检版本
        problems = check_versions()
        if problems:
            err("修复后版本仍异常，请检查：")
            for p in problems:
                print(f"    - {p}", flush=True)
            return False, problems

    # 阶段二：版本已就绪，做 import 测试
    problems = check_imports()
    if problems:
        warn(f"发现 {len(problems)} 处模块导入失败：")
        for p in problems:
            print(f"    - {p}", flush=True)
        if not auto_fix:
            return False, problems
        # 兜底：能定位到的版本冲突直接精确重装
        _fix_pydantic_core()
        problems = check_imports()
        if problems:
            err("修复后仍有模块无法导入，请检查：")
            for p in problems:
                print(f"    - {p}", flush=True)
            return False, problems

    ok(f"依赖全部就绪（{len(parse_requirements(REQUIREMENTS))} 个模块）")
    return True, []


def _fix_pydantic_core():
    """pydantic import 失败时的精确兜底：按报错里的版本号重装 pydantic-core。

    注意：若 pydantic_core 已被 import 进本进程，重装后同进程内仍读到旧版，
    此时只能提示重启。故本函数只在 import 测试阶段调用，属最后防线。
    """
    try:
        import pydantic  # noqa: F401
        return
    except SystemError as e:
        m = re.search(r"requires (\d+(?:\.\d+)+)", str(e))
        target = m.group(1) if m else "2.46.4"
        warn(f"pydantic 与 pydantic-core 不兼容，重装 pydantic-core=={target} ...")
        if _pip(["install", f"pydantic-core=={target}"]) != 0:
            return
        try:
            import pydantic  # noqa: F401
            ok("pydantic-core 已修复")
        except Exception:
            err("pydantic-core 已重装，但本进程需重启才能生效（旧版已加载进内存）")
    except Exception:
        return


def main():
    auto_fix = "--check" not in sys.argv
    ready, _ = ensure_dependencies(auto_fix=auto_fix)
    sys.exit(0 if ready else 1)


if __name__ == "__main__":
    main()
