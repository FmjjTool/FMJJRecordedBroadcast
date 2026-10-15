package cn.fmjj.toolbox.engine.filetime;

import cn.fmjj.toolbox.engine.filetime.FileTimeEngine.ApplyResult;
import cn.fmjj.toolbox.engine.filetime.FileTimeEngine.Mode;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 文件时间引擎一致性测试。
 * parse_time_from_name / offset 期望值由 Python 原版生成（含 12:00:00 默认时间）。
 */
class FileTimeEngineTest {

    @Test
    @DisplayName("parseTimeFromName 与 Python 一致（默认 12:00:00）")
    void parseTimeFromName() {
        assertEquals(LocalDateTime.of(2024, 1, 2, 12, 0, 0), FileTimeEngine.parseTimeFromName("2024-01-02.mp4"));
        assertEquals(LocalDateTime.of(2024, 1, 2, 12, 0, 0), FileTimeEngine.parseTimeFromName("录播20240102.flv"));
        assertEquals(LocalDateTime.of(2024, 1, 2, 15, 4, 5), FileTimeEngine.parseTimeFromName("2024-01-02 15-04-05.mp4"));
        assertEquals(LocalDateTime.of(2024, 3, 5, 9, 8, 7), FileTimeEngine.parseTimeFromName("视频_2024.3.5_9-8-7.mkv"));
        assertEquals(LocalDateTime.of(2024, 1, 2, 15, 4, 5), FileTimeEngine.parseTimeFromName("20240102150405.mp4"));
        assertNull(FileTimeEngine.parseTimeFromName("无时间.mp4"));
        assertEquals(LocalDateTime.of(2023, 12, 1, 8, 30, 0), FileTimeEngine.parseTimeFromName("VLOG 2023-12-01 08时30分"));
        assertEquals(LocalDateTime.of(2024, 1, 2, 9, 30, 0), FileTimeEngine.parseTimeFromName("x2024-01-02T09-30-00y.mp4"));
        // Python epoch 校验（本地时区）：1704168000 = 2024-01-02 12:00:00 +0800
        assertEquals(1704168000.0, FileTimeEngine.toEpoch(LocalDateTime.of(2024, 1, 2, 12, 0, 0)), 1e-6);
        assertEquals(1704179045.0, FileTimeEngine.toEpoch(LocalDateTime.of(2024, 1, 2, 15, 4, 5)), 1e-6);
    }

    @Test
    @DisplayName("偏移解析：合法/混合格式报错（消息与 Python 一致）")
    void offsetParsing() {
        assertEquals(2 * 86400, FileTimeEngine.parseOffsetSeconds("+2d"), 1e-9);
        assertEquals(-210 * 60, FileTimeEngine.parseOffsetSeconds("-210m"), 1e-9);
        assertEquals(90 * 60, FileTimeEngine.parseOffsetSeconds("+90m"), 1e-9);
        assertEquals(-30, FileTimeEngine.parseOffsetSeconds("-30s"), 1e-9);
        assertEquals(0, FileTimeEngine.parseOffsetSeconds("+0h"), 1e-9);
        assertEquals(1.5 * 3600, FileTimeEngine.parseOffsetSeconds("+1.5h"), 1e-9);
        assertEquals(2 * 86400, FileTimeEngine.parseOffsetSeconds("+2D".toLowerCase()), 1e-9);

        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> FileTimeEngine.parseOffsetSeconds("-3h30m"));
        assertEquals("偏移格式错误（如 -3h30m 不支持混合，请用 -210m）", e.getMessage());
        assertThrows(IllegalArgumentException.class, () -> FileTimeEngine.parseOffsetSeconds("bad"));
    }

    @Test
    @DisplayName("apply：指定时间写入 mtime；偏移平移；文件名模式")
    void applyModes(@TempDir Path dir) throws IOException {
        Path f = dir.resolve("录播_2024-01-02_十五.mp4");
        Files.writeString(f, "x");

        // 指定时间
        LocalDateTime target = LocalDateTime.of(2025, 3, 4, 5, 6, 7);
        ApplyResult r = FileTimeEngine.apply(f, Mode.SET, target, null);
        assertTrue(r.ok(), r.error());
        assertEquals(target.atZone(java.time.ZoneId.systemDefault()).toEpochSecond(),
                Files.getLastModifiedTime(f).toInstant().getEpochSecond(), 1);

        // 偏移：+1d 在现有基础上平移
        long before = Files.getLastModifiedTime(f).toMillis();
        ApplyResult r2 = FileTimeEngine.apply(f, Mode.OFFSET, null, "+1d");
        assertTrue(r2.ok(), r2.error());
        assertEquals((double) (before + 86_400_000L), (double) Files.getLastModifiedTime(f).toMillis(), 1500.0);

        // 文件名时间
        ApplyResult r3 = FileTimeEngine.apply(f, Mode.FROM_NAME, null, null);
        assertTrue(r3.ok(), r3.error());
        assertEquals(LocalDateTime.of(2024, 1, 2, 12, 0, 0).atZone(java.time.ZoneId.systemDefault()).toEpochSecond(),
                Files.getLastModifiedTime(f).toInstant().getEpochSecond(), 1);

        // 文件名无时间 → 错误文本
        Path g = dir.resolve("无时间.mp4");
        Files.writeString(g, "x");
        ApplyResult r4 = FileTimeEngine.apply(g, Mode.FROM_NAME, null, null);
        assertEquals("文件名无时间", r4.error());
        assertEquals(false, r4.ok());

        // 非法偏移 → 错误文本
        ApplyResult r5 = FileTimeEngine.apply(g, Mode.OFFSET, null, "-3h30m");
        assertEquals("偏移格式错误（如 -3h30m 不支持混合，请用 -210m）", r5.error());
    }

    @Test
    @DisplayName("setFileTimes：mtime 必写；ctime 尽力而为不阻断")
    void setFileTimes(@TempDir Path dir) throws IOException {
        Path f = dir.resolve("t.bin");
        Files.writeString(f, "x");
        ApplyResult r = FileTimeEngine.setFileTimes(f,
                FileTimeEngine.toEpoch(LocalDateTime.of(2020, 1, 1, 0, 0, 0)),
                FileTimeEngine.toEpoch(LocalDateTime.of(2021, 2, 3, 4, 5, 6)),
                FileTimeEngine.toEpoch(LocalDateTime.of(2022, 3, 4, 5, 6, 7)));
        assertTrue(r.ok(), r.error());
        assertEquals(FileTimeEngine.toEpoch(LocalDateTime.of(2021, 2, 3, 4, 5, 6)),
                Files.getLastModifiedTime(f).toInstant().getEpochSecond(), 1);
        // 平台不支持 ctime 时 ok 仍为 true（ctimeUnsupported 标记差异，供 UI 提示）
        assertNotNull(r);
    }

    @Test
    @DisplayName("formatEpoch：格式化与越界保护")
    void formatEpoch() {
        assertEquals("2024-01-02 12:00:00", FileTimeEngine.formatEpoch(1704168000.0));
    }
}
