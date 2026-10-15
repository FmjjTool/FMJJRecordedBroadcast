package cn.fmjj.toolbox.gui.pages;

import cn.fmjj.toolbox.engine.rename.BatchRenameEngine;
import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.AddCfg;
import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.DelCfg;
import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.NumCfg;
import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.OptionsCfg;
import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.RenameConfig;
import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.ReplaceCfg;
import cn.fmjj.toolbox.engine.rename.BatchRenameEngine.WholeCfg;
import javafx.beans.property.SimpleStringProperty;
import javafx.collections.FXCollections;
import javafx.collections.ObservableList;
import javafx.geometry.Insets;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.Button;
import javafx.scene.control.CheckBox;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Label;
import javafx.scene.control.Tab;
import javafx.scene.control.TabPane;
import javafx.scene.control.TableColumn;
import javafx.scene.control.TableView;
import javafx.scene.control.TextField;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Priority;
import javafx.scene.layout.VBox;
import javafx.stage.DirectoryChooser;
import javafx.stage.FileChooser;
import javafx.stage.Window;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * ✏️ 批量重命名页（zRenamer 风格：7 标签规则叠加 + 双列预览 + 撤销）。
 * 移植自 Python 版 gui_qt/pages/rename_batch.py。
 *
 * 与原版的已知差异：文本模式 tab（逐行直接编辑新名）未做，见路线图。
 */
public class RenameBatchPage extends BasePage {

    /** 列表项。 */
    private static final class Entry {
        String path;
        String original;
        String newName;
        Entry(String path, String original) { this.path = path; this.original = original; }
    }

    private final ObservableList<Entry> entries = FXCollections.observableArrayList();
    private final TableView<Entry> table = new TableView<>();
    private final List<String[]> history = new ArrayList<>(); // {旧path, 新path}
    private final Label countLabel = new Label();
    private final Label status = new Label();

    // 整体
    private final ComboBox<String> wCase = combo("（不变）", "全部大写", "全部小写", "首字母大写");
    private final ComboBox<String> wNum = combo("（不变）", "加随机数");
    private final ComboBox<String> wConv = combo("（不转换）", "全角→半角", "半角→全角");
    private final ComboBox<String> wExt = combo("（不变）", "大写", "小写", "删除");
    private final TextField wNewExt = new TextField();
    // 替换
    private final TextField rFind = new TextField();
    private final TextField rTo = new TextField();
    private final CheckBox rAll = new CheckBox("全部替换");
    private final CheckBox rRegex = new CheckBox("正则表达式");
    private final CheckBox rCase = new CheckBox("区分大小写");
    // 添加
    private final TextField aPrefix = new TextField();
    private final TextField aSuffix = new TextField();
    private final TextField aPosText = new TextField();
    private final TextField aPosIdx = new TextField("0");
    private final CheckBox aFromRight = new CheckBox("从右数");
    private final TextField aKw = new TextField();
    private final CheckBox aKwAfter = new CheckBox("关键词之后");
    private final TextField aKwText = new TextField();
    // 删除
    private final CheckBox dSpace = new CheckBox("空格");
    private final CheckBox dDigit = new CheckBox("数字");
    private final CheckBox dLower = new CheckBox("小写");
    private final CheckBox dUpper = new CheckBox("大写");
    private final CheckBox dChinese = new CheckBox("中文");
    private final CheckBox dSpecial = new CheckBox("特殊符号");
    private final TextField dPos = new TextField("0");
    private final TextField dCnt = new TextField("0");
    private final CheckBox dFromRight = new CheckBox("从右数");
    private final TextField dKw = new TextField();
    private final CheckBox dDirRight = new CheckBox("向右删");
    private final TextField dKwCnt = new TextField("0");
    private final CheckBox dInclKw = new CheckBox("含关键词");
    // 序号
    private final CheckBox nOn = new CheckBox("启用序号");
    private final TextField nStart = new TextField("1");
    private final TextField nStep = new TextField("1");
    private final TextField nWidth = new TextField("1");
    private final ComboBox<String> nMode = combo("后缀", "前缀");
    // 模板 / 选项
    private final TextField tTemplate = new TextField();
    private final CheckBox oIgnoreExt = new CheckBox("忽略扩展名");
    private final CheckBox oConflict = new CheckBox("同名冲突自动加序号");
    private final CheckBox oSubDir = new CheckBox("添加文件夹时含子文件夹");

