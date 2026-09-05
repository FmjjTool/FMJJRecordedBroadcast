"""套版封面制作模块测试（离屏）。

覆盖：
- 渲染引擎：文字像素确实绘制、比例化分辨率一致、自动换行宽度、溢出自动缩小
- 数据模型：FieldState 序列化/模板往返
- 底图引擎：递归扫描 / 随机抽取 / 缩放缓存
- QImage→PIL 转换、字体回退
- 页面冒烟：实例化 / 预览渲染 / PNG·JPG·GIF 导出 / 拖动更新 / 模板保存加载 / 深色渲染
- 集成：create_cover_module() 工厂、PAGE_CLASSES 注册
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QEventLoop, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication

import toolbox.config_manager as cm

_TMP = Path(tempfile.mkdtemp(prefix="fm_cover_"))
cm.get_config_dir = lambda: _TMP

import gui_qt.pages.cover_maker as cvr

cvr.get_config_dir = lambda: _TMP
cvr.get_export_dir = lambda: _TMP / "download"

_app = QApplication.instance() or QApplication(sys.argv)


class _FakeApp:
    def __init__(self):
        self.work_dir = str(_TMP)

    def toast(self, *args, **kwargs):
        pass

    def show_page(self, *args, **kwargs):
        pass


def _make_bg(w=320, h=180, color="#ff0000") -> QImage:
    img = QImage(w, h, QImage.Format.Format_RGB32)
    img.fill(QColor(color))
    return img


def _count_pixels(img: QImage, predicate) -> int:
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if predicate(c):
                n += 1
    return n


class TestRenderEngine(unittest.TestCase):
    def test_text_actually_drawn(self):
        states = cvr.default_states()
        states["title"].text = "封面标题"
        states["title"].color = "#ffffff"
        img, geoms = cvr.render_cover(960, 540, _make_bg(960, 540, "#aa0000"),
                                      cvr.FIT_CROP, states, draw_hint=False)
        self.assertFalse(img.isNull())
        self.assertIn("title", geoms)
        white = _count_pixels(img, lambda c: c.red() > 240 and c.green() > 240
                               and c.blue() > 240)
        self.assertGreater(white, 30, "标题文字未绘制出白色像素")

    def test_geometry_inside_canvas(self):
        states = cvr.default_states()
        states["title"].text = "标题"
        states["date"].text = "2026-09-05"
        states["remark"].text = "备注文字"
        _img, geoms = cvr.render_cover(960, 540, None, cvr.FIT_CROP, states,
                                       draw_hint=False)
        self.assertEqual(len(geoms), 3)
        for key, rect in geoms.items():
            self.assertGreaterEqual(rect.x(), -0.5, key)
            self.assertLessEqual(rect.x() + rect.width(), 960.5, key)
            self.assertGreaterEqual(rect.y(), -0.5, key)
            self.assertLessEqual(rect.y() + rect.height(), 540.5, key)

    def test_resolution_ratio_consistency(self):
        states = cvr.default_states()
        states["title"].text = "标题"
        _i1, g1 = cvr.render_cover(960, 540, None, cvr.FIT_CROP, states, draw_hint=False)
        _i2, g2 = cvr.render_cover(1920, 1080, None, cvr.FIT_CROP, states, draw_hint=False)
        h1, h2 = g1["title"].height(), g2["title"].height()
        self.assertGreater(h2 / h1, 1.7, "4K/1080p 字号应约两倍")
        self.assertLess(h2 / h1, 2.3)
        self.assertAlmostEqual(g1["title"].center().y() / 540.0,
                               g2["title"].center().y() / 1080.0, places=1)

    def test_word_wrap_max_width(self):
        states = cvr.default_states()
        states["remark"].text = "封" * 300
        _img, geoms = cvr.render_cover(960, 540, None, cvr.FIT_CROP, states,
                                       draw_hint=False)
        self.assertLessEqual(geoms["remark"].width(), 960 * 0.7 + 2.0,
                             "文字块宽度应不超过画布 70%")

    def test_auto_shrink_on_overflow(self):
        states = cvr.default_states()
        states["title"].text = "字" * 120
        states["title"].font_size_ratio = 300.0 / 1080.0
        _img, geoms = cvr.render_cover(960, 540, None, cvr.FIT_CROP, states,
                                       draw_hint=False)
        self.assertLessEqual(geoms["title"].height(), 540 * 0.88 + 2.0,
                             "超长文字应自动缩小字号以适配画布")

    def test_fit_modes(self):
        bg = _make_bg(320, 180, "#00aa00")  # 16:9，与画布同比例
        states = cvr.default_states()
        _img_c, _ = cvr.render_cover(960, 540, bg, cvr.FIT_CROP, states, draw_hint=False)
        # 竖长底图裁剪填充：画布四角仍是底图颜色；完整包含：上下留深色
        tall = _make_bg(180, 320, "#00aa00")
        img_crop, _ = cvr.render_cover(200, 100, tall, cvr.FIT_CROP,
                                       cvr.default_states(), draw_hint=False)
        self.assertEqual(img_crop.pixelColor(2, 2).name(), "#00aa00",
                         "裁剪填充应铺满画布")
        img_fit, _ = cvr.render_cover(200, 100, tall, cvr.FIT_CONTAIN,
                                      cvr.default_states(), draw_hint=False)
        self.assertEqual(img_fit.pixelColor(2, 2).name(), cvr.CANVAS_BG_FALLBACK.lower(),
                         "完整包含应在上下留白")
        self.assertEqual(img_fit.pixelColor(100, 50).name(), "#00aa00",
                         "完整包含时中心应为底图")

    def test_qimage_to_pil(self):
        img = _make_bg(12, 8, "#00ff00")
        pil = cvr.qimage_to_pil(img)
        self.assertEqual(pil.size, (12, 8))
        self.assertEqual(pil.getpixel((5, 4)), (0, 255, 0))


class TestModelAndHelpers(unittest.TestCase):
    def test_field_state_roundtrip(self):
        st = cvr.FieldState(text="abc", font_family="Arial", color="#112233",
                            bold=True, italic=True, alignment="right",
                            x_ratio=0.3, y_ratio=0.8, font_size_ratio=0.06)
        clone = cvr.FieldState.from_dict(st.to_dict())
        self.assertEqual(st, clone)

    def test_template_roundtrip_keeps_text(self):
        states = cvr.default_states()
        states["title"].text = "当前文字"
        states["title"].font_family = cvr.DEFAULT_FONT_FAMILY
        states["title"].color = "#ff8800"
        states["date"].y_ratio = 0.62
        tpl = {"version": 1, "fields": cvr.template_dict_from_states(states)}
        target = cvr.default_states()
        target["title"].text = "旧文字"
        warnings = cvr.apply_template_to_states(target, tpl)
        self.assertEqual(warnings, [])
        self.assertEqual(target["title"].color, "#ff8800")
        self.assertEqual(target["date"].y_ratio, 0.62)
        self.assertEqual(target["title"].text, "旧文字", "模板不包含文字内容")

    def test_template_missing_font_fallback(self):
        tpl = {"fields": {"title": {"font_family": "___不存在___", "x_ratio": 0.5}}}
        target = cvr.default_states()
        warnings = cvr.apply_template_to_states(target, tpl)
        self.assertTrue(any("回退" in w for w in warnings))
        self.assertEqual(target["title"].font_family, cvr.DEFAULT_FONT_FAMILY)

    def test_font_fallback(self):
        self.assertEqual(cvr._resolve_font_family("___no_such_font___"),
                         cvr.DEFAULT_FONT_FAMILY)
        families = set(QFontDatabase.families())
        if families:
            self.assertEqual(cvr._resolve_font_family(sorted(families)[0]),
                            sorted(families)[0])

    def test_sanitize_template_name(self):
        self.assertEqual(cvr._sanitize_template_name("a/b\\c:d*e?f<g>h|i"),
                         "a_b_c_d_e_f_g_h_i")


class TestBackgroundProvider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = _TMP / "cover_bgs" / "sub"
        cls.folder.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            img = _make_bg(40, 24, "#2030a0")
            img.save(str(cls.folder / f"bg{i}.png"), "PNG")

    def test_scan_recursive(self):
        p = cvr.BackgroundProvider(self.folder.parent)
        self.assertEqual(len(p.files), 3)
        # 原始对象在子目录中被递归发现
        self.assertTrue(all(f.parent == self.folder for f in p.files))

    def test_random_and_scaled_cache(self):
        p = cvr.BackgroundProvider(self.folder.parent)
        pick = p.random_path()
        self.assertIn(pick, p.files)
        scaled = p.scaled_background(pick, 200, 100, cvr.FIT_CROP)
        self.assertIsNotNone(scaled)
        self.assertEqual(scaled.width(), 200)
        self.assertGreaterEqual(scaled.height(), 100, "裁剪填充应覆盖目标尺寸")
        again = p.scaled_background(pick, 200, 100, cvr.FIT_CROP)
        self.assertIs(scaled, again, "同参数应命中缩放缓存")

    def test_missing_file_returns_none(self):
        p = cvr.BackgroundProvider(self.folder.parent)
        self.assertIsNone(p.scaled_background(Path("___missing___"), 100, 100,
                                              cvr.FIT_CROP))


class TestCoverMakerPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = cvr.CoverMakerPage(_FakeApp())

    @classmethod
    def tearDownClass(cls):
        cls.page.shutdown()

    def test_factory_and_registration(self):
        widget = cvr.create_cover_module(_FakeApp())
        self.assertIsInstance(widget, cvr.CoverMakerPage)
        widget.shutdown()
        from gui_qt.main_window import PAGE_CLASSES, NAV_GROUPS
        self.assertIs(PAGE_CLASSES.get("cover_maker"), cvr.CoverMakerPage)
        nav = [item for _g, items in NAV_GROUPS for item in items]
        self.assertIn(("cover_maker", "🎨 套版封面制作"), nav)

    def test_preview_render(self):
        self.page.states["title"].text = "测试封面"
        self.page._render_preview()
        img = self.page.preview._image
        self.assertFalse(img.isNull())
        self.assertEqual((img.width(), img.height()), (cvr.PREVIEW_W, cvr.PREVIEW_H))

    def test_export_png_jpg(self):
        self.page.states["title"].text = "导出测试"
        self.page._render_preview()
        png = self.page._export_image("PNG")
        jpg = self.page._export_image("JPEG")
        self.assertIsNotNone(png)
        self.assertIsNotNone(jpg)
        self.assertTrue(png.exists() and png.suffix == ".png")
        self.assertTrue(jpg.exists() and jpg.suffix == ".jpg")
        self.assertFalse(QImage(str(png)).isNull())
        self.assertIsNotNone(self.page.cached_export_image)

    def test_export_gif(self):
        self.page.states["title"].text = "GIF 测试"
        frames = self.page._render_gif_frames()
        self.assertEqual(len(frames), self.page.gif_frames_spin.value())
        out = _TMP / "cover.gif"
        cvr.CoverMakerPage.save_gif(frames, out)
        self.assertGreater(out.stat().st_size, 1000)
        from PIL import Image as PILImage
        with PILImage.open(out) as im:
            self.assertGreaterEqual(im.n_frames, 8)

    def test_drag_update(self):
        self.page._on_drag_update("title", 0.7, 0.25)
        self.assertAlmostEqual(self.page.states["title"].x_ratio, 0.7)
        self.assertAlmostEqual(self.page.states["title"].y_ratio, 0.25)
        self.assertTrue(self.page._debounce.isActive(), "拖动应经 10ms 节流触发渲染")

    def test_text_typing_renders_within_10ms(self):
        self.page._debounce.stop()
        self.page.states["title"].text = ""
        self.page._render_preview()
        self.assertNotIn("title", self.page.preview._geoms)
        self.page.panels["title"].text_edit.setText("实时渲染")
        self.assertTrue(self.page._debounce.isActive(), "打字后应启动渲染定时器")
        self.assertLessEqual(self.page._debounce.interval(), 10,
                             "节流间隔应为 10ms")
        loop = QEventLoop()
        QTimer.singleShot(80, loop.quit)
        loop.exec()
        self.assertIn("title", self.page.preview._geoms,
                      "80ms 内必须完成渲染（10ms 节流实时跟手）")

    def test_rapid_typing_does_not_starve(self):
        self.page._debounce.stop()
        self.page.states["title"].text = ""
        self.page._render_preview()
        self.assertNotIn("title", self.page.preview._geoms)
        self.page.panels["title"].text_edit.setText("打字1")
        QTimer.singleShot(20, lambda: self.page.panels["title"].text_edit.setText("打字2"))
        loop = QEventLoop()
        QTimer.singleShot(60, loop.quit)
        loop.exec()
        self.assertIn("title", self.page.preview._geoms,
                      "60ms 内第二次输入必须已渲染（定时器不得被连续输入反复重置饿死）")
        self.assertEqual(self.page.states["title"].text, "打字2")

    def test_reset_positions(self):
        self.page.states["title"].x_ratio = 0.9
        self.page.states["title"].y_ratio = 0.1
        self.page._on_reset_positions()
        defaults = cvr.default_states()
        self.assertAlmostEqual(self.page.states["title"].x_ratio,
                               defaults["title"].x_ratio)
        self.assertAlmostEqual(self.page.states["title"].y_ratio,
                               defaults["title"].y_ratio)

    def test_sync_style_propagation(self):
        self.page.sync_font_cb.setChecked(True)
        self.page.sync_color_cb.setChecked(True)
        try:
            fam = sorted(QFontDatabase.families())[0] if QFontDatabase.families() \
                else "SimSun"
            self.page.states["title"].font_family = fam
            self.page._on_panel_attr(self.page.panels["title"], "font")
            self.assertEqual(self.page.states["date"].font_family, fam)
            self.assertEqual(self.page.states["remark"].font_family, fam)
            self.page.states["title"].color = "#123456"
            self.page._on_panel_attr(self.page.panels["title"], "color")
            self.assertEqual(self.page.states["date"].color, "#123456")
            self.assertEqual(self.page.states["remark"].color, "#123456")
        finally:
            self.page.sync_color_cb.setChecked(False)

    def test_template_save_and_load(self):
        self.page.states["date"].color = "#123456"
        self.page._persist_state()
        tpl_path = cvr._templates_dir() / "unittest.json"
        tpl_path.write_text(json.dumps(
            {"version": 1, "fields": cvr.template_dict_from_states(self.page.states)}),
            encoding="utf-8")
        self.page.states["date"].color = "#ffffff"
        self.page._load_template_from_path(tpl_path)
        self.assertEqual(self.page.states["date"].color, "#123456")

    def test_persist_and_restore(self):
        self.page.states["title"].text = "持久化"
        self.page._persist_state()
        section = cvr._read_cover_config()
        self.assertEqual(section["fields"]["title"]["text"], "持久化")
        self.assertIn("cover_maker", (_TMP / "config.json").read_text(encoding="utf-8"))

    def test_dark_theme_render(self):
        from gui_qt import theme
        saved = theme._state.get("dark", False)
        theme._state["dark"] = True
        try:
            theme.install_auto_remap()
            theme.apply_theme(_app)
            page = cvr.CoverMakerPage(_FakeApp())
            page.resize(1200, 800)
            pix = page.grab()
            self.assertFalse(pix.isNull())
            page.shutdown()
        finally:
            theme._state["dark"] = saved
            theme.apply_theme(_app)

    def test_preview_map_to_canvas(self):
        self.page.preview.resize(800, 450)
        self.page._render_preview()
        # 960x540 画布在 800x450 内恰好铺满（同 16:9），映射 1:1.2
        pt = self.page.preview._map_to_canvas(QPointF(120.0, 120.0))
        self.assertAlmostEqual(pt.x(), 144.0, delta=2.0)
        self.assertAlmostEqual(pt.y(), 144.0, delta=2.0)
        hit = self.page.preview._hit_test(pt)
        self.assertIn(hit, (None, "title", "date", "remark"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
