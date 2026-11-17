package cn.fmjj.toolbox.gui.dialogs;

import cn.fmjj.toolbox.core.settings.Settings;
import cn.fmjj.toolbox.engine.accounts.AccountStore;
import cn.fmjj.toolbox.engine.platform.douyin.DouyinLive;
import javafx.application.Platform;
import javafx.beans.property.SimpleStringProperty;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.control.ButtonBar;
import javafx.scene.control.ButtonType;
import javafx.scene.control.Dialog;
import javafx.scene.control.Label;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableRow;
import javafx.scene.control.TableView;
import javafx.scene.layout.VBox;
import javafx.stage.Window;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Consumer;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 「已存账号 / 录播用户」→ 抖音直播间选择器。
 * 移植自 Python 版 gui_qt/room_picker.py 的抖音部分：
 * 最近连接、accounts.txt（直播链接/主页 sec_uid）、直播录制文件名；
 * 主页 sec_uid 通过 CDP 页面上下文请求 profile/other 拿 web_rid。
 */
public final class DouyinRoomPicker {

    private static final class Entry {
        String source;
        String name;
        String room;
        String note;
        String secUid;

        Entry(String source, String name, String room, String note, String secUid) {
            this.source = source;
            this.name = name;
            this.room = room;
            this.note = note;
            this.secUid = secUid;
        }
    }

    private DouyinRoomPicker() {}

