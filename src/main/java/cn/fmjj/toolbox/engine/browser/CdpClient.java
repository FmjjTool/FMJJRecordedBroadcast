package cn.fmjj.toolbox.engine.browser;

import cn.fmjj.toolbox.core.Log;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.File;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 极简 Chrome DevTools Protocol 客户端（启动 Edge/Chrome + 执行页面 JS）。
 *
 * <p>用途：抖音这类必须在**页面上下文**里执行签名的场景（`byted_acrawler.frontiersign`），
 * 纯 HTTP 伪造不了。用系统 Edge 的 `--headless=new --remote-debugging-port` 起一个无头实例，
 * 连上 CDP 后用 {@code Runtime.evaluate} 跑 JS。
 *
 * <p>比 Playwright 轻得多（不需要额外下载浏览器，直接用系统 Edge）。
 */
public final class CdpClient implements AutoCloseable {

    private static final ObjectMapper JSON = new ObjectMapper();
    private static final AtomicLong ID = new AtomicLong(1);

    private final Process process;
    private final Path userDataDir;
    private final org.java_websocket.client.WebSocketClient ws;
    /** 全局共享：匿名 WS 回调在构造前就要用到它。 */
    private static final Map<Long, CompletableFuture<JsonNode>> pending = new ConcurrentHashMap<>();
    private final String targetId;

    private CdpClient(Process process, Path userDataDir,
                      org.java_websocket.client.WebSocketClient ws, String targetId) {
        this.process = process;
        this.userDataDir = userDataDir;
        this.ws = ws;
        this.targetId = targetId;
    }

    /** 启动无头浏览器并连上第一个页面。 */
    public static CdpClient launch(int port, int timeoutSeconds) throws Exception {
        Path exe = findBrowser();
        if (exe == null) throw new IllegalStateException("未找到 Edge/Chrome（可在设置里指定路径）");
        Path profile = Files.createTempDirectory("fmjj-cdp-");
        List<String> cmd = new ArrayList<>(List.of(
                exe.toString(),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--remote-debugging-port=" + port,
                "--user-data-dir=" + profile,
                "about:blank"));
        Process p = new ProcessBuilder(cmd)
                .redirectErrorStream(true)
                .redirectOutput(ProcessBuilder.Redirect.DISCARD)
                .start();

        // 等端口就绪
        String wsUrl = null;
        long deadline = System.currentTimeMillis() + timeoutSeconds * 1000L;
        while (System.currentTimeMillis() < deadline) {
            try {
                String json = httpGet("http://127.0.0.1:" + port + "/json/list");
                JsonNode arr = JSON.readTree(json);
                if (arr.isArray()) {
                    for (JsonNode t : arr) {
                        // 只认真正的页面：扩展的后台页（chrome-extension://）不能用来跑业务 JS
                        if (!"page".equals(t.path("type").asText(""))) continue;
                        String u = t.path("url").asText("");
                        if (u.startsWith("chrome-extension://") || u.startsWith("devtools://")) continue;
                        wsUrl = t.path("webSocketDebuggerUrl").asText("");
                        if (!wsUrl.isEmpty()) break;
                    }
                    if (wsUrl != null && !wsUrl.isEmpty()) break;
                }
            } catch (Exception ignored) {
            }
            Thread.sleep(250);
        }
        if (wsUrl == null || wsUrl.isEmpty()) {
            p.destroyForcibly();
            throw new IllegalStateException("无头浏览器未在 " + timeoutSeconds + "s 内就绪（端口 " + port + "）");
        }

        String finalUrl = wsUrl;
        var holder = new org.java_websocket.client.WebSocketClient[1];
        var client = new org.java_websocket.client.WebSocketClient(URI.create(finalUrl)) {
            @Override
            public void onOpen(org.java_websocket.handshake.ServerHandshake handshake) {
            }

            @Override
            public void onMessage(String message) {
                try {
                    JsonNode n = JSON.readTree(message);
                    long id = n.path("id").asLong(0);
                    if (id > 0) {
                        CompletableFuture<JsonNode> f = pending.remove(id);
                        if (f != null) f.complete(n);
                    }
                } catch (Exception ignored) {
                }
            }

            @Override
            public void onClose(int code, String reason, boolean remote) {
            }

            @Override
            public void onError(Exception ex) {
            }
        };
        holder[0] = client;
        if (!client.connectBlocking(10, TimeUnit.SECONDS)) {
            p.destroyForcibly();
            throw new IllegalStateException("CDP 连接失败");
        }
        CdpClient cdp = new CdpClient(p, profile, client, wsUrl);
        cdp.send("Page.enable", null);
        cdp.send("Runtime.enable", null);
        return cdp;
    }

