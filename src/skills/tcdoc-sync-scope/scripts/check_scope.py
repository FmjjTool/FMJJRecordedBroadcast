"""腾讯文档写入范围校验（tcdoc-sync-scope 强制约束的可执行版本）。

用法：
  python scripts/check_scope.py --doc DRnd3bkhkQUR1aEJr --tab ss_h2uh2s
  python scripts/check_scope.py --doc DRnd3bkhkQUR1aEJr --tab ss_ktwk0t
  python scripts/check_scope.py --list

退出码：
  0  允许（目标表在 allow 列表内）
  2  禁止（目标在 deny 列表内，或所属文档整体禁写）
  3  未知目标（doc_key / tab_id 不在清单内，按 fail-closed 处理，必须停下来问用户）

纯标准库，不导入项目模块，可直接在任意环境运行。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCOPE_FILE = Path(__file__).resolve().parent.parent / "references" / "scope.json"

EXIT_ALLOW = 0
EXIT_DENY = 2
EXIT_UNKNOWN = 3


def load_scope() -> dict:
    """读取范围清单（唯一事实源 references/scope.json）。"""
    with open(SCOPE_FILE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def check(scope: dict, doc_key: str, tab_id: str) -> tuple[int, str]:
    """判断 (doc_key, tab_id) 是否允许写入，返回 (退出码, 说明)。"""
    doc = (scope.get("docs") or {}).get(doc_key)
    if not doc:
        deny_docs = scope.get("deny_docs") or {}
        if doc_key in deny_docs:
            return EXIT_DENY, f"禁止：文档《{deny_docs[doc_key]}》({doc_key}) 整体禁写"
        return EXIT_UNKNOWN, f"未知文档 {doc_key}：不在任何清单内，按禁止处理，需用户确认"

    doc_name = doc.get("name") or doc_key
    for item in doc.get("allow") or []:
        if item.get("tab_id") == tab_id:
            return (EXIT_ALLOW,
                    f"允许：{doc_name} / {item.get('tab_name')}（{tab_id}），"
                    f"可执行 {','.join(item.get('ops') or [])}")

    for item in doc.get("deny") or []:
        if item.get("tab_id") == tab_id:
            return EXIT_DENY, f"禁止：{doc_name} / {item.get('tab_name')}（{tab_id}）不在白名单内"

    return EXIT_UNKNOWN, f"未知工作表 {tab_id}（{doc_name}）：按禁止处理，需用户确认"


def main() -> int:
    parser = argparse.ArgumentParser(description="腾讯文档写入范围校验")
    parser.add_argument("--doc", help="文档 key，例如 DRnd3bkhkQUR1aEJr")
    parser.add_argument("--tab", help="工作表 tab_id，例如 ss_h2uh2s")
    parser.add_argument("--list", action="store_true", help="打印白名单与黑名单")
    args = parser.parse_args()

    scope = load_scope()

    if args.list:
        for key, doc in (scope.get("docs") or {}).items():
            print(f"{doc.get('name')} ({key})")
            for item in doc.get("allow") or []:
                print(f"  ALLOW  {item.get('tab_id')}  {item.get('tab_name')}")
            for item in doc.get("deny") or []:
                print(f"  DENY   {item.get('tab_id')}  {item.get('tab_name')}")
        for key, name in (scope.get("deny_docs") or {}).items():
            print(f"DENY-DOC  {key}  {name}")
        return EXIT_ALLOW

    if not args.doc or not args.tab:
        parser.error("需要同时提供 --doc 与 --tab（或使用 --list）")

    code, message = check(scope, args.doc.strip(), args.tab.strip())
    stream = sys.stdout if code == EXIT_ALLOW else sys.stderr
    print(message, file=stream)
    return code


if __name__ == "__main__":
    sys.exit(main())
