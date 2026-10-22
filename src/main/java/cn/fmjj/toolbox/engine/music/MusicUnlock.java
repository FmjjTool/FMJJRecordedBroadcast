package cn.fmjj.toolbox.engine.music;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import javax.crypto.Cipher;
import javax.crypto.spec.SecretKeySpec;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * 加密音乐解密（Unlock Music 算法移植）。
 * 逐行移植自 Python 版 music_unlock.py 的核心四族：
 * NCM（网易云）/ QMC（QQ音乐 v1 静态表 + v2 Map/RC4）/ KGM（酷狗）/ KWM（酷我）。
 *
 * 未移植：XM（虾米）/ TM（喜马拉雅）/ XMLY（x2m/x3m）—— 见路线图 M7 备注。
 */
public final class MusicUnlock {

    // ==================== 固定密钥 ====================

    private static final byte[] CORE_KEY = hex("687a4852416d736f356b496e62617857");   // "hzHRAmso5kInbaxW"
    private static final byte[] META_KEY = hex("2331346C6A6B5F215C5D2630553C2728");   // "#14ljk_!\]&0U<'("
    private static final byte[] NCM_HEADER = "CTENFDAM".getBytes(StandardCharsets.US_ASCII);

    private static final byte[] KWM_HEADER1 = "yeelion-kuwo-tme".getBytes(StandardCharsets.US_ASCII);
    private static final byte[] KWM_HEADER2 = "yeelion-kuwo\0\0\0\0".getBytes(StandardCharsets.US_ASCII);
    private static final String KWM_KEY = "MoOtOiTvINGwd2E6n0E1i7L5t2IoOoNk";

    /** Python: _QMC_EXT_MAP。 */
    public static final Map<String, String> QMC_EXT_MAP = Map.ofEntries(
            Map.entry("mgg", "ogg"), Map.entry("mgg0", "ogg"), Map.entry("mgg1", "ogg"),
            Map.entry("mggl", "ogg"), Map.entry("mflac", "flac"), Map.entry("mflac0", "flac"),
            Map.entry("mmp4", "m4a"), Map.entry("qmcflac", "flac"), Map.entry("qmcogg", "ogg"),
            Map.entry("qmc0", "mp3"), Map.entry("qmc2", "ogg"), Map.entry("qmc3", "mp3"),
            Map.entry("qmc4", "ogg"), Map.entry("qmc6", "ogg"), Map.entry("qmc8", "ogg"),
            Map.entry("tkm", "m4a"), Map.entry("bkcmp3", "mp3"), Map.entry("bkcm4a", "m4a"),
            Map.entry("bkcflac", "flac"), Map.entry("bkcwav", "wav"));

    private static final Set<String> KGM_EXTS = Set.of("kgm", "kgma", "vpr");
    private static final Set<String> TM_EXTS = Set.of("tm0", "tm2", "tm3", "tm6");
    private static final Set<String> XMLY_EXTS = Set.of("x2m", "x3m");

    /** XM（虾米）类型映射。 */
    private static final Map<String, String> XM_TYPE_MAP = Map.of(
            " WAV", "wav", "FLAC", "flac", " MP3", "mp3", " A4M", "m4a");
    private static final byte[] TM_HEADER = {0, 0, 0, 0x20, 'f', 't', 'y', 'p'};

    private MusicUnlock() {}

    /** 解密结果。 */
    public record Result(byte[] audio, Map<String, Object> meta, String ext) {}

    /** 支持的扩展名（不含点，小写）。 */
    public static Set<String> supportedExts() {
        Set<String> all = new java.util.HashSet<>(QMC_EXT_MAP.keySet());
        all.addAll(KGM_EXTS);
        all.addAll(TM_EXTS);
        all.addAll(XMLY_EXTS);
        all.addAll(Set.of("ncm", "kwm", "xm"));
        return all;
    }

    public static boolean isSupported(String ext) {
        return ext != null && supportedExts().contains(ext.toLowerCase(Locale.ROOT).replace(".", ""));
    }

