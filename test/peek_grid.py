# -*- coding: utf-8 -*-
"""离线解码 grid 表格的 related_sheet / workbook。"""
import base64
import json
import os
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "_dump", "grid_33mt1g.json")


def unblob(v, label=""):
    s = v.lstrip()
    if s[:1] in "[{":
        return json.loads(s)
    raw = base64.b64decode(v + "=" * (-len(v) % 4))
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            return json.loads(zlib.decompress(raw, wbits).decode("utf-8"))
        except Exception as exc:
            last = exc
    raise last


def main():
    doc = json.load(open(PATH, encoding="utf-8"))
    ch = doc["clientVars"]["collab_client_vars"]["initialAttributedText"]["text"][0]
    for key in ("workbook", "related_sheet"):
        blob = ch.get(key)
        if not blob:
            continue
        data = unblob(blob)
        print(f"\n===== {key}: {type(data).__name__} len={len(data)}")
        text = json.dumps(data, ensure_ascii=False)
        print(text[:2500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
