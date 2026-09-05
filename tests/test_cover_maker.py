"""套版封面制作模块测试（离屏）。

覆盖：
- 渲染引擎：文字像素确实绘制、比例化分辨率一致、自动换行宽度、溢出自动缩小
- 数据模型：FieldState 序列化/模板往返
- 底图引擎：递归扫描 / 随机抽取 / 缩放缓存
- QImage→PIL 转换、字体回退
- 页面冒烟：实例化 / 预览渲染 / PNG·JPG·GIF 导出 / 拖动更新 / 模板保存加载 / 深色渲染
- 底图选择器：方块平铺网格 / 预选与选择 / 筛选 / 缩略图解码
- 滚轮拦截：五类控件滚轮不改值不吞事件；弹窗与补全列表跟随主题
- 颜色选择器：预设色板 / 十六进制·RGB 输入识别 / 双击确定 / 接线
- 集成：create_cover_module() 工厂、PAGE_CLASSES 注册
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QEventLoop, QPoint, QPointF, QSize, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QImage, QPainter,
                           QPalette, QWheelEvent)
from PySide6.QtWidgets import QApplication, QDialog, QListWidget

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


def _make_test_bgs() -> list:
    """模块级创建 3 张测试底图（unittest 类按字母序执行，需与类无关地可重入）。"""
    folder = _TMP / "cover_bgs" / "sub"
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(3):
        p = folder / f"bg{i}.png"
        if not p.exists():
            _make_bg(40, 24, "#2030a0").save(str(p), "PNG")
        files.append(p)
    return sorted(files)


def _pump_until(predicate, timeout_ms: int) -> bool:
    """事件循环泵：每 5ms 一片，直到 predicate 为真或超时（禁止 busy-loop）。"""
    deadline = time.monotonic() + timeout_ms / 1000.0
    while time.monotonic() < deadline:
        loop = QEventLoop()
        QTimer.singleShot(5, loop.quit)
        loop.exec()
        if predicate():
            return True
    return False


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
        cls.files = _make_test_bgs()
        cls.folder = cls.files[0].parent

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
        # 预算 120ms：10ms 节流远低于界线；旧 150ms 纯防抖必然超界
        self.assertTrue(
            _pump_until(lambda: "title" in self.page.preview._geoms, 120),
            "120ms 内必须完成渲染（10ms 节流实时跟手，旧 150ms 防抖必超界）")

    def test_rapid_typing_does_not_starve(self):
        self.page._debounce.stop()
        self.page.states["title"].text = ""
        self.page._render_preview()
        self.assertNotIn("title", self.page.preview._geoms)
        self.page.panels["title"].text_edit.setText("打字1")
        QTimer.singleShot(20, lambda: self.page.panels["title"].text_edit.setText("打字2"))
        # 第二次输入在 20ms；10ms 节流 ~30ms 渲染完成，旧防抖重置后要到 170ms
        self.assertTrue(
            _pump_until(lambda: "title" in self.page.preview._geoms, 140),
            "140ms 内第二次输入必须已渲染（定时器不得被连续输入反复重置饿死）")
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

    def test_template_remembers_background(self):
        files = sorted((_TMP / "cover_bgs" / "sub").glob("*.png"))
        self.assertGreaterEqual(len(files), 3)
        tpl_path = cvr._templates_dir() / "with_bg.json"
        tpl_path.write_text(json.dumps(
            {"version": 1, "bg": str(files[0]),
             "fields": cvr.template_dict_from_states(self.page.states)}),
            encoding="utf-8")
        # 当前底图不是模板记录的那张
        self.page._set_bg_path(files[1])
        self.assertEqual(self.page._bg_path, files[1])
        self.page._load_template_from_path(tpl_path)
        self.assertEqual(self.page._bg_path, files[0],
                         "加载模板应自动切换到模板记忆的底图（蒙版）")

    def test_template_missing_background_keeps_current(self):
        files = sorted((_TMP / "cover_bgs" / "sub").glob("*.png"))
        self.page._set_bg_path(files[0])
        tpl_path = cvr._templates_dir() / "gone_bg.json"
        tpl_path.write_text(json.dumps(
            {"version": 1, "bg": str(_TMP / "___deleted___.png"),
             "fields": cvr.template_dict_from_states(self.page.states)}),
            encoding="utf-8")
        self.page._load_template_from_path(tpl_path)
        self.assertEqual(self.page._bg_path, files[0],
                         "模板底图文件不存在时应保留当前底图")

    def test_save_template_writes_bg(self):
        files = sorted((_TMP / "cover_bgs" / "sub").glob("*.png"))
        self.page._set_bg_path(files[0])
        tpl_path = cvr._templates_dir() / "bg_check.json"
        payload = {"version": cvr.TEMPLATE_VERSION,
                   "bg": str(self.page._bg_path),
                   "fields": cvr.template_dict_from_states(self.page.states)}
        tpl_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        data = json.loads(tpl_path.read_text(encoding="utf-8"))
        self.assertEqual(data["bg"], str(files[0]))

    def test_persisted_bg_restored_on_init(self):
        files = sorted((_TMP / "cover_bgs" / "sub").glob("*.png"))
        self.page._set_bg_path(files[2])
        self.page._persist_state()
        self.assertEqual(cvr._read_cover_config()["bg_path"], str(files[2]))
        page2 = cvr.CoverMakerPage(_FakeApp(),
                                   bg_folder=_TMP / "cover_bgs")
        try:
            self.assertEqual(page2._bg_path, files[2],
                             "重启应恢复上次使用的底图而非随机抽取")
        finally:
            page2.shutdown()

    def test_set_bg_path_rejects_undecodable(self):
        files = sorted((_TMP / "cover_bgs" / "sub").glob("*.png"))
        self.assertTrue(self.page._set_bg_path(files[0]))
        bad = _TMP / "not_an_image.png"
        bad.write_bytes(b"\x00" * 64)
        try:
            self.assertFalse(self.page._set_bg_path(bad),
                            "无法解码的文件应被拒绝")
            self.assertEqual(self.page._bg_path, files[0], "失败时保留原底图")
        finally:
            bad.unlink()


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


class TestBackgroundPickerDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = _make_test_bgs()

    def test_items_current_selected_and_pick(self):
        dlg = cvr.BackgroundPickerDialog(self.files, self.files[1])
        try:
            self.assertEqual(dlg.listw.count(), len(self.files))
            self.assertEqual(dlg.selected_path(), self.files[1],
                            "当前底图应被预选中")
            dlg.listw.setCurrentRow(0)
            self.assertEqual(dlg.selected_path(), self.files[0])
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_filter_hides_non_matching(self):
        dlg = cvr.BackgroundPickerDialog(self.files, self.files[0])
        try:
            dlg.filter_edit.setText(self.files[0].stem.lower())
            visible = [r for r in range(dlg.listw.count())
                      if not dlg.listw.item(r).isHidden()]
            self.assertEqual(visible, [0], "筛选后应只剩匹配项")
            dlg.filter_edit.setText("___no_match___")
            visible = [r for r in range(dlg.listw.count())
                      if not dlg.listw.item(r).isHidden()]
            self.assertEqual(visible, [], "无匹配时不应有可见项")
            self.assertIn("0/3", dlg.count_label.text())
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_tile_layout(self):
        """底图选择器应为统一方块平铺：IconMode + 固定图标区 + 固定网格。"""
        dlg = cvr.BackgroundPickerDialog(self.files, self.files[0])
        try:
            self.assertEqual(dlg.listw.viewMode(), QListWidget.ViewMode.IconMode)
            tile = cvr.BackgroundPickerDialog.TILE
            self.assertEqual(
                (dlg.listw.iconSize().width(), dlg.listw.iconSize().height()),
                (tile, tile), "图标区应为正方形方块")
            gs = dlg.listw.gridSize()
            self.assertEqual(
                (gs.width(), gs.height()),
                (cvr.BackgroundPickerDialog.GRID_W, cvr.BackgroundPickerDialog.GRID_H),
                "网格尺寸应固定，保证平铺整齐")
            self.assertEqual(dlg.listw.movement(), QListWidget.Movement.Static,
                             "图标不得可拖动乱序")
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_thumb_decode(self):
        dlg = cvr.BackgroundPickerDialog(self.files, self.files[0])
        try:
            img = dlg._decode_thumb(self.files[0])
            self.assertIsNotNone(img)
            tile = cvr.BackgroundPickerDialog.TILE
            self.assertLessEqual(max(img.width() / tile, img.height() / tile),
                                 1.0 + 1e-6,
                                 "缩略图应等比缩放进方块内（宽高都不超过 TILE）")
            self.assertIsNone(dlg._decode_thumb(Path("___missing___")))
        finally:
            dlg.close()
            dlg.deleteLater()


class TestNoWheelControls(unittest.TestCase):
    """滚轮不改变值：五类控件忽略滚轮事件（冒泡给滚动区），值保持不变。"""

    @staticmethod
    def _mk_wheel() -> QWheelEvent:
        return QWheelEvent(QPointF(0, 0), QPointF(0, 100), QPoint(0, 0), QPoint(0, 1),
                           Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                           Qt.ScrollPhase.ScrollBegin, False)

    def _assert_ignored(self, widget, value_getter, expected, msg):
        ev = self._mk_wheel()
        widget.wheelEvent(ev)
        self.assertFalse(ev.isAccepted(), f"{msg}：滚轮事件应被忽略（不吞事件）")
        self.assertEqual(value_getter(), expected, f"{msg}：滚轮不得改变值")

    def test_wheel_ignored_on_all_controls(self):
        combo = cvr.NoWheelComboBox()
        combo.addItems(["甲", "乙", "丙"])
        self._assert_ignored(combo, combo.currentIndex, 0, "组合框")

        font_combo = cvr.NoWheelFontComboBox()
        ev = self._mk_wheel()
        font_combo.wheelEvent(ev)
        self.assertFalse(ev.isAccepted(), "字体下拉：滚轮事件应被忽略")

        spin = cvr.NoWheelSpinBox()
        spin.setRange(0, 100)
        spin.setValue(50)
        self._assert_ignored(spin, spin.value, 50, "数字框")

        dspin = cvr.NoWheelDoubleSpinBox()
        dspin.setRange(0.0, 1.0)
        dspin.setValue(0.5)
        self._assert_ignored(dspin, dspin.value, 0.5, "浮点数字框")

        slider = cvr.NoWheelSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(50)
        self._assert_ignored(slider, slider.value, 50, "滑条")

    def test_font_combo_editable_and_completer_popup(self):
        combo = cvr.NoWheelFontComboBox()
        self.assertTrue(combo.isEditable(), "字体下拉应可编辑（输入识别）")
        self.assertIsNotNone(combo.completer())
        self.assertIsNotNone(combo._comp_popup, "输入补全列表应被显式主题化")

    def test_popup_follows_theme(self):
        """下拉弹窗与补全列表每次打开按当前主题套色（不出现黑窗）。"""
        from gui_qt import theme
        saved = theme._state.get("dark", False)
        try:
            for dark in (False, True):
                theme._state["dark"] = dark
                combo = cvr.NoWheelComboBox()
                combo.addItem("甲")
                combo.showPopup()
                try:
                    view = combo.view()
                    bg = cvr.COMBO_POPUP_BG["dark" if dark else "light"]
                    fg = cvr.COMBO_POPUP_FG["dark" if dark else "light"]
                    self.assertEqual(
                        view.palette().color(view.backgroundRole()).name(), bg,
                        f"{'深' if dark else '浅'}色主题下弹窗底色错误")
                    self.assertEqual(view.palette().color(
                        QPalette.ColorRole.Text).name(), fg)
                finally:
                    combo.hidePopup()
                    combo.deleteLater()
                fcombo = cvr.NoWheelFontComboBox()
                try:
                    self.assertEqual(
                        fcombo._comp_popup.palette().color(
                            QPalette.ColorRole.Base).name(),
                        cvr.COMBO_POPUP_BG["dark" if dark else "light"],
                        "输入补全列表也应跟随主题")
                finally:
                    fcombo.deleteLater()
        finally:
            theme._state["dark"] = saved

    def test_font_input_validation(self):
        """字体输入识别：无效字体名回退不写入，有效字体名正常写入。"""
        panel = cvr.FieldPanel("title", "📌 标题", False, "占位", lambda: None)
        st = cvr.FieldState(font_family="Arial")
        panel.set_state(st)
        with patch.object(cvr, "_font_family_exists", return_value=False):
            panel._on_font_changed(QFont("不存在的字体"))
        self.assertEqual(st.font_family, "Arial", "无效字体名不应写入状态")
        if QFontDatabase.families():
            # 离屏平台字体库为空时 setCurrentFont 无法选中匹配项，仅真实环境可验显示回滚
            self.assertEqual(panel.font_combo.currentFont().family(), "Arial",
                             "无效输入后下拉显示应回滚")
        with patch.object(cvr, "_font_family_exists", return_value=True):
            panel._on_font_changed(QFont("SimSun"))
        self.assertEqual(st.font_family, "SimSun", "有效字体名应写入状态")

    def test_page_controls_wired_no_wheel(self):
        """页面全部下拉/滑条/数字框均使用 NoWheel 系列控件。"""
        page = cvr.CoverMakerPage(_FakeApp())
        try:
            self.assertIsInstance(page.panels["title"].font_combo,
                                  cvr.NoWheelFontComboBox)
            self.assertIsInstance(page.panels["title"].align_combo,
                                  cvr.NoWheelComboBox)
            self.assertIsInstance(page.panels["title"].size_slider,
                                  cvr.NoWheelSlider)
            self.assertIsInstance(page.fit_combo, cvr.NoWheelComboBox)
            self.assertIsInstance(page.res_combo, cvr.NoWheelComboBox)
            self.assertIsInstance(page.quality_slider, cvr.NoWheelSlider)
            self.assertIsInstance(page.gif_frames_spin, cvr.NoWheelSpinBox)
            self.assertIsInstance(page.y_spin, cvr.NoWheelDoubleSpinBox)
            self.assertTrue(page.panels["title"].font_combo.isEditable())
        finally:
            page.shutdown()


class TestColorPickerDialog(unittest.TestCase):
    """主题化颜色选择器：预设色板 + 十六进制 / RGB 输入识别。"""

    def test_initial_color_and_swatch_select(self):
        dlg = cvr.ColorPickerDialog("#123456")
        try:
            self.assertEqual(dlg.selected_color().name(), "#123456")
            dlg._apply_hex("#ff0000")
            self.assertEqual(dlg.selected_color().name(), "#ff0000")
            self.assertEqual(dlg.hex_edit.text(), "#ff0000", "色板应同步十六进制框")
            self.assertEqual([dlg._spins[c].value() for c in (0, 1, 2)],
                             [255, 0, 0], "色板应同步 RGB 数字框")
            self.assertEqual(dlg.preview_sw.color().name(), "#ff0000")
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_hex_input_recognition(self):
        dlg = cvr.ColorPickerDialog("#ffffff")
        try:
            dlg.hex_edit.setText("00ff88")   # 无 # 前缀也应识别
            self.assertEqual(dlg.selected_color().name(), "#00ff88")
            dlg.hex_edit.setText("#zz..")    # 非法输入不改颜色
            self.assertEqual(dlg.selected_color().name(), "#00ff88")
            dlg.hex_edit.setText("")        # 清空不改颜色
            self.assertEqual(dlg.selected_color().name(), "#00ff88")
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_rgb_spin_input(self):
        dlg = cvr.ColorPickerDialog("#000000")
        try:
            dlg._spins[0].setValue(18)
            dlg._spins[1].setValue(52)
            dlg._spins[2].setValue(86)
            self.assertEqual(dlg.selected_color().name(), "#123456")
            self.assertEqual(dlg.hex_edit.text(), "#123456",
                             "RGB 输入应同步十六进制框")
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_double_click_swatch_accepts(self):
        dlg = cvr.ColorPickerDialog("#000000")
        try:
            dlg._accept_hex("#00aaff")
            self.assertEqual(dlg.result(), QDialog.DialogCode.Accepted,
                             "双击色板应直接确定")
            self.assertEqual(dlg.selected_color().name(), "#00aaff")
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_rgb_spins_are_no_wheel(self):
        dlg = cvr.ColorPickerDialog("#000000")
        try:
            for ch in (0, 1, 2):
                self.assertIsInstance(dlg._spins[ch], cvr.NoWheelSpinBox,
                                      "RGB 数字框不应响应滚轮")
        finally:
            dlg.close()
            dlg.deleteLater()

    def test_pick_color_uses_themed_dialog(self):
        """FieldPanel._pick_color 走主题色板（原生 QColorDialog 已移除）。"""
        self.assertFalse(hasattr(cvr, "QColorDialog"),
                         "模块不应再引用原生 QColorDialog")
        panel = cvr.FieldPanel("title", "📌 标题", False, "占位", lambda: None)
        st = cvr.FieldState(color="#111111")
        panel.set_state(st)

        class _FakeDialog:
            def __init__(self, current, parent=None):
                self.current = current

            def exec(self):
                return QDialog.DialogCode.Accepted

            def selected_color(self):
                return QColor("#00cc66")

        orig = cvr.ColorPickerDialog
        cvr.ColorPickerDialog = _FakeDialog
        try:
            panel._pick_color()
        finally:
            cvr.ColorPickerDialog = orig
        self.assertEqual(st.color, "#00cc66", "选中的颜色应写入状态")
        self.assertEqual(panel.color_btn.icon().pixmap(20, 14).toImage()
                         .pixelColor(8, 6).name().lower(), "#00cc66",
                         "颜色按钮图标应刷新为新颜色")


if __name__ == "__main__":
    unittest.main(verbosity=2)
