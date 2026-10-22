package cn.fmjj.toolbox.gui.dialogs;

import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.gui.Toast;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Scene;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.TextArea;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.Modality;
import javafx.stage.Stage;
import javafx.stage.Window;

/**
 * 登录对话框：系统浏览器打开登录页 + 粘贴 Cookie 保存。
 *
 * <p>与 Python 版的差异：原版用 Playwright 内嵌 Edge/Chrome 自动捕获全量 Cookie；
 * Java 版当前 JDK 27 已移除 jdk.jsobject，JavaFX WebView 不可用，故退化为
 * 「系统浏览器登录 → 复制 Cookie → 粘贴保存」。M5 引入 playwright-java 后
 * 升级为自动捕获（与原版一致）。
 */
public final class BrowserLoginDialog {

    private BrowserLoginDialog() {}

    public static void show(Window owner, String platform, Runnable onSaved) {
        CookieStore.LoginConfig cfg = CookieStore.BROWSER_LOGIN.get(platform);
        if (cfg == null) return;

        Stage stage = new Stage();
        stage.initOwner(owner);
        stage.initModality(Modality.WINDOW_MODAL);
        stage.setTitle(CookieStore.displayName(platform) + " 登录");

        Label hint = new Label("步骤：① 点「打开登录页」在系统浏览器完成登录 ② 复制 Cookie（F12 → "
                + "Network → 任一请求的 Cookie 头，或浏览器扩展）③ 粘贴到下方 → 保存");
        hint.setWrapText(true);

        Label tip = new Label(cfg.hint().isEmpty() ? "" : "提示：" + cfg.hint());
        tip.setWrapText(true);

        TextArea cookieArea = new TextArea();
        cookieArea.setPromptText("在此粘贴 Cookie，形如：SESSDATA=xxx; bili_jct=yyy; DedeUserID=123");
        cookieArea.setPrefRowCount(8);

        Button openBrowser = new Button("🌐 打开登录页");
        openBrowser.setOnAction(e -> {
            try {
                java.awt.Desktop.getDesktop().browse(java.net.URI.create(cfg.url()));
            } catch (Exception ex) {
                // 无桌面环境时给出手动地址
                cookieArea.setText(cfg.url() + "\n" + cookieArea.getText());
            }
        });

        Label status = new Label();
        status.setWrapText(true);

        Button save = new Button("💾 保存 Cookie");
        save.setDefaultButton(true);
        save.setOnAction(e -> {
            String cookie = cookieArea.getText() == null ? "" : cookieArea.getText().trim()
                    .replace("\r", "").replace("\n", " ");
            if (cookie.isBlank()) {
                status.setText("❌ 请先粘贴 Cookie");
                return;
            }
            boolean hasMarker = false;
            for (String marker : cfg.markers()) {
                if (cookie.contains(marker + "=")) { hasMarker = true; break; }
            }
            if (!hasMarker) {
                status.setText("❌ 未检测到登录态（需要包含 " + String.join(" / ", cfg.markers()) + "）");
                return;
            }
            CookieStore.set(platform, cookie);
            Toast.show(owner, CookieStore.displayName(platform) + " 登录态已保存");
            if (onSaved != null) onSaved.run();
            stage.close();
        });
        Button close = new Button("关闭");
        close.setOnAction(e -> stage.close());

        HBox buttons = new HBox(10, openBrowser, save, close, status);
        buttons.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        VBox root = new VBox(10, hint, tip, cookieArea, buttons);
        root.setPadding(new Insets(14));
        VBox.setVgrow(cookieArea, Priority.ALWAYS);
        stage.setScene(new Scene(root, 720, 420));
        stage.show();
    }
}
