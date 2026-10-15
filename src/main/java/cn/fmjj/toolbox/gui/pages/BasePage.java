package cn.fmjj.toolbox.gui.pages;

import javafx.scene.Node;

import java.nio.file.Path;
import java.util.List;

/**
 * 页面基类。对应 Python 版 BasePage 的生命周期钩子（main_window 反射调用）：
 * on_show / on_hide / shutdown / on_return_press / on_escape_press /
 * on_dir_changed / add_dropped_files。
 *
 * 约定：content() 返回的节点树在首次调用时构建并缓存（懒加载），
 * 之后切换页面不重复构建，与 Python 侧「懒加载 + 缓存实例」一致。
 */
public abstract class BasePage {

    private Node builtContent;

    /** 页面标题（导航下方显示，如「📝 字幕转换」）。 */
    public abstract String title();

    /** 构建页面内容（只调用一次）。 */
    protected abstract Node build();

    /** 页面内容根节点（懒构建 + 缓存）。 */
    public final Node content() {
        if (builtContent == null) builtContent = build();
        return builtContent;
    }

    // ==================== 生命周期钩子（默认空实现） ====================

    /** 每次切换到该页。 */
    public void onShow() {}

    /** 每次切走。 */
    public void onHide() {}

    /** 软件退出时（优先于 on_hide）。 */
    public void shutdown() {}

    /** 主窗口注入右下角提示出口（页面构建时主窗口已存在）。 */
    public void bindToast(java.util.function.Consumer<String> toastSink) {}

    /** Enter 键分发（输入控件聚焦时主窗口不拦截）。 */
    public void onReturnPressed() {}

    /** Esc 键分发。 */
    public void onEscapePressed() {}

    /** 全局工作目录变更广播。 */
    public void onDirChanged(Path dir) {}

    /** 拖放路由：文件列表。 */
    public void onDroppedFiles(List<Path> files) {}
}
