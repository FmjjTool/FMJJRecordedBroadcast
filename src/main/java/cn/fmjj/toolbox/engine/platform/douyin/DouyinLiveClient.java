package cn.fmjj.toolbox.engine.platform.douyin;

import cn.fmjj.toolbox.core.Log;
import cn.fmjj.toolbox.engine.auth.CookieStore;
import cn.fmjj.toolbox.engine.browser.CdpClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.java_websocket.client.WebSocketClient;
import org.java_websocket.handshake.ServerHandshake;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.net.ServerSocket;
import java.net.URI;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.zip.GZIPInputStream;

/**
 * 抖音直播弹幕客户端（对应 Python 版 toolbox/douyin_live.py 的 DouyinLiveFetcher）。
 *
 * <p>链路：room/enter 取 roomId → CDP 无头 Edge 打开直播间 &gt; 等 byted_acrawler →
 * 页面内跑 {@link DouyinLive#prepJs} 拿 cursor/internalExt/pushServer/signature →
 * Java-WebSocket 连 pushServer → protobuf 解码 PushFrame/Response/Message。
 *
 * <p>握手凭证取 CDP 的 {@code Network.getCookies}（ttwid 是 HttpOnly，document.cookie 读不到）。
 * 确认收到服务端推送后才关闭浏览器释放登录 Cookie。
 *
 * <p>回调都在后台线程触发，GUI 调用方需自行切回 UI 线程。
 */
public final class DouyinLiveClient {

    public static final String DEFAULT_UNIQUE_ID = "7673531686768002601";
    private static final int STATUS_LIVE = 2;
    private static final int HEARTBEAT_SECONDS = 10;
    private static final int CONFIRM_TIMEOUT_SECONDS = 20;

    /** Webcast 消息类型 → (中文名, 分类)。分类：chat/gift/member/like/social/other。 */
    private static final Map<String, String[]> MSG_TYPES = new LinkedHashMap<>();

    static {
        MSG_TYPES.put("WebcastChatMessage", new String[]{"弹幕", "chat"});
        MSG_TYPES.put("WebcastGiftMessage", new String[]{"礼物", "gift"});
        MSG_TYPES.put("WebcastBindingGiftMessage", new String[]{"礼物", "gift"});
        MSG_TYPES.put("WebcastMemberMessage", new String[]{"进场", "member"});
        MSG_TYPES.put("WebcastLikeMessage", new String[]{"点赞", "like"});
        MSG_TYPES.put("WebcastSocialMessage", new String[]{"关注", "social"});
        MSG_TYPES.put("WebcastEmojiChatMessage", new String[]{"表情弹幕", "chat"});
        MSG_TYPES.put("WebcastRoomMessage", new String[]{"系统消息", "other"});
        MSG_TYPES.put("WebcastRoomIntroMessage", new String[]{"房间介绍", "other"});
        MSG_TYPES.put("WebcastRoomUserSeqMessage", new String[]{"在线人数", "other"});
        MSG_TYPES.put("WebcastInRoomBannerMessage", new String[]{"横幅", "other"});
        MSG_TYPES.put("WebcastRoomStatsMessage", new String[]{"直播数据", "other"});
        MSG_TYPES.put("WebcastRoomDataSyncMessage", new String[]{"数据同步", "other"});
        MSG_TYPES.put("WebcastRanklistHourEntranceMessage", new String[]{"小时榜", "other"});
        MSG_TYPES.put("WebcastFansclubMessage", new String[]{"粉丝团", "other"});
        MSG_TYPES.put("WebcastRoomRankMessage", new String[]{"排行榜", "other"});
        MSG_TYPES.put("WebcastHotChatMessage", new String[]{"热议", "other"});
        MSG_TYPES.put("WebcastLotteryDrawResultEventMessage", new String[]{"抽奖", "other"});
    }

    private static final Map<String, Integer> USER_FIELD_MAP = Map.of(
            "WebcastGiftMessage", 7,
            "WebcastBindingGiftMessage", 7,
            "WebcastLikeMessage", 5,
            "WebcastChatMessage", 2,
            "WebcastMemberMessage", 2,
            "WebcastSocialMessage", 2,
            "WebcastEmojiChatMessage", 2);

    private static final Set<String> GIFT_TYPES = Set.of(
            "WebcastGiftMessage", "WebcastBindingGiftMessage");

    /** 一条已解码的直播消息（字段对齐 Python extract_msg 输出）。 */
    public record Msg(String type, String name, String category, String nickname, String content,
                      String giftName, long giftPrice, String giftIcon, long giftCount,
                      boolean repeatEnd, long likeCount, long likeTotal, long followCount,
                      long roomTotal, long roomTotalUser, String roomTotalUserStr,
                      long memberCount, long ts) {}

    public interface Listener {
        void onStatus(String state, String msg);

        void onRoomInfo(DouyinLive.RoomInfo info);

        void onMessage(Msg msg);
    }

    record Frame(long logId, String payloadType, byte[] payload, Map<String, String> headers) {}

    record Resp(boolean needAck, String cursor, String internalExt, List<byte[]> messages) {}

    /** 页面自身 im/fetch 响应 body 中的 WS 连接参数（字段号同 Python _parse_im_body）。 */
    record ImBody(String cursor, String internalExt, String pushServer) {}

    private record CapturedResponse(String requestId, String url) {}

    private final DouyinLive live = new DouyinLive();
    private final ObjectMapper json = new ObjectMapper();

    private volatile boolean running;
    private volatile boolean stopped;
    private volatile CdpClient cdp;
    private volatile WebSocketClient ws;
    private volatile ScheduledExecutorService heartbeat;

    public boolean isRunning() {
        return running;
    }

    /** 后台线程启动。重复调用在运行期间会被忽略。 */
    public void startAsync(String webRid, Listener listener) {
        if (running) return;
        running = true;
        stopped = false;
        Thread t = new Thread(() -> {
            try {
                run(webRid, listener);
            } finally {
                running = false;
            }
        }, "douyin-live");
        t.setDaemon(true);
        t.start();
    }

    public void stop() {
        stopped = true;
        shutdownHeartbeat();
        WebSocketClient w = ws;
        if (w != null) {
            try {
                w.closeBlocking();
            } catch (Exception ignored) {
            }
        }
        CdpClient c = cdp;
        if (c != null) {
            c.close();
            cdp = null;
        }
    }

    // ==================== 主流程 ====================

