---
name: ui-visual-spec
description: "界面视觉与主题规范（强制阅读）。统一浅色/深色两套主题、颜色语义、字号阶梯、背景层级、间距圆角与组件样式；凡新增或修改任何 PySide6 界面（页面、对话框、浮层、自绘控件、富文本、图表、图标），或改动 theme.py、内联 QSS、背景色、文字色、字号，必须先阅读本 skill 并逐项核对。触发词：深色模式、浅色模式、主题、配色、颜色、背景色、字号、字体、间距、圆角、样式、美化、UI、界面、卡片、按钮样式、对比度。"
---

# 界面视觉与主题规范

先阅读 [convention-skill](../convention-skill/SKILL.md)（拉取、备份、提交、报告约定）与 [recording-toolbox-core](../recording-toolbox-core/SKILL.md)（核心八模块保护）。

本 skill 只管**视觉**：颜色、主题、字号、背景层级、间距、圆角、组件外观。它不给任何模块授权——改动录播工具箱八个核心模块的外观，仍需用户对本次模块与范围明确授权；视觉规范不能作为绕过保护的依据。它也不改变任何业务逻辑、布局结构与交互流程。

本 skill 是纯 Markdown 约束：不含脚本、不含机器可读色板文件。核对由 Agent 阅读后人工完成，不外包给校验程序。

## 一、唯一事实来源

深色/浅色只有一套实现，全部收敛在 `src/gui_qt/theme.py`。新增页面**不得**自带第二套色板。

| 文件 | 职责 | 可否新增颜色 |
| --- | --- | --- |
| `src/gui_qt/theme.py` `_PALETTE_SPEC` | QPalette 角色 → 浅/深两值的唯一映射 | 仅当现有角色确实无法表达语义时，与用户确认后新增 |
| `src/gui_qt/theme.py` `_COLORS` | 语义色（success / warning / danger 等）双值表 | 同上 |
| `src/gui_qt/theme.py` `NEUTRAL_QSS` | 全局控件基线样式（控件级默认外观） | 否，只做角色引用 |
| `src/gui_qt/main_window.py` `_SHELL_QSS` | 外壳（侧栏、顶栏、窗口按钮、Cookie 卡片） | 否 |
| 各页面 `_xxx_styles()` | 页面内 objectName 选择器 | 否，只能引用角色 |
| `_SURFACE_L2D` / `_TEXT_L2D` | **历史兼容层**，把旧页面硬编码 hex 映射到暗色 | **禁止依赖**，见 2.5 |

判断标准：写完后 `grep -n "#[0-9a-fA-F]\{3,8\}" <改动文件>`，除 2.4 列出的系统语义色外应无结果。

## 二、颜色体系

### 2.1 表面三层（背景层级）

背景只有三层，靠层级而不是靠不同灰值区分。

| 角色 | 浅色 | 深色 | 用在哪 |
| --- | --- | --- | --- |
| `palette(window)` | `#f3f5f9` | `#151c29` | 页面底、窗口底、侧栏底部卡片底 |
| `palette(base)` | `#ffffff` | `#1d2737` | 卡片、输入框、侧栏、顶栏、抽屉 |
| `palette(alternate-base)` | `#f0f3f8` | `#243044` | 表头、标签页底、次级块、列表悬停底 |

规则：

1. 页面底永远 `window`，卡片永远 `base`，卡片里的次级块才 `alternate-base`。不要用「三级灰」表达层次，用 `alternate-base` 表达。
2. 子控件背景默认 `transparent`，让父卡片底透出来；不要逐层重设与父级不同的背景，否则切主题后会出现深浅不一的矩形块（历史截图出现过该问题）。
3. 需要底色的独立窗口必须设不透明表面：`setProperty("pageSurface", True)` / `setProperty("windowSurface", True)`，或 `setAttribute(Qt.WA_StyledBackground, True)`。只有浮层、拖拽预览、媒体覆盖层才用 `Qt.WA_TranslucentBackground`。

