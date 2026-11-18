"""本地资料站 · 站点后台访问统计（mandisa.wiki 内部 analytics API）。

数据源：``POST https://mandisa.wiki/api/internal/analytics``
- 正文只传当前后台密码（+ 可选 date / scope），不需要用户名、Cookie 或 Origin；
  网页里改密后这里要跟着改，旧密码立即失效。
- 与 /admin 共用「15 分钟内最多 5 次失败」的密码配额，密码错不要反复重试；
  正常查询另有限制（全局每分钟 60 次），超限返回 429 与 Retry-After。

本地落地（与站点代码目录解耦：换「资料站目录」不会丢历史）：
- ``download/站点监控数据/访问统计.db``   按 (日期, 口径) 去重保存采集结果
- ``download/站点监控数据/采集日志.log``  定时任务的无界面运行日志
- ``config/webwiki_analytics.json``       后台密码（Fernet + 本机标识派生密钥）

密码只进上面那个独立小文件，不写导出目录、不进日志、不回显；Cookie 管理器里的
真实 Cookie 与本模块无关，不做任何读写。
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from toolbox.config_manager import get_config_dir, get_export_subdir, load_convert_config

API_URL = "https://mandisa.wiki/api/internal/analytics"
ADMIN_URL = "https://mandisa.wiki/admin"

# 北京时间：站点按北京时间切日，本地也用它算「今天」
BEIJING = timezone(timedelta(hours=8))

# 统计口径：human 最严（默认），qualified 加行为合格，all 全部分类
SCOPES = (
    ("human", "严格验证（默认）"),
    ("qualified", "严格验证 + 行为合格"),
    ("all", "全部分类"),
)
SCOPE_LABELS = dict(SCOPES)

_TOTAL_KEYS = ("visitors", "sessions", "pageviews", "online")

_HTTP_HINTS = {
    400: "日期或统计口径不合法",
    401: "后台密码错误或已改密（连续失败会锁定 15 分钟）",
    403: "请求来源不在允许列表（走服务端 JSON POST 不需要 Origin）",
    405: "该接口只接受 POST",
    413: "请求正文超过 4 KiB",
    415: "该接口只接受 JSON 正文",
    503: "站点后台未配置 / 已关闭，或统计存储暂不可用",
}


class AnalyticsError(RuntimeError):
    """接口调用失败：文案可直接给界面，不含密码与请求正文。"""


# ==================== 基础信息 ====================

def today() -> str:
    """站点口径的今天（北京时间 YYYY-MM-DD）。"""
    return datetime.now(BEIJING).strftime("%Y-%m-%d")


def api_url() -> str:
    url = str(load_convert_config().get("webwiki_analytics_api") or "").strip()
    return url or API_URL


def scope_label(scope: str) -> str:
    return SCOPE_LABELS.get(str(scope or ""), str(scope or "-"))


# ==================== 密码存取（本机加密） ====================

def secret_path() -> Path:
    return get_config_dir() / "webwiki_analytics.json"


def _machine_seed() -> str:
    """本机标识（Windows MachineGuid）；取不到时退回网卡节点号。"""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography") as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            if value:
                return str(value)
    except Exception:  # noqa: BLE001 - 非 Windows / 无权限时退回节点号
        pass
    try:
        import uuid

        node = uuid.getnode()
        if node:
            return f"node-{node}"
    except Exception:  # noqa: BLE001
        pass
    return "local-default"


def _fernet_key() -> bytes:
    """独立盐值派生密钥：与 Cookie 加密互不影响，改这里不会动已有登录态。"""
    seed = f"fmjj-webwiki-analytics-v1:{_machine_seed()}".encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(seed).digest())


def _encrypt(text: str) -> str:
    if not text:
        return ""
    try:
        from cryptography.fernet import Fernet

        return "enc:" + Fernet(_fernet_key()).encrypt(text.encode("utf-8")).decode("ascii")
    except Exception:  # noqa: BLE001 - 加密库缺失时降级明文，至少有提示
        return text


def _decrypt(text: str) -> str:
    if not text:
        return ""
    if not text.startswith("enc:"):
        return text
    try:
        from cryptography.fernet import Fernet

        return Fernet(_fernet_key()).decrypt(text[4:].encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001 - 换机 / 重装系统后密钥不匹配
        return ""


def has_password() -> bool:
    return bool(load_password())


def load_password() -> str:
    try:
        data = json.loads(secret_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    return _decrypt(str(data.get("password") or ""))


def save_password(password: str) -> bool:
    """保存后台密码；传空串等于清除。返回是否已加密（本机缺 cryptography 时为 False）。"""
    password = str(password or "").strip()
    path = secret_path()
    if not password:
        try:
            path.unlink()
        except OSError:
            pass
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = _encrypt(password)
    payload = {
        "password": encrypted,
        "encrypted": encrypted.startswith("enc:"),
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return bool(payload["encrypted"])


# ==================== 本地历史库 ====================

DB_NAME = "访问统计.db"

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS snapshots (
        stat_date TEXT NOT NULL,
        scope TEXT NOT NULL,
        generated_at TEXT NOT NULL DEFAULT '',
        visitors INTEGER NOT NULL DEFAULT 0,
        sessions INTEGER NOT NULL DEFAULT 0,
        pageviews INTEGER NOT NULL DEFAULT 0,
        online INTEGER NOT NULL DEFAULT 0,
        payload TEXT NOT NULL DEFAULT '{}',
        fetched_at TEXT NOT NULL,
        PRIMARY KEY (stat_date, scope)
    )""",
    """CREATE TABLE IF NOT EXISTS fetch_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        stat_date TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT '',
        ok INTEGER NOT NULL DEFAULT 1,
        message TEXT NOT NULL DEFAULT ''
    )""",
)