    // ==================== 统一入口 ====================

    /** Python: decrypt_file —— 按扩展名分发，返回 (音频, meta, 输出扩展名)。 */
    public static Result decrypt(Path file) throws IOException {
        String ext = extOf(file);
        byte[] data = Files.readAllBytes(file);
        Result r;
        if ("ncm".equals(ext)) r = decryptNcm(data);
        else if ("kwm".equals(ext)) r = decryptKwm(data);
        else if ("xm".equals(ext)) r = decryptXm(data);
        else if (TM_EXTS.contains(ext)) r = decryptTm(data);
        else if (XMLY_EXTS.contains(ext)) r = decryptXmly(data, ext);
        else if (KGM_EXTS.contains(ext)) r = decryptKgm(data, ext);
        else if (QMC_EXT_MAP.containsKey(ext)) r = decryptQmc(data, ext);
        else throw new IllegalArgumentException("不支持的格式: ." + ext);
        String outExt = r.ext();
        if (outExt == null || outExt.isEmpty()) outExt = detectFormat(r.audio());
        return new Result(r.audio(), r.meta(), outExt);
    }

    /** Python: decrypt_to —— 解密并保存（meta 有名字用 meta 名，重名追加原文件名）。 */
    public static Path decryptTo(Path file, Path outDir) throws IOException {
        Result r = decrypt(file);
        String name = r.meta() != null && !r.meta().isEmpty()
                ? metaName(r.meta(), stemOf(file)) : stemOf(file);
        name = safeName(name);
        Files.createDirectories(outDir);
        Path out = outDir.resolve(name + "." + r.ext());
        if (Files.exists(out)) {
            out = outDir.resolve(name + "_" + stemOf(file) + "." + r.ext());
        }
        Files.write(out, r.audio());
        return out;
    }

    // ==================== NCM ====================

    static Result decryptNcm(byte[] data) {
        if (data.length < 16 || !startsWith(data, NCM_HEADER)) {
            throw new IllegalArgumentException("不是有效的 ncm 文件");
        }
        int pos = 10;
        int keyLen = readIntLE(data, pos);
        pos += 4;
        byte[] keyData = new byte[keyLen];
        for (int i = 0; i < keyLen; i++) keyData[i] = (byte) (data[pos + i] ^ 0x64);
        pos += keyLen;
        byte[] keyPlain = aesEcbDecryptPkcs7(CORE_KEY, keyData);
        byte[] keyBox = ncmRc4Keystream(java.util.Arrays.copyOfRange(keyPlain, 17, keyPlain.length));

        int metaLen = readIntLE(data, pos);
        pos += 4;
        Map<String, Object> meta = new LinkedHashMap<>();
        if (metaLen > 0) {
            byte[] metaEnc = new byte[metaLen];
            for (int i = 0; i < metaLen; i++) metaEnc[i] = (byte) (data[pos + i] ^ 0x63);
            pos += metaLen;
            try {
                byte[] b64 = Base64.getDecoder().decode(
                        java.util.Arrays.copyOfRange(metaEnc, 22, metaEnc.length));
                String text = new String(aesEcbDecryptPkcs7(META_KEY, b64), StandardCharsets.UTF_8);
                int idx = text.indexOf(':');
                if (idx >= 0) {
                    String head = text.substring(0, idx);
                    String body = text.substring(idx + 1);
                    JsonNode node = new ObjectMapper().readTree(body);
                    if ("dj".equals(head) && node.has("mainMusic")) node = node.get("mainMusic");
                    meta = new ObjectMapper().convertValue(node, Map.class);
                }
            } catch (Exception ignored) {
                meta = new LinkedHashMap<>();
            }
        }

        // CRC(4) + 0x00(1) + 图片大小(4) + 图片数据
        pos += 5;
        int imgSize = readIntLE(data, pos);
        pos += 4 + imgSize;

        byte[] audio = new byte[data.length - pos];
        for (int i = 0; i < audio.length; i++) {
            audio[i] = (byte) (data[pos + i] ^ keyBox[i & 0xFF]);
        }
        return new Result(audio, meta, "");
    }

