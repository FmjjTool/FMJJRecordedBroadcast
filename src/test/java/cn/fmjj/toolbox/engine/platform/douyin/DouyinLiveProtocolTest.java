package cn.fmjj.toolbox.engine.platform.douyin;

import org.junit.jupiter.api.Test;

import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 抖音弹幕协议跨语言一致性测试。
 *
 * <p>全部向量由 Python 原版 toolbox/douyin_live.py 运行时生成
 * （生成脚本：tools/gen_douyin_protocol_vectors.py），不是手写近似值。
 */
class DouyinLiveProtocolTest {

    private static byte[] b64(String s) {
        return Base64.getDecoder().decode(s);
    }

    private static String hex(byte[] data) {
        StringBuilder sb = new StringBuilder();
        for (byte b : data) sb.append(String.format("%02x", b));
        return sb.toString();
    }

    @Test
    void heartbeatAndAckBytesMatchPython() {
        assertEquals("3a026862", hex(DouyinLiveClient.heartbeatFrame()));
        assertEquals("3a0361636b420445585432107b",
                hex(DouyinLiveClient.ackFrame("EXT2", 123)));
    }

    @Test
    void decodePushFrameMatchesPython() {
        DouyinLiveClient.Frame f = DouyinLiveClient.decodePushFrame(b64(
                "EHsqFQoNY29tcHJlc3NfdHlwZRIEZ3ppcCoWCg9pbS1pbnRlcm5hbF9leHQSA0VYVDoDbXNnQgMBAgM="));
        assertEquals(123, f.logId());
        assertEquals("msg", f.payloadType());
        assertEquals("gzip", f.headers().get("compress_type"));
        assertEquals("EXT", f.headers().get("im-internal_ext"));
        assertArrayEquals(new byte[]{1, 2, 3}, f.payload());
    }

    @Test
    void decodeResponseMatchesPython() {
        DouyinLiveClient.Resp r = DouyinLiveClient.decodeResponse(b64(
                "CjQKEldlYmNhc3RDaGF0TWVzc2FnZRIeEg4aDOa1i+ivleeUqOaItxoM5L2g5aW95LiW55WMEgNDVVIqBEVYVDJIAQ=="));
        assertTrue(r.needAck());
        assertEquals("CUR", r.cursor());
        assertEquals("EXT2", r.internalExt());
        assertEquals(1, r.messages().size());
    }

    @Test
    void extractChatMessageMatchesPython() {
        DouyinLiveClient.Msg m = DouyinLiveClient.extractMsg(b64(
                "ChJXZWJjYXN0Q2hhdE1lc3NhZ2USHhIOGgzmtYvor5XnlKjmiLcaDOS9oOWlveS4lueVjA=="));
        assertEquals("WebcastChatMessage", m.type());
        assertEquals("弹幕", m.name());
        assertEquals("chat", m.category());
        assertEquals("测试用户", m.nickname());
        assertEquals("你好世界", m.content());
        assertEquals(1, m.giftCount());
        assertNull(m.giftName());
    }

    @Test
    void extractGiftMessageMatchesPython() {
        DouyinLiveClient.Msg m = DouyinLiveClient.extractMsg(b64(
                "ChJXZWJjYXN0R2lmdE1lc3NhZ2USazoOGgzmtYvor5XnlKjmiLcoAzADEAN6OWAFggEJ5bCP5b+D5b+DCikKJ2h0dHBzOi8vcDMtd2ViY2FzdGNkbi5kb3V5aW4uY29tL2Eud2VicJoBFxIV6YCB5Ye65LqG5bCP5b+D5b+DIHgz"));
        assertEquals("WebcastGiftMessage", m.type());
        assertEquals("礼物", m.name());
        assertEquals("测试用户", m.nickname());
        assertEquals("小心心", m.giftName());
        assertEquals(5, m.giftPrice());
        assertEquals("https://p3-webcastcdn.douyin.com/a.webp", m.giftIcon());
        assertEquals(3, m.giftCount());
        assertEquals("送出了小心心 x3", m.content());
        assertFalse(m.repeatEnd());
    }

