package cn.fmjj.toolbox.gui.dialogs;

import cn.fmjj.toolbox.engine.auth.CookieStore;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Scene;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.Label;
import javafx.scene.control.ScrollPane;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.Region;
import javafx.scene.layout.VBox;
import javafx.stage.Modality;
import javafx.stage.Stage;
import javafx.stage.Window;

import java.util.Map;

/**
 * Cookie 管理对话框（12 平台）。
 * 对应 Python 版 cookie_manager 主界面：总开关、已登录计数、扫码/浏览器登录/清除。
 *
 * 说明：Python 的扫码登录覆盖 B站/网易云/QQ/酷狗概念版；M3 先落地 B站（完整链路），
 * 其余扫码在 M6/M7 随各平台 API 一起补；所有平台都有「浏览器登录」兜底。
 */
public final class CookieManagerDialog {

    private final Window owner;
    private final VBox listBox = new VBox(4);
    private final Label summary = new Label();
    private Runnable onChanged;

    private CookieManagerDialog(Window owner) { this.owner = owner; }

    public static void show(Window owner, Runnable onChanged) {
        CookieManagerDialog dialog = new CookieManagerDialog(owner);
        dialog.onChanged = onChanged;
        dialog.open();
    }

    private void open() {
        Stage stage = new Stage();
        stage.initOwner(owner);
        stage.initModality(Modality.WINDOW_MODAL);
        stage.setTitle("🍪 Cookie 管理");

        CheckBox enabled = new CheckBox("启用 Cookie（总开关）");
        enabled.setSelected(CookieStore.isEnabled());
        enabled.setOnAction(e -> {
            CookieStore.setEnabled(enabled.isSelected());
            refresh();
            fireChanged();
        });
        Button refresh = new Button("🔄 刷新");
        refresh.setOnAction(e -> refresh());
        Button close = new Button("关闭");
        close.setOnAction(e -> stage.close());

        HBox top = new HBox(10, enabled, refresh, summary);
        top.setAlignment(Pos.CENTER_LEFT);
        top.setPadding(new Insets(10));
        HBox.setHgrow(summary, Priority.ALWAYS);

        ScrollPane scroll = new ScrollPane(listBox);
        scroll.setFitToWidth(true);
        scroll.setPadding(new Insets(0, 10, 0, 10));

        HBox bottom = new HBox(close);
        bottom.setAlignment(Pos.CENTER_RIGHT);
        bottom.setPadding(new Insets(10));

        VBox root = new VBox(top, scroll, bottom);
        VBox.setVgrow(scroll, Priority.ALWAYS);
        root.setPadding(new Insets(6));

        refresh();
        stage.setScene(new Scene(root, 760, 520));
        stage.show();
    }

    private void fireChanged() {
        if (onChanged != null) onChanged.run();
    }

    private void refresh() {
        Map<String, String> cookies = CookieStore.loadCookies();
        Map<String, String> times = CookieStore.loadTimes();
        long loggedIn = CookieStore.PLATFORM_NAMES.keySet().stream()
                .filter(p -> !cookies.getOrDefault(p, "").isEmpty()).count();
        summary.setText("已登录 " + loggedIn + " / " + CookieStore.PLATFORM_NAMES.size() + " 个平台");

        listBox.getChildren().clear();
        for (Map.Entry<String, String> entry : CookieStore.PLATFORM_NAMES.entrySet()) {
            String key = entry.getKey();
            String name = entry.getValue();
            String cookie = cookies.getOrDefault(key, "");
            boolean has = !cookie.isEmpty();
            boolean expired = has && CookieStore.expired(key);

            Label nameLabel = new Label(name);
            nameLabel.setPrefWidth(96);
            nameLabel.setStyle("-fx-font-weight: bold;");

            Label statusLabel = new Label(has ? (expired ? "⚠️ 已过期" : "✅ 已登录") : "⬜ 未登录");
            statusLabel.setPrefWidth(96);
            statusLabel.setStyle(has ? (expired ? "-fx-text-fill: #b45309;" : "-fx-text-fill: #15803d;")
                    : "-fx-text-fill: #9ca3af;");

            Label timeLabel = new Label(times.getOrDefault(key, "—"));
            timeLabel.setPrefWidth(150);
            timeLabel.setStyle("-fx-text-fill: #6b7280; -fx-font-size: 11px;");

            Region spacer = new Region();
            HBox.setHgrow(spacer, Priority.ALWAYS);

            HBox row = new HBox(8, nameLabel, statusLabel, timeLabel, spacer);
            row.setAlignment(Pos.CENTER_LEFT);
            row.setPadding(new Insets(6, 8, 6, 8));
            row.getStyleClass().add("cookie-row");

            if ("bilibili".equals(key)) {
                Button qr = new Button("扫码登录");
                qr.setOnAction(e -> QrLoginDialog.show(owner, () -> {
                    refresh();
                    fireChanged();
                }));
                row.getChildren().add(qr);
            }
            if (CookieStore.BROWSER_LOGIN.containsKey(key)) {
                Button browser = new Button("浏览器/粘贴");
                browser.setOnAction(e -> BrowserLoginDialog.show(owner, key, () -> {
                    refresh();
                    fireChanged();
                }));
                row.getChildren().add(browser);
            }
            if (has) {
                Button clear = new Button("清除");
                clear.setOnAction(e -> {
                    CookieStore.clear(key);
                    refresh();
                    fireChanged();
                });
                row.getChildren().add(clear);
            }
            listBox.getChildren().add(row);
        }
    }
}