_READY: set[str] = set()


def data_dir() -> Path:
    return get_export_subdir("webwiki_monitor")


def db_path() -> Path:
    return data_dir() / DB_NAME


def log_path() -> Path:
    return data_dir() / "采集日志.log"


def _connect() -> sqlite3.Connection:
    path = db_path()
    conn = sqlite3.connect(str(path), timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=8000")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    key = str(path)
    if key not in _READY:
        for statement in _SCHEMA:
            conn.execute(statement)
        conn.commit()
        _READY.add(key)
    return conn


def _as_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize(data: dict, scope: str) -> dict:
    """把接口的 data 拍平成固定结构：日期 / 口径 / 生成时间 / 4 个总量。"""
    totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
    clean = dict(data)
    clean["date"] = str(data.get("date") or today())
    clean["scope"] = str(data.get("scope") or scope)
    clean["generated_at"] = str(data.get("generated_at") or "")
    clean["totals"] = {key: _as_int(totals.get(key)) for key in _TOTAL_KEYS}
    return clean


def save_snapshot(data: dict, scope: str) -> dict:
    """按 (日期, 口径) 落库；同一天重复采集直接覆盖为最新值。

    返回结构与 ``load_history`` 的行保持一致（总量是顶层字段），页面同一套取法。
    """
    row = normalize(data, scope)
    totals = row["totals"]
    fetched = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO snapshots
                   (stat_date, scope, generated_at, visitors, sessions, pageviews,
                    online, payload, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(stat_date, scope) DO UPDATE SET
                   generated_at=excluded.generated_at,
                   visitors=excluded.visitors,
                   sessions=excluded.sessions,
                   pageviews=excluded.pageviews,
                   online=excluded.online,
                   payload=excluded.payload,
                   fetched_at=excluded.fetched_at""",
            (row["date"], row["scope"], row["generated_at"], totals["visitors"],
             totals["sessions"], totals["pageviews"], totals["online"],
             json.dumps(row, ensure_ascii=False), fetched))
        conn.commit()
    finally:
        conn.close()
    return {
        "stat_date": row["date"],
        "date": row["date"],
        "scope": row["scope"],
        "generated_at": row["generated_at"],
        "visitors": totals["visitors"],
        "sessions": totals["sessions"],
        "pageviews": totals["pageviews"],
        "online": totals["online"],
        "fetched_at": fetched,
    }


def _db_path_ro() -> Path:
    """只读定位统计库，不创建目录或空库。"""
    from toolbox.config_manager import get_export_dir

    return Path(get_export_dir()) / "站点监控数据" / DB_NAME


def load_latest_ro(scope: str | None = None) -> dict:
    """只读最近一次快照（优先当前口径）；不建库、不联网、不读取密码。"""
    from toolbox.webwiki import config as wiki_cfg

    scope = scope or wiki_cfg.analytics_scope()
    try:
        path = _db_path_ro()
    except Exception:  # noqa: BLE001 - 路径不可用按无数据处理
        return {}
    if not path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return {}
    try:
        row = conn.execute(
            "SELECT stat_date, scope, generated_at, visitors, sessions, pageviews,"
            " online, fetched_at FROM snapshots WHERE scope=?"
            " ORDER BY stat_date DESC, fetched_at DESC LIMIT 1", (scope,)).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT stat_date, scope, generated_at, visitors, sessions, pageviews,"
                " online, fetched_at FROM snapshots"
                " ORDER BY stat_date DESC, fetched_at DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return dict(row) if row else {}


def load_history_ro(limit: int = 14, scope: str | None = None) -> list[dict]:
    """只读历史快照；不建库、不联网、不读取密码。"""
    try:
        path = _db_path_ro()
    except Exception:  # noqa: BLE001 - 路径不可用按无历史处理
        return []
    if not path.is_file():
        return []
    sql = ("SELECT stat_date, scope, generated_at, visitors, sessions, pageviews,"
           " online, fetched_at FROM snapshots")
    params: list = []
    if scope:
        sql += " WHERE scope = ?"
        params.append(scope)
    sql += " ORDER BY stat_date DESC, fetched_at DESC LIMIT ?"
    params.append(max(1, int(limit)))
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def has_today_ro(scope: str | None = None) -> bool:
    """今天（当前口径）是否已有快照；供多时间点补采跳过使用。"""
    from toolbox.webwiki import config as wiki_cfg

    scope = scope or wiki_cfg.analytics_scope()
    rows = load_history_ro(limit=1, scope=scope)
    return bool(rows and str(rows[0].get("stat_date") or "") == today())


def load_fetch_log_ro(limit: int = 50) -> list[dict]:
    """只读采集历史（每次尝试的成功/失败记录）；不建库、不联网。"""
    try:
        path = _db_path_ro()
    except Exception:  # noqa: BLE001 - 路径不可用按无记录处理
        return []
    if not path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT at, stat_date, scope, ok, message FROM fetch_log"
            " ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [dict(row) for row in rows]


def load_history(limit: int = 14, scope: str | None = None) -> list[dict]:
    sql = ("SELECT stat_date, scope, generated_at, visitors, sessions, pageviews,"
           " online, fetched_at FROM snapshots")
    params: list = []
    if scope:
        sql += " WHERE scope = ?"
        params.append(scope)
    sql += " ORDER BY stat_date DESC, fetched_at DESC LIMIT ?"
    params.append(max(1, int(limit)))
    conn = _connect()
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def deltas(row: dict, rows: list[dict]) -> tuple[int | None, int | None]:
    """用本地历史算「前一日 / 7 日前」的访客差；本地没那天的数据返回 None。"""
    scope = str(row.get("scope") or "")
    try:
        base_day = datetime.strptime(
            str(row.get("stat_date") or row.get("date") or ""), "%Y-%m-%d")
    except ValueError:
        return None, None
    index = {r.get("stat_date"): r for r in rows if str(r.get("scope") or "") == scope}
    base = _as_int(row.get("visitors"))

    def diff(days: int) -> int | None:
        day = (base_day - timedelta(days=days)).strftime("%Y-%m-%d")
        other = index.get(day)
        return None if other is None else base - _as_int(other.get("visitors"))

    return diff(1), diff(7)


def log_run(ok: bool, message: str, stat_date: str = "", scope: str = "") -> None:
    """记录每次采集结果，只保留最近 300 条。"""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO fetch_log (at, stat_date, scope, ok, message) VALUES (?, ?, ?, ?, ?)",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(stat_date or ""),
             str(scope or ""), 1 if ok else 0, str(message or "")[:500]))
        conn.execute(
            "DELETE FROM fetch_log WHERE id <= (SELECT MAX(id) FROM fetch_log) - 300")
        conn.commit()
    finally:
        conn.close()


# ==================== 接口调用 ====================

def _http_message(error: urllib.error.HTTPError) -> str:
    code = int(getattr(error, "code", 0) or 0)
    hint = _HTTP_HINTS.get(code, "接口返回异常")
    if code == 429:
        retry = ""
        try:
            retry = str(error.headers.get("Retry-After") or "").strip()
        except Exception:  # noqa: BLE001
            retry = ""
        hint = f"查询过快或密码尝试过多，请 {retry or '稍后'} 秒后重试" if retry \
            else "查询过快或密码尝试过多，请稍后重试"
    return f"HTTP {code}：{hint}"


def _post(body: dict, timeout: float, direct: bool) -> dict:
    """POST JSON；direct=True 时绕过系统代理（站点在国内，直连更快）。"""
    raw_body = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        api_url(),
        data=raw_body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "FengMingToolbox-Analytics/1.0",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}) if direct else urllib.request.ProxyHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise AnalyticsError(_http_message(error)) from error
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AnalyticsError("接口返回的不是 JSON（可能被 CDN 缓存或拦截）") from error


def _network_hint(error: BaseException) -> str:
    reason = getattr(error, "reason", None) or error
    text = str(reason).strip() or error.__class__.__name__
    return text[:160]


def fetch(password: str, date: str | None = None, scope: str = "human",
          timeout: float = 20.0) -> dict:
    """调用内部统计接口，返回 data（已拍平）。失败抛 AnalyticsError。"""
    password = str(password or "")
    if not password:
        raise AnalyticsError("缺少后台密码")
    scope = scope if scope in SCOPE_LABELS else "human"
    body: dict = {"password": password, "scope": scope}
    if date:
        body["date"] = str(date)

    try:
        result = _post(body, timeout, direct=True)
    except AnalyticsError:
        raise
    except (urllib.error.URLError, OSError):
        # 直连不通（公司网络 / 需要代理）再走一次系统代理
        try:
            result = _post(body, timeout, direct=False)
        except AnalyticsError:
            raise
        except (urllib.error.URLError, OSError) as error:
            raise AnalyticsError(f"网络请求失败：{_network_hint(error)}") from error

    if not isinstance(result, dict):
        raise AnalyticsError("接口返回格式异常")
    if _as_int(result.get("code")) != 0:
        raise AnalyticsError(str(result.get("msg") or "接口返回失败"))
    data = result.get("data")
    if not isinstance(data, dict):
        raise AnalyticsError("接口没有返回统计数据")
    return normalize(data, scope)


def collect(scope: str | None = None, date: str | None = None, log=None) -> dict:
    """采集一次并落库，返回最新一行（含 totals / fetched_at）。"""
    say = log or (lambda _message: None)
    cfg = load_convert_config()
    scope = str(scope or cfg.get("webwiki_analytics_scope") or "human")
    if scope not in SCOPE_LABELS:
        scope = "human"
    password = load_password()
    if not password:
        raise AnalyticsError("还没有保存后台密码：先填密码点「保存密码」")

    target = str(date or today())
    say(f"请求站点统计接口（{scope_label(scope)}，{target}）")
    try:
        # 今天是默认值：不带 date，让服务端按北京时间自行判定，避免跨零点偏差
        data = fetch(password, None if target == today() else target, scope)
    except AnalyticsError as error:
        log_run(False, str(error), target, scope)
        raise

    row = save_snapshot(data, scope)
    message = (f"{row['date']} 访客 {row['visitors']} · 打开 {row['sessions']}"
               f" · 浏览量 {row['pageviews']} · 在线 {row['online']}")
    say(message)
    log_run(True, message, row["date"], scope)
    return row


def append_job_log(text: str) -> None:
    """定时任务的运行日志（无界面，只追加）。"""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with log_path().open("a", encoding="utf-8") as handle:
            handle.write(f"[{stamp}] {text}\n")
    except OSError:
        pass
