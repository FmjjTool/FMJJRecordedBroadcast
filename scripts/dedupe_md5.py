#!/usr/bin/env python3
"""按 MD5 查找重复文件，默认只报告；--apply 时把重复副本移入 Windows 回收站。

用法：
  python scripts/dedupe_md5.py "D:\目录"                # 只读分析
  python scripts/dedupe_md5.py "D:\目录" --apply        # 执行清理
  python scripts/dedupe_md5.py "D:\目录" --report r.json
  python scripts/dedupe_md5.py "D:\目录" --keep-top 微博配图 --keep-top 头像相册

保留策略（同一 MD5 组内依次比较，小者胜出）：
  1. 文件名不带浏览器副本后缀（尾部 "(1)"、"(2)"、" - 副本" 等）
  2. --keep-top 指定的顶层目录（按给出的先后顺序）
  3. 相对路径层级更浅
  4. 路径更短
  5. 修改时间更早

默认会解析目录内所有 .html/.css 的本地 src/href/url() 引用，
被引用的文件一律不删（--no-protect-refs 可关闭）。
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path

COPY_SUFFIX_RE = re.compile(r"(?:\(\d+\)|（\d+）| - 副本|副本|拷贝| copy)$", re.IGNORECASE)


def long_path(path: str) -> str:
    path = os.path.abspath(path)
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def md5_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.md5()
    with open(long_path(path), "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def iter_files(root: Path):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                stat = os.stat(long_path(full))
            except OSError:
                continue
            if not os.path.isfile(long_path(full)):
                continue
            yield full, stat.st_size, stat.st_mtime


def copy_penalty(path: str) -> int:
    stem = os.path.splitext(os.path.basename(path))[0]
    penalty = 0
    while True:
        match = COPY_SUFFIX_RE.search(stem)
        if not match:
            return penalty
        penalty += 1
        stem = stem[: match.start()]


def make_ranker(root: str, keep_top: list[str]):
    def rank(item: str):
        rel = os.path.relpath(item, root)
        parts = Path(rel).parts
        top = parts[0] if len(parts) > 1 else ""
        top_rank = keep_top.index(top) if top in keep_top else len(keep_top)
        return (
            copy_penalty(item),
            top_rank,
            len(parts),
            len(rel),
            os.stat(long_path(item)).st_mtime,
            rel.lower(),
        )

    return rank


REF_ATTR_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
CSS_URL_RE = re.compile(r"""url\(\s*["']?([^"')]+)["']?\s*\)""", re.IGNORECASE)
SKIP_PREFIXES = ("http://", "https://", "data:", "mailto:", "javascript:", "//", "#")


def collect_referenced_paths(root: str) -> set[str]:
    """解析目录内 .html/.css 的本地引用，返回小写绝对路径集合。"""
    refs: set[str] = set()
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            lower = name.lower()
            if not lower.endswith((".html", ".htm", ".css")):
                continue
            full = os.path.join(dirpath, name)
            try:
                text = Path(long_path(full)).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            targets = REF_ATTR_RE.findall(text) + CSS_URL_RE.findall(text)
            for raw in targets:
                value = raw.strip().split("?")[0].split("#")[0]
                if not value or value.lower().startswith(SKIP_PREFIXES):
                    continue
                if os.path.isabs(value) or re.match(r"^[a-zA-Z]:", value):
                    target = value
                else:
                    target = os.path.normpath(os.path.join(dirpath, value))
                refs.add(os.path.normpath(target).lower())
    return refs


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.2f} TB"


class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def send_to_recycle_bin(paths: list[str]) -> tuple[int, int]:
    """返回 (成功数, 失败数)。逐批调用，单批最多 1000 个。"""
    if not paths:
        return 0, 0
    FO_DELETE = 3
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040
    FOF_NOERRORUI = 0x0400
    FOF_NOCONFIRMMKDIR = 0x0200
    ok = fail = 0
    shell32 = ctypes.windll.shell32
    for start in range(0, len(paths), 1000):
        batch = paths[start : start + 1000]
        # SHFileOperationW 需要双 \0 结尾；不使用 \\?\ 前缀。
        payload = "\0".join(os.path.abspath(p) for p in batch) + "\0\0"
        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        op.pFrom = payload
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_NOCONFIRMMKDIR
        result = shell32.SHFileOperationW(ctypes.byref(op))
        if result == 0 and not op.fAnyOperationsAborted:
            ok += len(batch)
        else:
            fail += len(batch)
            print(f"  ! 回收站调用失败 rc={result} aborted={bool(op.fAnyOperationsAborted)}（本批 {len(batch)} 个）")
    return ok, fail


def main() -> int:
    parser = argparse.ArgumentParser(description="MD5 去重，重复副本移入回收站")
    parser.add_argument("root", help="要扫描的目录")
    parser.add_argument("--apply", action="store_true", help="实际移入回收站（默认仅分析）")
    parser.add_argument("--keep-top", action="append", default=[], help="保留优先级最高的顶层目录，可重复")
    parser.add_argument("--min-size", type=int, default=1, help="忽略小于该字节数的文件")
    parser.add_argument("--no-protect-refs", action="store_true", help="不保护 HTML/CSS 引用的资源")
    parser.add_argument("--report", help="把完整报告写入 JSON")
    parser.add_argument("--workers", type=int, default=min(32, (os.cpu_count() or 4) * 2))
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"目录不存在：{root}")
        return 2

    started = time.time()
    files = [(p, s, m) for p, s, m in iter_files(Path(root)) if s >= args.min_size]
    by_size: dict[int, list[str]] = defaultdict(list)
    for path, size, _mtime in files:
        by_size[size].append(path)
    candidates = [p for group in by_size.values() if len(group) > 1 for p in group]
    print(f"扫描 {len(files)} 个文件，按大小预筛出 {len(candidates)} 个候选，开始计算 MD5 ...")

    digests: dict[str, list[str]] = defaultdict(list)
    failures: list[str] = []
    done = 0

    def worker(path: str):
        return path, md5_file(path)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for path, digest in pool.map(worker, candidates):
            digests[digest].append(path)
            done += 1
            if done % 2000 == 0:
                print(f"  已计算 {done}/{len(candidates)}")

    groups: list[tuple[str, list[str]]] = []
    for digest, paths in digests.items():
        if len(paths) > 1:
            groups.append((digest, sorted(paths, key=make_ranker(root, args.keep_top))))
    groups.sort(key=lambda item: os.stat(long_path(item[1][0])).st_size * (len(item[1]) - 1), reverse=True)

    protected: set[str] = set() if args.no_protect_refs else collect_referenced_paths(root)
    if protected:
        print(f"HTML/CSS 本地引用：{len(protected)} 个文件受保护，不会清理")

    total_dup_files = sum(len(paths) - 1 for _digest, paths in groups)
    total_bytes = sum(os.stat(long_path(paths[0])).st_size * (len(paths) - 1) for _digest, paths in groups)
    print(
        f"完成：{len(groups)} 组重复，可清理 {total_dup_files} 个文件，"
        f"约 {human(total_bytes)}，耗时 {time.time() - started:.1f}s"
    )

    top_pairs: dict[tuple[str, str], int] = defaultdict(int)
    for _digest, group in groups:
        tops = sorted({Path(os.path.relpath(p, root)).parts[0] if len(Path(os.path.relpath(p, root)).parts) > 1 else "." for p in group})
        for i, a in enumerate(tops):
            for b in tops[i:]:
                top_pairs[(a, b)] += 1

    print("\n重复组分布（顶层目录两两组合）：")
    for (a, b), count in sorted(top_pairs.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {count:6d}  {a}  <->  {b}")

    protected_hits = [p for _d, paths in groups for p in paths[1:] if p.lower() in protected]
    if protected_hits:
        print(f"\n其中被 HTML/CSS 引用的 {len(protected_hits)} 个副本将保留，不清理")

    print("\n体积最大的 20 组：")
    for _digest, group in groups[:20]:
        size = os.stat(long_path(group[0])).st_size
        print(f"\n  [{human(size)} x {len(group)}] 保留：{os.path.relpath(group[0], root)}")
        for dup in group[1:4]:
            print(f"        重复：{os.path.relpath(dup, root)}")
        if len(group) > 4:
            print(f"        ... 另有 {len(group) - 4} 个")

    if args.report:
        report = {
            "root": root,
            "scanned": len(files),
            "groups": [
                {
                    "md5": digest,
                    "size": os.stat(long_path(paths[0])).st_size,
                    "keep": paths[0],
                    "duplicates": paths[1:],
                }
                for digest, paths in groups
            ],
            "protected_referenced": sorted(protected),
            "total_duplicate_files": total_dup_files,
            "total_duplicate_bytes": total_bytes,
        }
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写入：{args.report}")

    if not args.apply:
        print("\n只读模式，未改动任何文件。加 --apply 执行移入回收站。")
        return 0

    print("\n开始移入回收站（移动前重新校验 MD5）...")
    to_trash: list[str] = []
    skipped_refs: list[str] = []
    for _digest, group in groups:
        keep_digest = md5_file(group[0])
        for dup in group[1:]:
            if dup.lower() in protected:
                skipped_refs.append(dup)
                continue
            try:
                if md5_file(dup) == keep_digest:
                    to_trash.append(dup)
            except OSError as exc:
                failures.append(f"{dup}: {exc}")
    print(f"校验通过 {len(to_trash)} 个，因被引用保留 {len(skipped_refs)} 个，准备移入回收站 ...")
    ok, fail = send_to_recycle_bin(to_trash)
    remaining = [p for p in to_trash if os.path.exists(long_path(p))]
    moved = len(to_trash) - len(remaining)
    print(f"完成：实际移入回收站 {moved} 个，仍存在 {len(remaining)} 个，跳过 {len(failures)} 个")
    for item in failures[:20]:
        print("  跳过：", item)
    for item in remaining[:20]:
        print("  未移走：", item)
    if args.report:
        report_path = Path(args.report)
        data = json.loads(report_path.read_text(encoding="utf-8"))
        data["applied"] = {
            "moved": moved,
            "remaining": remaining,
            "failed_batches": fail,
            "skipped_errors": failures,
            "skipped_referenced": skipped_refs,
        }
        report_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