### 2.2 语义角色

QSS 内只用下列角色名。括号内为浅色 / 深色实际值，仅供核对，**写代码时不要抄数值**。

| 角色 | 浅色 / 深色 | 语义 |
| --- | --- | --- |
| `palette(window-text)` | `#263449` / `#dce5f3` | 正文、标题 |
| `palette(text)` | `#263449` / `#dce5f3` | 同正文（输入框/列表内文本） |
| `palette(placeholder-text)` | `#6e7f96` / `#9caec5` | 辅助文字、常态图标、表头字 |
| `palette(highlight)` | `#356bd6` / `#447ccf` | 主操作底、进度条、焦点框、选中底 |
| `palette(highlighted-text)` | `#ffffff` / `#ffffff` | 主操作上的文字 |
| `palette(link)` | `#285ebc` / `#93bcff` | 链接、激活图标、悬停强调、次级强调 |
| `palette(shadow)` | `#dce3ed` / `#303e52` | 默认边框、分隔线 |
| `palette(mid)` | `#cbd5e3` / `#3a4b63` | 输入框边框、滚动条 |
| `palette(midlight)` | `#e8effc` / `#293d5c` | 弱选中底、导航选中底、图标底座 |
| `palette(dark)` | `#93a7c5` / `#6c84a6` | 滚动条悬停 |
| `palette(bright-text)` | `#b93842` / `#ff969f` | 危险、错误、缺失 |
| `palette(link-visited)` | `#18754e` / `#7cd6af` | 成功、已完成 |
| `palette(light)` | `#fff1f2` / `#38252e` | 危险弱底 |

Python 侧（自绘、富文本、表格刷色）用 `theme.color(name)`，可选名：
`window` `surface` `surface_alt` `text` `muted` `border` `accent` `accent_soft` `on_accent` `link` `accent_hover` `success` `warning` `danger` `danger_soft` `danger_border`。

QSS 侧（`theme(token)` 内联函数）只支持 15 个，**没有 `warning`**：
`window` `surface` `surface_alt` `text` `muted` `border` `accent` `accent_soft` `on_accent` `link` `danger` `danger_soft` `danger_border` `success` `accent_hover`。

### 2.3 对比度基线（已实测）

按 WCAG 相对亮度核算的值。正文目标 ≥ 4.5:1，辅助文字 ≥ 3:1，大字（≥18px 或 bold ≥14px）≥ 3:1。

| 配对 | 浅色 | 深色 | 判定 |
| --- | --- | --- | --- |
| text on window | 11.52 | 13.45 | 达标 |
| text on base | 12.58 | 11.84 | 达标 |
| link on base | 6.14 | 7.78 | 达标 |
| white on highlight | 4.97 | 4.18 | 达标 / AA 大字 |
| muted on base | **4.09** | 6.64 | 浅色未达 4.5 |
| muted on window | **3.74** | 7.54 | 浅色未达 4.5 |
| danger on base | 5.62 | 7.24 | 达标 |
| success on base | 5.13 | 8.65 | 达标 |

结论：**浅色 `placeholder-text` 属弱对比**，只用于辅助说明（分组标题、表头、状态行、图标），不得承载用户必须读到的正文、错误原因、金额、统计值。需要更强的次级文字用 `window-text` 加小字号，而不是继续压灰。

### 2.4 颜色禁令

不得出现：

1. 直接 hex 写前景/背景/边框：`background: #ffffff`、`color: #2f3440`、`border: 1px solid #c8cdd6`。深色下必然出白块或黑字黑底。
2. 关键字色：`color: gray`、`black`、`white`。全局 remap 只处理 hex，这些关键字在深色下原样保留。
3. `rgba(0,0,0,...)` 做分隔或阴影。需要分隔用 `palette(shadow)`。
4. 在页面里自带一套主题判断：`if theme.is_dark(): setStyleSheet("...")`。两套分支必然漂移。
5. 用 `setPalette()` 改页面/控件底色绕过主题（`cover_maker.py` 的色板预览属合理例外）。