    public static void show(Window owner, String lastRoom, Consumer<String> onPick) {
        List<Entry> entries = loadEntries(lastRoom);
        if (entries.isEmpty()) {
            javafx.scene.control.Alert alert = new javafx.scene.control.Alert(
                    javafx.scene.control.Alert.AlertType.INFORMATION);
            alert.setTitle("选择直播间");
            alert.setHeaderText("本机暂无可选的抖音直播间");
            alert.setContentText("可先在「账号获取」添加抖音主页/直播间链接，或直接输入房间号。");
            alert.showAndWait();
            return;
        }
        Dialog<String> dlg = new Dialog<>();
        dlg.initOwner(owner);
        dlg.setTitle("选择直播间（抖音）");
        dlg.setResizable(true);
        dlg.getDialogPane().setPrefWidth(780);
        dlg.getDialogPane().setPrefHeight(460);
        ButtonType okType = new ButtonType("填入房间号", ButtonBar.ButtonData.OK_DONE);
        dlg.getDialogPane().getButtonTypes().addAll(ButtonType.CANCEL, okType);
        var okButton = dlg.getDialogPane().lookupButton(okType);
        okButton.setDisable(true);

        TableView<Entry> table = new TableView<>();
        TableColumn<Entry, String> cSource = new TableColumn<>("来源");
        cSource.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().source));
        cSource.setPrefWidth(110);
        TableColumn<Entry, String> cName = new TableColumn<>("主播/账号");
        cName.setCellValueFactory(d -> new SimpleStringProperty(
                d.getValue().name.isEmpty() ? "-" : d.getValue().name));
        cName.setPrefWidth(180);
        TableColumn<Entry, String> cRoom = new TableColumn<>("房间号");
        cRoom.setCellValueFactory(d -> new SimpleStringProperty(
                d.getValue().room.isEmpty() ? "解析中…" : d.getValue().room));
        cRoom.setPrefWidth(140);
        TableColumn<Entry, String> cNote = new TableColumn<>("备注");
        cNote.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().note));
        cNote.setPrefWidth(300);
        table.getColumns().addAll(List.of(cSource, cName, cRoom, cNote));
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);
        table.getItems().addAll(entries);
        table.getSelectionModel().selectedItemProperty().addListener((o, a, b) -> {
            okButton.setDisable(b == null || b.room.isEmpty());
            if (b != null && !b.room.isEmpty()) dlg.setResult(b.room);
        });
        table.setRowFactory(tv -> {
            TableRow<Entry> row = new TableRow<>();
            row.setOnMouseClicked(e -> {
                if (e.getClickCount() == 2 && !row.isEmpty() && !row.getItem().room.isEmpty()) {
                    dlg.setResult(row.getItem().room);
                    dlg.close();
                }
            });
            return row;
        });
        dlg.setResultConverter(bt -> {
            if (bt != okType) return null;
            Entry sel = table.getSelectionModel().getSelectedItem();
            return sel == null || sel.room.isEmpty() ? null : sel.room;
        });

        Label hint = new Label("提示：主页账号需要联网解析直播间（未开播/未登录时可能取不到）。");
        hint.setStyle("-fx-text-fill:#888;");
        hint.setWrapText(true);
        VBox box = new VBox(8, new Label("双击一行，或选中后点「填入房间号」："), table, hint);
        box.setPadding(new Insets(10));
        VBox.setVgrow(table, javafx.scene.layout.Priority.ALWAYS);
        box.setAlignment(Pos.TOP_LEFT);
        dlg.getDialogPane().setContent(box);

        for (Entry e : entries) {
            if (e.secUid == null || e.secUid.isEmpty()) continue;
            Thread t = new Thread(() -> {
                DouyinLive.UserRoom ur = new DouyinLive().resolveUserRoom(e.secUid);
                Platform.runLater(() -> {
                    if (ur == null) {
                        e.room = "";
                        e.note = (e.note.isEmpty() ? "" : e.note + " · ")
                                + "解析失败（未开播或需要登录 Cookie）";
                    } else {
                        if (!ur.nickname().isEmpty()) e.name = ur.nickname();
                        e.room = ur.webRid();
                        e.note = (e.note.isEmpty() ? "" : e.note + " · ")
                                + (ur.live() ? "直播中" : "未开播")
                                + (e.room.isEmpty() ? "（取不到房间号）" : "");
                    }
                    table.refresh();
                });
            }, "douyin-room-picker");
            t.setDaemon(true);
            t.start();
        }

        dlg.showAndWait().ifPresent(onPick);
    }

    private static List<Entry> loadEntries(String lastRoom) {
        List<Entry> out = new ArrayList<>();
        Set<String> seenRooms = new LinkedHashSet<>();
        if (lastRoom != null && lastRoom.matches("\\d{6,}")) {
            out.add(new Entry("最近连接", "", lastRoom, "", ""));
            seenRooms.add(lastRoom);
        }
        for (AccountStore.Account a : AccountStore.loadAccounts()) {
            String url = a.url == null ? "" : a.url;
            Matcher live = Pattern.compile("live\\.douyin\\.com/(\\d{6,})").matcher(url);
            if (live.find()) {
                String room = live.group(1);
                if (seenRooms.add(room)) {
                    out.add(new Entry("账号获取", a.name, room,
                            String.join(" / ", a.groupIds), ""));
                }
                continue;
            }
            Matcher user = Pattern.compile("douyin\\.com/user/([A-Za-z0-9_-]+)").matcher(url);
            if (user.find()) {
                out.add(new Entry("账号获取", a.name, "", "主页待解析", user.group(1)));
            }
        }
        Path recDir = Settings.load().effectiveExportDir().resolve("直播录制");
        if (Files.isDirectory(recDir)) {
            try (var stream = Files.list(recDir)) {
                stream.filter(p -> p.getFileName().toString().toLowerCase().endsWith(".flv"))
                        .sorted()
                        .limit(80)
                        .forEach(p -> {
                            Matcher m = Pattern.compile("^(\\d{6,})_").matcher(
                                    p.getFileName().toString());
                            if (m.find() && seenRooms.add(m.group(1))) {
                                out.add(new Entry("直播录制", "", m.group(1),
                                        p.getFileName().toString(), ""));
                            }
                        });
            } catch (Exception ignored) {
            }
        }
        return out;
    }
}
