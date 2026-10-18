"""冒烟测试：抖音商品解析（离线构造跳转数据 + 页面离屏构造 + 注册表断言）。

不发真实网络请求也能验证核心链路；末尾对真实分享链做一次可选联网校验。
"""
import base64
import gzip
import json
import os
import sys
import urllib.parse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

failures = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        failures.append(name)


from platforms import douyin_shop as ds

# ---------- 1) 查询串解析：'+' 不能被吃掉（base64 会被 parse_qs 破坏） ----------
check("_parse_query_keep_plus 保留 '+'", ds._parse_query_keep_plus("a=1+2&b=%2B")["a"] == "1+2")
check("_parse_query_keep_plus 正常解 % 转义", ds._parse_query_keep_plus("b=%2B")["b"] == "+")


# ---------- 2) 离线构造一条完整的 302 跳转 URL ----------
GOODS = {
    "title": "【封茗囧菌】2026.6.7生日会周边海报/丝带/色纸/立牌/发圈/吧唧",
    "sales": 0,
    "img": {"url_list": ["https://p26-item.ecombdimg.com/img/demo~tplv-5mmsx3fupr-image.png"]},
    "min_price": 1500,
    "max_price": 23400,
}
META = {
    "enter_from": "store_page",
    "entrance_info": json.dumps(
        {"shop_id": "302189776", "kol_id": "99514375927", "app_version": "40.7.0"},
        ensure_ascii=False),
}
mp_b64 = base64.b64encode(gzip.compress(json.dumps(META, ensure_ascii=False).encode())).decode()
schema = ("sslocal://ec_goods_detail?product_id=3848245909439774889"
          f"&kol_id=99514375927&meta_params={mp_b64}")
location = (
    "https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html"
    "?id=3848245909439774889"
    "&sec_author_id=MS4wLjABAAAA2I9NdgAKZrz9e0tLm1csyDMNqLESPDm34TdYYqXe8-I"
    "&use_link_command=1"
    f"&goods_detail={urllib.parse.quote(urllib.parse.quote(json.dumps(GOODS, ensure_ascii=False)))}"
    f"&detail_schema={urllib.parse.quote(urllib.parse.quote(schema))}")

p = ds.parse_location(location, share_url="https://v.douyin.com/demo/")
check("解析出商品ID", p.product_id == "3848245909439774889")
check("解析出标题", p.title.startswith("【封茗囧菌】2026.6.7"))
check("解析出价格区间（分 → 元）", (p.min_price, p.max_price) == (15.0, 234.0))
check("价格区间展示文本", p.price_text == "¥15.00 ~ ¥234.00")
check("解析出主图", p.main_image.startswith("https://p26-item.ecombdimg.com/"))
check("解析出店铺ID（来自 meta_params）", p.shop_id == "302189776")
check("解析出带货达人ID（来自 detail_schema）", p.kol_id == "99514375927")
check("解析出分享来源与客户端版本", (p.enter_from, p.app_version) == ("store_page", "40.7.0"))
check("商品页链接可用", p.detail_url.endswith("id=3848245909439774889"))
check("to_dict 带 price_text", p.to_dict().get("price_text") == "¥15.00 ~ ¥234.00")

# 单价商品只显示一个价格
p2 = ds.parse_location(location.replace("23400", "1500"))
check("最低=最高时只显示一个价格", p2.price_text == "¥15.00")

# 非商品链接要报错
try:
    ds.parse_location("https://www.douyin.com/video/123")
    check("非商品链接应报错", False)
except ValueError:
    check("非商品链接应报错", True)


# ---------- 3) 分享文案抠链接 / 多行拆分 ----------
real_text = ("【抖音商城】https://v.douyin.com/oLCw29IugNk/ 【封茗囧菌】2026.6.7生日会周边海报/"
             "丝带/色纸/立牌/发圈/吧唧\n长按复制此条消息，打开抖音极速版搜索，查看商品详情！")
check("从整段文案抠出短链", ds.extract_share_url(real_text) == "https://v.douyin.com/oLCw29IugNk/")
check("多行拆分只留有链接的行", len(ds.split_share_texts(real_text)) == 1)
try:
    ds.extract_share_url("这里没有链接")
    check("无链接应报错", False)
except ValueError:
    check("无链接应报错", True)


# ---------- 4) 文件名安全化 ----------
check("文件名安全化去掉非法字符", "/" not in ds._safe_name("a/b:c*d?e"))
check("文件名安全化不返回空", ds._safe_name("   ") == "未命名")


# ---------- 5) 注册表 / 导航 / 导出目录 ----------
from gui_qt.main_window import NAV_GROUPS, PAGE_CLASSES
from toolbox.config_manager import EXPORT_CATEGORIES

check("PAGE_CLASSES 注册 douyin_shop",
      PAGE_CLASSES.get("douyin_shop") == ("gui_qt.pages.douyin_shop_page", "DouyinShopPage"))
nav_keys = [k for _g, items in NAV_GROUPS for k, _t in items]
check("导航里出现 douyin_shop", "douyin_shop" in nav_keys)
check("导出目录分类注册 douyin_shop", EXPORT_CATEGORIES.get("douyin_shop") == "抖音商品")

# 页面类能导入且继承 BasePage
from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])
from gui_qt.pages.base import BasePage
from gui_qt.pages.douyin_shop_page import DouyinShopPage

check("DouyinShopPage 继承 BasePage", issubclass(DouyinShopPage, BasePage))


class _FakeApp:
    """页面构造只需要 toast。"""
    def toast(self, *a, **k):
        pass


page = DouyinShopPage(_FakeApp())
check("页面可离屏构造", page is not None)
page._products = [p]
page._fill_table()
check("表格填充行数正确", page.table.rowCount() == 1)
check("导出按钮随之启用", page.export_btn.isEnabled())
check("详情文本含关键字段",
      all(s in page._detail_text(p) for s in ("商品标题", "价格区间", "店铺ID", "带货达人ID")))
page._clear()
check("清空后列表归零", page.table.rowCount() == 0 and not page.export_btn.isEnabled())
check("复制链接按钮存在且清空后禁用",
      hasattr(page, "copy_btn") and not page.copy_btn.isEnabled())
check("详情文本提示需去 App 看详情图", "抖音 App" in page._detail_text(p))


# ---------- 6) 真实链路（可选，失败只提示不判失败） ----------
try:
    real = ds.parse_share_text(real_text)
    print(f"[INFO] 真实解析：{real.title} | {real.price_text} | 商品ID {real.product_id}")
    check("真实链路解析出商品ID", bool(real.product_id))
except Exception as e:  # noqa: BLE001
    print(f"[SKIP] 真实链路未验证（{e}）")

print()
if failures:
    print(f"结果：{len(failures)} 项失败 -> {failures}")
    sys.exit(1)
print("结果：全部通过")