    /** NCM 的 RC4 变体（返回 256 字节密钥流）。 */
    static byte[] ncmRc4Keystream(byte[] key) {
        int keyLen = key.length;
        int[] s = new int[256];
        for (int i = 0; i < 256; i++) s[i] = i;
        int j = 0;
        for (int i = 0; i < 256; i++) {
            j = (j + s[i] + (key[i % keyLen] & 0xFF)) & 0xFF;
            int t = s[i]; s[i] = s[j]; s[j] = t;
        }
        byte[] ks = new byte[256];
        for (int idx = 0; idx < 256; idx++) {
            int i = (idx + 1) % 256;
            int si = s[i];
            int sj = s[(i + si) & 0xFF];
            ks[idx] = (byte) s[(si + sj) & 0xFF];
        }
        return ks;
    }

    static byte[] aesEcbDecrypt(byte[] key, byte[] data) {
        try {
            Cipher c = Cipher.getInstance("AES/ECB/NoPadding");
            c.init(Cipher.DECRYPT_MODE, new SecretKeySpec(key, "AES"));
            return c.doFinal(data);
        } catch (Exception e) {
            throw new IllegalStateException("AES 解密失败：" + e.getMessage(), e);
        }
    }

    static byte[] aesEcbDecryptPkcs7(byte[] key, byte[] data) {
        byte[] raw = aesEcbDecrypt(key, data);
        if (raw.length == 0) return raw;
        int pad = raw[raw.length - 1] & 0xFF;
        if (pad > 0 && pad <= 16) return java.util.Arrays.copyOfRange(raw, 0, raw.length - pad);
        return raw;
    }

    // ==================== QMC ====================

    static Result decryptQmc(byte[] data, String ext) {
        String outExt = QMC_EXT_MAP.getOrDefault(ext, "mp3");
        int size = data.length;
        if (size < 8) throw new IllegalArgumentException("文件太小，不是有效的 qmc 文件");

        byte[] last4 = java.util.Arrays.copyOfRange(data, size - 4, size);
        int audioSize = size;
        byte[] keyDec = null;

        if (new String(last4, StandardCharsets.US_ASCII).equals("STag")) {
            throw new IllegalArgumentException("文件中没有写入密钥，无法解锁（请降级 App 后重新下载）");
        } else if (new String(last4, StandardCharsets.US_ASCII).equals("QTag")) {
            int keySize = readIntBE(data, size - 8);
            audioSize = size - keySize - 8;
            byte[] rawKey = java.util.Arrays.copyOfRange(data, audioSize, size - 8);
            int comma = indexOf(rawKey, (byte) ',');
            if (comma < 0) throw new IllegalArgumentException("invalid key");
            keyDec = qmcDeriveKey(java.util.Arrays.copyOfRange(rawKey, 0, comma));
        } else {
            int keySize = readIntLE(data, size - 4);
            if (keySize < 0x400) {
                audioSize = size - keySize - 4;
                byte[] rawKey = java.util.Arrays.copyOfRange(data, audioSize, size - 4);
                keyDec = qmcDeriveKey(rawKey);
            }
        }

        byte[] audio = java.util.Arrays.copyOfRange(data, 0, audioSize);
        if (keyDec == null || keyDec.length == 0) {
            QmcStaticCipher cipher = new QmcStaticCipher();
            for (int i = 0; i < audio.length; i++) audio[i] ^= (byte) cipher.mask(i);
        } else if (keyDec.length > 300) {
            new QmcRc4Cipher(keyDec).decrypt(audio, 0);
        } else {
            QmcMapCipher cipher = new QmcMapCipher(keyDec);
            for (int i = 0; i < audio.length; i++) audio[i] ^= (byte) cipher.mask(i);
        }
        return new Result(audio, new LinkedHashMap<>(), outExt);
    }

