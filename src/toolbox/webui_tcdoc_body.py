"""腾讯文档正文 Web 用例：共享本地存档、手动抓取与受控图片读取。

只读腾讯文档；正文与图片的唯一写入方仍是 collector/archive/body_images。
本模块不持有桌面状态，不接受任意 URL/本地路径，也不返回存储路径。
导入无文件或凭据操作；验证须使用独立程序副本与虚构夹具。
"""

from __future__ import annotations

from io import BytesIO

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 80_000_000
_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif",
          "WEBP": "image/webp", "BMP": "image/bmp", "AVIF": "image/avif"}


def _document(doc_key: str) -> dict:
    from toolbox.tcdoc import config
    key = str(doc_key or "").strip()
    if not key or len(key) > 200:
        raise ValueError("请选择文档")
    doc = next((dict(item) for item in config.load_docs()
                if item.get("key") == key), None)
    if doc is None:
        raise ValueError("文档不在当前清单中，请刷新后重试")
    if doc.get("kind") in ("sheet", "smartsheet"):
        raise ValueError("该文档是表格，请使用表格同步")
    return doc


def validate_document(doc_key: str) -> None:
    """后台排队前校验目标，非法文档不启动采集任务。"""
    _document(doc_key)


def cached(doc_key: str) -> dict:
    """返回可安全展示的本地正文；不会因读取而抓取或下载。"""
    from toolbox.tcdoc import archive, config
    from toolbox.tcdoc.preview_content import image_counts

    doc = _document(doc_key)
    pages = archive.pages_of(doc["key"]) if config.pages_db_path().is_file() else []
    result = []
    for page in pages:
        images = [{"key": str(item.get("key") or ""),
                   "name": str(item.get("name") or "图片"),
                   "status": str(item.get("status") or "failed"),
                   "error": str(item.get("error") or ""),
                   "width": int(item.get("width") or 0),
                   "height": int(item.get("height") or 0)}
                  for item in (page.get("images") or [])]
        result.append({"id": str(page.get("id") or ""),
                       "title": str(page.get("title") or ""),
                       "level": int(page.get("level") or 0),
                       "text": str(page.get("text") or ""),
                       "fetched_at": str(page.get("fetched_at") or ""),
                       "images_captured": bool(page.get("images_captured")),
                       "images": images})
    return {"doc_key": doc["key"], "name": doc.get("name") or "文档",
            "pages": result, "images": image_counts(pages),
            "last": max((item["fetched_at"] for item in result), default="")}


def fetch(doc_key: str, on_status=None, is_cancelled=None) -> dict:
    """手动抓取复用桌面链路，只写本地正文/图片存档。"""
    from toolbox.tcdoc import body_images, collector

    doc = _document(doc_key)
    try:
        result = collector.preview(doc, on_status=on_status,
                                   is_cancelled=is_cancelled)
    except body_images.BodyImagesCancelled:
        raise
    except collector.LoginRequired as exc:
        raise ValueError("抓取正文失败：需要登录，请稍后重试") from exc
    except Exception as exc:
        raise ValueError("抓取正文失败：网络或文档读取未完成，请重试") from exc
    if result.get("save_error"):
        raise ValueError("正文已读取，但本地存档失败，请检查可用空间后重试")
    saved = result.get("save") or {}
    outcome = cached(doc["key"])
    outcome["capture"] = {
        "pages": int(saved.get("pages") or 0),
        "skipped": int(saved.get("skipped") or 0),
        "complete": bool(saved.get("complete", True)),
        "snapshot_failed": bool(saved.get("file_error")),
    }
    return outcome


def read_image(doc_key: str, page_id: str, image_key: str) -> tuple[bytes, str]:
    """只读取该文档/页面已经登记的图片，不接受客户端文件路径。"""
    from PIL import Image
    from toolbox.tcdoc import archive, body_images, config

    doc = _document(doc_key)
    page_id, image_key = str(page_id or ""), str(image_key or "")
    if not page_id or not image_key or len(page_id) > 200 or len(image_key) > 200:
        raise ValueError("图片标识无效")
    if not config.pages_db_path().is_file():
        raise ValueError("图片尚未存档，请重新抓取正文")
    page = next((item for item in archive.pages_of(doc["key"])
                 if str(item.get("id")) == page_id), None)
    entry = next((item for item in (page or {}).get("images", [])
                  if str(item.get("key")) == image_key
                  and item.get("status") == "downloaded"), None)
    if entry is None:
        raise ValueError("图片尚未下载，请重新抓取正文")
    try:
        target = body_images.safe_image_path(str(entry.get("path") or ""))
        if not target.is_file() or not 0 < target.stat().st_size <= MAX_IMAGE_BYTES:
            raise ValueError("图片不存在或大小超出预览限制")
        data = target.read_bytes()
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError("图片大小超出预览限制")
        with Image.open(BytesIO(data)) as pic:
            mime = _MIMES.get(str(pic.format or "").upper())
            if (not mime or min(pic.size) < 1
                    or pic.width * pic.height > MAX_IMAGE_PIXELS):
                raise ValueError("图片格式或尺寸不支持预览")
            pic.verify()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("本地图片不可用，请重新抓取正文后重试") from exc
    return data, mime