    private void run(String webRid, Listener l) {
        CdpClient browser = null;
        boolean enteredWs = false;
        try {
            emitStatus(l, "connecting", "正在获取房间信息...");
            DouyinLive.RoomInfo info = live.roomInfo(webRid);
            emitRoomInfo(l, info);
            if (info.roomId() <= 0) {
                emitStatus(l, "error", info.error() == null || info.error().isEmpty()
                        ? "未获取到房间信息（直播间可能不存在或已结束）" : info.error());
                return;
            }
            if (info.status() != STATUS_LIVE) {
                emitStatus(l, "not_live", "主播当前未开播（房间 " + webRid + "）");
                return;
            }
            String md5 = DouyinLive.stubMd5(info.roomId(), DEFAULT_UNIQUE_ID);
            emitStatus(l, "connecting", "正在建立弹幕通道（过风控，确认收流后释放 Cookie）...");

            browser = CdpClient.launch(freePort(), 20);
            cdp = browser;
            // 无头 Edge 默认 UA 带 HeadlessChrome、视口 800x600，抖音会因此返回空响应体
            // （页面自己的 im/fetch 也拿不到 cursor）。对齐 Python Playwright 的
            // user_agent + viewport(1920x1080) 后再打开直播间。
            try {
                ObjectNode ua = json.createObjectNode();
                ua.put("userAgent", DouyinLive.UA);
                ua.put("acceptLanguage", "zh-CN,zh;q=0.9");
                ua.put("platform", "Win32");
                browser.call("Emulation.setUserAgentOverride", ua, 10);
                ObjectNode metrics = json.createObjectNode();
                metrics.put("width", 1920);
                metrics.put("height", 1080);
                metrics.put("deviceScaleFactor", 1);
                metrics.put("mobile", false);
                browser.call("Emulation.setDeviceMetricsOverride", metrics, 10);
            } catch (Exception e) {
                Log.warn("设置抖音浏览器指纹失败：" + e.getMessage());
            }
            injectCookies(browser, CookieStore.get("douyin"));
            // 页面自己那条 im/fetch 的签名/did 都是真的，是连接参数的可靠来源；
            // 必须在 Page.navigate 之前布好监听（页面加载后会自动发起）。
            List<CapturedResponse> captures = new CopyOnWriteArrayList<>();
            CdpClient.EventListener capture = (method, params) -> {
                if (!"Network.responseReceived".equals(method)) return;
                JsonNode resp = params.path("response");
                String url = resp.path("url").asText("");
                if (!url.contains("/webcast/im/fetch/")) return;
                Log.info("抖音 im/fetch 响应：status=" + resp.path("status").asInt(0));
                if (resp.path("status").asInt(0) != 200) return;
                String requestId = params.path("requestId").asText("");
                if (!requestId.isEmpty()) captures.add(new CapturedResponse(requestId, url));
            };
            try {
                browser.call("Network.enable", null, 10);
            } catch (Exception ignored) {
            }
            browser.addEventListener(capture);
            // 不等待完整 load：acrawler 轮询兜底（对应 Python wait_until="commit"）
            ObjectNode nav = json.createObjectNode();
            nav.put("url", "https://live.douyin.com/" + webRid);
            browser.send("Page.navigate", nav);
            if (!waitAcrawler(browser, 6000)) {
                emitStatus(l, "captcha", "检测到抖音风控，Java 版暂不支持弹出验证窗口，请稍后重试");
                return;
            }
            DouyinLive.Prep prep = prepFromCapture(browser, captures, info.roomId());
            if (prep == null) prep = prep(browser, info.roomId(), md5);
            browser.removeEventListener(capture);
            if (prep.error() != null && !prep.error().isEmpty()) {
                emitStatus(l, "error", "连接失败: " + prep.error());
                return;
            }
            if (prep.cursor().isEmpty() || prep.pushServer().isEmpty() || prep.signature().isEmpty()) {
                emitStatus(l, "error", "连接参数不完整（可能被抖音风控拦截）");
                return;
            }
            String cred = wsCredentials(browser);
            if (!cred.contains("ttwid=")) {
                emitStatus(l, "error", "未获取到会话凭证（ttwid），无法建立弹幕通道");
                return;
            }
            boolean guest = !cred.contains("sessionid=");
            enteredWs = true;
            wsLoop(prep, info, cred, guest, browser, l);
        } catch (Exception e) {
            emitStatus(l, "error", "抓取异常: " + e.getMessage());
        } finally {
            shutdownHeartbeat();
            WebSocketClient w = ws;
            if (w != null) {
                try {
                    w.closeBlocking();
                } catch (Exception ignored) {
                }
                ws = null;
            }
            if (browser != null) browser.close();
            cdp = null;
            if (enteredWs) {
                emitStatus(l, "disconnected", stopped ? "已停止" : "直播已结束 / 连接断开");
            }
        }
    }

    /** 旧路径兜底：自己拼参数发 im/fetch（风控期会拿到空体）。 */
    private DouyinLive.Prep prep(CdpClient browser, long roomId, String md5) throws Exception {
        JsonNode out = browser.evalJson(DouyinLive.prepJs(roomId, DEFAULT_UNIQUE_ID, md5));
        return new DouyinLive.Prep(text(out, "cursor"), text(out, "internalExt"),
                text(out, "pushServer"), text(out, "signature"),
                DEFAULT_UNIQUE_ID, 1920, 1080, text(out, "error"));
    }