    /** Python: _qmc_derive_key。 */
    static byte[] qmcDeriveKey(byte[] rawKey) {
        byte[] rawDec = Base64.getDecoder().decode(rawKey);
        if (rawDec.length < 16) throw new IllegalArgumentException("key length is too short");
        rawDec = qmcDecryptV2Key(rawDec);

        byte[] simpleKey = qmcSimpleMakeKey(106, 8);
        byte[] teaKey = new byte[16];
        for (int i = 0; i < 8; i++) {
            teaKey[i << 1] = simpleKey[i];
            teaKey[(i << 1) + 1] = rawDec[i];
        }
        byte[] sub = Tea.decryptCbc(java.util.Arrays.copyOfRange(rawDec, 8, rawDec.length), teaKey, 32);
        byte[] out = new byte[8 + sub.length];
        System.arraycopy(rawDec, 0, out, 0, 8);
        System.arraycopy(sub, 0, out, 8, sub.length);
        return out;
    }

    static byte[] qmcSimpleMakeKey(int salt, int length) {
        byte[] out = new byte[length];
        for (int i = 0; i < length; i++) {
            out[i] = (byte) (0xFF & (int) (Math.abs(Math.tan(salt + i * 0.1)) * 100.0));
        }
        return out;
    }

    /** Python: _qmc_decrypt_v2_key —— EncV2 主密钥（两次 TEA + base64）。 */
    static byte[] qmcDecryptV2Key(byte[] key) {
        byte[] prefix = "QQMusic EncV2,Key:".getBytes(StandardCharsets.US_ASCII);
        if (key.length < 18 || !startsWith(key, prefix)) return key;
        byte[] out = Tea.decryptCbc(java.util.Arrays.copyOfRange(key, 18, key.length),
                UnlockKeys.bytes("QMC_MIX_KEY1"), 32);
        out = Tea.decryptCbc(out, UnlockKeys.bytes("QMC_MIX_KEY2"), 32);
        return Base64.getDecoder().decode(out);
    }

    /** QMC v1 静态密码箱。 */
    static final class QmcStaticCipher {
        int mask(int offset) {
            if (offset > 0x7FFF) offset %= 0x7FFF;
            return UnlockKeys.get("QMC_STATIC_CIPHER_BOX")[(offset * offset + 27) & 0xFF];
        }
    }

    /** QMC v2 Map 密码（key <= 300）。 */
    static final class QmcMapCipher {
        private final byte[] key;
        private final int n;

        QmcMapCipher(byte[] key) {
            this.key = key;
            this.n = key.length;
        }

        int mask(int offset) {
            if (offset > 0x7FFF) offset %= 0x7FFF;
            int idx = (int) (((long) offset * offset + 71214) % n);
            int rotate = ((idx & 0x7) + 4) % 8;
            int value = key[idx] & 0xFF;
            int left = (value << rotate) & 0xFF;
            int right = value >> rotate;
            return (left | right) & 0xFF;
        }
    }

    /** QMC v2 RC4 密码（key > 300），分段 5120。 */
    static final class QmcRc4Cipher {
        static final int FIRST_SEGMENT_SIZE = 0x80;
        static final int SEGMENT_SIZE = 5120;

        private final byte[] key;
        private final int n;
        private final int[] s;
        private final long hash;

        QmcRc4Cipher(byte[] key) {
            this.key = key;
            this.n = key.length;
            this.s = new int[n];
            for (int i = 0; i < n; i++) s[i] = i & 0xFF;
            int j = 0;
            for (int i = 0; i < n; i++) {
                j = (s[i] + j + (key[i % n] & 0xFF)) % n;
                int t = s[i]; s[i] = s[j]; s[j] = t;
            }
            long h = 1;
            for (int i = 0; i < n; i++) {
                int value = key[i] & 0xFF;
                if (value == 0) continue;
                long next = (h * value) & 0xFFFFFFFFL;
                if (next == 0 || next <= h) break;
                h = next;
            }
            this.hash = h;
        }

