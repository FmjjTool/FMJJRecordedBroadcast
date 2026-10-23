package cn.fmjj.toolbox.engine.platform.bili;

import cn.fmjj.toolbox.core.bili.WbiSigner;
import cn.fmjj.toolbox.core.Log;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient.Redirect;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.zip.DataFormatException;
import java.util.zip.Inflater;

/**
 * B站直播弹幕客户端（移植自 Python 版 toolbox/bili_live.py 的 WS 部分）。
 *
 * <p>协议（与 BililiveRecorder 一致）：
 * <pre>
 *   包 = 大端 &gt;IHHII (总长, 16, 1, op, 1) + body
 *   op=7 认证（JSON: uid/roomid/protover=2/buvid/platform/type=2/key=token）
 *   op=2 心跳（30s）
 *   op=8 认证回执（code≠0 失败）
 *   op=5 消息帧（protover=2 → zlib 解压 → 内部多条带 16B 头的帧）
 * </pre>
 *
 * <p>getDanmuInfo **必须 WBI 签名**（否则 -352），并需要游客 buvid3。
 */
public final class BiliLiveClient {

    private static final String DANMAKU_WS = "wss://broadcastlv.chat.bilibili.com/sub";
    private static final String DANMU_INFO_API = "https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo";
    private static final String ROOM_INFO_API = "https://api.live.bilibili.com/room/v1/Room/get_info";
    private static final String BUVID_API = "https://api.bilibili.com/x/frontend/finger/spi";
    private static final int HEARTBEAT_SECONDS = 30;

    private static final ObjectMapper JSON = new ObjectMapper();

    /** 房间信息。 */
    public record RoomInfo(String roomId, long realRoomId, String title, String anchor,
                           String cover, String liveStatus, long online) {}

    /** 弹幕事件。 */
    public record Danmaku(String type, String user, String content, long timestamp, String extra) {}

    /** 监听器（回调在线程池里，UI 层需自行切回 FX 线程）。 */
    public interface Listener {
        default void onStatus(String message) {}
        default void onDanmaku(Danmaku d) {}
        default void onError(String message) {}
    }

    private final HttpClient http = new HttpClient();
    private volatile WebSocket ws;
    private ScheduledExecutorService heartbeat;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final ByteArrayOutputStream buffer = new ByteArrayOutputStream();
    private Listener listener;

    // ==================== 房间信息 / 鉴权 ====================

    public RoomInfo roomInfo(String roomId) throws Exception {
        JsonNode d = http.getJson(ROOM_INFO_API + "?room_id=" + HttpClient.urlEncode(roomId), headers(""));
        if (d.path("code").asInt(-1) != 0) {
            throw new IllegalStateException(d.path("message").asText("获取房间信息失败"));
        }
        var data = d.path("data");
        return new RoomInfo(
                data.path("room_id").asText(roomId),
                data.path("room_id").asLong(0),
                data.path("title").asText(""),
                data.path("uid").asText(""),
                data.path("user_cover").asText(data.path("keyframe").asText("")),
                data.path("live_status").asText("0"),
                data.path("online").asLong(0));
    }

    /**
     * 游客 buvid3（getDanmuInfo 与弹幕认证体都要）。
     * 优先用首页 Set-Cookie 里的 buvid3（与 Python 版 build_cookie_header 一致）；
     * 退而用 finger/spi 的 b_3（新格式，部分场景弹幕服务不认）。
     */
    public String guestBuvid() {
        try {
            var resp = http.get("https://www.bilibili.com/", headers(""));
            for (String c : resp.headers().allValues("Set-Cookie")) {
                if (c.startsWith("buvid3=")) {
                    String v = c.substring("buvid3=".length());
                    int semi = v.indexOf(';');
                    return semi > 0 ? v.substring(0, semi) : v;
                }
            }
        } catch (Exception ignored) {
        }
        try {
            JsonNode d = http.getJson(BUVID_API, headers(""));
            String b3 = d.path("data").path("b_3").asText("");
            if (!b3.isEmpty()) return b3;
        } catch (Exception ignored) {
        }
        return "";
    }

    /** 弹幕鉴权：(token, host)。 */
    public record Auth(String token, String host) {}

    public Auth danmakuAuth(String roomId, String cookie, String buvid) throws Exception {
        var keys = fetchWbiKeys(cookie);
        Map<String, String> params = new LinkedHashMap<>();
        params.put("id", roomId);
        params.put("type", "0");
        String url = WbiSigner.signUrl(DANMU_INFO_API, params, keys);
        JsonNode d = http.getJson(url, headers(cookie, buvid));
        if (d.path("code").asInt(-1) != 0) {
            throw new IllegalStateException("getDanmuInfo 失败：" + d.path("code").asInt()
                    + " " + d.path("message").asText());
        }
        var data = d.path("data");
        String host = "";
        JsonNode hosts = data.path("host_list");
        if (hosts.isArray() && !hosts.isEmpty()) host = hosts.get(0).path("host").asText("");
        return new Auth(data.path("token").asText(""), host);
    }

