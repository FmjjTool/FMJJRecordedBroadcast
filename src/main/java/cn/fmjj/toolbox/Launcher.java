package cn.fmjj.toolbox;

import javafx.application.Application;

/**
 * 非 Application 主类（jpackage / 普通 java -cp 启动用）。
 *
 * Java 启动器对「主类继承 javafx.application.Application」的 classpath 启动会检查
 * javafx.graphics 模块；用这个中间类转一手即可正常启动，也是打包的标准做法。
 */
public final class Launcher {
    public static void main(String[] args) {
        Application.launch(ToolboxApp.class, args);
    }
}
