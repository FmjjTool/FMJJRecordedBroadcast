package cn.fmjj.toolbox.engine.auth;

import javax.crypto.Cipher;
import javax.crypto.Mac;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.lang.foreign.Arena;
import java.lang.foreign.FunctionDescriptor;
import java.lang.foreign.Linker;
import java.lang.foreign.MemorySegment;
import java.lang.foreign.SymbolLookup;
import java.lang.foreign.ValueLayout;
import java.lang.invoke.MethodHandle;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Base64;

/**
 * Fernet 令牌的 Java 兼容实现（与 Python cryptography.fernet 逐字节互通）。
 *
 * wire format（Fernet spec）：
 * <pre>
 *   0x80 || timestamp(8B BE) || IV(16B) || AES-128-CBC/PKCS7(密文) || HMAC-SHA256(32B)
 *   整体 urlsafe-base64（带 = padding）
 * </pre>
 *
 * 密钥派生与 Python auth.py 一致：
 * {@code urlsafe_b64encode(sha256("fmjj-cookie-v1:" + machineGuid))}
 * → 32 字节：前 16 为 HMAC 签名键，后 16 为 AES 加密键。
 *
 * 跨语言一致性由 FernetCompatTest 保证：Java 能解密 Python 生成的 token，
 * 反之亦然（旧版 cookies.json 可直接迁移）。
 */
public final class FernetCompat {

    /** Python cryptography.fernet.InvalidToken。 */
    public static class InvalidTokenException extends RuntimeException {
        public InvalidTokenException(String message) { super(message); }
    }

    private static final byte VERSION = (byte) 0x80;
    private static final SecureRandom RANDOM = new SecureRandom();

    private FernetCompat() {}

