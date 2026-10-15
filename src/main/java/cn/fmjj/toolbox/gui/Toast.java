package cn.fmjj.toolbox.gui;

import javafx.animation.FadeTransition;
import javafx.animation.PauseTransition;
import javafx.animation.SequentialTransition;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.control.Label;
import javafx.scene.layout.StackPane;
import javafx.stage.Popup;
import javafx.stage.Window;
import javafx.util.Duration;

/**
 * 右下角淡入淡出 Toast。对应 Python 版 toast.py。
 */
public final class Toast {

    private Toast() {}

    public static void show(Window owner, String message) {
        if (owner == null) return;
        Label label = new Label(message);
        label.setPadding(new Insets(8, 14, 8, 14));
        label.setStyle("-fx-background-color: rgba(30,30,30,0.88); -fx-text-fill: white;"
                + " -fx-background-radius: 6; -fx-font-size: 13px;");

        StackPane stack = new StackPane(label);
        stack.setPickOnBounds(false);
        Popup popup = new Popup();
        popup.getContent().add(stack);
        popup.setAutoFix(true);

        double x = owner.getX() + owner.getWidth() - 320;
        double y = owner.getY() + owner.getHeight() - 110;
        popup.show(owner, x, y);

        FadeTransition fadeIn = new FadeTransition(Duration.millis(220), stack);
        fadeIn.setFromValue(0);
        fadeIn.setToValue(1);
        PauseTransition stay = new PauseTransition(Duration.seconds(2.2));
        FadeTransition fadeOut = new FadeTransition(Duration.millis(320), stack);
        fadeOut.setFromValue(1);
        fadeOut.setToValue(0);
        SequentialTransition seq = new SequentialTransition(fadeIn, stay, fadeOut);
        seq.setOnFinished(e -> popup.hide());
        seq.play();
    }
}