唯一允许的硬编码色：窗口关闭按钮悬停 `#e81123` / 按下 `#c42b1c`（Windows 系统语义，两个主题一致），以及 toast 的四种提示底色（见 2.6）。

### 2.5 已知偏差（不要依靠这些写法）

这些是当前实现的真实行为，写代码时若依赖会踩坑：

1. QSS 里 `theme(accent_hover)` 会被解析成 `palette(highlight)`，与 `theme.color("accent_hover")` 的 `#275bc1` / `#568fe5` **不是同一个颜色**。后果：`QPushButton[variant="primary"]:hover` 与常态同色，悬停无视觉反馈。
   → 主按钮需要 hover 层次时写 `palette(link)`（两个主题下都比 `highlight` 更深/更亮，层次成立），不要写 `theme(accent_hover)`。
2. QSS 里 `theme(danger_border)` 解析成 `palette(mid)`，不是 `theme.color("danger_border")` 的粉红边框。
   → 危险按钮边框用 `palette(mid)` 或 `palette(shadow)`，要靠 `theme.color("danger_border")` 就拿不到。
3. QSS 里写 `theme(warning)` **静默失效**（无映射，原样残留，Qt 忽略该声明）。警告色只能通过 `theme.color("warning")` 用在自绘、富文本、图表，QSS 侧用 `bright-text` 近似或改用图标表达。
4. `_SURFACE_L2D` / `_TEXT_L2D` 只覆盖历史页面里出现过的有限色值。新写 `#fafbfc`、`#eef2f7` 这类"没登记"的浅色，深色下不会被映射，直接留白。**兼容层不是规范**：它能救旧页面，不能给新代码兜底。

### 2.6 固定配色组件

toast（`src/gui_qt/toast.py`）刻意不随主题：深色底 + 白字，四种语义 `info #2a5a8c` / `success #2e7d32` / `warning #b8860b` / `error #c62828`。不要把它改成 `palette(base)`，浅色下会与页面底混在一起失去提示性。

## 三、字号与字体

### 3.1 阶梯（唯一允许的字号集合）

全局基线在 `NEUTRAL_QSS`：`QWidget { font-size: 13px; }`，字体族 `"Microsoft YaHei", "Segoe UI Emoji", "Segoe UI Symbol"`。

| 字号 | 用途 | 典型位置 |
| --- | --- | --- |
| 10px | 微型标注 | 品牌副标题、导航分组标题、工具卡描述、Cookie 状态 |
| 11px | 辅助 | 状态说明、表头、胶囊标签、次级按钮、日期 |
| 12px | 次级正文 | 导航项、顶栏搜索框、列表元信息、小按钮 |
| 13px | **正文默认** | 全局基线，页面内不要再声明 |
| 14px | 小标题 | 顶栏页标题、区块标题、行标题、`role="sectionTitle"` |
| 15px | 卡片标题 | 抽屉标题、资产页 sectionTitle |
| 18px | 页面标题 | 二级页主标题 |
| 22px | 大标题 | 资产页 `assetHeading` |
| 27px | 首屏主标题 / 统计数字 | 首页 hero、`workbenchStatValue` |

规则：

1. 只从表里取值。16 / 17 / 20 / 23 / 26 / 34 / 36 / 44px 是历史遗留越级值，**新代码一律不得新增**；改到某页时顺手归位。
2. 同一页面主标题只出现一次，层级靠「标题大一号 + 辅助文字小一号 + 留白」建立，不靠颜色深浅堆叠。
3. 数字（统计值、计数、时间码）可与界面字体不同族，用 `font-family: "Segoe UI", "Microsoft YaHei"` 让数字等宽易读（首页统计卡已如此）。
4. `QFont.setPointSize()` 与 QSS px 不等价（96 DPI 下 1pt ≈ 1.333px）。QSS 里用 px，`paintEvent` 自绘里用 pt，两者不要混算出"看起来差不多大"。