    private WbiSigner.WbiKeys fetchWbiKeys(String cookie) throws Exception {
        JsonNode d = http.getJson("https://api.bilibili.com/x/web-interface/nav", headers(cookie));
        var wbi = d.path("data").path("wbi_img");
        String img = basename(wbi.path("img_url").asText(""));
        String sub = basename(wbi.path("sub_url").asText(""));
        if (img.isEmpty() || sub.isEmpty()) throw new IllegalStateException("未取到 WBI 密钥");
        return new WbiSigner.WbiKeys(img, sub, "");
    }

    private static String basename(String url) {
        String s = url;
        int q = s.indexOf('?');
        if (q >= 0) s = s.substring(0, q);
        int slash = s.lastIndexOf('/');
        if (slash >= 0) s = s.substring(slash + 1);
        int dot = s.lastIndexOf('.');
        return dot > 0 ? s.substring(0, dot) : s;
    }

    private Map<String, String> headers(String cookie) {
        return headers(cookie, "");
    }

    private Map<String, String> headers(String cookie, String buvid) {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", WbiSigner.UA);
        h.put("Referer", "https://live.bilibili.com/");
        h.put("Origin", "https://live.bilibili.com");
        StringBuilder ck = new StringBuilder();
        if (cookie != null && !cookie.isEmpty()) ck.append(cookie);
        if (buvid != null && !buvid.isEmpty()) {
            if (ck.length() > 0) ck.append("; ");
            ck.append("buvid3=").append(buvid);
        }
        if (ck.length() > 0) h.put("Cookie", ck.toString());
        return h;
    }

    // ==================== WebSocket 连接 ====================

