package cn.fmjj.toolbox.gui.pages;

import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Label;
import javafx.scene.layout.VBox;

/**
 * 占位页：对应 Python 版已上线、但 Java 移植尚未覆盖的 33 个页面。
 * 逐个替换顺序见 {@code docs/JAVA_PORT_PLAN.md}。
 */
public class StubPage extends BasePage {

    private final String label;

    public StubPage(String label) {
        this.label = label;
    }

    @Override
    public String title() { return label; }

    @Override
    protected Node build() {
        Label emoji = new Label("🚧");
        emoji.setStyle("-fx-font-size: 42px;");
        Label title = new Label(label + " 尚未移植到 Java 版");
        title.getStyleClass().add("page-title");
        Label desc = new Label("""
                该功能在 Python 原版（PySide6）中已上线，Java 全栈移植进行中。

                本页为导航占位：架构（懒加载 / 生命周期钩子 / 主题）与已移植页面
                均已就绪，替换为本功能的真实实现即可，无需改动主窗口。

                移植顺序与每个页面的对应引擎模块，见 java/docs/JAVA_PORT_PLAN.md。""");
        desc.setWrapText(true);
        desc.setMaxWidth(520);
        desc.getStyleClass().add("stub-desc");

        VBox box = new VBox(12, emoji, title, desc);
        box.setAlignment(Pos.TOP_CENTER);
        box.setPadding(new Insets(48));
        box.getStyleClass().add("stub-root");
        return box;
    }
}
