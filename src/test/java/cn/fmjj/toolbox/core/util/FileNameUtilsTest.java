package cn.fmjj.toolbox.core.util;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 日期提取一致性测试。期望值由 Python 原版 extract_date_from_filename 生成：
 * '录播_20260608_标题.mp4' -> 20260608 / '录播-2026-06-08.flv' -> 20260608 /
 * '2026_06_08_录播.mp4' -> 20260608 / '2026.6.8.录播' -> 20260608 /
 * '封茗囧菌2026-02-30直播.mp4' -> None / '2026-06-31' -> None /
 * 'abc20240102def' -> 20240102 / '1999-01-01' -> None / '2026-13-01' -> None /
 * 'nodate.mp4' -> None / '' -> None / '20260608' -> 20260608 /
 * '2024-01-02 15-04-05' -> 20240102 / 'a.2025-12-31.b' -> 20251231
 */
class FileNameUtilsTest {

    @Test
    @DisplayName("extractDateFromFilename 与 Python 一致（含月末边界与范围过滤）")
    void extractDateMatchesPython() {
        assertEquals("20260608", FileNameUtils.extractDateFromFilename("录播_20260608_标题.mp4"));
        assertEquals("20260608", FileNameUtils.extractDateFromFilename("录播-2026-06-08.flv"));
        assertEquals("20260608", FileNameUtils.extractDateFromFilename("2026_06_08_录播.mp4"));
        assertEquals("20260608", FileNameUtils.extractDateFromFilename("2026.6.8.录播"));
        assertNull(FileNameUtils.extractDateFromFilename("封茗囧菌2026-02-30直播.mp4"));
        assertNull(FileNameUtils.extractDateFromFilename("2026-06-31"));
        assertEquals("20240102", FileNameUtils.extractDateFromFilename("abc20240102def"));
        assertNull(FileNameUtils.extractDateFromFilename("1999-01-01"));
        assertNull(FileNameUtils.extractDateFromFilename("2026-13-01"));
        assertNull(FileNameUtils.extractDateFromFilename("nodate.mp4"));
        assertNull(FileNameUtils.extractDateFromFilename(""));
        assertEquals("20260608", FileNameUtils.extractDateFromFilename("20260608"));
        assertEquals("20240102", FileNameUtils.extractDateFromFilename("2024-01-02 15-04-05"));
        assertEquals("20251231", FileNameUtils.extractDateFromFilename("a.2025-12-31.b"));
    }

    @Test
    @DisplayName("validateDate：合法/范围/月末边界")
    void validateDate() {
        assertEquals("20260228", FileNameUtils.validateDate(2026, 2, 28));
        assertNull(FileNameUtils.validateDate(2026, 2, 30));
        assertNull(FileNameUtils.validateDate(2026, 2, 29));
        assertEquals("20240229", FileNameUtils.validateDate(2024, 2, 29)); // 闰年
        // 6 月只有 30 天：Python last_day=30，d=31 超限 → None
        assertNull(FileNameUtils.validateDate(2026, 6, 31));
        assertEquals("20260630", FileNameUtils.validateDate(2026, 6, 30));
        assertNull(FileNameUtils.validateDate(1999, 12, 31));
        assertNull(FileNameUtils.validateDate(2026, 13, 1));
    }

    @Test
    @DisplayName("扩展名工具：splitext 语义（前导点不算扩展名）")
    void extensionHelpers() {
        assertEquals("a/b", FileNameUtils.stripExtension("a/b"));
        assertEquals("archive.tar", FileNameUtils.stripExtension("archive.tar.gz"));
        assertEquals("mp4", FileNameUtils.extensionOf("a.mp4"));
        assertEquals("zip", FileNameUtils.extensionOf("archive.tar.zip"));
        assertEquals("", FileNameUtils.extensionOf("noext"));
        assertEquals("", FileNameUtils.extensionOf(".hidden"));
    }

    @Test
    @DisplayName("organize 扫描：分组/排序/过滤（@TempDir 真实文件）")
    void organizeScan(@TempDir Path dir) throws Exception {
        Files.writeString(dir.resolve("录播_20260608_标题.mp4"), "x");
        Files.writeString(dir.resolve("录播-2026-06-08.flv"), "x");
        Files.writeString(dir.resolve("录播_20260609_晚.mp4"), "x");
        Files.writeString(dir.resolve("无日期.txt"), "x");
        Files.createDirectory(dir.resolve("子目录"));

        var scan = cn.fmjj.toolbox.engine.organize.OrganizeEngine.scan(dir);
        assertEquals(2, scan.groups().size());
        assertTrue(scan.groups().containsKey("20260608"));
        assertTrue(scan.groups().containsKey("20260609"));
        // dict(sorted(...))：按日期键升序
        assertEquals("20260608", scan.groups().keySet().iterator().next());
        assertEquals(2, scan.groups().get("20260608").size());
        assertEquals(1, scan.noDateFiles().size());
        assertFalse(scan.noDateFiles().contains("子目录"));
    }

    @Test
    @DisplayName("organize 执行：移动 + 冲突自动 _N")
    void organizeExecute(@TempDir Path dir) throws Exception {
        Files.writeString(dir.resolve("a_20260608.mp4"), "x");
        Files.writeString(dir.resolve("b_20260608.mp4"), "x");
        Files.createDirectory(dir.resolve("20260608"));
        Files.writeString(dir.resolve("20260608/a_20260608.mp4"), "old");

        var scan = cn.fmjj.toolbox.engine.organize.OrganizeEngine.scan(dir);
        var result = cn.fmjj.toolbox.engine.organize.OrganizeEngine.execute(dir, scan.groups());
        assertEquals(2, result.success());
        assertEquals(0, result.fail());
        assertTrue(Files.exists(dir.resolve("20260608/a_20260608.mp4")));
        assertTrue(Files.exists(dir.resolve("20260608/a_20260608_1.mp4")));
        assertTrue(Files.exists(dir.resolve("20260608/b_20260608.mp4")));
    }
}
