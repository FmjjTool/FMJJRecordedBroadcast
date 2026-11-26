"""购物解析 L3 输入路由：桌面与 WebUI 共用平台范围和派发计划。

只处理输入，不读取凭据、不发请求、不写文件。每次仍只解析首个已勾选
且识别成功的平台；抖音支持同组多行，淘宝、B站和闲鱼一次一条。
"""

from __future__ import annotations

import re

SHOPPING_PLATFORM_OPTIONS = (
    ("taobao", "淘宝"),
    ("douyin", "抖音商城"),
    ("bili", "B站小店"),
    ("xianyu", "闲鱼"),
)
PLATFORM_LABELS = dict(SHOPPING_PLATFORM_OPTIONS)

_DOUYIN_RE = re.compile(
    r"(?:v\.douyin\.com|haohuo\.jinritemai\.com|(?:www\.|m\.)?douyin\.com/|【抖音商城】)",
    re.I,
)
_BILI_RE = re.compile(
    r"(?:b23\.tv|mall\.bilibili\.com|bilibili\.com/h5/mall|space\.bilibili\.com)",
    re.I,
)
_BILI_UID_RE = re.compile(r"^\d{3,}$")
_TAOBAO_RE = re.compile(r"(?:taobao\.com|tmall\.com|tb\.cn)", re.I)
_XIANYU_RE = re.compile(
    r"https?://(?:www\.|h5\.m\.)?goofish\.com(?:[/:?\s]|$)"
    r"|https?://2\.taobao\.com/item"
    r"|https?://h5\.m\.taobao\.com/app/idleFish-F2e/", re.I)


def normalize_shopping_platforms(platforms=None) -> tuple[str, ...]:
    """None 沿用全部平台；显式空列表或未知键不能退回全部。"""
    if platforms is None:
        return tuple(PLATFORM_LABELS)
    if not isinstance(platforms, (list, tuple, set, frozenset)):
        raise ValueError("平台范围格式无效，请重新勾选平台")
    if not platforms:
        raise ValueError("请至少勾选一个平台")
    if any(not isinstance(key, str) or key not in PLATFORM_LABELS
           for key in platforms):
        raise ValueError("平台范围只支持淘宝 / 抖音商城 / B站小店 / 闲鱼")
    return tuple(key for key in PLATFORM_LABELS if key in platforms)


def detect_platform(line: str) -> str:
    """按链接头识别平台；不为未知文本猜测平台或展开短链。"""
    text = (line or "").strip()
    if _XIANYU_RE.search(text):
        return "xianyu"
    if _DOUYIN_RE.search(text):
        return "douyin"
    if _BILI_RE.search(text) or _BILI_UID_RE.fullmatch(text):
        return "bili"
    if _TAOBAO_RE.search(text):
        return "taobao"
    return ""


def group_shopping_input(text: str, platforms=None) -> dict:
    """纯输入分析，供界面即时提示与提交时的相同规则使用。"""
    selected = normalize_shopping_platforms(platforms)
    groups = {key: [] for key in PLATFORM_LABELS}
    unknown = 0
    first = ""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        key = detect_platform(line)
        if not key:
            unknown += 1
            continue
        groups[key].append(line)
        if not first and key in selected:
            first = key
    return {"platforms": selected, "groups": groups,
            "platform": first, "unknown": unknown}


def plan_shopping_parse(text: str, platforms=None) -> dict:
    """网络与凭据调用前确定唯一执行平台及实际提交文本。"""
    analysis = group_shopping_input(text, platforms)
    if not (text or "").strip():
        raise ValueError("请先粘贴店铺 / 商品链接或分享文案")
    groups = analysis["groups"]
    selected = analysis["platforms"]
    platform = analysis["platform"]
    counts = {key: len(lines) for key, lines in groups.items()}
    skipped = {key: count for key, count in counts.items()
               if count and key not in selected}
    if not platform:
        if skipped:
            raise ValueError("输入链接的平台均未勾选，请勾选对应平台后重试")
        raise ValueError("未识别到平台，请检查店铺 / 商品链接或分享文案")
    chosen = groups[platform] if platform == "douyin" else groups[platform][:1]
    deferred = {key: count - (len(chosen) if key == platform else 0)
                for key, count in counts.items()
                if key in selected and count > (len(chosen) if key == platform else 0)}
    parts = [f"已识别 {PLATFORM_LABELS[platform]}：{len(chosen)} 条"]
    if skipped:
        parts.append("未勾选，已跳过：" + "、".join(
            f"{PLATFORM_LABELS[key]} {count} 条" for key, count in skipped.items()))
    if deferred:
        parts.append("本次未处理：" + "、".join(
            f"{PLATFORM_LABELS[key]} {count} 条" for key, count in deferred.items())
            + "（一次解析一个平台；淘宝、B站和闲鱼一次一条）")
    if analysis["unknown"]:
        parts.append(f"未识别 {analysis['unknown']} 条，已跳过")
    return {"platform": platform, "text": "\n".join(chosen),
            "counts": counts, "skipped": skipped,
            "unknown": analysis["unknown"], "deferred": deferred,
            "summary": "；".join(parts)}
