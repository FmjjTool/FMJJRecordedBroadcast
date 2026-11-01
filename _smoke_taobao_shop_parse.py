"""淘宝店铺解析冒烟：验证 mtop 通道（含 token 过期自动刷新）+ Playwright 兜底顺序。

背景：淘宝「解析无结果」多为两类原因——
1. Cookie 里的 _m_h5_tk 过期 → wandering.get 返回 FAIL_SYS_TOKEN_*，
   和「店铺没商品」表现一样（此前不刷新 token 直接当空页处理）；
2. 首屏被风控 RECOMMEND_EMPTY → 降级 Playwright，旧候选顺序首选 m 站壳页，
   必然 DOM 0 候选，白跑几十秒。

跑法：python _smoke_taobao_shop_parse.py
"""
import sys

sys.path.insert(0, "src")

import auth
import shopping

URL = "https://f03rr9e3mrh0fdmsfil8w1sdcgp77kl.taobao.com/shop/view_shop.htm"


def log(m):
    print("  LOG:", m)


cookie = auth.get_cookie("taobao") or ""
print("cookie 长度:", len(cookie))

print("== 1. 店铺信息（游客 HTTP）==")
info = shopping.fetch_taobao_shop_info(URL)
print(f"  {info}")

print("== 2. mtop 通道 ==")
res = {}
try:
    res = shopping._try_mtop_shop_items(info.shop_id, info.seller_id, cookie,
                                        count=40, log=log)
except Exception as e:
    print("  mtop 异常:", e)
mtop_items = res.get("items") or []
print(f"  → {len(mtop_items)} 件")

print("== 3. 完整入口 parse_shopping_url ==")
shop, prods = shopping.parse_shopping_url(URL, cookie=cookie, count=40, log=log)
print(f"  → {shop.shop_name}：{len(prods)} 件")
for p in prods[:4]:
    print(f"    {p.item_id} | {p.title[:26]} | {p.price} | {p.sales}")

assert prods, "淘宝店铺解析应至少拿到 1 件商品（mtop 或 Playwright 任一通道）"
print("\n✅ 淘宝店铺解析冒烟通过")
