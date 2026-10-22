package cn.fmjj.toolbox.engine.auth;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Fernet 跨语言一致性测试。
 *
 * 向量由 Python cryptography.fernet 生成（密钥派生复刻 auth.py：
 * urlsafe_b64encode(sha256("fmjj-cookie-v1:" + GUID))，GUID=TEST-MACHINE-GUID-001）：
 * <pre>
 *   KEY_B64 = EEkZSUwanS0jSKeJBkt5vofJfeNTxXPIYBq9ORfZKwU=
 *   ENC 样例（5 条明文→token 映射见下方常量）
 *   篡改 token → InvalidToken（HMAC 校验）
 * </pre>
 */
class FernetCompatTest {

    private static final String GUID = "TEST-MACHINE-GUID-001";
    private static final String KEY_B64 = "EEkZSUwanS0jSKeJBkt5vofJfeNTxXPIYBq9ORfZKwU=";
    private static final String T1 = "gAAAAABq0HLXbCPPXxzInyeU6DAXeY8IX2UoGIbw2ige2pN3AO37B-svyKSurLVGmbLhZzxI0IW70hlDW2sNlihExJltYkOmGeBft31s0DfmOH406sA6Fsne1VNT1rwo8tbuy34sVZDW8HwCCiWsGaB2tVGZy9rDCA==";
    private static final String T2 = "gAAAAABq0HLX_b0gWCD8sF-H365qbvmsXdjE8I3JYdOeeBpbarftmokZlXg4aPsMtVFJDKf3foL5t-o1pcyq6oNipOQvG4bsEw==";
    private static final String T3 = "gAAAAABq0HLXBMeQZ1vVruVVLavkzYL_qdoucvuegaVeeVfsfD8185itMjmCKCdqUdc4q1a5EfTMbKJhoAnMNnOHGtDVBlszxReWIPd2h1Ij_xwzHs-oec26jSYmv8j9J3OFsz9Y7G_y";
    private static final String T4 = "gAAAAABq0HLX2sF3nUg7ckaILUai6HxrK3LDZwjBYB12jIQxusCDypzVsPcE0qRRVY6OAGcWbHbvpLPqB1kKMrRgDwccr4A9RQ==";
    private static final String T5 = "gAAAAABq0HLXVNdkdQdecIfXHUwz33h_iOdI0M8Mmgz5YNUbw2vVGhyv3DIuoPXH62Ic3de3FiFz5CDExmNgjCdvV1gldKMz2A_2mVZfQCo5tMcQyKTXiu_NfVyBiOVwj5wBCJvurKPx";

    private final byte[] key = KEY_B64.getBytes(StandardCharsets.US_ASCII);

    @Test
    @DisplayName("密钥派生与 Python auth.py._fernet_key 一致")
    void deriveKeyMatchesPython() {
        assertArrayEquals(key, FernetCompat.deriveKey(GUID));
    }

    @Test
    @DisplayName("Java 解密 Python 生成的 5 条 token（跨语言兼容硬指标）")
    void decryptPythonTokens() {
        assertEquals("SESSDATA=abc123; bili_jct=def456; DedeUserID=1234567",
                FernetCompat.decrypt(T1, key));
        assertEquals("MUSIC_U=1a2b3c", FernetCompat.decrypt(T2, key));
        assertEquals("无中文cookie值=测试&special=+/-_", FernetCompat.decrypt(T3, key));
        assertEquals("", FernetCompat.decrypt(T4, key));
        assertEquals("KugouID=123; userid=456; token=xyz", FernetCompat.decrypt(T5, key));
    }

    @Test
    @DisplayName("Java 加密可自解密；明文->token->明文往返")
    void roundTrip() {
        for (String plain : new String[] {
                "SESSDATA=abc123; bili_jct=def456; DedeUserID=1234567",
                "无中文cookie值=测试&special=+/-_",
                "KugouID=123; userid=456; token=xyz",
                "" }) {
            String token = FernetCompat.encrypt(plain, key);
            assertTrue(token.startsWith("gAAAAA"), token.substring(0, Math.min(10, token.length())));
            assertEquals(plain, FernetCompat.decrypt(token, key));
        }
    }

    @Test
    @DisplayName("Java 加密的 token 时间戳可读且与 Python 同前缀格式")
    void tokenTimestamp() {
        String token = FernetCompat.encrypt("x", key);
        long ts = FernetCompat.peekTimestamp(token);
        long now = System.currentTimeMillis() / 1000;
        assertTrue(Math.abs(now - ts) < 60, "ts=" + ts + " now=" + now);
        // 与 Python token 前缀同为 gAAAAA（version 0x80 + ts 高位在小端 base64 下的稳定形态）
        assertEquals("gAAAAA", token.substring(0, 6));
    }

    @Test
    @DisplayName("篡改 token → InvalidTokenException；错误密钥 → InvalidTokenException")
    void tamperAndWrongKey() {
        String token = FernetCompat.encrypt("SESSDATA=secret", key);
        // 改最后一个 base64 字符（落在 HMAC 区）
        char last = token.charAt(token.length() - 1);
        String tampered = token.substring(0, token.length() - 1) + (last == 'A' ? 'B' : 'A');
        assertThrows(FernetCompat.InvalidTokenException.class, () -> FernetCompat.decrypt(tampered, key));

        byte[] wrongKey = FernetCompat.deriveKey("ANOTHER-MACHINE");
        assertThrows(FernetCompat.InvalidTokenException.class,
                () -> FernetCompat.decrypt(token, wrongKey));

        assertThrows(FernetCompat.InvalidTokenException.class,
                () -> FernetCompat.decrypt("not-base64-token!!!", key));
    }

    @Test
    @DisplayName("不同机器 GUID 派生不同密钥")
    void keyVariesByMachine() {
        assertNotEquals(new String(FernetCompat.deriveKey("A"), StandardCharsets.US_ASCII),
                new String(FernetCompat.deriveKey("B"), StandardCharsets.US_ASCII));
    }

    @Test
    @DisplayName("machineGuid：Windows 注册表可读到；非空")
    void machineGuidReadable() {
        String guid = FernetCompat.machineGuid();
        assertTrue(guid != null && !guid.isBlank(), "machineGuid 为空");
    }
}
