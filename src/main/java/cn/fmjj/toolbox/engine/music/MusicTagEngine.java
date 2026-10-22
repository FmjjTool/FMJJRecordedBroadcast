package cn.fmjj.toolbox.engine.music;

import org.jaudiotagger.audio.AudioFile;
import org.jaudiotagger.audio.AudioFileIO;
import org.jaudiotagger.tag.FieldKey;
import org.jaudiotagger.tag.Tag;
import org.jaudiotagger.tag.images.Artwork;
import org.jaudiotagger.tag.images.ArtworkFactory;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashSet;
import java.util.Locale;
import java.util.Set;

/**
 * 音乐标签读写（对应 Python 版 music_tag.py 的 mutagen 部分）。
 * 基于 JAudiotagger：支持 mp3/flac/ogg/m4a/wav/wma/aiff。
 */
public final class MusicTagEngine {

    /** 支持的扩展名。 */
    public static final Set<String> SUPPORTED_EXTS = new LinkedHashSet<>(Set.of(
            "mp3", "flac", "ogg", "oga", "m4a", "mp4", "wav", "wma", "aif", "aiff"));

    /** 标签字段（空串表示未设置）。 */
    public record Tags(String title, String artist, String album, String year, String track,
                       String genre, boolean hasCover) {
        public static Tags empty() { return new Tags("", "", "", "", "", "", false); }

        public boolean sameAs(Tags other) {
            return title.equals(other.title) && artist.equals(other.artist)
                    && album.equals(other.album) && year.equals(other.year)
                    && track.equals(other.track) && genre.equals(other.genre)
                    && hasCover == other.hasCover;
        }
    }

    private MusicTagEngine() {}

    public static boolean isSupported(Path file) {
        String n = file.getFileName().toString();
        int dot = n.lastIndexOf('.');
        if (dot < 0) return false;
        return SUPPORTED_EXTS.contains(n.substring(dot + 1).toLowerCase(Locale.ROOT));
    }

    /** 读取标签（无标签文件返回空 Tags）。 */
    public static Tags read(Path file) {
        try {
            AudioFile f = AudioFileIO.read(file.toFile());
            Tag tag = f.getTag();
            if (tag == null) return Tags.empty();
            return new Tags(
                    nz(tag.getFirst(FieldKey.TITLE)),
                    nz(tag.getFirst(FieldKey.ARTIST)),
                    nz(tag.getFirst(FieldKey.ALBUM)),
                    nz(tag.getFirst(FieldKey.YEAR)),
                    nz(tag.getFirst(FieldKey.TRACK)),
                    nz(tag.getFirst(FieldKey.GENRE)),
                    tag.getFirstArtwork() != null);
        } catch (Exception e) {
            return Tags.empty();
        }
    }

    /** 写入标签（空字段会清除该字段；与 Python 版"表单留空即清空"一致）。 */
    public static void write(Path file, Tags tags) throws Exception {
        AudioFile f = AudioFileIO.read(file.toFile());
        Tag tag = f.getTagOrCreateAndSetDefault();
        setOrRemove(tag, FieldKey.TITLE, tags.title());
        setOrRemove(tag, FieldKey.ARTIST, tags.artist());
        setOrRemove(tag, FieldKey.ALBUM, tags.album());
        setOrRemove(tag, FieldKey.YEAR, tags.year());
        setOrRemove(tag, FieldKey.TRACK, tags.track());
        setOrRemove(tag, FieldKey.GENRE, tags.genre());
        f.commit();
    }

    /** 写入封面（jpg/png）。 */
    public static void writeCover(Path file, Path image) throws Exception {
        if (!Files.isRegularFile(image)) throw new IllegalArgumentException("封面文件不存在：" + image);
        AudioFile f = AudioFileIO.read(file.toFile());
        Tag tag = f.getTagOrCreateAndSetDefault();
        tag.deleteArtworkField();
        Artwork artwork = ArtworkFactory.createArtworkFromFile(image.toFile());
        tag.setField(artwork);
        f.commit();
    }

    /** 移除封面。 */
    public static void removeCover(Path file) throws Exception {
        AudioFile f = AudioFileIO.read(file.toFile());
        Tag tag = f.getTag();
        if (tag != null) {
            tag.deleteArtworkField();
            f.commit();
        }
    }

    private static void setOrRemove(Tag tag, FieldKey key, String value) throws Exception {
        String v = value == null ? "" : value.strip();
        if (v.isEmpty()) {
            tag.deleteField(key);
        } else {
            tag.setField(key, v);
        }
    }

    private static String nz(String s) { return s == null ? "" : s; }

    /** 便于测试/日志：文件可读性。 */
    public static boolean readable(Path file) {
        return Files.isRegularFile(file);
    }

    static File asFile(Path p) { return p.toFile(); }
}
