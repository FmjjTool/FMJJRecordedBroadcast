package cn.fmjj.toolbox.gui;

import javafx.scene.Scene;

import java.util.Objects;

/**
 * 主题：浅色 / 深色两套 QSS，对应 Python 版 theme.py 的手动切换部分
 * （跟随系统 / 定时自动切换在 M6 里程碑补齐）。
 */
public final class Theme {

    private static final String LIGHT = "/cn/fmjj/toolbox/gui/light.css";
    private static final String DARK = "/cn/fmjj/toolbox/gui/dark.css";

    private static boolean dark = false;

    private Theme() {}

    public static boolean isDark() { return dark; }

    /** 应用当前主题到 Scene（幂等）。 */
    public static void apply(Scene scene) {
        Objects.requireNonNull(scene, "scene");
        scene.getStylesheets().setAll(dark ? DARK : LIGHT);
    }

    /** 切换深浅色并返回新状态。 */
    public static boolean toggle(Scene scene) {
        dark = !dark;
        apply(scene);
        return dark;
    }
}