        int segmentKey(int idx) {
            int seed = key[idx % n] & 0xFF;
            if (seed == 0) return 0;
            return (int) ((double) hash / ((double) (idx + 1) * seed) * 100.0) % n;
        }

        void encFirstSegment(byte[] buf, int offset, int start, int end) {
            for (int i = start; i < end; i++) {
                buf[i] ^= key[segmentKey(offset + (i - start))];
            }
        }

        void encASegment(byte[] buf, int offset, int start, int end) {
            int[] S = s.clone();
            int skipLen = (offset % SEGMENT_SIZE) + segmentKey(offset / SEGMENT_SIZE);
            int j = 0, k = 0;
            for (int i = -skipLen; i < end - start; i++) {
                j = (j + 1) % n;
                k = (S[j] + k) % n;
                int t = S[j]; S[j] = S[k]; S[k] = t;
                if (i >= 0) {
                    buf[start + i] ^= (byte) S[(S[j] + S[k]) % n];
                }
            }
        }

        void decrypt(byte[] buf, int offset) {
            int toProcess = buf.length;
            int processed = 0;

            if (offset < FIRST_SEGMENT_SIZE) {
                int ln = Math.min(buf.length, FIRST_SEGMENT_SIZE - offset);
                encFirstSegment(buf, offset, processed, processed + ln);
                processed += ln;
                offset += ln;
                toProcess -= ln;
                if (toProcess == 0) return;
            }
            if (offset % SEGMENT_SIZE != 0) {
                int ln = Math.min(SEGMENT_SIZE - (offset % SEGMENT_SIZE), toProcess);
                encASegment(buf, offset, processed, processed + ln);
                processed += ln;
                offset += ln;
                toProcess -= ln;
            }
            while (toProcess > SEGMENT_SIZE) {
                encASegment(buf, offset, processed, processed + SEGMENT_SIZE);
                processed += SEGMENT_SIZE;
                offset += SEGMENT_SIZE;
                toProcess -= SEGMENT_SIZE;
            }
            if (toProcess > 0) {
                encASegment(buf, offset, processed, processed + toProcess);
            }
        }
    }

    // ==================== KGM / VPR ====================

    static Result decryptKgm(byte[] data, String ext) {
        boolean isVpr = "vpr".equals(ext);
        byte[] header = UnlockKeys.bytes(isVpr ? "KGM_VPR_HEADER" : "KGM_KGM_HEADER");
        if (data.length < 0x2C || !startsWith(data, header)) {
            throw new IllegalArgumentException("不是有效的 " + ext + " 文件");
        }
        int headerLen = readIntLE(data, 0x10);
        byte[] key = new byte[17];
        System.arraycopy(data, 0x1C, key, 0, 16);
        key[16] = 0;

        byte[] body = java.util.Arrays.copyOfRange(data, headerLen, data.length);
        int[] vprDiff = isVpr ? UnlockKeys.get("KGM_VPR_MASK_DIFF") : null;
        for (int i = 0; i < body.length; i++) {
            int med8 = (key[i % 17] & 0xFF) ^ (body[i] & 0xFF);
            med8 ^= (med8 & 0xF) << 4;
            int msk8 = kgmMask(i);
            msk8 ^= (msk8 & 0xF) << 4;
            int out = med8 ^ msk8;
            if (isVpr) out ^= vprDiff[i % 17];
            body[i] = (byte) (out & 0xFF);
        }
        return new Result(body, new LinkedHashMap<>(), "");
    }

    static int kgmMask(int pos) {
        int[] t1 = UnlockKeys.get("KGM_TABLE1");
        int[] t2 = UnlockKeys.get("KGM_TABLE2");
        int[] predef = UnlockKeys.get("KGM_MASK_V2_PREDEF");
        int offset = pos >> 4;
        int value = 0;
        while (offset >= 0x11) {
            value ^= t1[offset % 272];
            offset >>= 4;
            value ^= t2[offset % 272];
            offset >>= 4;
        }
        return predef[pos % 272] ^ value;
    }

    // ==================== KWM ====================

