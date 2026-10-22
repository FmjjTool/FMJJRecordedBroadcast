package cn.fmjj.toolbox.engine.music;

import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 酷我真实网络冒烟（默认跳过）。
 * 手动执行：{@code FMJJ_NET_TEST=1 mvn test -Dtest=KuwoNetworkIT}
 */
class KuwoNetworkIT {

    @Test
    @DisplayName("搜索 → 直链 → 歌词（真实接口）")
    void liveSearchAndPlayUrl() {
        Assumptions.assumeTrue("1".equals(System.getenv("FMJJ_NET_TEST")),
                "设置 FMJJ_NET_TEST=1 才跑真实网络测试");

        KuwoScraper k = new KuwoScraper();
        List<MusicSong> songs = k.searchSong("起风了 周深", 1, 5);
        assertFalse(songs.isEmpty(), "搜索结果为空（接口可能变更或网络受限）");
        for (MusicSong s : songs) {
            System.out.println("  " + s.singer() + " | " + s.songName() + " | " + s.songId());
        }

        String url = k.getPlayUrl(songs.get(0).songId(), "128kmp3");
        assertTrue(url.startsWith("http"), "直链获取失败：" + url);
        System.out.println("  playUrl=" + url.substring(0, Math.min(100, url.length())));

        String lrc = k.getLyric(songs.get(0).songId());
        System.out.println("  lyricLen=" + lrc.length());
    }
}
