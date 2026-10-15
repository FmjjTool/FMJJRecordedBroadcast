package cn.fmjj.toolbox.core;

import java.io.IOException;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;

/**
 * 轻量日志：控制台 + logs/app_yyyyMMdd.log 双写，进程内同步。
 * 对应 Python 版 app_logger.py 的最小子集（轮转/Stdout Tee 后续里程碑补齐）。
 */
public final class Log {

    private static final DateTimeFormatter TS = DateTimeFormatter.ofPattern("HH:mm:ss.SSS");
    private static final DateTimeFormatter DAY = DateTimeFormatter.ofPattern("yyyyMMdd");

    private static volatile boolean consoleEcho = true;
    private static Path logFile;

    private Log() {}

    public static synchronized void setup() {
        try {
            Path dir = AppPaths.logsDir();
            logFile = dir.resolve("app_" + LocalDate.now().format(DAY) + ".log");
        } catch (Exception ignored) {
            logFile = null;
        }
    }

    public static void info(String msg) { write("INFO", msg, null); }

    public static void warn(String msg) { write("WARN", msg, null); }

    public static void error(String msg) { write("ERROR", msg, null); }

    public static void error(String msg, Throwable t) { write("ERROR", msg, t); }

    private static void write(String level, String msg, Throwable t) {
        String line = String.format("%s [%s] %s",
                LocalDateTime.now().format(TS), level, msg == null ? "" : msg);
        if (consoleEcho) {
            System.out.println(line);
            if (t != null) t.printStackTrace();
        }
        if (logFile == null) return;
        try {
            StringBuilder sb = new StringBuilder(line).append(System.lineSeparator());
            if (t != null) {
                StringWriter sw = new StringWriter();
                t.printStackTrace(new PrintWriter(sw));
                sb.append(sw).append(System.lineSeparator());
            }
            Files.writeString(logFile, sb.toString(), StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException ignored) {
            // 日志写失败不阻断业务
        }
    }

    public static void setConsoleEcho(boolean on) { consoleEcho = on; }
}