    /**
     * 截页面自身 im/fetch 的响应解析 WS 参数（对应 Python _prep_from_capture）。
     * 用响应里的真实 did 重算 X-Bogus，签名与 user_unique_id 必须同源。
     */
    private DouyinLive.Prep prepFromCapture(CdpClient browser, List<CapturedResponse> captures,
                                            long roomId) {
        String want = "room_id=" + roomId;
        long deadline = System.currentTimeMillis() + 12_000;
        while (System.currentTimeMillis() < deadline && !stopped) {
            CapturedResponse hit = null;
            for (CapturedResponse c : captures) {
                if (c.url().contains(want)) {
                    hit = c;
                    break;
                }
            }
            if (hit != null) {
                try {
                    byte[] body = browser.responseBody(hit.requestId());
                    ImBody im = parseImBody(body);
                    Log.info("抖音 im/fetch 捕获：bodyLen=" + (body == null ? 0 : body.length)
                            + " parsed=" + (im != null));
                    if (im != null) {
                        String did = extractDid(im.internalExt(), hit.url());
                        if (did.isEmpty()) did = DEFAULT_UNIQUE_ID;
                        int sw = 1920;
                        int sh = 1080;
                        String sig = "";
                        try {
                            JsonNode sign = browser.evalJson(
                                    DouyinLive.signJs(DouyinLive.stubMd5(roomId, did)));
                            sig = text(sign, "signature");
                            int w = sign.path("screenWidth").asInt(0);
                            int h = sign.path("screenHeight").asInt(0);
                            if (w > 0) sw = w;
                            if (h > 0) sh = h;
                        } catch (Exception e) {
                            Log.warn("抖音握手签名生成失败：" + e.getMessage());
                        }
                        Log.info("抖音握手参数：didLen=" + did.length() + " sigLen=" + sig.length()
                                + " cursorLen=" + im.cursor().length()
                                + " extLen=" + im.internalExt().length()
                                + " pushServer=" + im.pushServer());
                        if (!sig.isEmpty()) {
                            return new DouyinLive.Prep(im.cursor(), im.internalExt(),
                                    im.pushServer(), sig, did, sw, sh, "");
                        }
                    }
                } catch (Exception e) {
                    Log.warn("解析抖音 im/fetch 捕获响应失败：" + e.getMessage());
                }
            }
            try {
                Thread.sleep(200);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
        }
        return null;
    }

    /** 从 im/fetch 响应体（Response protobuf）取 cursor/internalExt/pushServer。 */
    static ImBody parseImBody(byte[] body) {
        if (body == null || body.length < 32) return null;
        String cursor = null;
        String internalExt = null;
        String pushServer = null;
        try {
            Pb r = new Pb(body);
            while (r.hasMore()) {
                long tag = r.varint();
                int f = (int) (tag >>> 3);
                int w = (int) (tag & 7);
                if (w == 2) {
                    int len = (int) r.varint();
                    if (f == 2) cursor = r.str(len);
                    else if (f == 5) internalExt = r.str(len);
                    else if (f == 10) pushServer = r.str(len);
                    else r.skip(len);
                } else if (w == 0) {
                    r.varint();
                } else if (w == 1) {
                    r.skip(8);
                } else if (w == 5) {
                    r.skip(4);
                } else {
                    break;
                }
            }
        } catch (Exception e) {
            return null;
        }
        if (cursor == null || cursor.isEmpty() || internalExt == null || internalExt.isEmpty()
                || pushServer == null || pushServer.isEmpty()) {
            return null;
        }
        return new ImBody(cursor, internalExt, pushServer);
    }

    /** did 优先取 internalExt 的 wss_push_did，退回请求 URL 的 user_unique_id。 */
    static String extractDid(String internalExt, String url) {
        if (internalExt != null) {
            Matcher m = Pattern.compile("wss_push_did:(\\d+)").matcher(internalExt);
            if (m.find()) return m.group(1);
        }
        if (url != null) {
            int q = url.indexOf('?');
            if (q >= 0) {
                for (String pair : url.substring(q + 1).split("&")) {
                    int eq = pair.indexOf('=');
                    if (eq > 0 && "user_unique_id".equals(pair.substring(0, eq))) {
                        try {
                            return URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8);
                        } catch (Exception ignored) {
                            return pair.substring(eq + 1);
                        }
                    }
                }
            }
        }
        return "";
    }

    private static String text(JsonNode node, String field) {
        JsonNode v = node.path(field);
        return v.isMissingNode() || v.isNull() ? "" : v.asText("");
    }

    private boolean waitAcrawler(CdpClient browser, long timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (System.currentTimeMillis() < deadline && !stopped) {
            try {
                String ok = browser.evalString(
                        "!!(window.byted_acrawler && window.byted_acrawler.frontierSign)");
                if ("true".equals(ok)) return true;
            } catch (Exception ignored) {
            }
            try {
                Thread.sleep(150);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return false;
            }
        }
        return false;
    }

    /** 把 Cookie 管理里保存的抖音 Cookie 注入到无头浏览器（对应 Python ctx.add_cookies）。 */
    private void injectCookies(CdpClient browser, String cookieStr) {
        if (cookieStr == null || cookieStr.isBlank()) return;
        try {
            browser.call("Network.enable", null, 10);
        } catch (Exception ignored) {
        }
        for (String part : cookieStr.split(";")) {
            int eq = part.indexOf('=');
            if (eq <= 0) continue;
            String name = part.substring(0, eq).trim();
            String value = part.substring(eq + 1).trim();
            if (name.isEmpty() || value.isEmpty()) continue;
            ObjectNode p = json.createObjectNode();
            p.put("name", name);
            p.put("value", value);
            p.put("url", "https://live.douyin.com/");
            p.put("path", "/");
            try {
                browser.call("Network.setCookie", p, 10);
            } catch (Exception ignored) {
            }
        }
    }

    /** WS 握手凭证：ttwid（匿名会话，握手强制）+ sessionid（登录凭证，礼物消息需要）。 */
    private String wsCredentials(CdpClient browser) {
        try {
            browser.call("Network.enable", null, 10);
            ObjectNode p = json.createObjectNode();
            p.putArray("urls").add("https://live.douyin.com/");
            JsonNode res = browser.call("Network.getCookies", p, 10);
            String ttwid = null;
            String sessionid = null;
            for (JsonNode c : res.path("cookies")) {
                String n = c.path("name").asText("");
                if ("ttwid".equals(n)) ttwid = c.path("value").asText("");
                else if ("sessionid".equals(n)) sessionid = c.path("value").asText("");
            }
            StringBuilder sb = new StringBuilder();
            if (ttwid != null && !ttwid.isEmpty()) sb.append("ttwid=").append(ttwid);
            if (sessionid != null && !sessionid.isEmpty()) {
                if (sb.length() > 0) sb.append("; ");
                sb.append("sessionid=").append(sessionid);
            }
            return sb.toString();
        } catch (Exception e) {
            Log.warn("取抖音 WS 凭证失败：" + e.getMessage());
            return "";
        }
    }

    // ==================== WebSocket ====================

