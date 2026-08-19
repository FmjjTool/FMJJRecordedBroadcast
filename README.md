# 封茗囧菌数据汇总库 · 统一维护工具箱（PySide6 版）

这是原版（`../平台信息获取/`，基于 customtkinter）的 **PySide6 重写支线**，目标是用 Qt6 替换 tkinter 获得更流畅的 UI。

## 状态

**进行中** —— 引擎层（toolbox / platforms / music / netease / tracker / exporters 等）已从原版完整复用，GUI 层逐步迁移。

- ✅ 主窗口（左侧导航 + 右侧懒加载 + 深色主题）
- ✅ 全局设置页
- 🚧 其余 21 页（占位，逐步迁移）

## 为什么这样设计

原版 GUI 层有 969 处 customtkinter 控件调用、约 1 万行。而**引擎层与 GUI 完全解耦**（无反向依赖），因此：

1. `src/` 直接复用原版引擎（`toolbox/`、`platforms/`、`music/`、`netease/` 等），import 路径不变
2. 只重写 `src/gui_qt/`（PySide6 版 GUI 层）
3. 新增页面只需在 `gui_qt/pages/` 写一个 `BasePage` 子类，并在 `main_window.py` 的 `PAGE_CLASSES` 注册

## 运行

```bash
pip install -r requirements.txt
python app_qt.py
```

## 目录结构

```
PySide6/
├── app_qt.py                 # PySide6 入口
├── requirements.txt          # PySide6 + 引擎依赖
├── README.md                 # 本文件
└── src/
    ├── gui_qt/               # PySide6 GUI 层（全新重写）
    │   ├── main_window.py    #   主窗口：导航 + 懒加载 + 22 页注册
    │   └── pages/            #   各页面
    │       ├── base.py       #     页面基类
    │       ├── placeholder.py#     占位页（未迁移）
    │       └── settings.py   #     全局设置页（已迁移）
    ├── toolbox/              # ↓ 以下均从原版复用（引擎层）
    ├── platforms/
    ├── music/
    ├── netease/
    ├── tracker/
    ├── exporters/
    └── ...（auth.py、app_logger.py、各下载模块等）
```

## 迁移进度

| 页面 | 状态 |
|------|------|
| 全局设置 | ✅ 已迁移 |
| 其余 21 页 | 🚧 占位 |
