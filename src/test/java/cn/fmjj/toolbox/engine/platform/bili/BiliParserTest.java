package cn.fmjj.toolbox.engine.platform.bili;

import cn.fmjj.toolbox.engine.platform.bili.BiliParser.StreamInfo;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

/**
 * 选流 / 文件名测试。sanitize 期望值来自 Python toolbox.utils.sanitize_filename。
 */
class BiliParserTest {

    private static StreamInfo s(int id, int codecid, long bandwidth) {
        return new StreamInfo(id, codecid, bandwidth, List.of("https://example/" + id + "-" + codecid), 1920, 1080, "avc1");
    }

    @Test
    @DisplayName("_pick_video_stream：目标画质命中后优先 AVC(codecid=7)")
    void pickVideoAvcFirst() {
        List<StreamInfo> streams = List.of(
                s(80, 12, 3_000_000),   // HEVC 更高带宽
                s(80, 7, 5_000_000),    // AVC
                s(64, 7, 2_000_000));
        assertEquals(7, BiliParser.pickVideoStream(streams, 80).codecid());
    }

    @Test
    @DisplayName("_pick_video_stream：无 AVC 时选同画质最低带宽；目标画质缺失时退回最高画质")
    void pickVideoFallbacks() {
        List<StreamInfo> hevc = List.of(s(80, 12, 3_000_000), s(80, 12, 2_000_000));
        assertEquals(2_000_000, BiliParser.pickVideoStream(hevc, 80).bandwidth());

        // 目标画质缺失 → 退回全部流（id 降序），再优先 AVC：64(AVC) 胜过 80(HEVC)
        List<StreamInfo> noTarget = List.of(s(80, 12, 3_000_000), s(64, 7, 1_000_000));
        assertEquals(64, BiliParser.pickVideoStream(noTarget, 120).id());
    }

    @Test
    @DisplayName("_pick_audio_stream：带宽最高；空列表返回 null")
    void pickAudio() {
        assertEquals(320_000, BiliParser.pickAudioStream(List.of(
                s(30280, 0, 320_000), s(30232, 0, 132_000))).bandwidth());
        assertNull(BiliParser.pickAudioStream(List.of()));
        assertNull(BiliParser.pickVideoStream(List.of(), 80));
    }

    @Test
    @DisplayName("sanitize_filename 与 Python 一致（非法字符/路径遍历/压缩下划线/截断）")
    void sanitize() {
        assertEquals("a_b_c_d_e", BiliParser.sanitizeFilename("a/b:c*d?e", 80, "video"));
        assertEquals("录播_合集_2026", BiliParser.sanitizeFilename("录播 合集 2026", 80, "video"));
        assertEquals("隐藏", BiliParser.sanitizeFilename("..隐藏", 80, "video"));
        assertEquals("a_b_c", BiliParser.sanitizeFilename("a__b  c", 80, "video"));
        assertEquals("video", BiliParser.sanitizeFilename("", 80, "video"));
        assertEquals("x".repeat(80), BiliParser.sanitizeFilename("x".repeat(100), 80, "video"));
    }

    @Test
    @DisplayName("输出文件名：{date}_{author}_{title}.mp4")
    void outputFileName() {
        assertEquals("20260608_封茗囧菌_录播_第一集.mp4",
                BiliParser.outputFileName("2026-06-08 20:00:00", "封茗囧菌", "录播 第一集", "BV1xx"));
        // 无发布时间 → 用今天日期
        String name = BiliParser.outputFileName("", "UP", "标题", "BV1xx");
        assertEquals(true, name.matches("\\d{8}_UP_标题\\.mp4"), name);
        // 标题为空 → 用 bvid 兜底
        assertEquals(true, BiliParser.outputFileName("2026-06-08", "UP", "", "BV1xx")
                .endsWith("_UP_BV1xx.mp4"));
    }

    @Test
    @DisplayName("画质描述兜底表")
    void qualityDesc() {
        assertEquals("1080P", BiliParser.QUALITY_DESC.get(80));
        assertEquals("4K 超清", BiliParser.QUALITY_DESC.get(120));
        assertEquals("画质999", s(999, 7, 1).qualityText());
    }
}