    private void wsLoop(DouyinLive.Prep prep, DouyinLive.RoomInfo info, String cred,
                        boolean guest, CdpClient browser, Listener l) throws Exception {
        String uniqueId = prep.uniqueId() == null || prep.uniqueId().isBlank()
                ? DEFAULT_UNIQUE_ID : prep.uniqueId();
        int screenW = prep.screenWidth() <= 0 ? 1920 : prep.screenWidth();
        int screenH = prep.screenHeight() <= 0 ? 1080 : prep.screenHeight();
        String wsUrl = buildWsUrl(prep.pushServer(), info.roomId(), uniqueId,
                prep.cursor(), prep.internalExt(), prep.signature(), screenW, screenH);
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("User-Agent", DouyinLive.UA);
        headers.put("Origin", "https://live.douyin.com");
        headers.put("Cookie", cred);

        CountDownLatch wake = new CountDownLatch(1);
        AtomicBoolean gotFrame = new AtomicBoolean(false);
        AtomicBoolean released = new AtomicBoolean(false);
        Runnable release = () -> {
            if (released.compareAndSet(false, true)) browser.close();
        };
        AtomicBoolean firstAck = new AtomicBoolean(false);

        WebSocketClient client = new WebSocketClient(URI.create(wsUrl), headers) {
            @Override
            public void onOpen(ServerHandshake handshake) {
                try {
                    send(heartbeatFrame());
                } catch (Exception ignored) {
                }
            }

            @Override
            public void onMessage(String message) {
                // 弹幕通道只发二进制
            }

            @Override
            public void onMessage(ByteBuffer buf) {
                byte[] raw = new byte[buf.remaining()];
                buf.get(raw);
                gotFrame.set(true);
                wake.countDown();
                handleFrame(raw, this, l);
            }

            @Override
            public void onClose(int code, String reason, boolean remote) {
                wake.countDown();
                if (firstAck.get() && !stopped) {
                    Log.info("抖音弹幕 WS 关闭：" + code + " " + reason);
                }
            }

            @Override
            public void onError(Exception ex) {
                wake.countDown();
                if (!stopped) Log.warn("抖音弹幕 WS 异常：" + ex.getMessage());
            }
        };
        client.setConnectionLostTimeout(0);
        ws = client;
        try {
            if (!client.connectBlocking(10, TimeUnit.SECONDS)) {
                throw new IllegalStateException("弹幕通道连接超时（10s）");
            }
            heartbeat = Executors.newSingleThreadScheduledExecutor(r -> {
                Thread t = new Thread(r, "douyin-live-heartbeat");
                t.setDaemon(true);
                return t;
            });
            heartbeat.scheduleAtFixedRate(() -> {
                if (stopped || !client.isOpen()) return;
                try {
                    client.send(heartbeatFrame());
                } catch (Exception ignored) {
                }
            }, HEARTBEAT_SECONDS, HEARTBEAT_SECONDS, TimeUnit.SECONDS);

            try {
                wake.await(CONFIRM_TIMEOUT_SECONDS, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            if (stopped) return;
            if (!gotFrame.get() || !client.isOpen()) {
                throw new IllegalStateException("弹幕通道未确认收流（20 秒内无服务端推送，可能被风控）");
            }
            firstAck.set(true);
            release.run();
            emitStatus(l, "connected", guest
                    ? "已连接 · 房间 " + info.roomId() + "（游客态：无登录凭证，礼物消息收不到）"
                    : "已连接 · 房间 " + info.roomId() + "（已确认收流，Cookie 已释放）");

            while (!stopped && client.isOpen()) {
                Thread.sleep(300);
            }
        } finally {
            shutdownHeartbeat();
            try {
                client.closeBlocking();
            } catch (Exception ignored) {
            }
            release.run();
            ws = null;
        }
    }

    private void handleFrame(byte[] raw, WebSocketClient client, Listener l) {
        try {
            Frame frame = decodePushFrame(raw);
            byte[] payload = frame.payload();
            Map<String, String> headers = frame.headers();
            if (payload != null && "gzip".equals(headers.get("compress_type"))) {
                try {
                    payload = gunzip(payload);
                } catch (Exception ignored) {
                }
            }
            if (payload == null || payload.length == 0) return;
            String ext = headers.getOrDefault("im-internal_ext", "");
            boolean needAck = false;
            Resp res = null;
            try {
                res = decodeResponse(payload);
                needAck = res.needAck();
                if (res.internalExt() != null && !res.internalExt().isEmpty()) ext = res.internalExt();
            } catch (Exception ignored) {
            }
            if (res != null) {
                for (byte[] m : res.messages()) {
                    try {
                        Msg msg = extractMsg(m);
                        if (msg != null && !msg.type().isEmpty()) l.onMessage(msg);
                    } catch (Exception ignored) {
                    }
                }
            }
            if (needAck) {
                try {
                    client.send(ackFrame(ext, frame.logId()));
                } catch (Exception ignored) {
                }
            }
        } catch (Exception e) {
            Log.warn("抖音弹幕帧解析失败：" + e.getMessage());
        }
    }

    private void emitStatus(Listener l, String state, String msg) {
        try {
            l.onStatus(state, msg);
        } catch (Exception e) {
            Log.warn("抖音弹幕状态回调异常：" + e.getMessage());
        }
    }

    private void emitRoomInfo(Listener l, DouyinLive.RoomInfo info) {
        try {
            l.onRoomInfo(info);
        } catch (Exception e) {
            Log.warn("抖音弹幕房间回调异常：" + e.getMessage());
        }
    }

    private void shutdownHeartbeat() {
        ScheduledExecutorService hb = heartbeat;
        if (hb != null) {
            hb.shutdownNow();
            heartbeat = null;
        }
    }

    private static int freePort() throws IOException {
        try (ServerSocket s = new ServerSocket(0)) {
            return s.getLocalPort();
        }
    }

    // ==================== protobuf ====================

    /** 最小 protobuf 读取器（越界即抛，由上层按帧丢弃）。 */
    static final class Pb {
        private final byte[] b;
        private int p;

        Pb(byte[] b) {
            this.b = b;
        }

        boolean hasMore() {
            return p < b.length;
        }

        long varint() {
            long r = 0;
            int s = 0;
            while (true) {
                if (p >= b.length || s >= 64) throw new IllegalStateException("varint 越界");
                int x = b[p++] & 0xFF;
                r |= (long) (x & 0x7F) << s;
                if ((x & 0x80) == 0) break;
                s += 7;
            }
            return r;
        }

        byte[] take(int len) {
            if (len < 0 || p + len > b.length) throw new IllegalStateException("protobuf 长度越界");
            byte[] d = new byte[len];
            System.arraycopy(b, p, d, 0, len);
            p += len;
            return d;
        }

        String str(int len) {
            return new String(take(len), StandardCharsets.UTF_8);
        }

        void skip(int n) {
            if (n < 0 || p + n > b.length) throw new IllegalStateException("protobuf 跳过越界");
            p += n;
        }
    }

    static Frame decodePushFrame(byte[] b) {
        Pb r = new Pb(b);
        long logId = 0;
        String payloadType = "";
        byte[] payload = null;
        Map<String, String> headers = new LinkedHashMap<>();
        while (r.hasMore()) {
            long tag = r.varint();
            int f = (int) (tag >>> 3);
            int w = (int) (tag & 7);
            if (w == 0) {
                long v = r.varint();
                if (f == 2) logId = v;
            } else if (w == 2) {
                int len = (int) r.varint();
                if (f == 5) {
                    Pb h = new Pb(r.take(len));
                    String key = null;
                    String val = "";
                    while (h.hasMore()) {
                        long t2 = h.varint();
                        int f2 = (int) (t2 >>> 3);
                        int w2 = (int) (t2 & 7);
                        if (w2 != 2) break;
                        int l2 = (int) h.varint();
                        if (f2 == 1) key = h.str(l2);
                        else if (f2 == 2) val = h.str(l2);
                        else h.skip(l2);
                    }
                    if (key != null) headers.put(key, val);
                } else if (f == 7) {
                    payloadType = r.str(len);
                } else if (f == 8) {
                    payload = r.take(len);
                } else {
                    r.skip(len);
                }
            } else if (w == 1) {
                r.skip(8);
            } else if (w == 5) {
                r.skip(4);
            } else {
                break;
            }
        }
        return new Frame(logId, payloadType, payload, headers);
    }

    static Resp decodeResponse(byte[] b) {
        Pb r = new Pb(b);
        boolean needAck = false;
        String cursor = "";
        String internalExt = "";
        List<byte[]> messages = new ArrayList<>();
        while (r.hasMore()) {
            long tag = r.varint();
            int f = (int) (tag >>> 3);
            int w = (int) (tag & 7);
            if (w == 0) {
                long v = r.varint();
                if (f == 9) needAck = v == 1;
            } else if (w == 2) {
                int len = (int) r.varint();
                if (f == 1) messages.add(r.take(len));
                else if (f == 2) cursor = r.str(len);
                else if (f == 5) internalExt = r.str(len);
                else r.skip(len);
            } else if (w == 1) {
                r.skip(8);
            } else if (w == 5) {
                r.skip(4);
            } else {
                break;
            }
        }
        return new Resp(needAck, cursor, internalExt, messages);
    }

    static String parseUser(byte[] b) {
        Pb r = new Pb(b);
        while (r.hasMore()) {
            long tag = r.varint();
            int f = (int) (tag >>> 3);
            int w = (int) (tag & 7);
            if (w == 2) {
                int len = (int) r.varint();
                if (f == 3) return r.str(len);
                r.skip(len);
            } else if (w == 0) {
                r.varint();
            } else if (w == 1) {
                r.skip(8);
            } else if (w == 5) {
                r.skip(4);
            } else {
                break;
            }
        }
        return null;
    }

    private record Gift(String name, long price, String icon) {}

    /** GiftStruct → name(16) / diamondCount(12) / image(1)。 */
    static Gift parseGift(byte[] b) {
        Pb r = new Pb(b);
        String name = null;
        String icon = null;
        long price = -1;
        while (r.hasMore()) {
            long tag = r.varint();
            int f = (int) (tag >>> 3);
            int w = (int) (tag & 7);
            if (w == 0) {
                long v = r.varint();
                if (f == 12) price = v;
            } else if (w == 2) {
                int len = (int) r.varint();
                if (f == 16) name = r.str(len);
                else if (f == 1) icon = parseImageUrl(r.take(len));
                else r.skip(len);
            } else if (w == 1) {
                r.skip(8);
            } else if (w == 5) {
                r.skip(4);
            } else {
                break;
            }
        }
        return new Gift(name, price, icon);
    }

    private static final Pattern IMG_URL_RE = Pattern.compile("https?://[^\\s\"'\\\\]{8,400}");

    /**
     * Image 结构 → URL。兼容 f1=纯 URL（ChatMessage 表情片段）与
     * f1=嵌套 Image{repeated f1=url_list, f2=uri}（EmojiChat/VIP 表情）。
     */
    static String parseImageUrl(byte[] b) {
        try {
            Pb r = new Pb(b);
            while (r.hasMore()) {
                long tag = r.varint();
                int f = (int) (tag >>> 3);
                int w = (int) (tag & 7);
                if (w == 2) {
                    int len = (int) r.varint();
                    byte[] d = r.take(len);
                    if (f != 1) continue;
                    String s = new String(d, StandardCharsets.UTF_8);
                    if (s.startsWith("http")) return s;
                    String nested = firstHttpUrl(d);
                    if (nested != null) return nested;
                } else if (w == 0) {
                    r.varint();
                } else if (w == 1) {
                    r.skip(8);
                } else if (w == 5) {
                    r.skip(4);
                } else {
                    return null;
                }
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    private static String firstHttpUrl(byte[] nested) {
        try {
            Pb r2 = new Pb(nested);
            while (r2.hasMore()) {
                long t2 = r2.varint();
                int f2 = (int) (t2 >>> 3);
                int w2 = (int) (t2 & 7);
                if (w2 == 2) {
                    int l2 = (int) r2.varint();
                    byte[] d2 = r2.take(l2);
                    if (f2 == 1) {
                        String s2 = new String(d2, StandardCharsets.UTF_8);
                        if (s2.startsWith("http")) return s2;
                    }
                } else if (w2 == 0) {
                    r2.varint();
                } else if (w2 == 1) {
                    r2.skip(8);
                } else if (w2 == 5) {
                    r2.skip(4);
                } else {
                    break;
                }
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    /** Text → defaultPattern(field 2)。 */
    static String parseText(byte[] b) {
        try {
            Pb r = new Pb(b);
            while (r.hasMore()) {
                long tag = r.varint();
                int f = (int) (tag >>> 3);
                int w = (int) (tag & 7);
                if (w == 2) {
                    int len = (int) r.varint();
                    if (f == 2) return r.str(len);
                    r.skip(len);
                } else if (w == 0) {
                    r.varint();
                } else if (w == 1) {
                    r.skip(8);
                } else if (w == 5) {
                    r.skip(4);
                } else {
                    break;
                }
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    /** GiftTrayInfo → trayDisplayText.defaultPattern。 */
    static String parseTrayInfo(byte[] b) {
        try {
            Pb r = new Pb(b);
            while (r.hasMore()) {
                long tag = r.varint();
                int f = (int) (tag >>> 3);
                int w = (int) (tag & 7);
                if (w == 2) {
                    int len = (int) r.varint();
                    byte[] d = r.take(len);
                    if (f == 1) {
                        String s = parseText(d);
                        if (s != null && !s.isEmpty()) return s;
                    }
                } else if (w == 0) {
                    r.varint();
                } else if (w == 1) {
                    r.skip(8);
                } else if (w == 5) {
                    r.skip(4);
                } else {
                    break;
                }
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    // ==================== 消息提取 ====================

    private static final Pattern NOISE_RE = Pattern.compile(
            "荣誉等级|粉丝团等级|级勋章|勋章|人气开场|重庆|中部|新星|万粉团|多阶段任务|tier_icon|"
                    + "default_selector|is_current|desc|icon_src|selector|直播已结束|http|douyinpic|"
                    + "\\.com|\\.png|\\.jpg|\\.webp|\\.mp4|\\.jpeg|\\.gif|current|target|status|"
                    + "already|light_current|a_type_roof_text|pro_conf|reward_list|小时榜|挑战榜|人气榜");
    private static final Pattern CN_PHRASE_RE = Pattern.compile(
            "[\\u4e00-\\u9fff][\\u4e00-\\u9fffA-Za-z0-9_\\s]{3,60}");
    private static final Pattern TAIL_JUNK_RE = Pattern.compile("[\"\u002c:;\\\\]+$");
    private static final Pattern FANSCLUB_NAME_RE = Pattern.compile(
            "粉丝团|灯牌|点亮|会员|守护|团票|续费|入团|团卡|粉丝牌|粉丝徽章|贵族|会员卡");
    private static final long FANSCLUB_PRICE_FALLBACK = 1;

    /** 把托盘文案洗成纯礼物名（「送出了点亮粉丝团 x1」→「点亮粉丝团」）。 */
    static String cleanGiftName(String s) {
        if (s == null || s.isEmpty()) return "";
        String t = s.trim();
        t = t.replaceFirst("^送出了?\\s*", "");
        t = t.replaceFirst("\\s*[x×]\\s*\\d+\\s*$", "");
        return t.trim();
    }

    static long giftPriceOrFallback(String name, long price) {
        if (price > 0) return price;
        if (name != null && FANSCLUB_NAME_RE.matcher(name).find()) return FANSCLUB_PRICE_FALLBACK;
        return price;
    }

    static Msg extractMsg(byte[] m) {
        Pb outer = new Pb(m);
        String method = "";
        byte[] payload = null;
        while (outer.hasMore()) {
            long tag = outer.varint();
            int f = (int) (tag >>> 3);
            int w = (int) (tag & 7);
            if (w == 2) {
                int len = (int) outer.varint();
                byte[] d = outer.take(len);
                if (f == 1) method = new String(d, StandardCharsets.UTF_8);
                else if (f == 2) payload = d;
            } else if (w == 0) {
                outer.varint();
            } else if (w == 1) {
                outer.skip(8);
            } else if (w == 5) {
                outer.skip(4);
            } else {
                break;
            }
        }
        if (payload == null) payload = m;
        String mtype = method;
        if (mtype.isEmpty()) return null;

        if ("WebcastBindingGiftMessage".equals(mtype)) {
            payload = unwrapBindingGift(payload);
        }

        Integer userField = USER_FIELD_MAP.get(mtype);
        boolean isGift = GIFT_TYPES.contains(mtype);
        Pb r = new Pb(payload);
        String nickname = null;
        String content = null;
        String giftName = null;
        String giftIcon = null;
        long giftPrice = -1;
        Long comboCount = null;
        Long repeatCount = null;
        String action = null;
        String trayText = null;
        Long repeatEnd = null;
        Long giftCount = null;
        Long likeTotal = null;
        Long likeCount = null;
        Long followCount = null;
        Long roomTotal = null;
        Long roomTotalUser = null;
        String roomTotalUserStr = null;
        Long memberCount = null;
        String bannerJson = null;
        String hotLabel = null;
        String lotteryJson = null;

        while (r.hasMore()) {
            long tag = r.varint();
            int f = (int) (tag >>> 3);
            int w = (int) (tag & 7);
            if (w == 0) {
                long v = r.varint();
                if (f == 5) {
                    if (isGift) repeatCount = v;
                    else if ("WebcastRoomStatsMessage".equals(mtype)) roomTotal = v;
                } else if (f == 6) {
                    if (isGift) comboCount = v;
                    else if ("WebcastSocialMessage".equals(mtype)) followCount = v;
                } else if (f == 2) {
                    if (isGift) giftCount = v;
                    else if ("WebcastLikeMessage".equals(mtype)) likeCount = v;
                } else if (f == 3) {
                    if ("WebcastLikeMessage".equals(mtype)) likeTotal = v;
                    else if ("WebcastRoomUserSeqMessage".equals(mtype)) roomTotal = v;
                    else if ("WebcastMemberMessage".equals(mtype)) memberCount = v;
                } else if (f == 7) {
                    if ("WebcastRoomUserSeqMessage".equals(mtype)) roomTotalUser = v;
                } else if (f == 9) {
                    if (isGift) repeatEnd = v;
                }
            } else if (w == 2) {
                int len = (int) r.varint();
                byte[] d = r.take(len);
                if (userField != null && f == userField) {
                    nickname = parseUser(d);
                } else if (f == 3 && !"WebcastLikeMessage".equals(mtype)
                        && !"WebcastRoomUserSeqMessage".equals(mtype)) {
                    content = new String(d, StandardCharsets.UTF_8);
                } else if (f == 8 && "WebcastRoomUserSeqMessage".equals(mtype)) {
                    roomTotalUserStr = new String(d, StandardCharsets.UTF_8);
                } else if (f == 15 && isGift) {
                    Gift g = parseGift(d);
                    if (g.name() != null && !g.name().isEmpty()) giftName = g.name();
                    if (g.price() >= 0) giftPrice = g.price();
                    if (g.icon() != null && !g.icon().isEmpty()) giftIcon = g.icon();
                } else if (f == 11) {
                    action = new String(d, StandardCharsets.UTF_8);
                } else if (f == 2 && "WebcastInRoomBannerMessage".equals(mtype)) {
                    String s = new String(d, StandardCharsets.UTF_8);
                    if (s.stripLeading().startsWith("{")) bannerJson = s;
                } else if (f == 2 && "WebcastHotChatMessage".equals(mtype)) {
                    hotLabel = new String(d, StandardCharsets.UTF_8);
                } else if (f == 4 && "WebcastLotteryDrawResultEventMessage".equals(mtype)) {
                    lotteryJson = new String(d, StandardCharsets.UTF_8);
                } else if (f == 5 && "WebcastEmojiChatMessage".equals(mtype)) {
                    content = new String(d, StandardCharsets.UTF_8);
                } else if (f == 19) {
                    trayText = parseText(d);
                } else if (f == 21) {
                    String t = parseTrayInfo(d);
                    if (t != null && !t.isEmpty()) trayText = t;
                }
            } else if (w == 1) {
                r.skip(8);
            } else if (w == 5) {
                r.skip(4);
            } else {
                break;
            }
        }

        long outGiftCount = repeatCount != null ? repeatCount
                : (comboCount != null ? comboCount : 1);
        String outContent;
        if (isGift) {
            if ((giftName == null || giftName.isEmpty()) && trayText != null) {
                giftName = cleanGiftName(trayText);
            }
            String gname = giftName == null || giftName.isEmpty() ? "礼物" : giftName;
            giftPrice = giftPriceOrFallback(gname, giftPrice);
            outContent = "送出了" + gname + " x" + outGiftCount;
        } else if ("WebcastMemberMessage".equals(mtype)) {
            outContent = action != null && !action.isEmpty() ? action : "进入直播间";
        } else if ("WebcastLikeMessage".equals(mtype)) {
            outContent = "为主播点赞了(" + (likeCount != null ? likeCount : 1) + ")";
        } else if ("WebcastSocialMessage".equals(mtype)) {
            outContent = "关注了主播";
        } else if ("WebcastChatMessage".equals(mtype) || "WebcastEmojiChatMessage".equals(mtype)) {
            outContent = content == null ? "" : content;
        } else if ("WebcastRoomStatsMessage".equals(mtype)) {
            outContent = content == null ? "" : content;
        } else if ("WebcastHotChatMessage".equals(mtype)) {
            outContent = hotLabel != null && !hotLabel.isEmpty() && content != null && !content.isEmpty()
                    ? hotLabel + "：" + content : (content == null ? "" : content);
        } else if ("WebcastLotteryDrawResultEventMessage".equals(mtype)) {
            outContent = lotterySummary(lotteryJson);
        } else if ("WebcastInRoomBannerMessage".equals(mtype)) {
            outContent = bannerJson != null ? parseBannerJson(bannerJson) : "";
            if (outContent == null || outContent.isEmpty()) outContent = content == null ? "" : content;
            if (outContent.length() > 200) outContent = outContent.substring(0, 200) + "…";
        } else {
            outContent = firstChinesePhrase(payload);
        }

        String[] meta = MSG_TYPES.getOrDefault(mtype, new String[]{mtype, "other"});
        long ts = System.currentTimeMillis();
        return new Msg(mtype, meta[0], meta[1], nickname, outContent,
                giftName, Math.max(giftPrice, 0), giftIcon, outGiftCount,
                repeatEnd != null && repeatEnd == 1,
                likeCount != null ? likeCount : 0,
                likeTotal != null ? likeTotal : 0,
                followCount != null ? followCount : 0,
                roomTotal != null ? roomTotal : 0,
                roomTotalUser != null ? roomTotalUser : 0,
                roomTotalUserStr == null ? "" : roomTotalUserStr,
                memberCount != null ? memberCount : 0,
                ts);
    }

    /** BindingGiftMessage 多包一层：{f1: 真正 body, f2: Common}，剥壳后与 GiftMessage 同构。 */
    private static byte[] unwrapBindingGift(byte[] payload) {
        try {
            Pb rb = new Pb(payload);
            while (rb.hasMore()) {
                long tag = rb.varint();
                int f = (int) (tag >>> 3);
                int w = (int) (tag & 7);
                if (w == 2) {
                    int len = (int) rb.varint();
                    byte[] d = rb.take(len);
                    if (f == 1 && len > 64) return d;
                } else if (w == 0) {
                    rb.varint();
                } else if (w == 1) {
                    rb.skip(8);
                } else if (w == 5) {
                    rb.skip(4);
                } else {
                    break;
                }
            }
        } catch (Exception ignored) {
        }
        return payload;
    }

    private static String firstChinesePhrase(byte[] payload) {
        String txt = new String(payload, StandardCharsets.UTF_8);
        Matcher m = CN_PHRASE_RE.matcher(txt);
        while (m.find()) {
            String t = TAIL_JUNK_RE.matcher(m.group()).replaceAll("").trim();
            if (t.length() >= 4 && !NOISE_RE.matcher(t).find()) {
                return t.length() > 40 ? t.substring(0, 40) + "…" : t;
            }
        }
        return "";
    }

    // ==================== 横幅 / 抽奖 JSON ====================

    private static final Map<String, String> BANNER_TYPE_NAMES = Map.ofEntries(
            Map.entry("fans", "粉丝团"), Map.entry("ai_gift", "AI 礼物"),
            Map.entry("tier", "周榜任务"), Map.entry("activity", "活动"),
            Map.entry("gift", "礼物"), Map.entry("rank", "榜单"),
            Map.entry("task", "任务"), Map.entry("subscribe", "订阅"),
            Map.entry("vip", "会员"), Map.entry("qixi", "七夕"),
            Map.entry("flow", "充能"));

    private static final Map<String, String> BANNER_TASK_NAMES = Map.of(
            "gift_task", "礼物任务", "quota_task", "额度任务",
            "anchor_gift_task", "主播礼物任务");

    private static final Map<Integer, String> TASK_STATUS_NAMES = Map.of(
            0, "未开始", 1, "进行中", 2, "已完成", 3, "已结束");

    /** 横幅 f2 是完整活动 JSON，提取人类可读描述（标题/状态/规则/任务进度）。 */
    static String parseBannerJson(String raw) {
        try {
            JsonNode data = new ObjectMapper().readTree(raw);
            if (!data.isObject()) return null;
            List<String> outs = new ArrayList<>();
            data.fields().forEachRemaining(e -> {
                JsonNode node = e.getValue();
                if (!node.isObject()) return;
                String key = e.getKey();
                String shortName = key.contains("_") ? key.substring(key.lastIndexOf('_') + 1) : key;
                String label = BANNER_TYPE_NAMES.getOrDefault(shortName, shortName);
                List<String> parts = new ArrayList<>();
                JsonNode title = firstPresent(node, "title", "name");
                JsonNode sub = firstPresent(node, "subtitle", "desc", "description");
                if (title != null && !title.asText("").isEmpty()) parts.add(title.asText(""));
                if (sub != null && !sub.asText("").isEmpty()) parts.add(sub.asText(""));
                JsonNode status = node.path("status");
                if (status.isObject()) {
                    JsonNode nm = firstPresent(status, "Name", "name");
                    if (nm != null && !nm.asText("").isEmpty()) parts.add("[" + nm.asText("") + "]");
                }
                JsonNode extra = node.path("_extra");
                if (extra.isObject()) {
                    JsonNode rule = firstPresent(extra, "rule", "text");
                    if (rule != null && !rule.asText("").isEmpty()
                            && rule.asText("").length() < 150) {
                        parts.add(rule.asText(""));
                    }
                }
                if (parts.isEmpty()) parts.addAll(bannerTaskSummary(node));
                if (parts.isEmpty()) parts.addAll(bannerFansSummary(node));
                if (parts.isEmpty()) parts.addAll(bannerFlowSummary(node));
                if (parts.isEmpty() && extra.isObject()) {
                    JsonNode nm = extra.path("step").path("Name");
                    if (!nm.asText("").isEmpty()) parts.add("[" + nm.asText("") + "]");
                }
                if (!parts.isEmpty()) outs.add(label + "·" + String.join(" | ", parts));
            });
            return outs.isEmpty() ? null : String.join("；", outs);
        } catch (Exception e) {
            return null;
        }
    }

    private static JsonNode firstPresent(JsonNode node, String... names) {
        for (String n : names) {
            JsonNode v = node.get(n);
            if (v != null && !v.isNull()) return v;
        }
        return null;
    }

    private static List<String> bannerTaskSummary(JsonNode node) {
        List<String> outs = new ArrayList<>();
        node.fields().forEachRemaining(e -> {
            JsonNode sub = e.getValue();
            if (!sub.isObject() || !sub.has("task_items")) return;
            String cname = sub.path("component_name").asText(e.getKey());
            String label = BANNER_TASK_NAMES.getOrDefault(cname,
                    BANNER_TYPE_NAMES.getOrDefault(cname, cname));
            List<String> bits = new ArrayList<>();
            for (JsonNode item : sub.path("task_items")) {
                String stName = TASK_STATUS_NAMES.getOrDefault(item.path("status").asInt(-1), "");
                List<String> conds = new ArrayList<>();
                for (JsonNode ph : item.path("phases")) {
                    for (JsonNode c : ph.path("conditions")) {
                        long target = c.path("target").asLong(0);
                        if (target != 0) conds.add(c.path("current").asLong(0) + "/" + target);
                    }
                }
                String line = (stName + " " + String.join(" ", conds)).trim();
                if (!line.isEmpty()) bits.add(line);
            }
            if (!bits.isEmpty()) outs.add(label + "：" + String.join("；", bits));
        });
        return outs;
    }

    private static List<String> bannerFansSummary(JsonNode node) {
        List<String> outs = new ArrayList<>();
        node.fields().forEachRemaining(e -> {
            JsonNode sub = e.getValue();
            if (!sub.isObject()) return;
            JsonNode core = sub.path(e.getKey());
            if (!core.isObject()) return;
            JsonNode selectors = core.path("foot").path("extra").path("selectors");
            for (JsonNode s : selectors) {
                if (s.isTextual()) {
                    String period = e.getKey().contains("drank") ? "日榜" : "时榜";
                    outs.add(s.asText() + period);
                    break;
                }
            }
        });
        return outs;
    }

    private static List<String> bannerFlowSummary(JsonNode node) {
        List<String> outs = new ArrayList<>();
        JsonNode role2views = node.path("banner_detail").path("role2views");
        if (!role2views.isObject()) return outs;
        role2views.fields().forEachRemaining(e -> {
            for (JsonNode bn : e.getValue().path("banners")) {
                JsonNode bp = bn.path("basic_props");
                String title = bp.path("title").path(0).asText("");
                String desc = bp.path("desc").path(0).asText("");
                if (!title.isEmpty() || !desc.isEmpty()) {
                    outs.add((title + " " + desc).trim());
                }
            }
        });
        return outs;
    }

    private static String lotterySummary(String lotteryJson) {
        if (lotteryJson == null || lotteryJson.isEmpty()) return "";
        try {
            JsonNode d = new ObjectMapper().readTree(lotteryJson);
            if (!d.isObject()) return "";
            List<String> bits = new ArrayList<>();
            d.fields().forEachRemaining(e -> {
                String kn = "candidate_num".equals(e.getKey()) ? "候选" : e.getKey();
                bits.add(kn + " " + e.getValue().asText(""));
            });
            return bits.isEmpty() ? "" : "抽奖结果：" + String.join("，", bits);
        } catch (Exception e) {
            return "";
        }
    }

    // ==================== WS 参数 / 编码 ====================

    /** 组 WS 地址（参数与 Python build_ws_url 一致）。 */
    static String buildWsUrl(String pushServer, long roomId, String uniqueId,
                             String cursor, String internalExt, String signature) {
        return buildWsUrl(pushServer, roomId, uniqueId, cursor, internalExt, signature, 1920, 1080);
    }

    static String buildWsUrl(String pushServer, long roomId, String uniqueId,
                             String cursor, String internalExt, String signature,
                             int screenWidth, int screenHeight) {
        Map<String, String> params = new LinkedHashMap<>();
        params.put("aid", "6383");
        params.put("app_name", "douyin_web");
        params.put("browser_language", "zh-CN");
        params.put("browser_name", "Mozilla");
        params.put("browser_online", "true");
        params.put("browser_platform", "Win32");
        params.put("browser_version", DouyinLive.UA);
        params.put("compress", "gzip");
        params.put("cookie_enabled", "true");
        params.put("device_platform", "web");
        params.put("did_rule", "3");
        params.put("endpoint", "live_pc");
        params.put("heartbeatDuration", "0");
        params.put("host", "https://live.douyin.com");
        params.put("identity", "audience");
        params.put("insert_task_id", "");
        params.put("live_id", "1");
        params.put("live_reason", "");
        params.put("need_persist_msg_count", "15");
        params.put("screen_height", String.valueOf(screenHeight));
        params.put("screen_width", String.valueOf(screenWidth));
        params.put("support_wrds", "1");
        params.put("tz_name", "Asia/Shanghai");
        params.put("update_version_code", "1.0.14-beta.0");
        params.put("version_code", "180800");
        params.put("webcast_sdk_version", "1.0.14-beta.0");
        params.put("room_id", String.valueOf(roomId));
        params.put("user_unique_id", uniqueId);
        params.put("cursor", cursor);
        params.put("internal_ext", internalExt);
        params.put("signature", signature);
        StringBuilder sb = new StringBuilder(pushServer).append('?');
        boolean first = true;
        for (Map.Entry<String, String> e : params.entrySet()) {
            if (!first) sb.append('&');
            first = false;
            sb.append(e.getKey()).append('=').append(urlEncode(e.getValue()));
        }
        return sb.toString();
    }

    private static String urlEncode(String s) {
        return URLEncoder.encode(s == null ? "" : s, StandardCharsets.UTF_8);
    }

    static byte[] encVarint(long n) {
        List<Byte> out = new ArrayList<>();
        while (n > 0x7F) {
            out.add((byte) ((n & 0x7F) | 0x80));
            n >>= 7;
        }
        out.add((byte) n);
        byte[] r = new byte[out.size()];
        for (int i = 0; i < r.length; i++) r[i] = out.get(i);
        return r;
    }

    /** 编码 PushFrame（心跳 hb / 应答 ack）。 */
    static byte[] encPushFrame(String payloadType, byte[] payload, Long logId) {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        if (payloadType != null && !payloadType.isEmpty()) {
            byte[] s = payloadType.getBytes(StandardCharsets.UTF_8);
            out.write(0x3A);
            out.writeBytes(encVarint(s.length));
            out.writeBytes(s);
        }
        if (payload != null && payload.length > 0) {
            out.write(0x42);
            out.writeBytes(encVarint(payload.length));
            out.writeBytes(payload);
        }
        if (logId != null && logId != 0) {
            out.write(0x10);
            out.writeBytes(encVarint(logId));
        }
        return out.toByteArray();
    }

    static byte[] heartbeatFrame() {
        return encPushFrame("hb", null, null);
    }

    static byte[] ackFrame(String ext, long logId) {
        return encPushFrame("ack", (ext == null ? "" : ext).getBytes(StandardCharsets.UTF_8),
                logId == 0 ? null : logId);
    }

    private static byte[] gunzip(byte[] data) throws IOException {
        try (GZIPInputStream in = new GZIPInputStream(new ByteArrayInputStream(data))) {
            return in.readAllBytes();
        }
    }
}
