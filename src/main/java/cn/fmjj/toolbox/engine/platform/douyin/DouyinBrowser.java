package cn.fmjj.toolbox.engine.platform.douyin;

import cn.fmjj.toolbox.core.Log;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.TimeUnit;

/**
 * 用系统自带浏览器（Edge / Chrome）的 headless `--dump-dom` 渲染抖音页面。
 *
 * <p>为什么需要：抖音对纯 HTTP 请求返回 72KB 空壳（JS 渲染 + Argus 风控），
 * 分享页 SSR 对真实视频也不返回 `videoInfoRes`。用系统浏览器渲染后 DOM 里
 * 有 `<source src>` 直链、`data-e2e-aweme-id`、标题与作者 —— 无需下载 Chromium，
 * 也不需要 playwright。
 *
 * <p>代价：每次约 3-8 秒（进程启动 + 页面渲染）。
 */
public final class DouyinBrowser {

    private static final String[] CANDIDATES = {
            "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
            "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
            "C:/Program Files/Google/Chrome/Application/chrome.exe",
            "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    };

    private static volatile String cached;

    private DouyinBrowser() {}

    /** 找可用浏览器（Edge 优先，Windows 自带）。 */
    public static String findBrowser() {
        if (cached != null) return cached;
        String env = System.getenv("FMJJ_BROWSER");
        if (env != null && !env.isBlank() && Files.isRegularFile(Path.of(env))) {
            cached = env;
            return cached;
        }
        for (String c : CANDIDATES) {
            if (Files.isRegularFile(Path.of(c))) {
                cached = c;
                return cached;
            }
        }
        cached = "";
        return null;
    }

    public static boolean available() {
        String b = findBrowser();
        return b != null && !b.isEmpty();
    }

    /** 渲染页面并返回 DOM（headless --dump-dom）。 */
    public static String fetchDom(String url, int timeoutSeconds) throws IOException, InterruptedException {
        String browser = findBrowser();
        if (browser == null) throw new IllegalStateException("未找到 Edge/Chrome（抖音解析需要浏览器渲染）");

        Path profile = Files.createTempDirectory("fmjj-dy-");
        List<String> cmd = new ArrayList<>(List.of(
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--mute-audio",
                "--disable-dev-shm-usage",
                "--user-data-dir=" + profile,
                "--virtual-time-budget=5000",
                "--dump-dom",
                url));
        Process p = new ProcessBuilder(cmd).redirectErrorStream(false).start();
        byte[] out;
        try (InputStream in = p.getInputStream()) {
            out = in.readAllBytes();
        }
        boolean finished = p.waitFor(timeoutSeconds, TimeUnit.SECONDS);
        if (!finished) p.destroyForcibly();
        deleteTree(profile);

        String html = new String(out, StandardCharsets.UTF_8);
        if (!finished) throw new IllegalStateException("浏览器渲染超时（" + timeoutSeconds + "s）");
        if (html.length() < 500) throw new IllegalStateException("浏览器未返回页面内容");
        Log.info("抖音页面渲染完成，DOM " + html.length() + " 字符");
        return html;
    }

    private static void deleteTree(Path dir) {
        try (var s = Files.walk(dir)) {
            s.sorted(java.util.Comparator.reverseOrder()).forEach(p -> {
                try {
                    Files.deleteIfExists(p);
                } catch (IOException ignored) {
                }
            });
        } catch (IOException ignored) {
        }
    }
}
