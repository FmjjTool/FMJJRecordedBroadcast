package cn.fmjj.toolbox.engine.platform;

import java.util.List;

/**
 * 统一媒体条目：各平台解析器的公共输出。
 * 对应 Python 版 info_parse 页的解析结果 dict。
 */
public record MediaItem(
        String platform,        // bilibili / douyin / weibo / kuaishou / xiaohongshu
        String mediaId,         // 平台内唯一 ID（B站 bvid / 抖音 aweme_id …）
        String title,
        String author,
        String cover,
        String publishTime,     // yyyy-MM-dd HH:mm:ss（尽力）
        int durationSeconds,
        String description,
        List<Stream> streams,   // 可下载流（视频/音频/图片）
        String rawJson) {

    /** 一条可下载流。 */
    public record Stream(String kind,      // video / audio / image
                         String quality,   // 画质描述（1080P / 原图 …）
                         String url,
                         List<String> backups,
                         long sizeBytes) {}

    public static MediaItem of(String platform, String mediaId, String title, String author,
                               String cover, String publishTime, int duration, String desc,
                               List<Stream> streams, String rawJson) {
        return new MediaItem(platform, mediaId, title, author, cover, publishTime, duration,
                desc, streams, rawJson);
    }

    /** 展示用：时长 mm:ss。 */
    public String durationText() {
        if (durationSeconds <= 0) return "—";
        return String.format("%02d:%02d", durationSeconds / 60, durationSeconds % 60);
    }
}
