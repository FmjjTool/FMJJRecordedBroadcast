"""WebUI 购物解析：淘宝店铺 / 淘宝商品 / 抖音商品 / B站小店。

复用桌面 L4：`shopping`（淘宝）、`platforms.douyin_shop`、`platforms.bili_shop`；
输出沿用桌面目录（`淘宝商品` / `抖音商品` / `B站小店`）。本模块 Qt-free，
真实解析需要网络与对应平台登录态。
"""

from __future__ import annotations

from pathlib import Path

_PLATFORMS = {
    "taobao": "淘宝",
    "douyin": "抖音",
    "bili": "B站小店",
}


def _cookie(platform: str) -> str:
    try:
        import auth
        return auth.get_cookie(platform) or ""
    except Exception:  # noqa: BLE001 - 未登录时按游客处理
        return ""


def parse(payload: dict, on_log=None, is_cancelled=None) -> dict:
    """解析购物链接 / 分享文案，返回商品行并写入桌面同款产物目录。"""
    platform = str(payload.get("platform") or "").strip()
    if platform not in _PLATFORMS:
        raise ValueError("平台只支持淘宝 / 抖音 / B站小店")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("请粘贴店铺 / 商品链接或分享文案")
    if is_cancelled and is_cancelled():
        raise ValueError("任务已取消")

    def say(message) -> None:
        if on_log:
            on_log(str(message))

    if platform == "taobao":
        return _parse_taobao(text, say, is_cancelled)
    if platform == "douyin":
        return _parse_douyin(text, say, is_cancelled)
    return _parse_bili(text, say, is_cancelled)


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


def _parse_taobao(text: str, say, is_cancelled) -> dict:
    import shopping as shopping_mod

    cookie = _cookie("taobao")
    kind, _ = shopping_mod.detect_shopping_platform(text)
    if not kind:
        raise ValueError("无法识别的购物链接（当前支持淘宝店铺 / 商品）")
    if kind == "taobao_item":
        say("解析淘宝商品详情…")
        try:
            detail = shopping_mod.parse_taobao_item_url(text, cookie, log=say)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"解析失败：{exc}") from exc
        if is_cancelled and is_cancelled():
            raise ValueError("任务已取消（未保存图片）")
        from toolbox.config_manager import get_export_dir
        base = Path(get_export_dir()) / "淘宝商品"
        images = {"main": 0, "sku": 0, "desc": 0}
        try:
            shopping_mod.download_item_images(detail, base, log=say)
            images = {"main": len(getattr(detail, "local_main", []) or []),
                      "sku": len(getattr(detail, "local_sku", []) or []),
                      "desc": len(getattr(detail, "local_desc", []) or [])}
            shopping_mod.export_item_xlsx(
                detail, shopping_mod.default_item_xlsx_path(detail, base))
        except Exception as exc:  # noqa: BLE001 - 图片 / Excel 失败不影响详情
            say(f"图片或 Excel 导出失败：{exc}")
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
                        f"详情 {images['desc']}，Excel 已导出）"),
            "note": "图片与 Excel 写入「淘宝商品」导出目录。",
        }
    say("解析淘宝店铺与商品列表…")
    try:
        shop, products = shopping_mod.parse_shopping_url(
            text, cookie=cookie, log=say)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"解析失败：{exc}") from exc
    if is_cancelled and is_cancelled():
        raise ValueError("任务已取消（未保存缓存）")
    try:
        shopping_mod.save_latest_products(products,
                                          getattr(shop, "shop_name", ""))
    except Exception as exc:  # noqa: BLE001
        say(f"缓存保存失败：{exc}")
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
        "note": "商品列表写入「淘宝商品」缓存；图片与 Excel 在桌面端按商品导出。",
    }


def _parse_douyin(text: str, say, is_cancelled) -> dict:
    from platforms import douyin_shop as ds

    say("解析抖音商品分享文案…")
    try:
        products, errors = ds.parse_many(text, progress=say)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"解析失败：{exc}") from exc
    saved = 0
    for product in products:
        if is_cancelled and is_cancelled():
            raise ValueError("任务已取消（未保存商品）")
        try:
            folder = ds.product_dir(product)
            ds.download_main_image(product, folder)
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


def _parse_bili(text: str, say, is_cancelled) -> dict:
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
    return {
        "platform": "bili",
        "kind": "shop",
        "shop": {"name": shop.name or "B站小店", "id": shop.mid or "",
                 "url": shop.space_url or shop.share_url or text},
        "items": rows,
        "total": len(rows),
        "summary": f"{shop.name or 'B站小店'}：{len(rows)} 件商品",
        "note": "小店信息写入「B站小店」导出目录；Excel 导出仍在桌面端。",
    }