### 3.2 现状与收敛

全项目 `font-size` 分布：11px 139 处、12px 61、14px 39、13px 26、15px 23、18px 20、16px 19、10px 13，其余为个位数。

不做全量重排（风险远大于收益）。约定：**新增代码严格按 3.1 取值；改动某个页面时，该页字号收敛到阶梯，不改其他页。**

### 3.3 字重与字体族

1. 字重只用 `bold`（或 `600`）。`700`、`900`、`lighter` 不要用。
2. 页面内不写 `font-family`，除非是等宽内容（路径、命令、日志、时间码）用 `Consolas, "Cascadia Mono", monospace`。中文界面字体只由全局 QSS 提供，页面重复声明会出现同一窗口两种中文字形。
3. emoji 图标（⬆⬇✕📁 等）需要 `font-family: "Segoe UI Emoji"`，微软雅黑不含部分 emoji 字形——统一走 `common.icon_btn_style()`，不要各页手写。

## 四、间距、圆角、尺寸

### 4.1 间距

主用 4 的倍数：`2 / 4 / 6 / 8 / 10 / 12 / 16`。

| 场景 | 值 |
| --- | --- |
| 图标与文字紧排 | 2~4 |
| 同组控件之间（`setSpacing`） | 6（最多）/ 4 / 8 |
| 卡片内部边距（`setContentsMargins`） | 10~12；大卡片 14~16 |
| 区块之间 | 12~16 |
| 页面外边距 | 0（页面底由外壳负责），内容区自己留 16 |

禁止新增 5 / 7 / 9 / 11 / 13 / 14（现存 5、7、9 保留不动，不扩散）。

### 4.2 圆角

| 圆角 | 用在哪 |
| --- | --- |
| 4px | 小标签、徽章、进度条、勾选框 |
| 6px | 输入框、下拉、小按钮 |
| 8px | 标准按钮、导航项、窗口控制按钮、搜索框 |
| 10px | 次级卡片、工具卡、Cookie 卡 |
| 12px | 主卡片、抽屉、工具栏卡片 |
| 50%（全圆） | 状态圆点、指示点 |

禁止新增 5 / 7 / 9 / 16 / 28 / 42。同一张卡片内部的小件不得比卡片本身更大圆角。

### 4.3 外壳与控件尺寸

| 元素 | 尺寸 |
| --- | --- |
| 主窗口默认 | 1240×820（超出屏幕时按可用区收缩） |
| 主窗口最小 | 880×560 |
| 侧栏宽度 | 228px；窗口宽 < 1040 时 200px |
| 顶栏高度 | 54px |
| 窗口控制按钮 | 40×30，图标 16，圆角 8 |
| 导航项 | `padding: 8px 11px`，字号 12，图标 18，圆角 8 |
| 仅图标小按钮 | 30×26（`common.icon_button` 默认），必须带 tooltip |
| 图标线宽 | 导航/工具图标 1.65，窗口控制图标 1.4 |
| 图标绘制 | 2 倍分辨率 + `setDevicePixelRatio(2)`，兼顾高 DPI |

窗口外壳由 `window_chrome.py` 负责：无边框、DWM 圆角、四边/四角热区缩放、顶栏拖动与双击最大化。不要为单个页面重新实现标题栏或缩放逻辑。

## 五、组件写法

优先用语义属性 + 全局 QSS，不要把样式写成一页一套。

| 组件 | 写法 | 落点 |
| --- | --- | --- |
| 按钮变体 | `setProperty("variant", "primary" / "danger" / "secondary" / "ghost")` | `NEUTRAL_QSS` |
| 卡片 | `setProperty("variant", "card" / "subtle" / "toolbar")` | `NEUTRAL_QSS` |
| 文本角色 | `setProperty("role", "title" / "subtitle" / "muted" / "caption" / "sectionTitle")` | `NEUTRAL_QSS` |
| 页面级自定义 | 页面根控件一次 `setStyleSheet(""" ... """)`，选择器用 `objectName` 限定 | 页面自身 |

