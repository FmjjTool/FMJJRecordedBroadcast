"""腾讯文档官方 OpenAPI：读取字段/记录，按字段标题回写智能表格。

只做官方接口，不碰内部协同协议。凭证存在 Cookie 管理器的加密文件里
（key = tcdoc_openapi），不写 config.json，也不打印到日志。
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests

API_ROOT = "https://docs.qq.com/openapi"
BATCH = 50
CRED_KEY = "tcdoc_openapi"

FIELD_TYPES = {
    1: "文本", 2: "数字", 3: "复选框", 4: "日期", 5: "图片", 6: "附件",
    7: "电话", 8: "链接", 9: "单选", 10: "创建人", 11: "成员",
    12: "创建时间", 13: "最后编辑时间", 16: "邮箱", 17: "多选",
    18: "关联", 19: "公式", 21: "关联", 25: "自动编号",
}
SUPPORTED_TYPES = {1, 2, 3, 4, 8, 9, 17}


class ApiError(RuntimeError):
    pass


# ==================== 凭证（加密存 Cookie 管理器） ====================

def load_credentials() -> dict:
    data: dict = {}
    try:
        import auth
        raw = auth.load_cookies().get(CRED_KEY, "")
        if raw:
            data = json.loads(raw)
    except Exception:  # noqa: BLE001 - 凭据缺失/损坏按空处理
        data = {}
    for key, env in (("client_id", "TC_CLIENT_ID"),
                     ("access_token", "TC_ACCESS_TOKEN"),
                     ("open_id", "TC_OPEN_ID")):
        if not data.get(key):
            data[key] = os.environ.get(env, "")
    return data


def save_credentials(client_id: str, access_token: str, open_id: str) -> None:
    import auth
    auth.set_cookie(CRED_KEY, json.dumps(
        {"client_id": client_id.strip(), "access_token": access_token.strip(),
         "open_id": open_id.strip()}, ensure_ascii=False))


def clear_credentials() -> None:
    try:
        import auth
        auth.clear_cookie(CRED_KEY)
    except Exception:  # noqa: BLE001
        pass


# ==================== 官方 API ====================

def normalize_fields(raw_fields: list[dict]) -> list[dict]:
    fields = []
    for raw in raw_fields:
        field = dict(raw)
        field["_options"] = []
        for key, value in raw.items():
            if key.startswith("property") and isinstance(value, dict):
                options = value.get("options")
                if isinstance(options, list):
                    field["_options"] = [opt for opt in options if isinstance(opt, dict)]
                    break
        fields.append(field)
    return fields


class TdClient:
    def __init__(self, client_id: str, token: str, open_id: str):
        self.client_id = client_id.strip()
        self.token = token.strip()
        self.open_id = open_id.strip()
        self.sess = requests.Session()

    def _headers(self) -> dict:
        return {"Access-Token": self.token, "Client-Id": self.client_id,
                "Open-Id": self.open_id, "Content-Type": "application/json"}

    def _request(self, method: str, url: str, **kwargs) -> dict:
        kwargs.setdefault("timeout", 30)
        resp = self.sess.request(method, url, headers=self._headers(), **kwargs)
        try:
            body = resp.json()
        except ValueError as exc:
            raise ApiError(f"HTTP {resp.status_code}: {resp.text[:200]}") from exc
        if body.get("ret") != 0:
            raise ApiError(f"ret={body.get('ret')} {body.get('msg')}")
        return body.get("data") or {}

    def convert_file_id(self, encoded_id: str) -> str:
        data = self._request(
            "GET", f"{API_ROOT}/drive/v2/util/converter",
            params={"type": 2, "value": encoded_id})
        file_id = data.get("fileID")
        if not file_id:
            raise ApiError("fileID 转换失败")
        return file_id

    def list_sheets(self, file_id: str) -> list[dict]:
        data = self._request(
            "GET", f"{API_ROOT}/smartbook/v2/files/{quote(file_id, safe='')}/sheets")
        return data.get("getSheet") or []

    def _sheet_url(self, file_id: str, sheet_id: str) -> str:
        return (f"{API_ROOT}/smartbook/v2/files/{quote(file_id, safe='')}"
                f"/sheets/{quote(sheet_id, safe='')}")

    def _action(self, file_id: str, sheet_id: str, payload: dict) -> dict:
        return self._request("POST", self._sheet_url(file_id, sheet_id), json=payload)

    def get_fields(self, file_id: str, sheet_id: str) -> list[dict]:
        data = self._action(file_id, sheet_id, {"getFields": {}})
        return normalize_fields((data.get("getFields") or {}).get("fields") or [])

    def get_records_page(self, file_id: str, sheet_id: str,
                         offset: int = 0, limit: int = 100) -> dict:
        data = self._action(file_id, sheet_id,
                            {"getRecords": {"offset": offset, "limit": limit}})
        return data.get("getRecords") or {}

    def get_records(self, file_id: str, sheet_id: str, log=None) -> list[dict]:
        out: list[dict] = []
        offset = 0
        for _ in range(1000):
            block = self.get_records_page(file_id, sheet_id, offset, 100)
            records = block.get("records") or []
            out.extend(records)
            total = block.get("total")
            if log:
                log(f"读取腾讯记录 {len(out)}/{total if total is not None else '?'}")
            if not block.get("hasMore") or not records:
                break
            next_offset = block.get("next")
            offset = (int(next_offset) if isinstance(next_offset, int)
                      and next_offset > offset else offset + len(records))
        return out

    def add_records(self, file_id: str, sheet_id: str, records: list[dict]) -> dict:
        return self._action(file_id, sheet_id, {"addRecords": {"records": records}})

    def update_records(self, file_id: str, sheet_id: str, records: list[dict]) -> dict:
        return self._action(file_id, sheet_id, {"updateRecords": {"records": records}})

    def delete_records(self, file_id: str, sheet_id: str, record_ids: list[str]) -> dict:
        return self._action(file_id, sheet_id, {"deleteRecords": {"recordIDs": record_ids}})


# ==================== 值编码 / 展示 ====================

def text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(
            str(item.get("text") or item.get("link") or item.get("name") or "")
            for item in value if isinstance(item, dict))
    if isinstance(value, dict):
        return str(value.get("text") or value.get("link") or value.get("name") or "")
    return str(value)


def _to_ms(value: Any) -> str | None:
    if isinstance(value, datetime):
        return str(int(value.timestamp() * 1000))
    text = str(value).strip()
    if re.fullmatch(r"\d{12,}", text):
        return text
    if re.fullmatch(r"\d{10}", text):
        return str(int(text) * 1000)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return str(int(datetime.strptime(text, fmt).timestamp() * 1000))
        except ValueError:
            continue
    return None


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y", "是", "✓", "on")


def value_key(field: dict, value: Any) -> str:
    ftype = int(field.get("fieldType") or 0)
    if value is None:
        return ""
    if ftype in (1, 7, 16):
        return text_of(value).strip()
    if ftype == 2:
        raw = text_of(value).strip()
        try:
            return str(float(raw))
        except ValueError:
            return raw
    if ftype == 3:
        return "1" if (value is True or _truthy(value)) else "0"
    if ftype == 4:
        return str(value).strip()
    if ftype in (9, 17):
        if isinstance(value, list):
            ids = [str(x.get("id") or x.get("text") or "") for x in value
                   if isinstance(x, dict)]
            return "|".join(sorted(i for i in ids if i))
        return text_of(value).strip()
    if ftype in (5, 6, 8):
        return text_of(value).strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def display_value(field: dict, value: Any) -> str:
    ftype = int(field.get("fieldType") or 0)
    if value is None:
        return ""
    if ftype in (9, 17) and isinstance(value, list):
        return "、".join(text_of(x) for x in value)
    if ftype == 4 and str(value).strip().isdigit():
        try:
            return datetime.fromtimestamp(int(value) / 1000).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            return str(value)
    return text_of(value)


def encode_value(field: dict, value: Any) -> tuple[bool, Any]:
    ftype = int(field.get("fieldType") or 0)
    title = field.get("fieldTitle") or field.get("fieldID")
    if value is None or (isinstance(value, str) and not value.strip()):
        return True, None
    text = str(value).strip() if not isinstance(value, (list, dict)) else text_of(value).strip()

    if ftype in (1, 7, 16):
        return True, [{"type": "text", "text": str(value)}]
    if ftype == 2:
        try:
            return True, float(text) if "." in text else int(text)
        except ValueError:
            return False, f"「{title}」需要数字"
    if ftype == 3:
        return True, _truthy(value)
    if ftype == 4:
        ms = _to_ms(value)
        return (True, ms) if ms else (False, f"「{title}」日期格式无法识别")
    if ftype == 8:
        if not re.match(r"^https?://", text):
            return False, f"「{title}」需要 http(s) 链接"
        return True, [{"link": text, "text": text, "type": "url"}]
    if ftype in (9, 17):
        options = field.get("_options") or []
        if not options:
            return False, f"「{title}」没有可选选项"

        def find_option(raw: str):
            return next((opt for opt in options
                         if str(opt.get("text") or "").strip() == raw
                         or str(opt.get("id") or "") == raw), None)

        if isinstance(value, (list, tuple)):
            raw_values = [str(v).strip() for v in value if str(v).strip()]
        else:
            raw_values = [text]
        matched, missing = [], []
        for raw in raw_values:
            hit = find_option(raw)
            if hit is not None:
                matched.append(hit)
                continue
            parts = [part.strip() for part in re.split(r"[,，、;；|]", raw)
                     if part.strip()]
            if len(parts) <= 1:
                missing.append(raw)
                continue
            for part in parts:
                part_hit = find_option(part)
                if part_hit is None:
                    missing.append(part)
                else:
                    matched.append(part_hit)
        if missing:
            names = "、".join(str(opt.get("text") or "") for opt in options[:6])
            return False, f"「{title}」找不到选项：{'、'.join(missing)}（可选：{names}）"
        if ftype == 9:
            matched = matched[:1]
        encoded = []
        for opt in matched:
            item = {"id": opt.get("id")}
            if opt.get("style") is not None:
                item["style"] = opt["style"]
            if opt.get("text") is not None:
                item["text"] = opt["text"]
            encoded.append(item)
        return True, encoded
    return False, f"「{title}」类型 {FIELD_TYPES.get(ftype, ftype)} 暂不支持写入"


# ==================== 本地文件 ====================

def _dedupe_headers(headers: list[str]) -> list[str]:
    used: dict[str, int] = {}
    out = []
    for header in headers:
        name = str(header or "").strip() or "列"
        used[name] = used.get(name, 0) + 1
        out.append(name if used[name] == 1 else f"{name}_{used[name]}")
    return out


def load_table(path: str) -> tuple[list[str], list[dict]]:
    suffix = Path(path).suffix.lower()
    if suffix in (".csv", ".tsv", ".txt"):
        raw = Path(path).read_bytes()
        for encoding in ("utf-8-sig", "gbk", "utf-8"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("CSV 编码无法识别（试过 UTF-8 / GBK）")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
        except csv.Error:
            dialect = csv.excel()
            if suffix == ".tsv":
                dialect.delimiter = "\t"
        rows = list(csv.reader(io.StringIO(text), dialect))
        if not rows:
            raise ValueError("文件没有内容")
        headers = _dedupe_headers(rows[0])
        out = []
        for row in rows[1:]:
            if not any(str(cell).strip() for cell in row):
                continue
            out.append({headers[i]: (row[i] if i < len(row) else "")
                        for i in range(len(headers))})
        return headers, out

    if suffix in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not rows:
            raise ValueError("Excel 没有内容")
        headers = _dedupe_headers([str(cell or "") for cell in rows[0]])
        out = []
        for row in rows[1:]:
            if not any(cell is not None and str(cell).strip() for cell in row):
                continue
            out.append({headers[i]: (row[i] if i < len(row) and row[i] is not None else "")
                        for i in range(len(headers))})
        return headers, out

    if suffix == ".json":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("data") or data.get("rows") or data.get("records") or []
        if not isinstance(data, list) or not data:
            raise ValueError("JSON 需要是非空对象数组")
        if not all(isinstance(item, dict) for item in data):
            raise ValueError("JSON 数组里每一项都必须是对象")
        headers: list[str] = []
        for item in data:
            for key in item:
                if key not in headers:
                    headers.append(str(key))
        return headers, [{k: item.get(k, "") for k in headers} for item in data]

    raise ValueError("只支持 CSV / TSV / Excel / JSON")


# ==================== 差异计算 / 写入 ====================

def build_actions(headers: list[str], rows: list[dict], mapping: dict[str, str],
                  field_by_id: dict[str, dict], key_col: str,
                  records: list[dict]) -> list[dict]:
    key_field = field_by_id.get(mapping.get(key_col, ""))
    if key_field is None:
        raise ValueError("请选择已映射的主键列")
    field_by_title = {f.get("fieldTitle"): f for f in field_by_id.values()}
    index: dict[str, dict] = {}
    for record in records:
        key = value_key(key_field, (record.get("values") or {}).get(key_field["fieldTitle"]))
        if key and key not in index:
            index[key] = record

    actions: list[dict] = []
    for row_no, row in enumerate(rows, start=1):
        key = str(row.get(key_col, "") or "").strip()
        base = {"row_no": row_no, "key": key}
        if not key:
            actions.append({**base, "op": "错误", "message": "主键为空"})
            continue
        values: dict[str, Any] = {}
        errors: list[str] = []
        for col, field_id in mapping.items():
            field = field_by_id.get(field_id)
            if field is None:
                continue
            ok, encoded = encode_value(field, row.get(col))
            if not ok:
                errors.append(str(encoded))
            elif encoded is not None:
                values[field["fieldTitle"]] = encoded
        if errors:
            actions.append({**base, "op": "错误", "message": "；".join(errors)})
            continue
        record = index.get(key)
        if record is None:
            actions.append({**base, "op": "新增", "values": values,
                            "message": "腾讯表格中无此主键"})
            continue
        existing = record.get("values") or {}
        changed: dict[str, Any] = {}
        for field_title, encoded in values.items():
            field = field_by_title.get(field_title)
            if field is None:
                continue
            if value_key(field, existing.get(field_title)) != value_key(field, encoded):
                changed[field_title] = encoded
        if changed:
            actions.append({**base, "op": "更新", "record_id": record.get("recordID"),
                            "values": changed,
                            "message": "变更：" + "、".join(changed.keys())})
        else:
            actions.append({**base, "op": "不变", "message": "值一致"})
    return actions


def execute_actions(client: TdClient, file_id: str, sheet_id: str,
                    actions: list[dict], log: Callable[[str], None]) -> dict:
    adds = [{"values": action["values"]} for action in actions
            if action["op"] == "新增"]
    updates = [{"recordID": action["record_id"], "values": action["values"]}
               for action in actions if action["op"] == "更新"]
    done_add = done_update = 0
    for start in range(0, len(adds), BATCH):
        chunk = adds[start:start + BATCH]
        client.add_records(file_id, sheet_id, chunk)
        done_add += len(chunk)
        log(f"新增 {done_add}/{len(adds)}")
        time.sleep(0.2)
    for start in range(0, len(updates), BATCH):
        chunk = updates[start:start + BATCH]
        client.update_records(file_id, sheet_id, chunk)
        done_update += len(chunk)
        log(f"更新 {done_update}/{len(updates)}")
        time.sleep(0.2)
    return {"added": done_add, "updated": done_update}
