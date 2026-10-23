package cn.fmjj.toolbox.engine.platform.douyin;

import cn.fmjj.toolbox.core.Log;
import cn.fmjj.toolbox.engine.net.HttpClient;
import com.fasterxml.jackson.databind.JsonNode;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 抖音直播（对应 Python 版 toolbox/douyin_live.py 的房间解析与弹幕准备）。
 *
 * <p>房间信息：`live.douyin.com/webcast/room/web/enter/`（需 ttwid；ttwid 是 **HttpOnly**，
 * `document.cookie` 读不到，只能从 HTTP 响应的 Set-Cookie 取）。
 *
 * <p>弹幕准备：必须在**页面上下文**里跑 `fetch('/webcast/im/fetch/')`（见 {@link #prepJs}），
 * 因为签名依赖 `window.byted_acrawler.frontiersign`。
 */
public final class DouyinLive {

    public static final String UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            + " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36";

    /** 直播间信息。 */
    public record RoomInfo(long roomId, String anchor, String title, int status,
                           String online, String cover, String error) {}

    /** 弹幕连接参数（浏览器里拿到的）。 */
    public record Prep(String cursor, String internalExt, String pushServer,
                       String signature, String ttwid, String sessionId, String error) {}

    private final HttpClient http = new HttpClient();

    private volatile String ttwidCache = "";
    private volatile long ttwidAt;

    private Map<String, String> headers(String referer, String cookie) {
        Map<String, String> h = new LinkedHashMap<>();
        h.put("User-Agent", UA);
        h.put("Referer", referer == null ? "https://live.douyin.com/" : referer);
        if (cookie != null && !cookie.isEmpty()) h.put("Cookie", cookie);
        return h;
    }

    /** 取游客 ttwid（访问直播间页，从 Set-Cookie 抓；缓存 6 小时）。 */
    public String ttwid(String webRid) {
        if (!ttwidCache.isEmpty() && System.currentTimeMillis() - ttwidAt < 6 * 3600_000L) {
            return ttwidCache;
        }
        try {
            var resp = http.get("https://live.douyin.com/" + webRid,
                    headers("https://live.douyin.com/", ""));
            for (String c : resp.headers().allValues("Set-Cookie")) {
                if (c.startsWith("ttwid=")) {
                    String v = c.split(";")[0].substring("ttwid=".length());
                    if (!v.isEmpty()) {
                        ttwidCache = v;
                        ttwidAt = System.currentTimeMillis();
                        return v;
                    }
                }
            }
        } catch (Exception e) {
            Log.warn("取抖音 ttwid 失败：" + e.getMessage());
        }
        return ttwidCache;
    }

    /** 解析直播间（web_rid → roomId 等）。 */
    public RoomInfo roomInfo(String webRid) {
        String ttwid = ttwid(webRid);
        StringBuilder url = new StringBuilder("https://live.douyin.com/webcast/room/web/enter/?")
                .append("aid=6383&app_name=douyin_web&live_id=1&device_platform=web")
                .append("&language=zh-CN&browser_language=zh-CN&browser_platform=Win32")
                .append("&browser_name=Mozilla&browser_version=")
                .append(HttpClient.urlEncode(UA))
                .append("&web_rid=").append(HttpClient.urlEncode(webRid));
        try {
            JsonNode d = http.getJson(url.toString(),
                    headers("https://live.douyin.com/" + webRid, "ttwid=" + ttwid));
            JsonNode arr = d.path("data").path("data");
            if (!arr.isArray() || arr.isEmpty()) {
                String prompt = d.path("data").path("prompts").asText("");
                return new RoomInfo(0, "", "", 0, "", "",
                        prompt.isEmpty() ? "未获取到房间信息（可能不存在或已结束，"
                                + "code=" + d.path("status_code").asInt() + "）" : prompt);
            }
            JsonNode room = arr.get(0);
            long roomId = room.path("id_str").asText("").isEmpty()
                    ? room.path("id").asLong(0) : Long.parseLong(room.path("id_str").asText("0"));
            String cover = "";
            JsonNode cov = room.path("cover");
            if (cov.has("url_list") && cov.path("url_list").size() > 0) {
                cover = cov.path("url_list").get(0).asText("");
            }
            return new RoomInfo(roomId,
                    room.path("owner").path("nickname").asText(""),
                    room.path("title").asText(""),
                    room.path("status").asInt(0),
                    room.path("stats").path("user_count_str").asText(
                            room.path("stats").path("user_count").asText("")),
                    cover, "");
        } catch (Exception e) {
            return new RoomInfo(0, "", "", 0, "", "", "请求失败：" + e.getMessage());
        }
    }

    /** 拼 X-MS-STUB 的 md5（对应 Python stub_md5）。 */
    public static String stubMd5(long roomId, String uniqueId) {
        String stub = "live_id=1,aid=6383,version_code=180800,webcast_sdk_version=1.0.14-beta.0,"
                + "room_id=" + roomId + ",sub_room_id=,sub_channel_id=,did_rule=3,"
                + "user_unique_id=" + uniqueId + ",device_platform=web,device_type=,ac=,identity=audience";
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            byte[] d = md.digest(stub.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            for (byte b : d) sb.append(String.format("%02x", b));
            return sb.toString();
        } catch (Exception e) {
            return "";
        }
    }

    /**
     * 在页面上下文里跑的 PREP JS（对应 Python _PREP_JS 的核心部分）。
     *
     * <p>做的事：拿 X-Bogus 签名 → 页面内 fetch `/webcast/im/fetch/` → 解析 protobuf
     * 取 cursor / internalExt / pushServer。
     */
    public static String prepJs(long roomId, String uniqueId, String md5hex) {
        return "(async function(){"
                + "const roomId=" + roomId + ",uniqueId=" + quote(uniqueId) + ",md5hex=" + quote(md5hex) + ";"
                + "const out={};const UA=navigator.userAgent;"
                + "function makeReader(b){let p=0;return{varint(){let r=0n,s=0n;while(true){const x=b[p++];r|=BigInt(x&0x7f)<<s;if(!(x&0x80))break;s+=7n;}return r;},"
                + "str(l){const s=new TextDecoder().decode(b.slice(p,p+l));p+=l;return s;},skip(n){p+=n;},get pos(){return p;}};}"
                + "try{out.signature=window.byted_acrawler.frontierSign({'X-MS-STUB':md5hex})['X-Bogus'];}catch(e){out.error='sign:'+e.message;}"
                + "const msToken=Array.from({length:184},()=>'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'[Math.floor(Math.random()*64)]).join('');"
                + "const params=new URLSearchParams({aid:'6383',app_name:'douyin_web',browser_language:'zh-CN',browser_name:'Mozilla',browser_online:'true',browser_platform:'Win32',browser_version:UA,cookie_enabled:'true',cursor:'',device_id:'',device_platform:'web',did_rule:'3',endpoint:'live_pc',fetch_rule:'1',identity:'audience',insert_task_id:'',internal_ext:'',last_rtt:'0',live_id:'1',live_reason:'',need_persist_msg_count:'15',resp_content_type:'protobuf',screen_height:screen.height,screen_width:screen.width,support_wrds:'1',tz_name:'Asia/Shanghai',version_code:'180800',msToken:msToken,room_id:roomId,user_unique_id:uniqueId,live_pc:roomId,a_bogus:'00000000'});"
                + "try{const resp=await fetch('/webcast/im/fetch/?'+params.toString(),{credentials:'include'});"
                + "const buf=new Uint8Array(await resp.arrayBuffer());"
                + "const r=makeReader(buf);let cursor=null,internalExt=null,pushServer=null;"
                + "while(r.pos<buf.length){const tag=Number(r.varint());const f=tag>>3,w=tag&7;"
                + "if(w===2){const l=Number(r.varint());"
                + "if(f===2)cursor=r.str(l);else if(f===5)internalExt=r.str(l);else if(f===10)pushServer=r.str(l);else r.skip(l);}"
                + "else if(w===0)r.varint();else if(w===1)r.skip(8);else if(w===5)r.skip(4);else break;}"
                + "out.cursor=cursor||'';out.internalExt=internalExt||'';out.pushServer=pushServer||'';"
                + "}catch(e){out.error='imfetch:'+e.message;}"
                + "return out;})()";
    }

    private static String quote(String s) {
        return "'" + (s == null ? "" : s.replace("\\", "\\\\").replace("'", "\\'")) + "'";
    }
}
