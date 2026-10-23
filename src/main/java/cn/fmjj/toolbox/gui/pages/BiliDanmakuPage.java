package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.platform.bili.BiliLiveClient;
import javafx.application.Platform;
import javafx.collections.FXCollections;
import javafx.collections.ObservableList;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.control.ListView;
import javafx.scene.control.TextField;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;

import java.util.function.Consumer;

/**
 * 💬 B站弹幕姬（对齐 Python 版 bili_danmaku.py + toolbox/bili_live.py 的 WS 链路）。
 *
 * <p>顶部：房间号 + `🔌 连接` / `⏹ 断开` + 状态；主体：实时弹幕列表（最多保留 500 条）；
 * 底部：房间信息（标题 / 在线人数 / 主播）。
 *
 * <p>**鉴权要点（血泪）**：取 token（getDanmuInfo）与 WS 握手的 Cookie 必须一致，
 * 且必须是完整设备 Cookie（`buvid3 + buvid4 + b_nut + bili_ticket`）；
 * 只带 buvid3 拿到的 token 会在握手时被 1006 断开。
 */
public class BiliDanmakuPage extends BasePage {

    private final TextField roomField = new TextField("6");
    private final Button connectBtn = new Button("🔌 连接");
    private final Button stopBtn = new Button("⏹ 断开");
    private final Label status = new Label("就绪");
    private final Label roomInfo = new Label("");
    private final ListView<String> danmakuList = new ListView<>();
    private final ObservableList<String> items = FXCollections.observableArrayList();

    private final BiliLiveClient client = new BiliLiveClient();
    private boolean connected;

    private Consumer<String> toast;

    @Override
    public String title() { return "💬 B站弹幕"; }

    @Override
    public void bindToast(Consumer<String> sink) { this.toast = sink; }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    @Override
    protected Node build() {
        Label pageTitle = new Label("💬 B站弹幕姬");
        pageTitle.setStyle("-fx-font-size: 18px; -fx-font-weight: bold;");

        roomField.setPromptText("房间号（如 6 / 7734200）");
        roomField.setPrefWidth(180);
        roomField.setOnAction(e -> connect());
        connectBtn.setStyle("-fx-background-color: #2e7d32; -fx-text-fill: white;");
        connectBtn.setOnAction(e -> connect());
        stopBtn.setDisable(true);
        stopBtn.setOnAction(e -> disconnect());
        Button clear = new Button("🧹 清空");
        clear.setOnAction(e -> items.clear());
        status.setStyle("-fx-text-fill: #888;");

        HBox bar = new HBox(6, new Label("房间号"), roomField, connectBtn, stopBtn, clear,
                spacer(), status);
        bar.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        roomInfo.setStyle("-fx-text-fill: #6b7280;");
        roomInfo.setWrapText(true);

        danmakuList.setItems(items);
        danmakuList.setPlaceholder(new Label("连接后实时显示弹幕 / 礼物 / 进场 / SC"));
        VBox.setVgrow(danmakuList, Priority.ALWAYS);

        Label hint = new Label("鉴权要点：取 token 与 WS 握手必须用同一份完整设备 Cookie"
                + "（buvid3 + buvid4 + b_nut + bili_ticket）；只带 buvid3 会被 1006 断开。");
        hint.setWrapText(true);
        hint.setStyle("-fx-font-size: 11px; -fx-text-fill: #8a93a5;");

        VBox root = new VBox(6, pageTitle, bar, roomInfo, danmakuList, hint);
        root.getStyleClass().add("page-wrapper");
        root.setPadding(new Insets(10));
        VBox.setVgrow(danmakuList, Priority.ALWAYS);
        return root;
    }

    private static Node spacer() {
        HBox h = new HBox();
        HBox.setHgrow(h, Priority.ALWAYS);
        return h;
    }

    private void append(String line) {
        Platform.runLater(() -> {
            items.add(line);
            if (items.size() > 500) items.remove(0, items.size() - 500);
            danmakuList.scrollTo(items.size() - 1);
        });
    }

    // ==================== 连接 ====================

    private void connect() {
        if (connected) return;
        String room = roomField.getText() == null ? "" : roomField.getText().trim();
        if (room.isEmpty()) { status.setText("请输入房间号"); return; }
        connectBtn.setDisable(true);
        status.setText("连接中…");

        javafx.concurrent.Task<long[]> task = new javafx.concurrent.Task<>() {
            @Override
            protected long[] call() throws Exception {
                var info = client.roomInfo(room);
                Platform.runLater(() -> roomInfo.setText("📺 " + info.title()
                        + "　在线 " + info.online() + "　" + (("1".equals(info.liveStatus())) ? "🔴 直播中" : "⚪ 未开播")));
                return new long[] {info.realRoomId() > 0 ? info.realRoomId() : Long.parseLong(room)};
            }
        };
        task.setOnSucceeded(e -> {
            long real = task.getValue()[0];
            try {
                String dev = client.guestCookie();
                String buvid = "";
                for (String p : dev.split(";")) {
                    String t = p.trim();
                    if (t.startsWith("buvid3=")) buvid = t.substring("buvid3=".length());
                }
                var auth = client.danmakuAuth(String.valueOf(real), CookieStore.get("bilibili"), buvid, dev);
                client.connect(real, auth.token(), auth.host(), buvid, dev, new BiliLiveClient.Listener() {
                    @Override
                    public void onStatus(String m) {
                        Platform.runLater(() -> status.setText(m));
                        append("── " + m);
                    }

                    @Override
                    public void onError(String m) {
                        Platform.runLater(() -> {
                            status.setText("❌ " + m);
                            connectBtn.setDisable(false);
                            stopBtn.setDisable(true);
                            connected = false;
                        });
                        append("❌ " + m);
                    }

                    @Override
                    public void onDanmaku(BiliLiveClient.Danmaku d) {
                        String extra = d.extra() == null || d.extra().isEmpty() ? "" : " " + d.extra();
                        append("[" + d.type() + "] " + d.user() + "：" + d.content() + extra);
                    }
                });
                connected = true;
                connectBtn.setDisable(true);
                stopBtn.setDisable(false);
                status.setText("已连接房间 " + real);
            } catch (Exception ex) {
                connectBtn.setDisable(false);
                status.setText("连接失败：" + ex.getMessage());
                append("❌ 连接失败：" + ex.getMessage());
            }
        });
        task.setOnFailed(e -> {
            connectBtn.setDisable(false);
            status.setText("连接失败：" + task.getException().getMessage());
        });
        new Thread(task, "bili-danmaku-connect").start();
    }

    private void disconnect() {
        client.close();
        connected = false;
        connectBtn.setDisable(false);
        stopBtn.setDisable(true);
        status.setText("已断开");
        append("── 已断开连接");
    }

    @Override
    public void onHide() {
        // 切页时断开，避免后台占用连接
        if (connected) disconnect();
    }
}