规则：

1. 主操作（"开始 / 保存 / 执行"）用 `variant="primary"`；破坏性操作（删除、清空、覆盖）用 `variant="danger"`；其余用默认或 `secondary`。
2. `background: #2e7d32; color: white` 这种"绿底白字主按钮"是历史写法，新代码改用 `variant="primary"`（蓝）；确需绿色语义（如"启用任务"）再单独说明。
3. 页面内选择器必须带 `objectName`（`QFrame#assetToolbar`），禁止写裸 `QFrame { ... }` 全局命中，否则会污染懒加载后的兄弟页面与弹窗。
4. 一处页面级 `setStyleSheet` 优于几十处控件级 `setStyleSheet`：控件级写法让主题 remap 逐控件跑一遍，是切主题卡顿的主要来源。
5. 仅图标按钮统一 `common.icon_button(...)` / `common.icon_btn_style(...)`，必须给 tooltip——用户无法从图形得知功能。

## 六、主题切换铁律

主题有两条生效路径：`QPalette` + 全局 QSS（自动生效），以及内联样式（由 `theme` 的 remap 补丁处理）。下列七条是自绘与缓存内容的必守项。

1. **内联 QSS 只用角色**：`palette(...)` 或 `theme(...)`，不写 hex。remap 只兜底历史色值，不会为新色值发明映射。
2. **自绘控件每帧取色**：`paintEvent` 内 `pal = theme.current_palette()`，不要 `__init__` 里缓存 `QColor`。并在 `on_theme_changed()` 里 `self.update()`（首页环形图/趋势图即此模式）。
3. **预生成的 QIcon 必须重生成**：`line_icon(name, color)` 把颜色烘进像素，切主题后不会变。参考 `MainWindow._sync_shell_icons()`：主题变化时重新取 `palette(link)` / `palette(placeholder-text)` 再生成。
4. **富文本与表格不要直接刷色**：给 `QTextCharFormat` / item 打 role 属性，`on_theme_changed()` 里按 role 重新 setForeground（`asset.py` 的 `refresh_theme()` 模式）。这样不破坏选择、滚动位置与图片资源。
5. **页面实现 `on_theme_changed()`，不重建控件**：主窗口只通知当前页，其余页记入 `_theme_dirty`，切回时补通知。重建会丢输入、丢滚动位置、重跑加载逻辑。
6. **不要用 `setPalette()` 改界面底色**：与全局主题打架，且 Qt 会缓存 `palette()` 解析结果。
7. **独立窗口与浮层**：普通窗口要有不透明底（否则深色下露出桌面或形成黑洞）；`WA_TranslucentBackground` 只给浮层、拖拽预览、媒体覆盖层。带系统标题栏的窗口由 `theme._set_native_titlebar()` 对齐深色，走无边框外壳的则由 `window_chrome` 负责。

## 七、验证路径（四条都要过）

新增或修改任何视觉相关代码后，逐条走完：

1. **侧栏手动切换**：浅 → 深 → 浅，观察新控件是否有残留白块、黑字黑底、错位边框、图标消失。
2. **跟随系统**：全局设置里开启「跟随系统深浅色」，切换 Windows 主题，10 秒内应自动跟随。
3. **定时切换**：设置浅色/深色时间点，验证到点自动切换且手动切换会在下个检查点被拉回目标主题。
4. **重启恢复**：深色下关闭再启动，首屏（骨架页 + 首页）不得先亮后暗闪烁。

补充三条容易漏的场景：

- **懒加载页**：先在浅色下打开 A 页，切深色，再进 B 页（未加载过）。B 页必须直接是深色。
- **弹窗**：主题切换后新开的对话框、`QMessageBox`、右键菜单必须正确着色（依赖 `Show` 事件适配，不能只在构造时算一次）。
- **托盘/独立窗口**：直播录制二级窗口、弹幕浮层、拖拽预览窗口单独验证。

