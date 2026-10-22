# FengMingToolbox Java（封茗囧菌数据汇总库 · 工具箱 Java 全栈版）

> Java 全栈移植版 · 对应 Python 原版（PySide6）`FengMingToolbox v2.9.1`
> 原项目：<https://github.com/FmjjTool/FMJJRecordedBroadcast>

把原 Python 工具箱（引擎层 3.3 万行 + PySide6 GUI 4.1 万行，共约 7.6 万行）
整体迁移到 **Java 21 + JavaFX** 技术栈。本仓库是独立的新工程，与 Python 版并行维护。

## 当前进度（M5 第一刀：B站解析与下载链路打通）

| 范围 | 状态 |
|---|---|
| 构建体系（Maven / Java 27 / JavaFX 27 / Jackson / JUnit5 / zxing / sqlite-jdbc） | ✅ |
| 路径与日志 / 主窗口（9 组 35 页）/ 双主题 / Toast | ✅ |
| 字幕转换 / 歌词转换（KRC 加解密）/ WBI 签名 | ✅ M1 |
| 批量重命名 / 自动整理 / 文件时间修改 / 全局设置 | ✅ M2 |
| 网络层 / Cookie 仓库（与 Python 版互通）/ B站扫码登录 | ✅ M3 |
| 视频转换 / 音频提取 / 文件迁移 / 日历浏览 / 继续操作 | ✅ M4 |
| **B站解析**（链接识别 / view 详情 / playurl DASH / 选流） | ✅ M5 |
| **下载器**（镜像回退 / .part / Range 断点续传 / **多线程分段**） | ✅ M5 |
| **微博解析**（statuses/show：视频 + 图文原图 + 长文本展开） | ✅ M5 |
| **ffmpeg 合并** + **SQLite 入库**（collected.db） | ✅ M5 |
| **单个解析页**（B站/微博入口 → 解析 → 下载 → 自动入库；抖音给出明确提示） | ✅ M5 |
| 抖音解析（playwright）、快手/小红书、批量导入/已获取页 | 🚧 M5 第三刀 |
| 其余 23 个页面 | 🚧 占位中 |

**移植正确性保障**：所有引擎级单元测试的期望值均由 Python 原版运行时生成
（见 `src/test/java/.../` 测试类的注释），不是手写近似值——包括
`secondsToTimeStr(61.125)` 的 half-even 舍入这类 Python/Java 默认行为差异点。

## 目录结构

