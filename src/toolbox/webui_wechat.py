"""公众号资料的 Qt-free Web 边界：声明式字段、共享用例与安全 DTO。"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import re

from toolbox.wechat_store import ACCOUNT_SORTS, ARTICLE_SORTS, STATUS_LABELS, article_id


class WebWechatError(ValueError):
    """仅包含本模块定义的公开校验提示。"""


_FORMATS = [{"value": "html", "label": "HTML"},
            {"value": "docx", "label": "Word"},
            {"value": "md", "label": "Markdown"}]
_SORT_ORDER = ["", "asc", "desc"]
FIELDS = {
    "accounts": {
        "keyword": {"type": "str", "label": "公众号关键词", "default": "", "max_length": 200},
        "sort": {"type": "choice", "default": "", "allow": ["", *ACCOUNT_SORTS], "hidden": True},
        "order": {"type": "choice", "default": "", "allow": _SORT_ORDER, "hidden": True},
        "offset": {"type": "int", "default": 0, "min": 0, "max": 10000000, "hidden": True},
        "limit": {"type": "int", "label": "每页条数", "default": 200, "min": 1, "max": 200},
    },
    "articles": {
        "biz": {"type": "str", "label": "公众号标识", "default": "", "max_length": 256},
        "keyword": {"type": "str", "label": "标题关键词", "default": "", "max_length": 200},
        "date_from": {"type": "date", "label": "开始日期", "default": ""},
        "date_to": {"type": "date", "label": "结束日期", "default": ""},
        "status": {"type": "choice", "label": "归档状态", "default": "", "options": [{"value": "", "label": "全部状态"}, *[{"value": k, "label": v} for k, v in STATUS_LABELS.items()]]},
        "sort": {"type": "choice", "default": "", "allow": ["", *ARTICLE_SORTS], "hidden": True},
        "order": {"type": "choice", "default": "", "allow": _SORT_ORDER, "hidden": True},
        "offset": {"type": "int", "default": 0, "min": 0, "max": 10000000, "hidden": True},
        "limit": {"type": "int", "label": "每页条数", "default": 200, "min": 1, "max": 200},
    },
    "account": {
        "biz": {"type": "str", "label": "公众号标识", "default": "", "max_length": 256, "required": True, "readonly": True},
        "name": {"type": "str", "label": "公众号名称", "default": "", "max_length": 160},
        "remark": {"type": "str", "label": "备注", "default": "", "max_length": 500},
    },
    "collect": {
        "text": {"type": "text", "label": "文章链接", "default": "", "max_length": 60000, "required": True, "placeholder": "粘贴 mp.weixin.qq.com 文章链接，一行一个"},
    },
    "sync": {
        "biz": {"type": "str", "default": "", "max_length": 256, "required": True, "hidden": True},
        "mode": {"type": "choice", "label": "同步方式", "default": "latest", "options": [{"value": "latest", "label": "同步最新"}, {"value": "continue", "label": "继续同步"}, {"value": "all", "label": "抓取全部"}]},
        "max_pages": {"type": "int", "label": "最多页数", "default": 10, "min": 1, "max": 50},
    },
    "export": {
        "ids": {"type": "idlist", "label": "文章", "default": [], "required": True, "hidden": True, "max_items": 200},
        "formats": {"type": "strlist", "label": "导出格式", "default": ["html", "docx", "md"], "options": _FORMATS, "required": True},
    },
    "retry": {
        "biz": {"type": "str", "default": "", "max_length": 256, "hidden": True},
        "formats": {"type": "strlist", "label": "导出格式", "default": ["html", "docx", "md"], "options": _FORMATS, "required": True},
    },
}


def schema() -> dict:
    return {"groups": deepcopy(FIELDS), "statuses": dict(STATUS_LABELS)}


def normalize(group: str, payload: dict | None = None) -> dict:
    fields = FIELDS[group]
    source = payload if isinstance(payload, dict) else {}
    result = {}
    for name, spec in fields.items():
        value = source.get(name, deepcopy(spec.get("default")))
        kind = spec["type"]
        label = spec.get("label", "参数")
        allowed = spec.get("allow", [item["value"] for item in spec.get("options", [])])
        if kind == "int":
            try:
                if isinstance(value, (bool, float)):
                    raise ValueError
                value = int(value)
            except (TypeError, ValueError):
                raise WebWechatError(f"{label}必须是整数") from None
            if not spec["min"] <= value <= spec["max"]:
                raise WebWechatError(f"{label}超出允许范围")
        elif kind in ("strlist", "idlist"):
            if not isinstance(value, list) or len(value) > spec.get("max_items", 10):
                raise WebWechatError(f"{label}数量不正确")
            clean = []
            for item in value:
                if kind == "idlist":
                    if not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{64}", item):
                        raise WebWechatError("文章选择无效")
                elif item not in allowed:
                    raise WebWechatError("不支持的导出格式")
                if item not in clean:
                    clean.append(item)
            value = clean
        else:
            if not isinstance(value, str):
                raise WebWechatError(f"{label}格式不正确")
            value = value.strip()
            if len(value) > spec.get("max_length", 1000):
                raise WebWechatError(f"{label}过长")
            if kind == "choice" and value not in allowed:
                raise WebWechatError(f"{label}选项无效")
            if kind == "date" and value:
                try:
                    value = date.fromisoformat(value).isoformat()
                except ValueError:
                    raise WebWechatError(f"{label}格式不正确") from None
        if spec.get("required") and not value:
            raise WebWechatError(f"请填写或选择{label}")
        result[name] = value
    if result.get("date_from") and result.get("date_to") and result["date_from"] > result["date_to"]:
        raise WebWechatError("开始日期不能晚于结束日期")
    return result


def _library():
    from toolbox import wechat_library
    return wechat_library


def safe_message(value, fallback: str = "操作失败，请稍后重试") -> str:
    """Web 可见错误与任务日志不携带外部异常、会话参数或本机路径。"""
    text = str(value or "")
    if "需要登录" in text or "未登录" in text or "登录已失效" in text:
        return "需要登录"
    if re.search(r"(?:[A-Za-z]:[\\/]|\\\\|/(?:[A-Za-z0-9_.-]+/)+|https?://|cookie|token|secret|authorization|pass_ticket|[?&](?:key|uin|ticket)=)", text, re.I):
        return fallback
    if len(text) > 160 or not text.strip():
        return fallback
    return text.strip()


def _text(value, limit=1000) -> str:
    return str(value or "")[:limit]


def _account(item: dict) -> dict:
    return {"biz": _text(item.get("biz"), 256), "name": _text(item.get("name"), 200),
            "remark": _text(item.get("remark")), "article_count": int(item.get("article_count") or 0),
            "last_sync": _text(item.get("last_sync"), 40)}


def _article(item: dict) -> dict:
    status = item.get("status", "archived")
    url = str(item.get("url") or "")
    try:
        article_id(url)  # 仅接受主库允许的公开链接；拒绝会话参数和外部地址。
    except ValueError:
        url = ""
    return {"id": _text(item["id"], 64), "biz": _text(item.get("biz"), 256),
            "account": _text(item.get("account"), 200), "title": _text(item.get("title"), 1000),
            "url": url,
            "publish_time": _text(item.get("publish_time"), 40),
            "status": status if status in STATUS_LABELS else "archived",
            "error": safe_message(item.get("error"), "处理失败，请重试") if item.get("error") else ""}


def list_accounts(payload=None) -> dict:
    result = _library().list_accounts(**normalize("accounts", payload))
    return {"items": [_account(item) for item in result.get("items", [])],
            "total": int(result.get("total") or 0)}


def list_articles(payload=None) -> dict:
    result = _library().list_articles(**normalize("articles", payload))
    return {"items": [_article(item) for item in result.get("items", [])],
            "total": int(result.get("total") or 0)}


def save_account(payload=None) -> dict:
    return _account(_library().save_account(**normalize("account", payload)))


def index_csv(payload=None) -> bytes:
    return _library().index_csv(normalize("articles", payload))


def run(action: str, payload: dict, *, on_progress, on_log, is_cancelled) -> dict:
    args = normalize(action, payload)
    library = _library()
    callbacks = {"on_progress": on_progress, "on_log": on_log, "is_cancelled": is_cancelled}
    if action == "collect":
        result = library.collect_links(**args, **callbacks)
    elif action == "sync":
        result = library.sync_account(**args, **callbacks)
    elif action == "export":
        result = library.export_articles(**args, out_dir=library.current_export_dir(), **callbacks)
    elif action == "retry":
        result = library.retry_failed(**args, out_dir=library.current_export_dir(), **callbacks)
    else:
        raise ValueError("不支持的操作")
    return {"ok": int(result.get("ok") or 0), "failed": int(result.get("failed") or 0),
            "summary": safe_message(result.get("summary"), "任务完成")}
