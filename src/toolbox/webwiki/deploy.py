"""本地资料站 · 依赖安装、腾讯文档同步与前端构建。"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import config, imgcache, localimages, runner


class DeployError(RuntimeError):
    """部署步骤失败。"""


def ensure_python_env(root: Path | None = None, log=print) -> Path:
    """创建资料站专用虚拟环境并安装 webwiki-api 依赖，返回 venv python。"""
    root = Path(root or config.root_dir())
    python = config.venv_python(root)
    api = config.api_dir(root)
    if not (api / "requirements.txt").is_file():
        raise DeployError("webwiki-api 代码不存在，请先拉取代码")
    if not python.is_file():
        base = config.find_python()
        log(f"创建虚拟环境：{config.venv_dir(root)}")
        runner.run_stream([base, "-m", "venv", str(config.venv_dir(root))],
                          root, log, title="创建 Python 虚拟环境")
        python = config.venv_python(root)
        if not python.is_file():
            raise DeployError("虚拟环境创建失败（未找到 Scripts/python.exe）")
    log("安装 / 校验 webwiki-api 依赖（fastapi / uvicorn / httpx）")
    runner.run_stream(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check",
         "-r", str(api / "requirements.txt")],
        api, log, title="安装 Python 依赖")
    return python


def ensure_node_deps(root: Path | None = None, log=print, force: bool = False) -> Path:
    """安装前端依赖（node_modules 已存在时默认跳过）。"""
    web = config.web_dir(root)
    if not (web / "package.json").is_file():
        raise DeployError("webwiki 代码不存在，请先拉取代码")
    _node, npm = config.find_node()
    if (web / "node_modules").is_dir() and not force:
        log("前端依赖已存在，跳过 npm 安装")
        return web
    if force and (web / "node_modules").is_dir():
        # 显式「安装依赖」只做增量更新，不用 npm ci 删掉现有 node_modules
        cmd = [npm, "install", "--no-audit", "--no-fund"]
        title = "更新前端依赖（npm install）"
    elif (web / "package-lock.json").is_file():
        cmd = [npm, "ci", "--no-audit", "--no-fund"]
        title = "安装前端依赖（npm ci）"
    else:
        cmd = [npm, "install", "--no-audit", "--no-fund"]
        title = "安装前端依赖（npm install）"
    runner.run_stream(cmd, web, log, title=title)
    return web


def build_frontend(root: Path | None = None, log=print) -> Path:
    """npm run build，产物必须在 webwiki/dist/index.html。"""
    web = config.web_dir(root)
    if not (web / "node_modules").is_dir():
        raise DeployError("前端依赖未安装，请先点「安装依赖」")
    _node, npm = config.find_node()
    runner.run_stream([npm, "run", "build"], web, log, title="构建前端")
    dist = config.dist_dir(root)
    if not (dist / "index.html").is_file():
        raise DeployError("前端构建完成但未找到 dist/index.html")
    return dist


def sync_docs(root: Path | None = None, log=print) -> str:
    """从「腾讯文档监控」已同步的本地 tables.db 重建站点库（不联网）。

    ETL 会全量重建 data/webwiki.db，所以每次都直接读本地最新表格即可，
    不需要版本号判断、强制全量或重新请求腾讯文档。
    """
    root = Path(root or config.root_dir())
    python = config.venv_python(root)
    api = config.api_dir(root)
    if not python.is_file():
        raise DeployError("Python 依赖未安装，请先点「安装依赖」")
    if not (api / "app" / "etl.py").is_file():
        raise DeployError("webwiki-api 代码不存在，请先拉取代码")

    from toolbox.tcdoc import config as tcdoc_config
    source = Path(tcdoc_config.tables_db_path())
    if not source.is_file():
        raise DeployError(
            "腾讯文档还没有同步过表格：请先到「腾讯文档 → 表格同步」同步一次")
    try:
        conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=3)
        try:
            row_count = conn.execute("SELECT COUNT(*) FROM rows").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise DeployError(f"读取腾讯文档本地库失败：{exc}") from exc
    if not row_count:
        raise DeployError(
            "腾讯文档的表格库还是空的：请先到「腾讯文档 → 表格同步」同步一次")

    log(f"数据来源：{source}（{row_count} 行，直接读本地，不再请求腾讯文档）")
    code = (
        "from pathlib import Path\n"
        "from app import etl\n"
        f"etl.SRC_DB = Path({str(source)!r})\n"
        "raise SystemExit(etl.main())\n"
    )
    runner.run_stream([str(python), "-c", code], api, log,
                      title="从腾讯文档本地库重建站点数据")
    status = data_status(root)
    if status.get("exists"):
        return (f"已重建：歌曲 {status.get('songs', 0)} / 活动 {status.get('events', 0)} / "
                f"周边 {status.get('goods', 0)} / 动态 {status.get('posts', 0)}")
    return "重建完成（未找到服务库）"


def data_status(root: Path | None = None) -> dict:
    """读取 data/webwiki.db 的更新时间与主要表行数。"""
    db = Path(root or config.root_dir()) / "webwiki-api" / "data" / "webwiki.db"
    if not db.is_file():
        return {"exists": False}
    result = {
        "exists": True,
        "path": db,
        "mtime": datetime.fromtimestamp(db.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        "size_mb": round(db.stat().st_size / 1024 / 1024, 1),
    }
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        for table, key in (("songs", "songs"), ("events", "events"),
                           ("goods", "goods"), ("posts", "posts"),
                           ("live_records", "live"), ("changelogs", "changelogs")):
            try:
                result[key] = conn.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                result[key] = 0
    except sqlite3.Error:
        pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
    return result


def warm_covers(root: Path | None = None, log=print, limit: int = 12) -> str:
    """预热歌曲列表首屏封面缩略图。

    腾讯图床的缩略图由源站 COS 按需生成，第一次请求每张约 3~5 秒；
    启动前并发预热前 12 张，浏览器打开时就不用对着空白封面等。
    失败只记日志，不影响启动。
    """
    root = Path(root or config.root_dir())
    db = root / "webwiki-api" / "data" / "webwiki.db"
    if not db.is_file():
        return ""
    rows = []
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=3)
        rows = conn.execute(
            "SELECT cover_url FROM songs ORDER BY release_date DESC, id DESC LIMIT ?",
            (int(limit),)).fetchall()
    except sqlite3.Error as exc:
        log(f"封面预热跳过：读取歌曲库失败（{exc}）")
        return ""
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
    items: list[tuple[str, str]] = []
    for (raw,) in rows:
        first = str(raw or "").strip().split(" ")[0]
        if not first:
            continue
        host = (urlparse(first).hostname or "").lower()
        base = first.partition("?")[0]
        if host.endswith("docs.qq.com"):
            items.append((first, f"{base}?imageMogr2/thumbnail/320x320/format/webp"))
        elif host.endswith("hdslb.com"):
            items.append((first, first if "@" in first else first + "@320w.webp"))
        else:
            items.append((first, first))
    if not items:
        return ""

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/152.0.0.0 Safari/537.36"),
        "Referer": "https://docs.qq.com/",
        "Accept": "image/webp,image/*,*/*;q=0.8",
    }

    local_ready = sum(1 for original, _url in items if localimages.find(original))

    def fetch(item: tuple[str, str]) -> bool:
        original, url = item
        if localimages.find(original):
            # 腾讯文档监控已经下过原图：直接生成本地缩略图，完全不走网络
            return bool(localimages.serve(root, original, 320))
        for trust_env in (False, True):
            try:
                with httpx.Client(trust_env=trust_env, timeout=20,
                                  follow_redirects=True, headers=headers) as client:
                    resp = client.get(url)
                    if resp.status_code == 200 and resp.content:
                        imgcache.save(root, original, 320, resp.content)
                        return True
            except (httpx.HTTPError, OSError):
                continue
        return False

    with ThreadPoolExecutor(max_workers=min(8, len(items))) as pool:
        results = list(pool.map(fetch, items))
    ok = sum(1 for item in results if item)
    extra = f"，其中本地图片 {local_ready} 张" if local_ready else ""
    log(f"预热首屏封面 {ok}/{len(items)} 张{extra}（首次打开更快）")
    return f"{ok}/{len(items)}"


def deploy_all(root: Path | None = None, token: str = "", log=print,
               progress=None, cancel=None) -> Path:
    """一键部署：更新小程序主源、Wiki API 与网页前端 → 装依赖 → 重建并构建。

    progress(step, total, label) 用于界面进度；cancel() 返回真时在步骤之间停止。
    """
    from . import gitops

    root = Path(root or config.root_dir())
    steps = [
        ("更新全部仓库", lambda: gitops.pull_repositories("all", token, log, root)),
        ("安装依赖", lambda: (ensure_python_env(root, log),
                          ensure_node_deps(root, log))),
        ("重建站点数据", lambda: sync_docs(root, log)),
        ("构建前端", lambda: build_frontend(root, log)),
    ]
    total = len(steps)
    for index, (label, run) in enumerate(steps, start=1):
        if cancel is not None and cancel():
            raise RuntimeError(f"已停止（{label}前）")
        if progress is not None:
            progress(index, total, label)
        log(f"[{index}/{total}] {label}")
        run()
    return ensure_python_env(root, log)