    static Result decryptKwm(byte[] data) {
        if (data.length < 0x400 || !(startsWith(data, KWM_HEADER1) || startsWith(data, KWM_HEADER2))) {
            throw new IllegalArgumentException("不是有效的 kwm 文件");
        }
        long fileKey = 0;
        for (int i = 0; i < 8; i++) {
            fileKey |= (long) (data[0x18 + i] & 0xFF) << (8 * i);
        }
        String keyStr = String.valueOf(fileKey);
        if (keyStr.length() > 32) {
            keyStr = keyStr.substring(0, 32);
        } else if (keyStr.length() < 32) {
            StringBuilder sb = new StringBuilder();
            while (sb.length() < 32) sb.append(keyStr);
            keyStr = sb.substring(0, 32);
        }
        byte[] mask = new byte[32];
        for (int i = 0; i < 32; i++) {
            mask[i] = (byte) (KWM_KEY.charAt(i) ^ keyStr.charAt(i));
        }
        byte[] audio = java.util.Arrays.copyOfRange(data, 0x400, data.length);
        for (int i = 0; i < audio.length; i++) {
            audio[i] ^= mask[i % 0x20];
        }
        return new Result(audio, new LinkedHashMap<>(), "");
    }

    // ==================== XM（虾米） ====================

    static Result decryptXm(byte[] data) {
        if (data.length < 0x10
                || !(data[0] == 'i' && data[1] == 'f' && data[2] == 'm' && data[3] == 't')
                || !(data[8] == (byte) 0xFE && data[9] == (byte) 0xFE
                     && data[10] == (byte) 0xFE && data[11] == (byte) 0xFE)) {
            throw new IllegalArgumentException("不是有效的 xm 文件");
        }
        String typeText = new String(java.util.Arrays.copyOfRange(data, 4, 8), StandardCharsets.ISO_8859_1);
        String outExt = XM_TYPE_MAP.get(typeText);
        if (outExt == null) throw new IllegalArgumentException("未知的 xm 文件类型: " + typeText);

        int key = data[0xF] & 0xFF;
        int offset = (data[0xC] & 0xFF) | ((data[0xD] & 0xFF) << 8) | ((data[0xE] & 0xFF) << 16);
        byte[] audio = java.util.Arrays.copyOfRange(data, 0x10, data.length);
        for (int cur = offset; cur < audio.length; cur++) {
            audio[cur] = (byte) (((((audio[cur] & 0xFF) - key) & 0xFF) ^ 0xFF));
        }
        return new Result(audio, new LinkedHashMap<>(), outExt);
    }

    // ==================== TM（喜马拉雅） ====================

    static Result decryptTm(byte[] data) {
        byte[] audio = data.clone();
        System.arraycopy(TM_HEADER, 0, audio, 0, 8);
        return new Result(audio, new LinkedHashMap<>(), "m4a");
    }

    // ==================== x2m / x3m（喜马拉雅 xmly） ====================

    static Result decryptXmly(byte[] data, String ext) {
        byte[] audio = data.clone();
        int[] table = UnlockKeys.get("x2m".equals(ext) ? "XMLY_X2M_AE" : "XMLY_X3M_OE");
        byte[] xorKey = ("x2m".equals(ext) ? "xmly" : "3989d111aad5613940f4fc44b639b292")
                .getBytes(StandardCharsets.US_ASCII);
        int n = Math.min(1024, audio.length);
        byte[] orig = java.util.Arrays.copyOfRange(audio, 0, n);
        for (int i = 0; i < n; i++) {
            int idx = table[i];
            if (idx >= n) throw new IllegalArgumentException("x2m/x3m 索引越界");
            audio[i] = (byte) (orig[idx] ^ xorKey[i % xorKey.length]);
        }
        return new Result(audio, new LinkedHashMap<>(), "m4a");
    }

    // ==================== 工具 ====================

