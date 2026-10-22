package cn.fmjj.toolbox.engine.platform.weibo;

import cn.fmjj.toolbox.engine.platform.MediaItem;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 微博解析测试。链接/清洗/播放数向量来自 Python 原版运行结果；
 * fromStatus 用离线 JSON fixture（不触网）。
 */
class WeiboParserTest {

    @Test
    @DisplayName("extract_id 与 Python 一致（detail/status/weibo.cn/weibo.com 四种）")
    void extractId() {
        assertEquals("5012345678901234", WeiboParser.extractId("https://m.weibo.cn/detail/5012345678901234"));
        assertEquals("5012345678901234", WeiboParser.extractId("https://m.weibo.cn/status/5012345678901234"));
        assertEquals("abcDEF123", WeiboParser.extractId("https://weibo.cn/abcDEF123"));
        assertEquals("NabcDEF12", WeiboParser.extractId("https://weibo.com/1234567890/NabcDEF12"));
        assertEquals("", WeiboParser.extractId("https://weibo.com/tv/show/1034:5339069916577805"));
        assertEquals("", WeiboParser.extractId("无链接文本"));
        assertEquals("", WeiboParser.extractId(null));
    }

    @Test
    @DisplayName("extract_fid：播放器页 fid")
    void extractFid() {
        assertEquals("1034:5339069916577805",
                WeiboParser.extractFid("https://weibo.com/tv/show/1034:5339069916577805"));
        assertEquals("1034:5339069916577805",
                WeiboParser.extractFid("https://video.weibo.com/show?fid=1034:5339069916577805"));
        assertEquals("", WeiboParser.extractFid("https://m.weibo.cn/detail/5012345678901234"));
    }

    @Test
    @DisplayName("clean_rich：<br> 保留换行，其余标签去除")
    void cleanRich() {
        assertEquals("话题\n第二行", WeiboParser.cleanRich("<a href=\"x\">话题</a><br />第二行"));
        assertEquals("普通文本", WeiboParser.cleanRich("普通文本"));
        assertEquals("内容", WeiboParser.cleanRich("<span class=\"url-icon\"><img src=\"a\"/></span>内容"));
        assertEquals("", WeiboParser.cleanRich(null));
    }

    @Test
    @DisplayName("parse_play_count：万/亿/纯数字容错")
    void parsePlayCount() throws Exception {
        ObjectMapper m = new ObjectMapper();
        assertEquals(50000, WeiboParser.parsePlayCount(m.readTree("\"5万次播放\"")));
        assertEquals(120000000, WeiboParser.parsePlayCount(m.readTree("\"1.2亿次播放\"")));
        assertEquals(999, WeiboParser.parsePlayCount(m.readTree("\"999+\"")));
        assertEquals(12345, WeiboParser.parsePlayCount(m.readTree("12345")));
        assertEquals(0, WeiboParser.parsePlayCount(m.readTree("\"\"")));
    }

    @Test
    @DisplayName("fromStatus：视频帖（media_info 视频 + 封面）")
    void fromStatusVideo() throws Exception {
        String json = """
                {
                  "id": "5012345678901234",
                  "text": "录播 <a href='x'>#话题#</a><br />第二行",
                  "created_at": "2026-06-08 20:00:00",
                  "user": {"screen_name": "封茗囧菌"},
                  "page_info": {
                    "media_info": {
                      "stream_url_hd": "https://f.video.weibocdn.com/hd.mp4",
                      "stream_url": "https://f.video.weibocdn.com/sd.mp4",
                      "pic_url": "//wx1.sinaimg.cn/cover.jpg"
                    }
                  }
                }""";
        MediaItem item = WeiboParser.fromStatus(new ObjectMapper().readTree(json), "5012345678901234");
        assertEquals("weibo", item.platform());
        assertEquals("5012345678901234", item.mediaId());
        assertEquals("封茗囧菌", item.author());
        assertEquals("录播 #话题#\n第二行", item.description());
        assertEquals("https://wx1.sinaimg.cn/cover.jpg", item.cover());
        assertEquals(1, item.streams().size());
        assertEquals("video", item.streams().get(0).kind());
        assertEquals("https://f.video.weibocdn.com/hd.mp4", item.streams().get(0).url());
    }

    @Test
    @DisplayName("fromStatus：图文帖（pics 原图）")
    void fromStatusImages() throws Exception {
        String json = """
                {
                  "id": "5012345678909999",
                  "text": "九宫格",
                  "user": {"screen_name": "UP"},
                  "pics": [
                    {"large": {"url": "//wx2.sinaimg.cn/1.jpg"}, "url": "https://wx2.sinaimg.cn/s1.jpg"},
                    {"large": {"url": "//wx2.sinaimg.cn/2.jpg"}}
                  ]
                }""";
        MediaItem item = WeiboParser.fromStatus(new ObjectMapper().readTree(json), "5012345678909999");
        List<MediaItem.Stream> streams = item.streams();
        assertEquals(2, streams.size());
        assertEquals("https://wx2.sinaimg.cn/1.jpg", streams.get(0).url());
        assertEquals("image", streams.get(0).kind());
        assertEquals("https://wx2.sinaimg.cn/2.jpg", streams.get(1).url());
    }

    @Test
    @DisplayName("fromStatus：长文本优先 longTextContent")
    void fromStatusLongText() throws Exception {
        String json = """
                {
                  "id": "1",
                  "text": "短…",
                  "isLongText": true,
                  "longText": {"longTextContent": "<b>完整</b>正文第一行<br />第二行"},
                  "user": {"screen_name": "A"}
                }""";
        MediaItem item = WeiboParser.fromStatus(new ObjectMapper().readTree(json), "1");
        assertEquals("完整正文第一行\n第二行", item.description());
        assertTrue(item.title().startsWith("完整正文第一行"));
    }
}