    private Consumer<String> toast;

    @Override
    public String title() { return "✏️ 批量重命名"; }

    @Override
    public void bindToast(Consumer<String> toastSink) { this.toast = toastSink; }

    private static ComboBox<String> combo(String... items) {
        ComboBox<String> cb = new ComboBox<>(FXCollections.observableArrayList(items));
        cb.getSelectionModel().select(0);
        return cb;
    }

    @Override
    protected Node build() {
        rAll.setSelected(true);
        aKwAfter.setSelected(true);
        dDirRight.setSelected(true);
        oIgnoreExt.setSelected(true);
        oConflict.setSelected(true);

        // 预览表
        TableColumn<Entry, String> cIdx = new TableColumn<>("序号");
        cIdx.setCellValueFactory(d -> new SimpleStringProperty(
                String.valueOf(table.getItems().indexOf(d.getValue()) + 1)));
        cIdx.setPrefWidth(50);
        TableColumn<Entry, String> cOld = new TableColumn<>("文件名");
        cOld.setCellValueFactory(d -> new SimpleStringProperty(d.getValue().original));
        TableColumn<Entry, String> cNew = new TableColumn<>("新文件名");
        cNew.setCellValueFactory(d -> new SimpleStringProperty(
                d.getValue().newName == null || d.getValue().newName.equals(d.getValue().original)
                        ? "（不变）" : d.getValue().newName));
        table.getColumns().addAll(List.of(cIdx, cOld, cNew));
        table.setItems(entries);
        table.setColumnResizePolicy(TableView.CONSTRAINED_RESIZE_POLICY);

        // 工具栏
        Button addFiles = new Button("📄 添加文件");
        addFiles.setOnAction(e -> addFiles(false));
        Button addDir = new Button("📂 添加文件夹");
        addDir.setOnAction(e -> addFiles(true));
        Button clear = new Button("🧹 清空");
        clear.setOnAction(e -> { entries.clear(); history.clear(); refreshCount(); });
        Button undo = new Button("↩ 撤销上次");
        undo.setOnAction(e -> undo());
        Button refresh = new Button("🔄 刷新预览");
        refresh.setOnAction(e -> refresh());
        HBox bar = new HBox(8, addFiles, addDir, clear, refresh, undo, countLabel);
        bar.getStyleClass().add("toolbar");
        bar.setAlignment(Pos.CENTER_LEFT);

        // 规则标签
        TabPane tabs = new TabPane(
                tab("整体", wholeTab()), tab("替换", replaceTab()), tab("添加", addTab()),
                tab("删除", delTab()), tab("序号", numTab()), tab("模板", templateTab()),
                tab("选项", optionsTab()));
        tabs.getStyleClass().add("page-tabs");

        Button run = new Button("✅ 执行改名");
        run.setDefaultButton(true);
        run.setOnAction(e -> run());
        HBox bottom = new HBox(10, run, status);
        bottom.setAlignment(Pos.CENTER_LEFT);
        HBox.setHgrow(status, Priority.ALWAYS);

        Label pageTitle = new Label(title());
        pageTitle.getStyleClass().add("page-title");
        VBox root = new VBox(10, pageTitle, bar, tabs, table, bottom);
        root.getStyleClass().add("page-wrapper");
        VBox.setVgrow(table, Priority.ALWAYS);
        return root;
    }

    private Tab tab(String name, Node content) {
        Tab t = new Tab(name, content);
        t.setClosable(false);
        return t;
    }

    private HBox row(Node... nodes) {
        HBox box = new HBox(8);
        box.setAlignment(Pos.CENTER_LEFT);
        box.getChildren().addAll(nodes);
        return box;
    }

    private Node wholeTab() {
        VBox v = new VBox(8, row(new Label("大小写："), wCase, new Label("数字："), wNum,
                new Label("转换："), wConv),
                row(new Label("扩展名："), wExt, new Label("新扩展名："), wNewExt));
        v.getStyleClass().add("page-root");
        v.setPadding(new Insets(12));
        return v;
    }