    /** Python: _detect_format —— 魔数判断，兜底 mp3。 */
    public static String detectFormat(byte[] a) {
        if (a.length >= 4 && a[0] == 'f' && a[1] == 'L' && a[2] == 'a' && a[3] == 'C') return "flac";
        if (a.length >= 4 && a[0] == 'O' && a[1] == 'g' && a[2] == 'g' && a[3] == 'S') return "ogg";
        if (a.length >= 8 && ((a[0] == 0 && a[1] == 0 && a[2] == 0 && a[3] == 0x20 && a[4] == 'f' && a[5] == 't' && a[6] == 'y' && a[7] == 'p')
                || (a[4] == 'f' && a[5] == 't' && a[6] == 'y' && a[7] == 'p'))) return "m4a";
        if (a.length >= 4 && ((a[0] == 'R' && a[1] == 'I' && a[2] == 'F' && a[3] == 'F')
                || (a[0] == 'I' && a[1] == 'D' && a[2] == '3'))) return "mp3";
        if (a.length > 1 && (a[0] & 0xFF) == 0xFF && ((a[1] & 0xE0) == 0xE0)) return "mp3";
        return "mp3";
    }

    /** Python: _safe_name。 */
    public static String safeName(String name) {
        String s = name == null ? "" : name.replaceAll("[<>:\"/\\\\|?*\\n\\r\\t]", "_").strip();
        return s.isEmpty() ? "未命名" : s;
    }

    /** Python: ncm_meta_name —— "标题 - 歌手"。 */
    @SuppressWarnings("unchecked")
    public static String metaName(Map<String, Object> meta, String def) {
        Object titleObj = meta.get("musicName") != null ? meta.get("musicName") : meta.get("title");
        String title = titleObj == null ? "" : String.valueOf(titleObj).strip();
        String artist = "";
        Object artists = meta.get("artist");
        if (artists instanceof List<?> list && !list.isEmpty()) {
            Object first = list.get(0);
            if (first instanceof List<?> inner) {
                List<String> names = new ArrayList<>();
                for (Object a : list) {
                    if (a instanceof List<?> pair && !pair.isEmpty() && pair.get(0) != null) {
                        names.add(String.valueOf(pair.get(0)));
                    }
                }
                artist = String.join(", ", names);
            } else if (first instanceof String) {
                artist = String.valueOf(first);
            }
        } else if (artists instanceof String s) {
            artist = s;
        }
        artist = artist.strip();
        if (!title.isEmpty() && !artist.isEmpty()) return title + " - " + artist;
        return title.isEmpty() ? def : title;
    }

    static String extOf(Path p) {
        String name = p.getFileName().toString();
        int dot = name.lastIndexOf('.');
        return dot > 0 ? name.substring(dot + 1).toLowerCase(Locale.ROOT) : "";
    }

    static String stemOf(Path p) {
        String name = p.getFileName().toString();
        int dot = name.lastIndexOf('.');
        return dot > 0 ? name.substring(0, dot) : name;
    }

    private static boolean startsWith(byte[] data, byte[] prefix) {
        if (data.length < prefix.length) return false;
        for (int i = 0; i < prefix.length; i++) if (data[i] != prefix[i]) return false;
        return true;
    }

    private static int readIntLE(byte[] b, int off) {
        return (b[off] & 0xFF) | ((b[off + 1] & 0xFF) << 8) | ((b[off + 2] & 0xFF) << 16) | ((b[off + 3] & 0xFF) << 24);
    }

    private static int readIntBE(byte[] b, int off) {
        return ((b[off] & 0xFF) << 24) | ((b[off + 1] & 0xFF) << 16) | ((b[off + 2] & 0xFF) << 8) | (b[off + 3] & 0xFF);
    }

    private static int indexOf(byte[] data, byte target) {
        for (int i = 0; i < data.length; i++) if (data[i] == target) return i;
        return -1;
    }

    private static byte[] hex(String s) {
        byte[] out = new byte[s.length() / 2];
        for (int i = 0; i < out.length; i++) {
            out[i] = (byte) Integer.parseInt(s.substring(i * 2, i * 2 + 2), 16);
        }
        return out;
    }
}
