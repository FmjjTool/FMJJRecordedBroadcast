package cn.fmjj.toolbox.engine.music;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.InputStream;
import java.util.HashMap;
import java.util.Map;

/**
 * 解密密钥表加载（对应 Python music_unlock._load_keys）。
 * 数据文件来自 Unlock Music 开源项目，随 resources 一起打包。
 */
final class UnlockKeys {

    private static volatile Map<String, int[]> cache;

    private UnlockKeys() {}

    static int[] get(String name) {
        Map<String, int[]> keys = load();
        int[] v = keys.get(name);
        if (v == null) throw new IllegalStateException("缺少密钥表：" + name);
        return v;
    }

    static byte[] bytes(String name) {
        int[] ints = get(name);
        byte[] out = new byte[ints.length];
        for (int i = 0; i < ints.length; i++) out[i] = (byte) (ints[i] & 0xFF);
        return out;
    }

    private static Map<String, int[]> load() {
        if (cache != null) return cache;
        synchronized (UnlockKeys.class) {
            if (cache != null) return cache;
            Map<String, int[]> map = new HashMap<>();
            try (InputStream in = UnlockKeys.class.getResourceAsStream("unlock_keys.json")) {
                if (in == null) throw new IllegalStateException("找不到 unlock_keys.json");
                JsonNode root = new ObjectMapper().readTree(in);
                root.fields().forEachRemaining(e -> {
                    JsonNode arr = e.getValue();
                    int[] values = new int[arr.size()];
                    for (int i = 0; i < arr.size(); i++) values[i] = arr.get(i).asInt();
                    map.put(e.getKey(), values);
                });
            } catch (Exception e) {
                throw new IllegalStateException("读取密钥表失败：" + e.getMessage(), e);
            }
            cache = map;
            return map;
        }
    }
}
