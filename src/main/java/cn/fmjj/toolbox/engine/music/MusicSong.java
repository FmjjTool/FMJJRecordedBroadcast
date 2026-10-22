package cn.fmjj.toolbox.engine.music;

/**
 * 音乐搜索结果（对应 Python 版 music/models.py 的 SongInfo 精简子集）。
 * 只保留搜索/下载链路真正用到的字段；完整 60+ 字段随 M6 后续页面按需扩展。
 */
public record MusicSong(String platform, String songId, String songName, String singer,
                        String albumName, int durationSeconds, String coverUrl, String pageUrl) {

    /** 展示用：mm:ss。 */
    public String durationText() {
        if (durationSeconds <= 0) return "—";
        return String.format("%02d:%02d", durationSeconds / 60, durationSeconds % 60);
    }

    /** 文件名（歌手 - 歌名），供下载器使用。 */
    public String fileNameBase() {
        String s = (singer == null || singer.isBlank() ? "未知歌手" : singer)
                + " - " + (songName == null || songName.isBlank() ? songId : songName);
        return s;
    }
}
