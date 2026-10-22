package cn.fmjj.toolbox.engine.music;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertArrayEquals;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 解密引擎跨语言测试。
 * 样本由 tools/gen_music_samples.py（Python + pycryptodome）按原版算法加密生成，
 * Java 侧解密后与 .expected 明文逐字节比对；QMC 的 mask 向量同样来自 Python。
 */
class MusicUnlockTest {

    private byte[] res(String name) throws Exception {
        try (InputStream in = getClass().getResourceAsStream("/music/" + name)) {
            if (in == null) throw new IllegalStateException("缺少测试资源：" + name);
            return in.readAllBytes();
        }
    }

    private Path tempCopy(String name, Path dir) throws Exception {
        Path p = dir.resolve(name);
        Files.write(p, res(name));
        return p;
    }

    @Test
    @DisplayName("NCM：AES-ECB keybox + RC4 密钥流 + meta 解析")
    void ncm(@TempDir Path tmp) throws Exception {
        Path file = tempCopy("sample.ncm", tmp);
        var r = MusicUnlock.decrypt(file);
        assertArrayEquals(res("sample.ncm.expected"), r.audio());
        assertEquals("mp3", r.ext());
        assertEquals("测试歌曲 - 测试歌手", MusicUnlock.metaName(r.meta(), "fallback"));
        assertEquals("专辑", r.meta().get("album"));
    }

    @Test
    @DisplayName("KGM：header + 动态 mask（T 变换）")
    void kgm(@TempDir Path tmp) throws Exception {
        Path file = tempCopy("sample.kgm", tmp);
        var r = MusicUnlock.decrypt(file);
        assertArrayEquals(res("sample.kgm.expected"), r.audio());
        assertEquals("flac", r.ext());
    }

    @Test
    @DisplayName("KWM：固定密钥 XOR + 尾部 0x400 偏移")
    void kwm(@TempDir Path tmp) throws Exception {
        Path file = tempCopy("sample.kwm", tmp);
        var r = MusicUnlock.decrypt(file);
        assertArrayEquals(res("sample.kwm.expected"), r.audio());
        assertEquals("mp3", r.ext());
    }

    @Test
    @DisplayName("QMC mask 向量与 Python 一致（静态表 / Map 密码）")
    void qmcMasks() throws Exception {
        String text = new String(res("qmc_vectors.txt"), java.nio.charset.StandardCharsets.US_ASCII);
        MusicUnlock.QmcStaticCipher stat = new MusicUnlock.QmcStaticCipher();
        MusicUnlock.QmcMapCipher map = new MusicUnlock.QmcMapCipher(new byte[] {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15});
        for (String line : text.strip().split("\\R")) {
            String[] parts = line.split("\\|");
            String kind = parts[0];
            for (int i = 1; i < parts.length; i++) {
                String[] kv = parts[i].split(":");
                int offset = Integer.parseInt(kv[0]);
                int expected = Integer.parseInt(kv[1]);
                int actual = "static".equals(kind) ? stat.mask(offset) : map.mask(offset);
                assertEquals(expected, actual, kind + " mask(" + offset + ")");
            }
        }
    }

    @Test
    @DisplayName("detect_format：魔数识别 flac/ogg/m4a/mp3")
    void detectFormat() {
        assertEquals("flac", MusicUnlock.detectFormat("fLaC....".getBytes()));
        assertEquals("ogg", MusicUnlock.detectFormat("OggS....".getBytes()));
        assertEquals("m4a", MusicUnlock.detectFormat(new byte[] {0, 0, 0, 0x20, 'f', 't', 'y', 'p'}));
        assertEquals("m4a", MusicUnlock.detectFormat(new byte[] {0, 0, 0, 0x20, 'f', 't', 'y', 'p', 1}));
        assertEquals("mp3", MusicUnlock.detectFormat(new byte[] {'I', 'D', '3', 4}));
        assertEquals("mp3", MusicUnlock.detectFormat("RIFFxxxx".getBytes()));
        assertEquals("mp3", MusicUnlock.detectFormat(new byte[] {(byte) 0xFF, (byte) 0xFB}));
        assertEquals("mp3", MusicUnlock.detectFormat(new byte[] {1, 2, 3}));
    }

    @Test
    @DisplayName("safeName / metaName / 扩展名判断")
    void helpers() {
        assertEquals("a_b_c", MusicUnlock.safeName("a/b:c"));
        assertEquals("未命名", MusicUnlock.safeName("  "));

        Map<String, Object> m = new LinkedHashMap<>();
        m.put("musicName", "歌名");
        m.put("artist", List.of(List.of("歌手A", 1L), List.of("歌手B", 2L)));
        assertEquals("歌名 - 歌手A, 歌手B", MusicUnlock.metaName(m, "x"));

        Map<String, Object> t = new LinkedHashMap<>();
        t.put("title", "只有标题");
        assertEquals("只有标题", MusicUnlock.metaName(t, "x"));
        assertEquals("x", MusicUnlock.metaName(new LinkedHashMap<>(), "x"));

        assertTrue(MusicUnlock.isSupported("ncm"));
        assertTrue(MusicUnlock.isSupported(".NCM"));
        assertTrue(MusicUnlock.isSupported("mflac"));
        assertTrue(MusicUnlock.isSupported("kgma"));
        assertTrue(!MusicUnlock.isSupported("mp3"));
    }

    @Test
    @DisplayName("decryptTo：按 meta 命名落盘，重名追加原文件名")
    void decryptTo(@TempDir Path tmp) throws Exception {
        Path file = tempCopy("sample.ncm", tmp);
        Path outDir = tmp.resolve("out");
        Path out = MusicUnlock.decryptTo(file, outDir);
        assertEquals("测试歌曲 - 测试歌手.mp3", out.getFileName().toString());
        assertArrayEquals(res("sample.ncm.expected"), Files.readAllBytes(out));

        // 再解一次同名 → 追加原文件名
        Path again = MusicUnlock.decryptTo(file, outDir);
        assertEquals("测试歌曲 - 测试歌手_sample.mp3", again.getFileName().toString());
    }

    @Test
    @DisplayName("非法输入：非 NCM 文件报错、不支持的扩展名报错")
    void invalidInput(@TempDir Path tmp) throws Exception {
        Path bad = tmp.resolve("bad.ncm");
        Files.write(bad, "not an ncm file at all".getBytes());
        assertThrows(IllegalArgumentException.class, () -> MusicUnlock.decrypt(bad));

        Path mp3 = tmp.resolve("a.mp3");
        Files.write(mp3, new byte[] {1, 2, 3});
        assertThrows(IllegalArgumentException.class, () -> MusicUnlock.decrypt(mp3));
    }
}