```
FMJJRecordedBroadcast_JAVA/
├── pom.xml                     Maven 构建（Java 21 + JavaFX 21 + Jackson + JUnit5）
├── docs/
│   └── JAVA_PORT_PLAN.md       ★ 全量移植路线图（35 页面逐项映射 + 里程碑 + 难点）
└── src/
    ├── main/java/cn/fmjj/toolbox/
    │   ├── ToolboxApp.java     JavaFX 入口（日志→目录→主窗口→主题）
    │   ├── core/
    │   │   ├── AppPaths.java   config/download/logs 目录定位
    │   │   ├── JsonMapper.java 全局 ObjectMapper（字段级可见性）
    │   │   ├── Log.java        轻量日志（logs/app_yyyyMMdd.log）
    │   │   ├── settings/Settings.java   settings.json 持久化
    │   │   └── bili/
    │   │       ├── WbiSigner.java   WBI/App 签名（纯函数）
    │   │       └── BiliAuth.java    WBI 网络层 + buvid/ticket + 扫码登录
    │   ├── engine/             ★ 引擎层（零 GUI 依赖，与 Python 版分层一致）
    │   │   ├── subtitle/SubtitleEngine.java        # 字幕格式互转
    │   │   ├── lyrics/LyricsEngine.java            # 歌词互转 + KRC 加解密
    │   │   ├── rename/BatchRenameEngine.java       # 批量重命名 8 类规则
    │   │   ├── organize/OrganizeEngine.java        # 按日期自动整理
    │   │   ├── filetime/FileTimeEngine.java        # 文件时间三模式
    │   │   ├── net/HttpClient.java                 # java.net.http 封装（重试/JSON/表单）
    │   │   ├── media/
    │   │   │   ├── Ffmpeg.java                     # ffmpeg 定位 + 子进程 + 错误分类
    │   │   │   ├── ConvertEngine.java              # 无损/转码/重试/缓存
    │   │   │   └── AudioEngine.java                # 音频提取 / 日期分组 / 回收站删除
    │   │   └── auth/
    │   │       ├── FernetCompat.java               # Fernet 逐字节兼容 Python
    │   │       └── CookieStore.java                # 12 平台 Cookie 仓库
    │   └── gui/
    │       ├── MainWindow.java     侧栏导航 + 懒加载 + 钩子分发
    │       ├── Theme.java          浅色/深色切换
    │       ├── Toast.java          右下角提示
    │       ├── dialogs/
    │       │   ├── CookieManagerDialog.java   12 平台 Cookie 管理
    │       │   ├── QrLoginDialog.java         B站扫码登录（zxing）
    │       │   └── BrowserLoginDialog.java    系统浏览器 + 粘贴 Cookie
    │       └── pages/
    │           ├── BasePage.java           生命周期基类
    │           ├── SubtitlePage.java       字幕转换（可用）
    │           ├── LyricsPage.java         歌词转换（可用）
    │           ├── RenameBatchPage.java    批量重命名（可用）
    │           ├── OrganizePage.java       自动整理（可用）
    │           ├── FileTimePage.java       文件时间修改（可用）
    │           ├── GlobalSettingsPage.java 全局设置（可用）
    │           ├── ConvertPage.java        视频转换（可用）
    │           ├── AudioPage.java          音频提取（可用）
    │           ├── MigratePage.java        文件迁移（可用）
    │           ├── CalendarPage.java       日历浏览（可用）
    │           ├── LauncherPage.java       继续操作（可用）
    │           └── StubPage.java           其余页面占位
    └── main/resources/cn/fmjj/toolbox/gui/
        ├── light.css / dark.css
    └── test/java/cn/fmjj/toolbox/   # 跨语言一致性测试
```

与 Python 版的对应关系：`core/` ≈ 配置/日志/鉴权底层，`engine/` ≈ `src/toolbox`、
`src/platforms`、`src/music` 等引擎模块，`gui/` ≈ `src/gui_qt/`。

## 构建与运行

要求：**JDK 27+**（JavaFX 27 配套；FernetCompat 用 JDK 22+ 的 FFM 直调 advapi32 读 MachineGuid），Maven 3.9+。

```bash
# 编译 + 单元测试
mvn clean test

# 直接启动 GUI（javafx-maven-plugin 自动拼 module-path）
mvn javafx:run

# 打包 jar
mvn package
```

Windows 下如果 IDE 直接运行 `ToolboxApp.main()`，需要 VM options：

```
--module-path "javafx-sdk-21/lib" --add-modules javafx.controls
```

## 运行时目录

与 Python 版一致（均已 gitignore，隐私数据不进仓库）：

```
<appRoot>/config/     配置与登录态
<appRoot>/download/   导出数据（字幕/歌词转换默认输出到这里）
<appRoot>/logs/       运行日志
```

`appRoot` = jar 所在目录（IDE 开发模式为工作目录）。

## 移植约定（给后续开发者的几条硬规则）

1. **引擎层零 GUI 依赖**：`engine/` 不许 import `javafx.*`，对应 Python 版
   「引擎不反向依赖 GUI」的分层约定。
2. **bug 级一致优先**：移植期不顺手「修」原版行为（例如歌词 to_ass 用
   `_fmt_lrc_time` 输出时间这种可疑写法），先在注释和路线图里标记，与作者确认后再改。
3. **测试向量来自 Python 运行时**：新增引擎测试，期望值必须由原版 Python
   代码跑出来，不允许手推。
4. **新增页面**：`gui/pages/` 写 `BasePage` 子类 → 在 `MainWindow.registerPages()`
   注册 → 导航项已在 `NAV_GROUPS` 里（35 页全部预注册，替换占位即可）。

详见 `docs/JAVA_PORT_PLAN.md`。
