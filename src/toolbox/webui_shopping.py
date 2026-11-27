"""WebUI 购物解析：淘宝 / 抖音 / B站小店 / 闲鱼搜索与入库下载。

复用桌面 L4：`shopping`（淘宝）、`platforms.douyin_shop`、`platforms.bili_shop`；
输出沿用桌面目录（`淘宝商品` / `抖音商品` / `B站小店` / `闲鱼商品`）。本模块 Qt-free，
真实解析需要网络与对应平台登录态。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from toolbox.shopping_parse import (
    SHOPPING_PLATFORM_OPTIONS, normalize_shopping_platforms, plan_shopping_parse,
)


def options() -> dict:
    return {"platform_options": [{"key": key, "label": label}
                                 for key, label in SHOPPING_PLATFORM_OPTIONS]}


def prepare_payload(payload: dict) -> dict:
    """兼容旧单平台参数，所有调用在读取登录态/网络请求前走同一计划。"""
    requested = payload.get("platforms") if "platforms" in payload else (
        [payload["platform"]] if payload.get("platform") else None)
    selected = normalize_shopping_platforms(requested)
    text = str(payload.get("text") or "").strip()
    plan_shopping_parse(text, selected)
    return {"text": text, "platforms": list(selected)}


def _cookie(platform: str) -> str:
    try:
        import auth
        return auth.get_cookie(platform) or ""
    except Exception:  # noqa: BLE001 - 未登录时按游客处理
        return ""


def parse(payload: dict, on_log=None, is_cancelled=None, on_models=None,
          on_taobao_models=None) -> dict:
    """解析购物链接 / 分享文案，返回商品行并写入桌面同款产物目录。"""
    payload = prepare_payload(payload)
    plan = plan_shopping_parse(payload["text"], payload["platforms"])
    platform, text = plan["platform"], plan["text"]
    if is_cancelled and is_cancelled():
        raise ValueError("任务已取消")

    def say(message) -> None:
        if on_log:
            on_log(str(message))

    if platform == "taobao":
        result = _parse_taobao(text, say, is_cancelled, on_models=on_taobao_models)
    elif platform == "douyin":
        result = _parse_douyin(text, say, is_cancelled, on_models=on_models)
    elif platform == "xianyu":
        from toolbox import xianyu_shopping
        result = xianyu_shopping.parse(text, on_log=say,
                                       is_cancelled=is_cancelled)
    else:
        result = _parse_bili(text, say, is_cancelled, on_models=on_models)
    result["platforms"] = payload["platforms"]
    result["selection_note"] = plan["summary"]
    return result


def _require_xianyu(payload: dict) -> None:
    """显式取消闲鱼勾选时，在任务提交前拒绝相关请求。"""
    if "platforms" in payload:
        selected = normalize_shopping_platforms(payload["platforms"])
        if "xianyu" not in selected:
            raise ValueError("请先勾选闲鱼")


def prepare_xianyu_search(payload: dict) -> dict:
    from platforms.xianyu import validate_search_page
    _require_xianyu(payload)
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("请先输入闲鱼搜索关键词")
    if len(text.strip()) > 100:
        raise ValueError("搜索关键词不能超过 100 字")
    limit = payload.get("limit", 30)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
        raise ValueError("搜索数量须为 1 至 30 件")
    page = validate_search_page(payload.get("page", 1))
    return {"text": text.strip(), "limit": limit, "page": page}


def search_xianyu(payload: dict, on_log=None, is_cancelled=None) -> dict:
    from toolbox import xianyu_shopping
    prepared = prepare_xianyu_search(payload)
    return xianyu_shopping.search(**prepared, on_log=on_log,
                                  is_cancelled=is_cancelled)


def prepare_xianyu_save(payload: dict) -> dict:
    _require_xianyu(payload)
    from toolbox import xianyu_shopping
    # 与桌面共用规范化/去重，只向任务传递服务器核对过的稳定 ID。
    return {"items": [{"item_id": value} for value in
                      xianyu_shopping.normalize_items(payload.get("items"))]}


def save_xianyu(payload: dict, on_log=None, is_cancelled=None) -> dict:
    from toolbox import xianyu_shopping
    prepared = prepare_xianyu_save(payload)
    return xianyu_shopping.save_items(prepared["items"], on_log=on_log,
                                      is_cancelled=is_cancelled)


def _rows_from_products(products) -> list[dict]:
    rows = []
    for item in products:
        price = getattr(item, "price", "") or ""
        rows.append({
            "item_id": str(getattr(item, "item_id", "") or ""),
            "title": str(getattr(item, "title", "") or ""),
            "price": str(price),
            "sales": str(getattr(item, "sales", "") or ""),
            "url": str(getattr(item, "url", "") or ""),
            "image": bool(getattr(item, "pic", "")),
        })
    return rows


def _parse_taobao(text: str, say, is_cancelled, on_models=None) -> dict:
    import shopping as shopping_mod

    cookie = _cookie("taobao")
    kind, _ = shopping_mod.detect_shopping_platform(text)
    if not kind:
        raise ValueError("无法识别的购物链接（当前支持淘宝店铺 / 商品）")
    if kind == "taobao_item":
        say("解析淘宝商品详情…")
        try:
            detail = shopping_mod.parse_taobao_item_url(text, cookie, log=None)
        except Exception:  # noqa: BLE001 - 不公开平台原始错误与认证信息
            raise ValueError("淘宝商品解析失败，请检查登录状态与链接后重试") from None
        if is_cancelled and is_cancelled():
            raise ValueError("任务已取消（未保存图片）")
        from toolbox.config_manager import get_export_dir
        base = Path(get_export_dir()) / "淘宝商品"
        images = {"main": 0, "sku": 0, "desc": 0}
        exported = False
        try:
            shopping_mod.download_item_images(detail, base, log=None)
            images = {"main": len(getattr(detail, "local_main", []) or []),
                      "sku": len(getattr(detail, "local_sku", []) or []),
                      "desc": len(getattr(detail, "local_desc", []) or [])}
            shopping_mod.export_item_xlsx(
                detail, shopping_mod.default_item_xlsx_path(detail, base))
            exported = True
        except Exception:  # noqa: BLE001 - 图片 / Excel 失败不影响详情
            say("商品已解析，但图片或 Excel 保存失败，请稍后重试")
        return {
            "platform": "taobao",
            "kind": "item",
            "shop": {"name": getattr(detail, "shop_name", "") or "淘宝商品",
                     "id": getattr(detail, "shop_id", "") or "",
                     "url": getattr(detail, "url", "") or text},
            "items": [{
                "item_id": getattr(detail, "item_id", "") or "",
                "title": getattr(detail, "title", "") or "",
                "price": getattr(detail, "price", "") or "",
                "sales": getattr(detail, "sales", "") or "",
                "url": getattr(detail, "url", "") or text,
                "image": bool(images["main"]),
            }],
            "total": 1,
            "images": images,
            "has_cookie": bool(cookie),
            "summary": (f"商品已解析：{getattr(detail, 'title', '')[:40]}"
                        f"（主图 {images['main']} / 规格 {images['sku']} / "
                        f"详情 {images['desc']}，"
                        + ("Excel 已导出）" if exported else "Excel 未导出）")),
            "note": "图片与 Excel 写入「淘宝商品」导出目录。",
        }
    say("解析淘宝店铺与商品列表…")
    try:
        shop, products = shopping_mod.parse_shopping_url(
            text, cookie=cookie, log=None)
    except Exception:  # noqa: BLE001 - 不公开平台原始错误与认证信息
        raise ValueError("淘宝店铺解析失败，请检查登录状态与链接后重试") from None
    if is_cancelled and is_cancelled():
        raise ValueError("任务已取消（未保存缓存）")
    try:
        shopping_mod.save_latest_products(products,
                                          getattr(shop, "shop_name", ""))
    except Exception:  # noqa: BLE001
        say("商品列表缓存保存失败，本次解析结果仍可查看")
    if on_models is not None:
        on_models(shop, products)
    rows = _rows_from_products(products)
    name = getattr(shop, "shop_name", "") or "淘宝店铺"
    return {
        "platform": "taobao",
        "kind": "shop",
        "shop": {"name": name, "id": getattr(shop, "shop_id", "") or "",
                 "url": getattr(shop, "url", "") or text},
        "items": rows,
        "total": len(rows),
        "has_cookie": bool(cookie),
        "summary": (f"{name}：{len(rows)} 件商品" if rows
                    else f"{name}：已拿到店铺信息，"
                         + ("但未取到商品（可能风控或无在售）" if cookie
                            else "未登录，商品列表需要登录后获取")),
        "note": "商品列表写入「淘宝商品」缓存；勾选商品可抓取详情与图片并逐件导出 Excel。",
    }


def _parse_douyin(text: str, say, is_cancelled, on_models=None) -> dict:
    from platforms import douyin_shop as ds

    say("解析抖音商品分享文案…")
    try:
        products, errors = ds.parse_many(text, progress=say)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"解析失败：{exc}") from exc
    captured = ([replace(product, local_dir="", image_file="")
                 for product in products] if on_models is not None else None)
    try:
        image_root = ds.shop_dir() if on_models is not None and products else None
    except Exception:
        # An unavailable optional picture root must not discard parsed rows.
        image_root = None
    saved = 0
    for index, product in enumerate(products):
        if is_cancelled and is_cancelled():
            raise ValueError("任务已取消（未保存商品）")
        try:
            folder = ds.product_dir(product)
            image = ds.download_main_image(product, folder)
            if image is not None and captured is not None:
                # Only the downloader's actual return value may authorize a
                # picture for the private export snapshot, never client fields.
                image = Path(image)
                captured[index].local_dir = str(image.parent)
                captured[index].image_file = image.name
            ds.save_product_json(product, folder)
            saved += 1
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响其它
            errors = list(errors or []) + [(product.title, str(exc)[:120])]
    rows = [{
        "item_id": product.product_id,
        "title": product.title,
        "price": (f"{product.min_price:g}"
                  + (f"-{product.max_price:g}"
                     if product.max_price and product.max_price != product.min_price
                     else "")),
        "sales": str(product.sales or ""),
        "url": product.detail_url or product.share_url or "",
        "image": bool(product.main_image),
    } for product in products]
    if on_models is not None:
        on_models("douyin", captured, image_root)
    return {
        "platform": "douyin",
        "kind": "shop",
        "shop": {"name": "抖音商品", "id": "", "url": ""},
        "items": rows,
        "total": len(rows),
        "saved": saved,
        "failures": [{"text": str(item[0])[:60], "error": str(item[1])[:120]}
                     for item in (errors or [])][:20],
        "summary": f"抖音商品：解析 {len(rows)} 件，保存 {saved} 件"
                   + (f"，失败 {len(errors)} 条" if errors else ""),
        "note": "商品目录与 JSON / 主图写入「抖音商品」导出目录。",
    }


def _parse_bili(text: str, say, is_cancelled, on_models=None) -> dict:
    from platforms import bili_shop as bs

    say("解析 B站小店…")
    try:
        shop = bs.fetch_shop(text, sort="total_rank", progress=say)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"解析失败：{exc}") from exc
    if is_cancelled and is_cancelled():
        raise ValueError("任务已取消（未保存）")
    try:
        bs.save_shop_json(shop)
    except Exception as exc:  # noqa: BLE001
        say(f"保存小店信息失败：{exc}")
    rows = [{
        "item_id": item.item_id,
        "title": item.title,
        "price": item.price_text,
        "sales": "售罄" if item.sell_out else "",
        "url": item.card_url or "",
        "image": bool(item.cover),
    } for item in (shop.items or [])]
    if on_models is not None:
        on_models("bili", shop, None)
    return {
        "platform": "bili",
        "kind": "shop",
        "shop": {"name": shop.name or "B站小店", "id": shop.mid or "",
                 "url": shop.space_url or shop.share_url or text},
        "items": rows,
        "total": len(rows),
        "summary": f"{shop.name or 'B站小店'}：{len(rows)} 件商品",
        "note": "小店信息写入「B站小店」导出目录；本次完整结果可导出 Excel。",
    }
