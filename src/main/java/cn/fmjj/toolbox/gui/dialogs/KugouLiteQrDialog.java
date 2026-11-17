package cn.fmjj.toolbox.gui.dialogs;

import cn.fmjj.toolbox.engine.music.KugouLiteScraper;
import javafx.application.Platform;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Scene;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.image.Image;
import javafx.scene.image.ImageView;
import javafx.scene.layout.VBox;
import javafx.stage.Modality;
import javafx.stage.Stage;
import javafx.stage.Window;

import java.io.ByteArrayInputStream;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 酷狗概念版扫码登录。
 * 移植自 Python 版 cookie_manager._kugou_lite_qr：
 * v2/qrcode 生成 → 每 2 秒轮询 get_userinfo_qrcode → 成功后写会话文件。
 */
public final class KugouLiteQrDialog {

    private KugouLiteQrDialog() {}

    public static void show(Window owner, Runnable onSuccess) {
        Stage stage = new Stage();
        stage.initOwner(owner);
        stage.initModality(Modality.WINDOW_MODAL);
        stage.setTitle("酷狗概念版扫码登录");

        ImageView qrView = new ImageView();
        qrView.setFitWidth(260);
        qrView.setFitHeight(260);
        Label status = new Label("正在生成二维码…");
        status.setWrapText(true);
        Button close = new Button("关闭");
        close.setOnAction(e -> stage.close());

        VBox box = new VBox(12, qrView, status, close);
        box.setAlignment(Pos.CENTER);
        box.setPadding(new Insets(18));
        stage.setScene(new Scene(box, 340, 390));
        stage.show();

        AtomicBoolean closed = new AtomicBoolean(false);
        stage.setOnHidden(e -> closed.set(true));

        Thread worker = new Thread(() -> {
            KugouLiteScraper client = new KugouLiteScraper();
            try {
                KugouLiteScraper.QrCode qr = client.qrCreate();
                Platform.runLater(() -> {
                    if (qr.image() != null && qr.image().length > 0) {
                        qrView.setImage(new Image(new ByteArrayInputStream(qr.image())));
                    } else {
                        qrView.setImage(QrLoginDialog.renderQr(qr.content()));
                    }
                    status.setText("📱 请用酷狗概念版 APP 扫码");
                });
                while (!closed.get()) {
                    Thread.sleep(2000);
                    if (closed.get()) return;
                    var poll = client.qrPoll(qr.key());
                    Platform.runLater(() -> status.setText(poll.message()));
                    if (poll.status() == 4 && poll.loggedIn()) {
                        Platform.runLater(() -> {
                            status.setText("✅ 登录成功，会话已保存");
                            if (onSuccess != null) onSuccess.run();
                        });
                        Thread.sleep(1200);
                        Platform.runLater(stage::close);
                        return;
                    }
                    if (poll.status() == 0) return;
                }
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
            } catch (Exception e) {
                Platform.runLater(() -> status.setText("❌ " + e.getMessage()));
            }
        }, "kugou-lite-qr-login");
        worker.setDaemon(true);
        worker.start();
    }
}