    /** 找系统 Edge / Chrome。 */
    public static Path findBrowser() {
        String[] candidates = {
                System.getenv("ProgramFiles(x86)") + "/Microsoft/Edge/Application/msedge.exe",
                System.getenv("ProgramFiles") + "/Microsoft/Edge/Application/msedge.exe",
                System.getenv("ProgramFiles") + "/Google/Chrome/Application/chrome.exe",
                System.getenv("ProgramFiles(x86)") + "/Google/Chrome/Application/chrome.exe",
                System.getenv("LOCALAPPDATA") + "/Google/Chrome/Application/chrome.exe",
        };
        for (String c : candidates) {
            if (c == null) continue;
            File f = new File(c);
            if (f.isFile()) return f.toPath();
        }
        return null;
    }

    /** 发命令（无返回值关心）。 */
    public JsonNode send(String method, JsonNode params) throws Exception {
        return call(method, params, 15);
    }

    /** 发命令并等结果。 */
    public JsonNode call(String method, JsonNode params, int timeoutSeconds) throws Exception {
        long id = ID.getAndIncrement();
        ObjectNode req = JSON.createObjectNode();
        req.put("id", id);
        req.put("method", method);
        if (params != null) req.set("params", params);
        CompletableFuture<JsonNode> f = new CompletableFuture<>();
        pending.put(id, f);
        ws.send(JSON.writeValueAsString(req));
        JsonNode resp = f.get(timeoutSeconds, TimeUnit.SECONDS);
        JsonNode err = resp.path("error");
        if (!err.isMissingNode() && !err.isNull()) {
            throw new IllegalStateException(method + " 失败：" + err.path("message").asText());
        }
        return resp.path("result");
    }

    /** 打开 URL 并等 load。 */
    public void navigate(String url) throws Exception {
        ObjectNode p = JSON.createObjectNode();
        p.put("url", url);
        call("Page.navigate", p, 20);
        // 轮询 document.readyState
        long deadline = System.currentTimeMillis() + 15_000;
        while (System.currentTimeMillis() < deadline) {
            try {
                String state = evalString("document.readyState");
                if ("complete".equals(state) || "interactive".equals(state)) return;
            } catch (Exception ignored) {
            }
            Thread.sleep(200);
        }
    }

    /** 执行 JS（可返回对象），等 Promise。 */
    public JsonNode evalJson(String expression) throws Exception {
        ObjectNode p = JSON.createObjectNode();
        p.put("expression", expression);
        p.put("awaitPromise", true);
        p.put("returnByValue", true);
        JsonNode result = call("Runtime.evaluate", p, 30);
        JsonNode ex = result.path("exceptionDetails");
        if (!ex.isMissingNode() && !ex.isNull()) {
            throw new IllegalStateException("JS 异常：" + ex.path("text").asText("")
                    + " " + ex.path("exception").path("description").asText(""));
        }
        return result.path("result").path("value");
    }

    /** 执行 JS 返回字符串。 */
    public String evalString(String expression) throws Exception {
        JsonNode v = evalJson(expression);
        return v.isMissingNode() || v.isNull() ? "" : v.asText("");
    }

    /** 页面当前 URL。 */
    public String currentUrl() throws Exception {
        return evalString("location.href");
    }

    /** 页面 cookie（`k=v; k=v`）。 */
    public String cookies() throws Exception {
        return evalString("document.cookie");
    }

    @Override
    public void close() {
        try {
            ws.closeBlocking();
        } catch (Exception ignored) {
        }
        try {
            process.destroy();
            if (!process.waitFor(3, TimeUnit.SECONDS)) process.destroyForcibly();
        } catch (Exception ignored) {
        }
        try {
            deleteTree(userDataDir);
        } catch (Exception e) {
            Log.warn("清理 CDP 临时目录失败：" + e.getMessage());
        }
    }

    private static void deleteTree(Path dir) throws Exception {
        if (dir == null || !Files.exists(dir)) return;
        try (var s = Files.walk(dir)) {
            s.sorted(java.util.Comparator.reverseOrder()).forEach(p -> {
                try {
                    Files.deleteIfExists(p);
                } catch (Exception ignored) {
                }
            });
        }
    }

    private static String httpGet(String url) throws Exception {
        var client = HttpClient.newBuilder().connectTimeout(java.time.Duration.ofSeconds(3)).build();
        var req = HttpRequest.newBuilder(URI.create(url)).timeout(java.time.Duration.ofSeconds(5)).GET().build();
        return client.send(req, HttpResponse.BodyHandlers.ofString()).body();
    }
}
