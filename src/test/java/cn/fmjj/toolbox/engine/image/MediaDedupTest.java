package cn.fmjj.toolbox.engine.image;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 媒体去重测试：大小预分组 + MD5 精确匹配。 */
class MediaDedupTest {

    @Test
    @DisplayName("内容相同 → 一组；不同内容 → 不合并")
    void duplicateGroups(@TempDir Path tmp) throws IOException {
        Files.writeString(tmp.resolve("a.jpg"), "SAME-CONTENT-123");
        Files.writeString(tmp.resolve("b.jpg"), "SAME-CONTENT-123");
        Files.writeString(tmp.resolve("c.jpg"), "SAME-CONTENT-123");
        Files.writeString(tmp.resolve("d.jpg"), "DIFFERENT-CONTENT");

        var r = MediaDedup.scan(tmp, Set.of(".jpg"));
        assertEquals(4, r.scanned());
        assertEquals(1, r.groups().size());
        var g = r.groups().get(0);
        assertEquals(3, g.files().size());
        assertEquals(2, r.duplicateCount());
        assertEquals(g.size() * 2, r.wastedBytes());
    }

    @Test
    @DisplayName("大小不同直接排除（不做哈希）")
    void sizePrefilter(@TempDir Path tmp) throws IOException {
        Files.writeString(tmp.resolve("a.jpg"), "12345");
        Files.writeString(tmp.resolve("b.jpg"), "1234567890");
        var r = MediaDedup.scan(tmp, Set.of());
        assertEquals(2, r.scanned());
        assertTrue(r.groups().isEmpty());
    }

    @Test
    @DisplayName("扩展名过滤：非目标类型不参与")
    void extFilter(@TempDir Path tmp) throws IOException {
        Files.writeString(tmp.resolve("a.jpg"), "X");
        Files.writeString(tmp.resolve("b.jpg"), "X");
        Files.writeString(tmp.resolve("c.txt"), "X");
        var onlyJpg = MediaDedup.scan(tmp, Set.of(".jpg"));
        assertEquals(2, onlyJpg.scanned());
        assertEquals(1, onlyJpg.groups().size());

        var all = MediaDedup.scan(tmp, Set.of());
        assertEquals(3, all.scanned());
    }

    @Test
    @DisplayName("md5：已知向量（空串/abc）")
    void md5Vectors(@TempDir Path tmp) throws IOException {
        assertEquals("d41d8cd98f00b204e9800998ecf8427e", MediaDedup.md5OfText(""));
        assertEquals("900150983cd24fb0d6963f7d28e17f72", MediaDedup.md5OfText("abc"));

        Path f = tmp.resolve("x.bin");
        Files.writeString(f, "abc");
        assertEquals("900150983cd24fb0d6963f7d28e17f72", MediaDedup.md5(f));
    }

    @Test
    @DisplayName("目录不存在 → 空结果，不抛异常")
    void missingDir(@TempDir Path tmp) throws IOException {
        var r = MediaDedup.scan(tmp.resolve("nope"), Set.of());
        assertEquals(0, r.scanned());
        assertTrue(r.groups().isEmpty());
    }
}