    @Test
    void extractMemberLikeSocialMatchPython() {
        DouyinLiveClient.Msg member = DouyinLiveClient.extractMsg(b64(
                "ChRXZWJjYXN0TWVtYmVyTWVzc2FnZRIYEg4aDOa1i+ivleeUqOaIt1oG5p2l5LqG"));
        assertEquals("进场", member.name());
        assertEquals("测试用户", member.nickname());
        assertEquals("来了", member.content());

        DouyinLiveClient.Msg like = DouyinLiveClient.extractMsg(b64(
                "ChJXZWJjYXN0TGlrZU1lc3NhZ2USFCoOGgzmtYvor5XnlKjmiLcQAxhk"));
        assertEquals("点赞", like.name());
        assertEquals("为主播点赞了(3)", like.content());
        assertEquals(3, like.likeCount());
        assertEquals(100, like.likeTotal());

        DouyinLiveClient.Msg social = DouyinLiveClient.extractMsg(b64(
                "ChRXZWJjYXN0U29jaWFsTWVzc2FnZRISEg4aDOa1i+ivleeUqOaItzAB"));
        assertEquals("关注", social.name());
        assertEquals("关注了主播", social.content());
        assertEquals(1, social.followCount());
    }

    @Test
    void extractRoomSeqAndStatsMatchPython() {
        DouyinLiveClient.Msg seq = DouyinLiveClient.extractMsg(b64(
                "ChlXZWJjYXN0Um9vbVVzZXJTZXFNZXNzYWdlEg4Y0gk4j05CBjEw5LiHKw=="));
        assertEquals("在线人数", seq.name());
        assertEquals(1234, seq.roomTotal());
        assertEquals(9999, seq.roomTotalUser());
        assertEquals("10万+", seq.roomTotalUserStr());
        assertEquals("", seq.content());

        DouyinLiveClient.Msg stats = DouyinLiveClient.extractMsg(b64(
                "ChdXZWJjYXN0Um9vbVN0YXRzTWVzc2FnZRIYKOEhGhPlnKjnur/op4LkvJfvvJo0MzIx"));
        assertEquals("直播数据", stats.name());
        assertEquals(4321, stats.roomTotal());
        assertEquals("在线观众：4321", stats.content());
    }

    @Test
    void bannerJsonMatchesPython() {
        DouyinLiveClient.Msg m = DouyinLiveClient.extractMsg(b64(
                "ChpXZWJjYXN0SW5Sb29tQmFubmVyTWVzc2FnZRLqAhLnAnsiMjV2XzdfYWN0aXZpdHkiOiB7InRpdGxlIjogIua0u+WKqOagh+mimCIsICJzdWJ0aXRsZSI6ICLlia/moIfpopgiLCAic3RhdHVzIjogeyJOYW1lIjogIui/m+ihjOS4rSJ9LCAiX2V4dHJhIjogeyJydWxlIjogIuinhOWImeaWh+acrCJ9fSwgIjlfMV9naWZ0X3Rhc2siOiB7ImNvbXBvbmVudF9uYW1lIjogImdpZnRfdGFzayIsICJ0YXNrX2l0ZW1zIjogW3sic3RhdHVzIjogMSwgInBoYXNlcyI6IFt7ImNvbmRpdGlvbnMiOiBbeyJjdXJyZW50IjogMiwgInRhcmdldCI6IDV9XX1dfV19LCAiM18yX2ZhbnMiOiB7IjNfMl9mYW5zIjogeyJmb290IjogeyJleHRyYSI6IHsic2VsZWN0b3JzIjogWyLnmb7kuIfnsonlm6IiXX19fX19"));
        assertEquals("横幅", m.name());
        assertEquals("活动·活动标题 | 副标题 | [进行中] | 规则文本", m.content());

        String taskJson = "{\"9_1_gift\": {\"gift_task\": {\"component_name\": \"gift_task\","
                + " \"task_items\": [{\"status\": 1, \"phases\": [{\"conditions\":"
                + " [{\"current\": 2, \"target\": 5}]}]}, {\"status\": 2, \"phases\":"
                + " [{\"conditions\": [{\"current\": 10, \"target\": 10}]}]}]}}}";
        assertEquals("礼物·礼物任务：进行中 2/5；已完成 10/10",
                DouyinLiveClient.parseBannerJson(taskJson));
    }

