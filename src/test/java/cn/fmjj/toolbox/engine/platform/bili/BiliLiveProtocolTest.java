package cn.fmjj.toolbox.engine.platform.bili;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.zip.Deflater;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 弹幕协议纯逻辑测试（不发网络）：封包字节布局、zlib 解压。
 *
 * <p>WebSocket 握手在 JDK 侧被服务端断开（1006/RST），详见路线图 3.19；
 * 协议本身的封包/解包逻辑用单测保证正确。
 */
class BiliLiveProtocolTest {

    @Test
    @DisplayName("pack：>IHHII 头（总长/16/1/op/1）+ body")
    void pack() {
        byte[] body = "{\"a\":1}".getBytes(StandardCharsets.UTF_8);
        byte[] packet = BiliLiveClient.pack(7, body);
        assertEquals(16 + body.length, packet.length);

        ByteBuffer bb = ByteBuffer.wrap(packet);
        assertEquals(16 + body.length, bb.getInt(), "总长");
        assertEquals(16, bb.getShort() & 0xFFFF, "头长");
        assertEquals(1, bb.getShort() & 0xFFFF, "版本");
        assertEquals(7, bb.getInt(), "操作码（认证）");
        assertEquals(1, bb.getInt(), "序列");
        assertArrayEquals(body, java.util.Arrays.copyOfRange(packet, 16, packet.length));
    }

    @Test
    @DisplayName("pack：心跳包只有 16 字节头")
    void packHeartbeat() {
        byte[] packet = BiliLiveClient.pack(2, new byte[0]);
        assertEquals(16, packet.length);
        ByteBuffer bb = ByteBuffer.wrap(packet);
        assertEquals(16, bb.getInt());
        assertEquals(2, bb.getInt(8), "op=2 心跳（偏移 8：total4+hlen2+ver2）");
    }

    @Test
    @DisplayName("inflate：zlib 压缩体可解回原文（protover=2 分支）")
    void inflate() throws Exception {
        String text = "{\"cmd\":\"DANMU_MSG\",\"info\":[[],\"测试弹幕\",[1,\"用户A\"]]}".repeat(20);
        byte[] raw = text.getBytes(StandardCharsets.UTF_8);

        Deflater deflater = new Deflater();
        deflater.setInput(raw);
        deflater.finish();
        ByteArrayOutputStream compressed = new ByteArrayOutputStream();
        byte[] buf = new byte[4096];
        while (!deflater.finished()) {
            int n = deflater.deflate(buf);
            compressed.write(buf, 0, n);
        }
        deflater.end();

        assertTrue(compressed.size() < raw.length, "压缩应更小");
        assertArrayEquals(raw, BiliLiveClient.inflate(compressed.toByteArray()));
    }
}
