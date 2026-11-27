"""本地表格映射回写的 Web 用例；只持有短期内存预览，复用共享逐行执行器。"""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import date, datetime
import io
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time
import zipfile

from toolbox.table_sort_keys import sort_key, sort_records
from toolbox.tcdoc import mapping_workflow as workflow, openapi

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_ROWS = 10000
MAX_COLUMNS = 100
SESSION_TTL = 1800
MAX_SESSIONS = 8
TARGETS = {
    "official": {"name": "官方动态", "tab_id": "ss_h2uh2s"},
    "live": {"name": "直播记录", "tab_id": "ss_1xgstm"},
}


def _target(tab):
    target = TARGETS.get(str(tab or ""))
    if target is None:
        raise ValueError("请选择「官方动态」或「直播记录」")
    openapi.validate_write_scope(openapi.WRITABLE_DOC_KEY, target["tab_id"])
    return target


def _client():
    # get_cookie 尊重管理层总开关；不使用绕过有效状态的旧读取入口。
    import auth
    try:
        raw = json.loads(auth.get_cookie(openapi.CRED_KEY) or "{}")
        values = [str(raw.get(key) or "").strip()
                  for key in ("client_id", "access_token", "open_id")]
    except Exception:
        raise ValueError("需要登录后重试连接") from None
    if not all(values):
        raise ValueError("需要登录后重试连接")
    return openapi.TdClient(*values)


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _read_upload(payload):
    name = str(payload.get("name") or "")
    suffix = Path(name).suffix.lower()
    if suffix not in {".csv", ".tsv", ".xlsx", ".json"}:
        raise ValueError("请选择 CSV、TSV、XLSX 或 JSON 表格")
    encoded = payload.get("content")
    if not isinstance(encoded, str) or len(encoded) > (MAX_FILE_BYTES + 2) // 3 * 4:
        raise ValueError("导入文件不能超过 4 MB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise ValueError("文件内容无法读取，请重新选择") from None
    if not data or len(data) > MAX_FILE_BYTES:
        raise ValueError("请选择非空且不超过 4 MB 的表格")
    if suffix == ".xlsx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                items = archive.infolist()
                if (len(items) > 2000 or sum(item.file_size for item in items) > 32 * 1024 * 1024
                        or any(item.file_size > 16 * 1024 * 1024 for item in items)):
                    raise ValueError
        except (ValueError, zipfile.BadZipFile):
            raise ValueError("Excel 文件过大或格式损坏，请另存为 CSV 后重试") from None
    try:
        with tempfile.TemporaryDirectory(prefix="fmjj_mapping_") as folder:
            path = Path(folder) / ("table" + suffix)
            path.write_bytes(data)
            headers, rows = openapi.load_table(str(path))
    except ImportError:
        raise ValueError("缺少表格读取组件，请在全局设置中检查依赖") from None
    except Exception:
        raise ValueError("表格读取失败，请检查首行列名及文件格式后重试") from None
    if not headers or not rows:
        raise ValueError("表格没有数据行，请检查首行列名和内容")
    if len(headers) > MAX_COLUMNS or len(rows) > MAX_ROWS:
        raise ValueError("每次最多导入 100 列、10 000 行，请拆分表格后重试")
    if any(len(str(header)) > 200 for header in headers):
        raise ValueError("列名过长，请缩短后重试")
    # 日期变为可传输的文本；结构值保留供共享编码器校验。
    rows = [{header: (_text(row.get(header)) if isinstance(row.get(header), (date, datetime))
                       else row.get(header, "")) for header in headers} for row in rows]
    if any(len(_text(value)) > 100000 for row in rows for value in row.values()):
        raise ValueError("单元格内容过长，请缩短后重试")
    return suffix[1:].upper(), headers, rows


class MappingSessions:
    """预览不落盘、不接收主机路径；服务端保存可信计划并防重复提交。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions = {}

    def _get(self, session_id):
        now = time.monotonic()
        for key, item in list(self._sessions.items()):
            if now - item["touched"] > SESSION_TTL and not item["busy"]:
                del self._sessions[key]
        item = self._sessions.get(str(session_id or ""))
        if item is None:
            raise ValueError("本地表格预览已过期，请重新导入")
        item["touched"] = now
        return item

    def import_table(self, payload):
        kind, headers, rows = _read_upload(payload)
        with self._lock:
            # 同时约束条目数；旧的空闲预览可重建，运行中任务不被回收。
            now = time.monotonic()
            idle = sorted((item["touched"], key) for key, item in self._sessions.items()
                          if not item["busy"])
            while len(self._sessions) >= MAX_SESSIONS and idle:
                del self._sessions[idle.pop(0)[1]]
            if len(self._sessions) >= MAX_SESSIONS:
                raise ValueError("运行中的表格任务过多，请等待完成后重试")
            sid = secrets.token_urlsafe(24)
            self._sessions[sid] = {"headers": headers, "rows": rows,
                "tab": "", "fields": [], "mapping": {}, "key_col": "",
                "actions": [], "preview": "", "busy": False, "touched": now}
        return {"session": sid, "format": kind, "headers": headers, "total": len(rows)}

    def _begin(self, sid):
        with self._lock:
            item = self._get(sid)
            if item["busy"]:
                raise ValueError("此表格已有任务进行中，请等待完成")
            item["busy"] = True
            return item

    def release(self, sid):
        with self._lock:
            item = self._sessions.get(sid)
            if item:
                item["busy"] = False
                item["touched"] = time.monotonic()

    def schema(self, sid, tab, log, is_cancelled):
        target = _target(tab)
        item = self._begin(sid)
        try:
            if is_cancelled():
                return {"cancelled": True}
            log("正在连接目标工作表")
            client = _client()
            file_id = client.convert_file_id(openapi.WRITABLE_DOC_KEY)
            client.validate_write_target(file_id, target["tab_id"])
            fields = client.get_fields(file_id, target["tab_id"])
            if is_cancelled():
                return {"cancelled": True}
            mapping = workflow.suggest_mapping(item["headers"], fields)
            field_by_id = {str(field["fieldID"]): field for field in fields}
            key_col = workflow.suggest_key(item["headers"], mapping, field_by_id)
            with self._lock:
                item.update(tab=tab, fields=deepcopy(fields), mapping=mapping, key_col=key_col,
                            actions=[], preview="")
            return {"fields": [{"id": field["fieldID"], "name": field["fieldTitle"],
                     "type": openapi.FIELD_TYPES.get(field.get("fieldType"), "其他"),
                     "supported": field.get("fieldType") in openapi.SUPPORTED_TYPES} for field in fields],
                    "mapping": mapping, "key_col": key_col, "target": target["name"]}
        except workflow.MappingError:
            raise
        except ValueError:
            raise
        except Exception:
            raise ValueError("连接工作表失败，请检查网络和登录状态后重试") from None
        finally:
            self.release(sid)

    def compare(self, payload, log, is_cancelled):
        sid = str(payload.get("session") or "")
        item = self._begin(sid)
        try:
            target = _target(item["tab"])
            mapping = payload.get("mapping")
            if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                    for k, v in mapping.items()):
                raise ValueError("列匹配格式无效，请重新选择")
            mapping = {key: value for key, value in mapping.items() if value}
            key_col = str(payload.get("key_col") or "")
            with self._lock:
                item.update(preview="", actions=[])
            if is_cancelled():
                return {"cancelled": True}
            log("正在读取云端记录并逐行比对")
            client = _client()
            file_id = client.convert_file_id(openapi.WRITABLE_DOC_KEY)
            client.validate_write_target(file_id, target["tab_id"])
            fields = client.get_fields(file_id, target["tab_id"])
            records = client.get_records(file_id, target["tab_id"])
            if is_cancelled():
                return {"cancelled": True}
            by_id = {str(field["fieldID"]): field for field in fields}
            actions = workflow.compare(item["headers"], item["rows"], mapping, by_id, key_col, records)
            counts = {op: sum(action["op"] == op for action in actions)
                      for op in ("新增", "更新", "不变", "错误")}
            preview = secrets.token_urlsafe(24)
            with self._lock:
                item.update(fields=deepcopy(fields), mapping=dict(mapping), key_col=key_col,
                            actions=actions, preview=preview)
            return {"preview": preview, "counts": counts, "total": len(actions),
                    "summary": "比对已完成，请逐行查看并勾选需要写入的差异行"}
        except workflow.MappingError:
            raise
        except ValueError:
            raise
        except Exception:
            raise ValueError("比对失败，请检查网络和登录状态后重试") from None
        finally:
            self.release(sid)

    def page(self, sid, page=1, mode="all", sort_column="", direction="", preview=""):
        if mode not in {"all", "differences", "新增", "更新", "不变", "错误"}:
            raise ValueError("筛选条件无效")
        if direction not in {"", "asc", "desc"}:
            raise ValueError("排序方向无效")
        with self._lock:
            item = self._get(sid)
            if preview and preview != item["preview"]:
                raise ValueError("比对结果已失效，请重新比对")
            headers = item["headers"]
            actions = {action["row_no"]: action for action in item["actions"]} if preview else {}
            result = []
            for number, values in enumerate(item["rows"], 1):
                action = actions.get(number, {})
                op = action.get("op", "未比对")
                if mode == "differences" and op not in workflow.WRITABLE_OPS:
                    continue
                if mode not in {"all", "differences"} and op != mode:
                    continue
                result.append({"row_no": number, "local": {key: _text(value) for key, value in values.items()},
                    "op": op, "key": str(action.get("key") or ""),
                    "message": str(action.get("message") or ""),
                    "changes": deepcopy(action.get("changes") or [])})
            if direction:
                if sort_column not in {"row_no", "op", "key", "message"} and sort_column not in headers:
                    raise ValueError("排序列不存在")
                result = sort_records(result, lambda row: sort_key(row[sort_column]
                    if sort_column in {"row_no", "op", "key", "message"}
                    else row["local"].get(sort_column)), ascending=direction == "asc")
            total = len(result)
            pages = max(1, (total + 199) // 200)
            page = max(1, min(int(page), pages))
            return {"rows": result[(page - 1) * 200:page * 200], "total": total,
                    "page": page, "pages": pages, "headers": list(headers)}

    def reserve(self, payload):
        if payload.get("confirmed") is not True:
            raise ValueError("请先确认选中行的写入范围")
        sid = str(payload.get("session") or "")
        selected = payload.get("selected")
        if (not isinstance(selected, list) or not selected or len(selected) > MAX_ROWS
                or any(type(number) is not int for number in selected)):
            raise ValueError("请勾选要写入的差异行")
        with self._lock:
            item = self._get(sid)
            if item["busy"]:
                raise ValueError("此表格已有任务进行中，请等待完成")
            if not item["preview"] or payload.get("preview") != item["preview"]:
                raise ValueError("比对结果已失效或已提交，请重新比对")
            allowed = {action["row_no"] for action in item["actions"]
                       if action["op"] in workflow.WRITABLE_OPS}
            if len(set(selected)) != len(selected) or not set(selected) <= allowed:
                raise ValueError("选中行包含重复或不可写记录，请重新比对")
            _target(item["tab"])
            # 提交即消费；即使排队失败也须重新比对，不自动重放写请求。
            item["preview"] = ""
            item["busy"] = True
            return sid, item, sorted(selected)

    def execute(self, reservation, log, is_cancelled):
        sid, item, selected = reservation
        try:
            target = _target(item["tab"])
            client = _client()
            file_id = client.convert_file_id(openapi.WRITABLE_DOC_KEY)
            client.validate_write_target(file_id, target["tab_id"])
            result = workflow.execute_selected(client, file_id, target["tab_id"], item["headers"],
                item["rows"], item["mapping"], {str(field["fieldID"]): field for field in item["fields"]},
                item["key_col"], item["actions"], selected, log, is_cancelled)
            result["summary"] = f"已写入选中行：新增 {result['added']} 条，更新 {result['updated']} 条，读回一致"
            return result
        except workflow.MappingError as exc:
            # 保留部分完成回执；未知结果必须人工核对，前端绝不自动重试。
            receipt = dict(getattr(exc, "receipt", {}) or {})
            receipt.update(summary=str(exc), error=str(exc), cancelled=bool(is_cancelled()))
            return receipt
        except ValueError:
            raise
        except Exception:
            raise ValueError("写入未能完成，请核对云端后重新比对；不会自动重试") from None
        finally:
            self.release(sid)