    /**
     * 连接弹幕通道。
     *
     * @param realRoomId 真实房间号（不是短号）
     */
    public void connect(long realRoomId, String token, String host, String buvid, Listener l) throws Exception {
        this.listener = l;
        running.set(true);
        buffer.reset();

        String url = host == null || host.isEmpty() ? DANMAKU_WS
                : (host.startsWith("wss://") ? host : "wss://" + host + "/sub");

        // 显式禁用代理：系统代理会破坏 WebSocket 升级握手（1006 断开）
        var builder = java.net.http.HttpClient.newBuilder()
                .connectTimeout(java.time.Duration.ofSeconds(10))
                .proxy(java.net.ProxySelector.of(null))
                .followRedirects(Redirect.NORMAL);
        var client = builder.build();

        WebSocket.Listener wsListener = new WebSocket.Listener() {
            @Override
            public void onOpen(WebSocket webSocket) {
                l.onStatus("弹幕通道已打开，正在鉴权…");
                webSocket.request(1);
            }

            @Override
            public CompletionStage<?> onBinary(WebSocket webSocket, ByteBuffer data, boolean last) {
                byte[] chunk = new byte[data.remaining()];
                data.get(chunk);
                buffer.write(chunk, 0, chunk.length);
                drain();
                webSocket.request(1);
                return null;
            }

            @Override
            public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
                webSocket.request(1);
                return null;
            }

            @Override
            public void onError(WebSocket webSocket, Throwable error) {
                if (running.get()) l.onError("弹幕连接异常：" + error.getMessage());
            }

            @Override
            public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
                running.set(false);
                l.onStatus("弹幕连接已关闭（" + statusCode + "）");
                return null;
            }
        };

        ws = client.newWebSocketBuilder()
                .header("User-Agent", WbiSigner.UA)
                .header("Origin", "https://live.bilibili.com")
                .buildAsync(URI.create(url), wsListener)
                .get(15, TimeUnit.SECONDS);

        // 认证包
        Map<String, Object> auth = new LinkedHashMap<>();
        auth.put("uid", 0);
        auth.put("roomid", realRoomId);
        auth.put("protover", 2);
        auth.put("buvid", buvid == null ? "" : buvid);
        auth.put("platform", "web");
        auth.put("type", 2);
        auth.put("key", token == null ? "" : token);
        // 少数服务端在 101 完成后需要极短延迟才接受首帧，否则直接 RST
        Thread.sleep(150);
        send(7, JSON.writeValueAsString(auth).getBytes(StandardCharsets.UTF_8));
        send(2, new byte[0]);

        heartbeat = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "bili-danmaku-heartbeat");
            t.setDaemon(true);
            return t;
        });
        heartbeat.scheduleAtFixedRate(() -> {
            if (!running.get()) return;
            try {
                send(2, new byte[0]);
            } catch (Exception e) {
                running.set(false);
            }
        }, HEARTBEAT_SECONDS, HEARTBEAT_SECONDS, TimeUnit.SECONDS);

        l.onStatus("已发起认证（房间 " + realRoomId + "）");
    }

    public void close() {
        running.set(false);
        if (heartbeat != null) heartbeat.shutdownNow();
        if (ws != null) {
            try {
                ws.sendClose(WebSocket.NORMAL_CLOSURE, "bye");
            } catch (Exception ignored) {
            }
            ws.abort();
        }
    }

    public boolean isRunning() { return running.get(); }

    private void send(int op, byte[] body) {
        WebSocket w = ws;
        if (w == null) return;
        byte[] packet = pack(op, body);
        try {
            w.sendBinary(ByteBuffer.wrap(packet), true).get(5, TimeUnit.SECONDS);
        } catch (Exception e) {
            Log.warn("弹幕发包失败 op=" + op + "：" + e.getMessage());
        }
    }

    /** 打包：>IHHII (总长, 16, 1, op, 1) + body。 */
    static byte[] pack(int op, byte[] body) {
        int total = 16 + body.length;
        ByteBuffer buf = ByteBuffer.allocate(total);
        buf.putInt(total);
        buf.putShort((short) 16);
        buf.putShort((short) 1);
        buf.putInt(op);
        buf.putInt(1);
        buf.put(body);
        return buf.array();
    }

    // ==================== 解包 ====================

    private void drain() {
        byte[] all = buffer.toByteArray();
        int offset = 0;
        while (all.length - offset >= 16) {
            ByteBuffer bb = ByteBuffer.wrap(all, offset, 16);
            int total = bb.getInt();
            int hlen = bb.getShort() & 0xFFFF;
            int protover = bb.getShort() & 0xFFFF;
            int op = bb.getInt();
            if (total < 16 || offset + total > all.length) break;
            byte[] body = java.util.Arrays.copyOfRange(all, offset + hlen, offset + total);
            offset += total;
            handleFrame(op, protover, body);
        }
        buffer.reset();
        if (offset < all.length) buffer.write(all, offset, all.length - offset);
    }

    private void handleFrame(int op, int protover, byte[] body) {
        if (op == 8) {
            try {
                JsonNode d = JSON.readTree(new String(body, StandardCharsets.UTF_8));
                if (d.path("code").asInt(0) != 0) {
                    listener.onError("弹幕通道鉴权失败（token 无效），code=" + d.path("code").asInt());
                    running.set(false);
                } else {
                    listener.onStatus("已连接弹幕通道 ✅");
                }
            } catch (Exception ignored) {
            }
            return;
        }
        if (op != 5) return;

        byte[] payload = body;
        if (protover == 2) {
            try {
                payload = inflate(body);
            } catch (Exception e) {
                return;
            }
        }
        // 解压体常是多条内部帧串（各带 16B 头）
        int off = 0;
        while (payload.length - off >= 16) {
            ByteBuffer bb = ByteBuffer.wrap(payload, off, 16);
            int it = bb.getInt();
            int ih = bb.getShort() & 0xFFFF;
            bb.getShort();
            int iop = bb.getInt();
            if (it < 16 || off + it > payload.length) break;
            if (iop == 5) {
                emitJson(java.util.Arrays.copyOfRange(payload, off + ih, off + it));
            }
            off += it;
        }
        if (off < payload.length && payload.length - off > 0) {
            emitJson(java.util.Arrays.copyOfRange(payload, off, payload.length));
        }
    }

    static byte[] inflate(byte[] data) throws DataFormatException {
        Inflater inf = new Inflater();
        try {
            inf.setInput(data);
            ByteArrayOutputStream out = new ByteArrayOutputStream(data.length * 4);
            byte[] buf = new byte[64 * 1024];
            while (!inf.finished()) {
                int n = inf.inflate(buf);
                if (n == 0) {
                    if (inf.needsInput() || inf.needsDictionary()) break;
                } else {
                    out.write(buf, 0, n);
                }
            }
            return out.toByteArray();
        } finally {
            inf.end();
        }
    }

    /** 单条 JSON 消息 → 事件（cmd 决定类型）。 */
    private void emitJson(byte[] json) {
        try {
            JsonNode d = JSON.readTree(new String(json, StandardCharsets.UTF_8));
            String cmd = d.path("cmd").asText("");
            if (cmd.startsWith("DANMU_MSG")) {
                var info = d.path("info");
                String text = info.path(1).asText("");
                String user = info.path(2).path(1).asText("");
                listener.onDanmaku(new Danmaku("弹幕", user, text, System.currentTimeMillis(), ""));
            } else if (cmd.startsWith("SEND_GIFT")) {
                var data = d.path("data");
                String user = data.path("uname").asText("");
                String action = data.path("action").asText("投喂");
                String gift = data.path("giftName").asText("");
                int num = data.path("num").asInt(1);
                listener.onDanmaku(new Danmaku("礼物", user, action + " " + gift + " ×" + num,
                        System.currentTimeMillis(), ""));
            } else if (cmd.startsWith("SUPER_CHAT_MESSAGE")) {
                var data = d.path("data");
                listener.onDanmaku(new Danmaku("SC", data.path("user_info").path("uname").asText(""),
                        data.path("message").asText(""), System.currentTimeMillis(),
                        "¥" + data.path("price").asInt(0)));
            } else if (cmd.startsWith("INTERACT_WORD")) {
                var data = d.path("data");
                String msg = data.path("msg_type").asInt(0) == 2 ? "关注了主播" : "进入直播间";
                listener.onDanmaku(new Danmaku("进场", data.path("uname").asText(""), msg,
                        System.currentTimeMillis(), ""));
            }
        } catch (Exception ignored) {
        }
    }
}