    @Test
    void buildWsUrlMatchesPythonParams() {
        String url = DouyinLiveClient.buildWsUrl(
                "wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/",
                123456789, DouyinLiveClient.DEFAULT_UNIQUE_ID, "CUR", "EXT", "SIG");
        assertTrue(url.startsWith("wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/?"));
        Map<String, String> q = new LinkedHashMap<>();
        for (String pair : url.substring(url.indexOf('?') + 1).split("&")) {
            int eq = pair.indexOf('=');
            q.put(pair.substring(0, eq),
                    URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8));
        }
        assertEquals("6383", q.get("aid"));
        assertEquals("123456789", q.get("room_id"));
        assertEquals(DouyinLiveClient.DEFAULT_UNIQUE_ID, q.get("user_unique_id"));
        assertEquals("CUR", q.get("cursor"));
        assertEquals("EXT", q.get("internal_ext"));
        assertEquals("SIG", q.get("signature"));
        assertEquals("gzip", q.get("compress"));
        assertEquals(DouyinLive.UA, q.get("browser_version"));
        assertEquals("https://live.douyin.com", q.get("host"));
    }

    @Test
    void parseImBodyMatchesPython() {
        // 向量由 Python 运行时按 protobuf 字段 2/5/10 编码生成（wss_push_did 在 field 5 内）
        DouyinLiveClient.ImBody im = DouyinLiveClient.parseImBody(b64(
                "EgRDVVIyKiF3c3NfcHVzaF9kaWQ6MTIzNDU2Nzg5MDEyMzQ1NiZrPXYaBlNLSVBNRVI3d3NzOi8vd2ViY2FzdDUtd3Mtd2ViLWxmLmRvdXlpbi5jb20vd2ViY2FzdC9pbS9wdXNoL3YyLw=="));
        assertEquals("CUR2", im.cursor());
        assertEquals("wss_push_did:1234567890123456&k=v", im.internalExt());
        assertEquals("wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/", im.pushServer());

        // 缺 pushServer（field 10）→ 视为无效，走旧路径兜底
        assertNull(DouyinLiveClient.parseImBody(b64("EgNDVVIqA0VYVA==")));
    }

    @Test
    void extractDidPrefersInternalExtThenUrl() {
        assertEquals("1234567890123456", DouyinLiveClient.extractDid(
                "wss_push_did:1234567890123456&k=v", "https://x/?user_unique_id=1"));
        assertEquals("777", DouyinLiveClient.extractDid(
                "no-did", "https://x/?a=1&user_unique_id=777&b=2"));
        assertEquals("", DouyinLiveClient.extractDid("", "https://x/"));
    }

    @Test
    void buildWsUrlUsesCapturedDidAndScreen() {
        String url = DouyinLiveClient.buildWsUrl(
                "wss://webcast5-ws-web-lf.douyin.com/webcast/im/push/v2/",
                123456789, "987654321", "CUR", "EXT", "SIG", 1280, 720);
        Map<String, String> q = new LinkedHashMap<>();
        for (String pair : url.substring(url.indexOf('?') + 1).split("&")) {
            int eq = pair.indexOf('=');
            q.put(pair.substring(0, eq),
                    URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8));
        }
        assertEquals("987654321", q.get("user_unique_id"));
        assertEquals("1280", q.get("screen_width"));
        assertEquals("720", q.get("screen_height"));
    }
}
