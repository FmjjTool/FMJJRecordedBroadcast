package cn.fmjj.toolbox.core.bili;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * B 站 Web 鉴权层：WBI 签名 / App 签名。
 *
 * 逐行移植自 Python 版 {@code src/bili/wbi.py}（含字符过滤集与排序规则），
 * 保证与线上签名算法完全一致。单元测试使用 Python 原版生成的向量做跨语言校验。
 *
 * 网络层（取 wbi_img / buvid / bili_ticket）在 M3 里程碑接入，移植时只补
 * fetchKeys() 一个方法，签名逻辑本身不依赖网络。
 */
public final class WbiSigner {

    public static final String UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0";

    public static final String REFERER_BILI = "https://www.bilibili.com/";

    private static final int[] MIXIN_KEY_ENC_TAB = {
            46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
            27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
            37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
            22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
    };

    private static final String APP_KEY = "1d8b6e7d45233436";
    private static final String APP_SEC = "560c52ccd288fed045859ed18bffd973";

    private WbiSigner() {}

    /** WBI 密钥三元组。 */
    public record WbiKeys(String imgKey, String subKey, String wWebid) {}

    // ==================== WBI ====================

    /** 当前 Unix 秒（Python: int(time.time())）。 */
    public static long wts() { return Instant.now().getEpochSecond(); }

    /** Python: raw = img + sub; "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32] */
    public static String mixinKey(String imgKey, String subKey) {
        String raw = imgKey + subKey;
        StringBuilder sb = new StringBuilder(32);
        for (int i : MIXIN_KEY_ENC_TAB) {
            if (i < raw.length()) sb.append(raw.charAt(i));
        }
        return sb.substring(0, Math.min(32, sb.length()));
    }

    /** Python: value.replace("!","").replace("'","").replace("(","")...（5 个字符剔除）。 */
    public static String sanitizeValue(String v) {
        return v.replace("!", "").replace("'", "").replace("(", "").replace(")", "").replace("*", "");
    }

    /**
     * Python urllib.parse.quote(s, safe='') 的等价物：仅保留未保留字符
     * A-Za-z0-9-._~，其余按 UTF-8 字节 percent-encode（大写十六进制）。
     */
    public static String percentEncode(String s) {
        byte[] bytes = s.getBytes(StandardCharsets.UTF_8);
        StringBuilder sb = new StringBuilder(bytes.length * 3);
        for (byte b : bytes) {
            int c = b & 0xFF;
            boolean unreserved = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
                    || (c >= '0' && c <= '9') || c == '-' || c == '.' || c == '_' || c == '~';
            if (unreserved) {
                sb.append((char) c);
            } else {
                sb.append('%').append(String.format("%02X", c));
            }
        }
        return sb.toString();
    }

    /**
     * 构造已排序的查询串（Python: _wbi_query，ts 固定以便测试）。
     * 合并 wts / w_webid 后按 key 字典序排列，逐项过滤+编码，追加 w_rid。
     */
    public static String buildQuery(Map<String, String> params, WbiKeys keys, long ts) {
        String mixin = mixinKey(keys.imgKey(), keys.subKey());
        TreeMap<String, String> merged = new TreeMap<>();
        if (params != null) params.forEach(merged::put);
        merged.put("wts", String.valueOf(ts));
        merged.put("w_webid", keys.wWebid() == null ? "" : keys.wWebid());

        List<String> parts = new ArrayList<>(merged.size());
        merged.forEach((k, v) -> parts.add(percentEncode(k) + "=" + percentEncode(sanitizeValue(v))));
        String query = String.join("&", parts);
        return query + "&w_rid=" + md5Hex(query + mixin);
    }

    /** 对 url + params 做 WBI 签名，返回完整 URL（ts 取当前时间）。 */
    public static String signUrl(String url, Map<String, String> params, WbiKeys keys) {
        String sep = url.contains("?") ? "&" : "?";
        return url + sep + buildQuery(params, keys, wts());
    }

    // ==================== App 签名 ====================

    /** Python: sign_app —— build=8160400 固定 ts，qs 不编码直接拼接，md5(qs+APP_SEC)。 */
    public static Map<String, String> signApp(Map<String, String> params) {
        Map<String, String> merged = new TreeMap<>();
        if (params != null) merged.putAll(params);
        merged.put("build", "8160400");
        merged.put("ts", "1700000000");
        merged.put("appkey", APP_KEY);
        StringBuilder qs = new StringBuilder();
        for (Map.Entry<String, String> e : merged.entrySet()) {
            if (qs.length() > 0) qs.append('&');
            qs.append(e.getKey()).append('=').append(e.getValue());
        }
        Map<String, String> out = new LinkedHashMap<>(merged);
        out.put("sign", md5Hex(qs + APP_SEC));
        return out;
    }

    // ==================== 内部 ====================

    private static String md5Hex(String s) {
        try {
            MessageDigest md = MessageDigest.getInstance("MD5");
            byte[] digest = md.digest(s.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(32);
            for (byte b : digest) {
                sb.append(Character.forDigit((b >> 4) & 0xF, 16))
                  .append(Character.forDigit(b & 0xF, 16));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("MD5 不可用", e);
        }
    }
}