    private Node replaceTab() {
        VBox v = new VBox(8, row(new Label("查找："), rFind, new Label("替换为："), rTo),
                row(rAll, rRegex, rCase));
        v.getStyleClass().add("page-root");
        v.setPadding(new Insets(12));
        return v;
    }

    private Node addTab() {
        VBox v = new VBox(8, row(new Label("前缀："), aPrefix, new Label("后缀："), aSuffix),
                row(new Label("位置插入："), aPosText, new Label("偏移："), aPosIdx, aFromRight),
                row(new Label("关键词后插入："), aKw, aKwAfter, new Label("文本："), aKwText));
        v.getStyleClass().add("page-root");
        v.setPadding(new Insets(12));
        return v;
    }

    private Node delTab() {
        VBox v = new VBox(8, row(new Label("删除字符类型："), dSpace, dDigit, dLower, dUpper, dChinese, dSpecial),
                row(new Label("按位置删除："), dPos, new Label("个数："), dCnt, dFromRight),
                row(new Label("按关键词删除："), dKw, dDirRight, new Label("个数："), dKwCnt, dInclKw));
        v.getStyleClass().add("page-root");
        v.setPadding(new Insets(12));
        return v;
    }

    private Node numTab() {
        nWidth.setPrefWidth(60);
        nStart.setPrefWidth(60);
        nStep.setPrefWidth(60);
        VBox v = new VBox(8, row(nOn, new Label("起始："), nStart, new Label("步长："), nStep,
                new Label("位数："), nWidth, new Label("模式："), nMode));
        v.getStyleClass().add("page-root");
        v.setPadding(new Insets(12));
        return v;
    }

    private Node templateTab() {
        tTemplate.setPromptText("如：录播_{序号}_{日期}_{名}（支持 {名}/{扩展}/{序号}/{日期}/{时间}/{年}/{月}/{日}）");
        VBox v = new VBox(8, row(new Label("模板："), tTemplate));
        v.getStyleClass().add("page-root");
        v.setPadding(new Insets(12));
        return v;
    }

    private Node optionsTab() {
        VBox v = new VBox(8, row(oIgnoreExt, oConflict), row(oSubDir));
        v.getStyleClass().add("page-root");
        v.setPadding(new Insets(12));
        return v;
    }

    // ==================== 逻辑 ====================

    private Window owner() {
        return table.getScene() == null ? null : table.getScene().getWindow();
    }

    private void toast(String msg) {
        if (toast != null) toast.accept(msg);
    }

    private static int intOf(TextField f, int def) {
        try { return Integer.parseInt(f.getText().trim()); } catch (Exception e) { return def; }
    }

    private RenameConfig collectConfig() {
        return new RenameConfig(
                new WholeCfg(caseMode(wCase), mode(wNum), mode(wConv), extMode(wExt), wNewExt.getText()),
                new ReplaceCfg(rFind.getText(), rTo.getText(), rAll.isSelected(),
                        rRegex.isSelected(), rCase.isSelected()),
                new AddCfg(aPrefix.getText(), aSuffix.getText(), aPosText.getText(), intOf(aPosIdx, 0),
                        aFromRight.isSelected(), aKw.getText(), aKwAfter.isSelected(), aKwText.getText()),
                new DelCfg(delTypes(), intOf(dPos, 0), intOf(dCnt, 0), dFromRight.isSelected(),
                        dKw.getText(), dDirRight.isSelected(), intOf(dKwCnt, 0), dInclKw.isSelected()),
                new NumCfg(nOn.isSelected(), intOf(nStart, 1), intOf(nStep, 1), intOf(nWidth, 1),
                        nMode.getSelectionModel().getSelectedIndex() == 1 ? "prefix" : "suffix"),
                tTemplate.getText(),
                new OptionsCfg(oIgnoreExt.isSelected(), oConflict.isSelected()));
    }

    private static String caseMode(ComboBox<String> cb) {
        return switch (cb.getSelectionModel().getSelectedIndex()) {
            case 1 -> "upper";
            case 2 -> "lower";
            case 3 -> "capital";
            default -> "";
        };
    }

    private static String mode(ComboBox<String> cb) {
        return cb.getSelectionModel().getSelectedIndex() == 1 ? "random" : "";
    }

