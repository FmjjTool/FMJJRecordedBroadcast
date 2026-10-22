package cn.fmjj.toolbox.gui.dialogs;

import cn.fmjj.toolbox.core.bili.BiliAuth;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import com.google.zxing.BarcodeFormat;
import com.google.zxing.MultiFormatWriter;
import com.google.zxing.common.BitMatrix;
import javafx.application.Platform;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Scene;
import javafx.scene.control.Button;
import javafx.scene.control.Label;
import javafx.scene.image.ImageView;
import javafx.scene.image.WritableImage;
import javafx.scene.layout.VBox;
import javafx.scene.paint.Color;
import javafx.stage.Modality;
import javafx.stage.Stage;
import javafx.stage.Window;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * B 站扫码登录对话框。
 * 移植自 Python 版 cookie_manager._bilibili_qr：
 * generate 取二维码 → 每 2 秒轮询 → code=0 时保存 Cookie。
 */
public final class QrLoginDialog {

    private QrLoginDialog() {}

    public static void show(Window owner, Runnable onSuccess) {
        Stage stage = new Stage();
        stage.initOwner(owner);
        stage.initModality(Modality.WINDOW_MODAL);
        stage.setTitle("B站扫码登录");

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
        stage.setScene(new Scene(box, 330, 380));
        stage.show();

        AtomicBoolean closed = new AtomicBoolean(false);
        stage.setOnHidden(e -> closed.set(true));

        Thread worker = new Thread(() -> {
            BiliAuth auth = new BiliAuth();
            try {
                String[] gen = auth.qrGenerate();
                Platform.runLater(() -> {
                    qrView.setImage(renderQr(gen[0]));
                    status.setText("📱 请用哔哩哔哩 APP 扫码");
                });
                while (!closed.get()) {
                    Thread.sleep(2000);
                    if (closed.get()) return;
                    var poll = auth.qrPoll(gen[1]);
                    String msg = poll.message();
                    Platform.runLater(() -> status.setText(msg));
                    if (poll.code() == BiliAuth.QrCode.SUCCESS) {
                        String cookie = BiliAuth.formatCookieHeader(poll.cookies());
                        if (cookie.isBlank()) {
                            Platform.runLater(() -> status.setText("⚠️ 登录成功但未拿到 Cookie，请改用浏览器登录"));
                            return;
                        }
                        CookieStore.set("bilibili", cookie);
                        Platform.runLater(() -> {
                            status.setText("✅ B站登录成功，Cookie 已保存");
                            if (onSuccess != null) onSuccess.run();
                        });
                        Thread.sleep(1200);
                        Platform.runLater(stage::close);
                        return;
                    }
                    if (poll.code() == BiliAuth.QrCode.EXPIRED) return;
                }
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
            } catch (Exception e) {
                Platform.runLater(() -> status.setText("❌ " + e.getMessage()));
            }
        }, "bili-qr-login");
        worker.setDaemon(true);
        worker.start();
    }

    /** URL → QR 位图（zxing）。 */
    public static WritableImage renderQr(String content) {
        try {
            BitMatrix matrix = new MultiFormatWriter().encode(content, BarcodeFormat.QR_CODE, 280, 280);
            WritableImage img = new WritableImage(matrix.getWidth(), matrix.getHeight());
            var pw = img.getPixelWriter();
            for (int y = 0; y < matrix.getHeight(); y++) {
                for (int x = 0; x < matrix.getWidth(); x++) {
                    pw.setColor(x, y, matrix.get(x, y) ? Color.BLACK : Color.WHITE);
                }
            }
            return img;
        } catch (Exception e) {
            return null;
        }
    }
}
