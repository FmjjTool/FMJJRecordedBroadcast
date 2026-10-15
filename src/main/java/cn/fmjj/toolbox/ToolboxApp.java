package cn.fmjj.toolbox;

import cn.fmjj.toolbox.core.AppPaths;
import cn.fmjj.toolbox.core.Log;
import cn.fmjj.toolbox.gui.MainWindow;
import javafx.application.Application;
import javafx.scene.Scene;
import javafx.stage.Stage;

/**
 * 入口。对应 Python 版 main.py 的初始化序列：
 * 日志初始化 → 目录初始化 → 主窗口 → 主题应用 → 显示。
 */
public class ToolboxApp extends Application {

    @Override
    public void start(Stage stage) {
        Log.setup();
        AppPaths.ensureDirs();
        Log.info("FengMingToolbox Java 2.9.1 启动");

        MainWindow window = new MainWindow(stage);
        Scene scene = new Scene(window.root(), 1240, 800);
        window.attachScene(scene);
        stage.setScene(scene);
        stage.setTitle("封茗囧菌数据汇总库 · 统一维护工具箱 Java 2.9.1");
        stage.show();
    }

    public static void main(String[] args) {
        launch(args);
    }
}