    private static String extMode(ComboBox<String> cb) {
        return switch (cb.getSelectionModel().getSelectedIndex()) {
            case 1 -> "upper";
            case 2 -> "lower";
            case 3 -> "delete";
            default -> "";
        };
    }

    private List<String> delTypes() {
        List<String> types = new ArrayList<>();
        if (dSpace.isSelected()) types.add("space");
        if (dDigit.isSelected()) types.add("digit");
        if (dLower.isSelected()) types.add("lower");
        if (dUpper.isSelected()) types.add("upper");
        if (dChinese.isSelected()) types.add("chinese");
        if (dSpecial.isSelected()) types.add("special");
        return types;
    }

    private void addFiles(boolean fromDir) {
        List<String> picked = new ArrayList<>();
        if (fromDir) {
            DirectoryChooser chooser = new DirectoryChooser();
            chooser.setTitle("选择文件夹");
            File d = chooser.showDialog(owner());
            if (d == null) return;
            try (var s = Files.walk(d.toPath(), oSubDir.isSelected() ? Integer.MAX_VALUE : 1)) {
                s.filter(Files::isRegularFile).map(Path::toString).forEach(picked::add);
            } catch (IOException e) {
                status.setText("读取目录失败：" + e.getMessage());
                return;
            }
        } else {
            FileChooser chooser = new FileChooser();
            chooser.setTitle("选择文件");
            List<File> files = chooser.showOpenMultipleDialog(owner());
            if (files != null) files.forEach(f -> picked.add(f.getAbsolutePath()));
        }
        for (String p : picked) {
            String name = Paths.get(p).getFileName().toString();
            boolean exists = entries.stream().anyMatch(e -> e.path.equalsIgnoreCase(p));
            if (!exists) entries.add(new Entry(p, name));
        }
        refresh();
    }

    /** 对应 Python _refresh：全量重算预览（冲突序号在这一次遍历内消化）。 */
    private void refresh() {
        RenameConfig cfg = collectConfig();
        List<String> originals = entries.stream().map(e -> e.original).toList();
        Map<String, Integer> used = new LinkedHashMap<>();
        LocalDateTime now = LocalDateTime.now();
        boolean conflict = cfg.options().conflict();
        List<Entry> snapshot = List.copyOf(entries);
        for (int i = 0; i < snapshot.size(); i++) {
            Entry e = snapshot.get(i);
            e.newName = BatchRenameEngine.resolveNewName(
                    e.original, cfg, i, now, conflict, used, originals, i);
        }
        table.refresh();
        refreshCount();
        status.setText("预览已刷新");
    }

    private void refreshCount() {
        long changed = entries.stream().filter(e -> e.newName != null && !e.newName.equals(e.original)).count();
        countLabel.setText(entries.size() + " 个文件（将改名 " + changed + " 个）");
    }

    private void run() {
        List<Entry> pending = List.copyOf(entries).stream()
                .filter(e -> e.newName != null && !e.newName.equals(e.original)).toList();
        if (pending.isEmpty()) {
            status.setText("没有需要改名的文件（先调整规则再预览）");
            return;
        }
        int ok = 0, fail = 0;
        for (Entry e : pending) {
            try {
                Path src = Paths.get(e.path);
                Path dst = src.resolveSibling(e.newName);
                Files.move(src, dst);
                history.add(new String[] {e.path, dst.toString()});
                e.path = dst.toString();
                e.original = e.newName; // 改名后以新名为基准（与原版刷新语义一致）
                ok++;
            } catch (IOException ex) {
                fail++;
            }
        }
        refresh();
        status.setText("完成：" + ok + " 个成功" + (fail > 0 ? "，" + fail + " 个失败" : ""));
        if (ok > 0) toast("已改名 " + ok + " 个文件（可撤销）");
    }

    private void undo() {
        if (history.isEmpty()) {
            status.setText("没有可撤销的操作");
            return;
        }
        String[] last = history.remove(history.size() - 1);
        try {
            Files.move(Paths.get(last[1]), Paths.get(last[0]));
            status.setText("已撤销：" + Paths.get(last[0]).getFileName());
            refresh();
        } catch (IOException ex) {
            history.add(last); // 撤销失败放回
            status.setText("撤销失败：" + ex.getMessage());
        }
    }

    @Override
    public void onReturnPressed() { refresh(); }
}