烟测以真实窗口（`windows` 平台插件）+ offscreen 各跑一遍，截图存 `Progress/ui/<日期>/`，作为报告证据。仅 offscreen 通过不算完成。

## 八、提交前自检清单

- [ ] 无新增 hex 前景/背景/边框色（`grep` 过一遍改动文件）
- [ ] 无 `gray` / `black` / `white` 关键字色
- [ ] 新字号 ∈ 10/11/12/13/14/15/18/22/27
- [ ] 新圆角 ∈ 4/6/8/10/12
- [ ] 新间距 ∈ 2/4/6/8/10/12/16
- [ ] 背景层级只用 window / base / alternate-base 三层
- [ ] 子控件背景为 `transparent`，未叠出不透明矩形
- [ ] 自绘控件每次取 `current_palette()`，并实现了 `on_theme_changed()`
- [ ] 预生成图标在切主题后重新生成
- [ ] 页面内 `setStyleSheet` 选择器带 `objectName`
- [ ] 仅图标按钮有 tooltip
- [ ] 弱对比 `placeholder-text` 未承载必读正文
- [ ] 四条验证路径 + 懒加载页 + 弹窗 + 独立窗口都已实测

## 九、反例与改法

以下取自 `src/gui_qt/pages/tracker.py`，是项目里最典型的旧写法，其他页面存在同类问题。

| 反例 | 深色下后果 | 改法 |
| --- | --- | --- |
| `setStyleSheet("QFrame { background: #ffffff; border-radius: 8px; }")` | 依赖兼容层映射，未登记的近似白会留白块 | `setProperty("variant", "card")`，或 `background: palette(base); border: 1px solid palette(shadow); border-radius: 12px;` |
| `"background: transparent; border: 1px solid #c8cdd6; color: #2f3440;"` | 靠 remap 勉强可用，但边框/文字分属两张映射表，易错配 | `border: 1px solid palette(mid); color: palette(text);` |
| `"background: #2e7d32; color: white;"` | 底色不映射，深色下依旧亮绿；`white` 关键字不映射 | `setProperty("variant", "primary")`，或 `background: palette(highlight); color: palette(highlighted-text);` |
| `"color: gray;"` | 关键字不映射，深色底上灰字变模糊 | `color: palette(placeholder-text);` |
| `setStyleSheet("color: #888; font-size: 15px;")` | `#888888` 命中兼容层变浅灰，但 15px 与 11px 说明文字混用破坏层级 | `color: palette(placeholder-text); font-size: 11px;` |
| 裸 `QFrame { ... }` 页面级样式 | 命中该页面所有 QFrame，含懒加载子卡片与弹窗 | 加 `objectName` 限定 |

## 十、待评估改进项（需用户明确同意后才动）

这些改动影响全站观感与既有截图，不在本次范围内，不要顺手改。

1. **浅色 `placeholder-text` 对比度不足**（4.09 / 3.74，低于 4.5）。若要达标，浅色值需从 `#6e7f96` 调到 `#5f7089`（白底 5.04 / 页面底 4.62）。代价：全站辅助文字变深，视觉更"重"。
2. **深色主按钮白字对比度 4.18**（略低于 4.5）。若要达标，`Highlight` 深色值需从 `#447ccf` 调到 `#3f76c9`（白字 4.52）。
3. **`primary` 按钮 hover 无差异**（见 2.5 第 1 条）。修 `_TOKEN_PALETTE["accent_hover"]` 可让 `NEUTRAL_QSS` 的 hover 规则真正生效，属 `theme.py` 全局行为变更，需单独确认。
4. **字号越级值收敛**（16/17/20/23/26/34/36/44 共 27 处）。建议按页面逐页收敛，不做一次性全局替换。
5. **`tracker.py` / `music_tracker.py` / `test_page.py` 内联样式下沉**（分别 61 / 50 / 65 处 `setStyleSheet`）。改造风险中等，需逐页授权。