    /** Python: _fernet_key() —— 返回 44 字符 ASCII（urlsafe base64 of sha256 digest）。 */
    public static byte[] deriveKey(String machineGuid) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] seed = ("fmjj-cookie-v1:" + machineGuid).getBytes(StandardCharsets.UTF_8);
            return Base64.getUrlEncoder().encode(md.digest(seed));
        } catch (Exception e) {
            throw new IllegalStateException("SHA-256 不可用", e);
        }
    }

    /** Python: _encrypt_cookie 的 Fernet 部分（不含 "enc:" 前缀）。 */
    public static String encrypt(String plaintext, byte[] keyB64) {
        try {
            byte[] key = Base64.getUrlDecoder().decode(keyB64);
            byte[] signingKey = copy(key, 0, 16);
            byte[] encryptionKey = copy(key, 16, 16);

            byte[] iv = new byte[16];
            RANDOM.nextBytes(iv);

            Cipher aes = Cipher.getInstance("AES/CBC/PKCS5Padding");
            aes.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(encryptionKey, "AES"), new IvParameterSpec(iv));
            byte[] ciphertext = aes.doFinal(plaintext.getBytes(StandardCharsets.UTF_8));

            long ts = System.currentTimeMillis() / 1000L;
            byte[] head = new byte[1 + 8 + 16];
            head[0] = VERSION;
            for (int i = 0; i < 8; i++) head[1 + i] = (byte) (ts >>> (56 - 8 * i));
            System.arraycopy(iv, 0, head, 9, 16);

            byte[] token = concat(head, ciphertext);
            byte[] hmac = hmacSha256(signingKey, token);
            return Base64.getUrlEncoder().encodeToString(concat(token, hmac));
        } catch (Exception e) {
            throw new IllegalStateException("Fernet 加密失败", e);
        }
    }

    /** Python: Fernet.decrypt —— 失败抛 InvalidTokenException。 */
    public static String decrypt(String tokenB64, byte[] keyB64) {
        try {
            byte[] key = Base64.getUrlDecoder().decode(keyB64);
            byte[] signingKey = copy(key, 0, 16);
            byte[] encryptionKey = copy(key, 16, 16);

            byte[] all = Base64.getUrlDecoder().decode(tokenB64);
            if (all.length < 1 + 8 + 16 + 32) throw new InvalidTokenException("token 过短");
            if (all[0] != VERSION) throw new InvalidTokenException("版本字节不匹配");

            int bodyLen = all.length - 32;
            byte[] hmac = hmacSha256(signingKey, copy(all, 0, bodyLen));
            if (!MessageDigest.isEqual(hmac, copy(all, bodyLen, 32))) {
                throw new InvalidTokenException("HMAC 校验失败");
            }
            byte[] iv = copy(all, 9, 16);
            byte[] ciphertext = copy(all, 25, bodyLen - 25);
            Cipher aes = Cipher.getInstance("AES/CBC/PKCS5Padding");
            aes.init(Cipher.DECRYPT_MODE, new SecretKeySpec(encryptionKey, "AES"), new IvParameterSpec(iv));
            return new String(aes.doFinal(ciphertext), StandardCharsets.UTF_8);
        } catch (InvalidTokenException e) {
            throw e;
        } catch (Exception e) {
            throw new InvalidTokenException("解密失败：" + e.getMessage());
        }
    }

    /** 读 token 内嵌时间戳（调试/迁移用）。 */
    public static long peekTimestamp(String tokenB64) {
        byte[] all = Base64.getUrlDecoder().decode(tokenB64);
        long ts = 0;
        for (int i = 1; i <= 8; i++) ts = (ts << 8) | (all[i] & 0xFF);
        return ts;
    }

    // ==================== 本机标识（Python: _machine_guid） ====================

    private static volatile String cachedGuid;

    /**
     * Python: _machine_guid() —— Windows MachineGuid（注册表）→ uuid.getnode() → "local-default"。
     * 同一台机器上两个语言版必须得到相同值，否则 Cookie 无法互通。
     */
    public static String machineGuid() {
        if (cachedGuid != null) return cachedGuid;
        String guid = windowsRegistryGuid();
        if (guid == null || guid.isBlank()) guid = regQueryGuid();
        if (guid == null || guid.isBlank()) guid = powershellGuid();
        if (guid == null || guid.isBlank()) {
            cn.fmjj.toolbox.core.Log.warn("读取 MachineGuid 失败，退化为 MAC 兜底（与 Python 版 Cookie 将不互通）");
            guid = macNodeFallback();
        }
        if (guid == null || guid.isBlank()) guid = "local-default";
        cachedGuid = guid;
        return guid;
    }

    /**
     * Python: _machine_guid() 的 Windows 主路径 —— HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid。
     * 用 JDK 22 FFM 直调 advapi32（零子进程；不依赖 reg.exe，避免被安全软件拦截）。
     */
    private static String windowsRegistryGuid() {
        String os = System.getProperty("os.name", "").toLowerCase(java.util.Locale.ROOT);
        if (!os.contains("win")) return null;
        try (Arena arena = Arena.ofConfined()) {
            var linker = Linker.nativeLinker();
            var lookup = SymbolLookup.libraryLookup("advapi32", arena);

            MethodHandle regOpenKeyEx = linker.downcallHandle(lookup.find("RegOpenKeyExW").orElseThrow(),
                    FunctionDescriptor.of(ValueLayout.JAVA_LONG,
                            ValueLayout.JAVA_LONG, ValueLayout.ADDRESS,
                            ValueLayout.JAVA_INT, ValueLayout.JAVA_INT, ValueLayout.ADDRESS));
            MethodHandle regQueryValueEx = linker.downcallHandle(lookup.find("RegQueryValueExW").orElseThrow(),
                    FunctionDescriptor.of(ValueLayout.JAVA_LONG,
                            ValueLayout.JAVA_LONG, ValueLayout.ADDRESS, ValueLayout.ADDRESS,
                            ValueLayout.ADDRESS, ValueLayout.ADDRESS, ValueLayout.ADDRESS));
            MethodHandle regCloseKey = linker.downcallHandle(lookup.find("RegCloseKey").orElseThrow(),
                    FunctionDescriptor.of(ValueLayout.JAVA_LONG, ValueLayout.JAVA_LONG));

            MemorySegment subKey = arena.allocateFrom("SOFTWARE\\Microsoft\\Cryptography", StandardCharsets.UTF_16LE);
            MemorySegment valueName = arena.allocateFrom("MachineGuid", StandardCharsets.UTF_16LE);
            MemorySegment hKeySlot = arena.allocate(ValueLayout.JAVA_LONG);

            // 注意：这里不能写成 (long)(Long) —— (Long) 装箱强转会改变 invokeExact 的
            // 期望签名（返回 Long 而非 long），运行时抛 WrongMethodTypeException
            long status = (long) regOpenKeyEx.invokeExact(
                    0x80000002L, subKey, 0, 0x20019, hKeySlot); // HKLM, KEY_READ
            if (status != 0) return null;
            long hKey = hKeySlot.get(ValueLayout.JAVA_LONG, 0);
            try {
                MemorySegment data = arena.allocate(512);
                MemorySegment size = arena.allocate(ValueLayout.JAVA_INT);
                size.set(ValueLayout.JAVA_INT, 0, 512);
                long qs = (long) regQueryValueEx.invokeExact(
                        hKey, valueName, MemorySegment.NULL, MemorySegment.NULL, data, size);
                if (qs != 0) return null;
                int bytes = size.get(ValueLayout.JAVA_INT, 0);
                if (bytes < 2) return null;
                return new String(data.toArray(java.lang.foreign.ValueLayout.JAVA_BYTE), 0, bytes - 2,
                        StandardCharsets.UTF_16LE).trim();
            } finally {
                // RegCloseKey 返回 LONG，必须接收（否则 invokeExact 期望 void → WrongMethodTypeException）
                long closed = (long) regCloseKey.invokeExact(hKey);
                if (closed != 0) {
                    cn.fmjj.toolbox.core.Log.warn("RegCloseKey 返回 " + closed);
                }
            }
        } catch (Throwable t) {
            cn.fmjj.toolbox.core.Log.warn("FFM 读取 MachineGuid 失败，尝试 reg query：" + t);
            return null;
        }
    }

    /**
     * 兜底 2：PowerShell 读注册表。
     * 部分环境会拦 reg.exe（安全软件/沙箱黑名单），PowerShell 通常仍可用。
     */
    private static String powershellGuid() {
        String os = System.getProperty("os.name", "").toLowerCase(java.util.Locale.ROOT);
        if (!os.contains("win")) return null;
        try {
            Process p = new ProcessBuilder("powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Cryptography').MachineGuid")
                    .redirectErrorStream(true).start();
            byte[] out = p.getInputStream().readAllBytes();
            p.waitFor(10, java.util.concurrent.TimeUnit.SECONDS);
            String text = new String(out, java.nio.charset.StandardCharsets.ISO_8859_1).trim();
            // 输出形如 6e9cd39c-8e44-4a0a-9345-0a4663bf27e1
            var m = java.util.regex.Pattern.compile("([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
                    .matcher(text);
            return m.find() ? m.group(1) : null;
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * 兜底：调 reg.exe 读 MachineGuid。
     * FFM 在某些 JDK/安全软件环境下受限（restricted method），必须有这条退路，
     * 否则会落到 MAC 兜底 → 密钥与 Python 版不一致 → 已保存的 Cookie 全部解不开。
     */
    private static String regQueryGuid() {
        String os = System.getProperty("os.name", "").toLowerCase(java.util.Locale.ROOT);
        if (!os.contains("win")) return null;
        try {
            Process p = new ProcessBuilder("reg", "query",
                    "HKLM\\SOFTWARE\\Microsoft\\Cryptography", "/v", "MachineGuid")
                    .redirectErrorStream(true).start();
            byte[] out = p.getInputStream().readAllBytes();
            p.waitFor(5, java.util.concurrent.TimeUnit.SECONDS);
            String text = new String(out, java.nio.charset.StandardCharsets.ISO_8859_1);
            var m = java.util.regex.Pattern.compile("MachineGuid\\s+REG_SZ\\s+(\\S+)").matcher(text);
            return m.find() ? m.group(1).trim() : null;
        } catch (Exception e) {
            return null;
        }
    }

    /** Python uuid.getnode() 的近似：第一个非虚拟网卡 MAC → 48 位无符号长整数。 */
    private static String macNodeFallback() {
        try {
            var interfaces = java.net.NetworkInterface.getNetworkInterfaces();
            while (interfaces.hasMoreElements()) {
                var ni = interfaces.nextElement();
                if (ni.isLoopback() || ni.isVirtual() || !ni.isUp()) continue;
                byte[] mac = ni.getHardwareAddress();
                if (mac == null || mac.length < 6) continue;
                long node = 0;
                for (int i = 0; i < 6; i++) node = (node << 8) | (mac[i] & 0xFF);
                return "node-" + node;
            }
        } catch (Exception ignored) {
        }
        return null;
    }

    // ==================== 工具 ====================

    private static byte[] hmacSha256(byte[] key, byte[] data) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(key, "HmacSHA256"));
        return mac.doFinal(data);
    }

    private static byte[] copy(byte[] src, int off, int len) {
        byte[] out = new byte[len];
        System.arraycopy(src, off, out, 0, len);
        return out;
    }

    private static byte[] concat(byte[] a, byte[] b) {
        byte[] out = new byte[a.length + b.length];
        System.arraycopy(a, 0, out, 0, a.length);
        System.arraycopy(b, 0, out, a.length, b.length);
        return out;
    }
}
