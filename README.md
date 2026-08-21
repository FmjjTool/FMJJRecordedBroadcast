# 封茗囧菌数据汇总库 · 统一维护工具箱（PySide6 版）

原版（`../平台信息获取/`，基于 customtkinter）的 **PySide6 重写版**，v2.1.0。
引擎层（toolbox / platforms / music / netease / tracker / exporters）从原版完整复用，GUI 层用 Qt6 全部重写。

## 状态

**GUI 22 页全部迁移完成**（侧栏 7 组导航），含离屏回归测试与一键打包。

| 分组 | 页面 |
|------|------|
| 📊 信息获取 | 单个解析 / 批量导入 / 账号获取 / 已获取 / 视频下载 |
| 🗂 资产管理 | 资产管理 |
| 🎬 录播工具箱 | 自动整理 / 视频转换 / 重命名 / 音频提取 / 字幕转换 / 继续操作 / 日历浏览 / 文件迁移 |
| 🎤 歌词工具 | 歌词转换 |
| 🎵 音乐服务 | 音乐信息获取 / 音乐下载 / 音乐解密 |
| 📈 直播·追踪 | 账号追踪 / 弹幕姬（直播监控·回放，另集成 dycast 弹幕姬启动页） |
| ⚙️ 全局 | 全局设置 |

## 运行

```bash
pip install -r requirements.txt
python app_qt.py
```

- **无 CMD 黑窗启动**（开发模式）：双击 `启动工具箱(无窗口).vbs`（用 pythonw 启动，子进程自动加 CREATE_NO_WINDOW）
- **依赖自检**：`python check_deps.py`（缺依赖时自动安装）
- 默认窗口 1120×700（最小 880×560），小屏自动收缩到可用区域内

## 功能要点

- **主题**：浅色/深色手动切换（侧栏按钮，自动保存）+ **定时黑白开关**（设置页配置时间点，默认 07:00 浅色 / 19:00 深色，每 10 秒自动检测，到点即变；改动即时生效）
- **已获取数据持久化**：账号获取/解析结果自动写入 `config/collected.json`（去重、原子写入），重启自动恢复，可勾选批量导出/下载
- **Cookie 管理**：10 平台 Cookie 存本地 `config/cookies.json`，支持 B站/网易云扫码登录
- **隐私**：`config/`、`download/`、`logs/`、追踪账号数据均已 gitignore，不进仓库、不进安装包

## 打包发布

```bash
python build.py                 # 完整：PyInstaller 绿色版 → 隐私扫描 → Inno Setup 安装器
python build.py --no-installer  # 只出绿色版
python build.py --skip-build    # 复用 dist/，仅重编安装器
python build.py --with-dycast   # 附带 dycast-main 弹幕姬源码
```

- 依赖 `pip install pyinstaller`；安装器需 [Inno Setup 6](https://jrsoftware.org/isdl.php)（winget：`winget install JRSoftware.InnoSetup`）
- 产物：`dist/<名称>/`（绿色版，无 CMD 窗口，双击即用）、`dist/installer/*_Setup_v*.exe`
- 安装器支持：**自选安装位置**、**写注册表**（HKLM\Software\FengMingToolbox + 控制面板卸载条目）、开始菜单/桌面图标、完整卸载（默认保留用户数据）
- 打包后自动**隐私扫描**：dist 内出现 Cookie/配置/账号等本机数据即报错中止；首次运行由 `init_all_configs()` 重建默认配置
- 打包后追踪账号数据自动重定向到 exe 旁 `config/`（规避 Program Files 只读）

## 测试

```bash
python smoke_test.py           # 全 22 页离屏加载冒烟（含目录切换广播）
python smoke_test_replay.py    # 直播/回放深度回归：19 大项 127 断言
```

`smoke_test_replay.py` 首次运行会用 ffmpeg 自动生成临时测试视频 `_test_replay.flv`（gitignore，可删）。

## 目录结构

```
PySide6/
├── app_qt.py                 # PySide6 入口（无控制台守卫 + 子进程 CREATE_NO_WINDOW）
├── build.py                  # 一键打包（PyInstaller + Inno Setup + 隐私扫描）
├── check_deps.py             # 依赖自检/自动安装
├── 启动工具箱(无窗口).vbs      # 开发模式无黑窗启动
├── smoke_test.py             # 全页面冒烟测试
├── smoke_test_replay.py      # 直播/回放回归测试
├── requirements.txt
├── dycast-main/              # dycast 弹幕姬（Node 项目，运行时定位，--with-dycast 可入包）
├── config/                   # 运行时配置（gitignore：config.json / cache.json / cookies.json / collected.json）
├── download/                 # 运行时产出（gitignore：回放包/导出图片/已获取数据等）
├── logs/                     # 运行日志（gitignore）
└── src/
    ├── gui_qt/               # PySide6 GUI 层（全新重写）
    │   ├── main_window.py    #   主窗口：导航 + 懒加载 + 22 页注册 + 主题定时器
    │   ├── theme.py          #   浅色/深色主题 + 内联样式自动重映射 + 定时开关
    │   ├── common.py         #   跨页共享（COLLECTED 及其持久化、线程桥等）
    │   └── pages/            #   22 个页面（BasePage 子类）
    ├── toolbox/              # ↓ 以下引擎层从原版复用
    ├── platforms/            #   B站/抖音/微博解析
    ├── music/                #   音乐信息聚合
    ├── netease/              #   网易云下载
    ├── tracker/              #   账号追踪采集（打包后数据目录自动外移）
    ├── exporters/            #   JSON/XLSX/MD 导出
    └── ...（auth.py、app_logger.py、各下载模块等）
```

## 架构说明

原版 GUI 层有 969 处 customtkinter 控件调用、约 1 万行，而**引擎层与 GUI 完全解耦**（无反向依赖），因此：

1. `src/` 直接复用原版引擎，import 路径不变
2. 只重写 `src/gui_qt/`（PySide6 版 GUI 层）
3. 新增页面只需在 `gui_qt/pages/` 写一个 `BasePage` 子类，并在 `main_window.py` 的 `PAGE_CLASSES` 注册
