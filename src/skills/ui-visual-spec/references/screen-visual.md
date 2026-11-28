# 屏幕 UI 视觉与主题规范（详细参考）

从[统一入口](../SKILL.md)按任务选读相关章节。本文只管屏幕 UI 的外观与布局；交互见[交互与反馈](interaction.md)，程序生成的导出图片见[图片输出](generated-images.md)。这些规范不授权改变业务逻辑，不解除核心八模块保护；仅在本次已授权的页面与范围内收敛。

[界面规范样张](界面规范样张.md)保留历史实现线索，不能覆盖本文的现行规则，也不要求运行样张目录。

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
| `palette(placeholder-text)` | `#5f7089` / `#9caec5` | 辅助文字、常态图标、表头字 |
| `palette(highlight)` | `#356bd6` / `#3f76c9` | 主操作底、进度条、焦点框、选中底 |
| `palette(highlighted-text)` | `#ffffff` / `#ffffff` | 主操作上的文字 |
| `palette(accent)` | `#275bc1` / `#568fe5` | 主操作悬停底（**仅此用途**，见 2.7） |
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

QSS 侧（`theme(token)` 内联函数）支持上表全部 16 个名字，走两条解析路径：

1. **转 palette 角色**（15 个）：`window` `surface` `surface_alt` `text` `muted` `border` `accent` `accent_soft` `on_accent` `link` `danger` `danger_soft` `danger_border` `success` `accent_hover`。解析结果与实际主题无关，所以 `NEUTRAL_QSS` 生成的模板恒定，切主题只换 QPalette。
2. **按主题注入实色**（1 个）：`warning` → `#99600c` / `#edc478`。QPalette 没有警告色角色，只能把当前主题的十六进制值写进样式串；渲染结果与 `dark` 绑定并进缓存键，切主题后由 `_adapt_widget` 重新渲染。

两条路径都不需要手写 hex。未登记的 token（如 `theme(warningn)`）不会静默通过：`_theme_template` 会保留原文并打一条 warning 日志，列出可用名称。启动时 `_audit_theme_tokens()` 还会检查每个 token 的目标角色在当前 Qt 是否真实存在（`Accent` 是 Qt 6.6+ 新增，低版本会缺），缺失即报警。

### 2.3 对比度基线（WCAG 2.2）

普通文字最低对比度为 4.5:1；大字最低为 3:1。WCAG 大字指至少 18pt（约 24 CSS px），或至少 14pt 粗体（约 18.67 CSS px），不是 18px / 14px。辅助文字、placeholder、hover、focus 没有普遍豁免，均按实际文字大小及背景计算。WCAG 对不可操作的禁用控件、纯装饰及标识等有特定例外；不能把“辅助”或“短暂显示”当作这些例外。W3C 说明：<https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html>（核对日期：2026-11-28）。下表保留既有测量记录；实际改色后必须重新核算，不能把历史值视为本次验证结果。

| 配对 | 浅色 | 深色 | 判定 |
| --- | --- | --- | --- |
| text on window | 11.52 | 13.45 | 达标 |
| text on base | 12.58 | 11.84 | 达标 |
| link on base | 6.14 | 7.78 | 达标 |
| white on highlight | 4.97 | 4.52 | 达标 |
| muted on base | 5.04 | 6.64 | 达标 |
| muted on window | 4.62 | 7.54 | 达标 |
| muted on alternate-base | 4.53 | 5.87 | 达标（余量仅 0.03） |
| muted on midlight | **4.37** | 4.84 | 浅色未达 4.5 |
| danger on base | 5.66 | 7.24 | 达标 |
| success on base | 5.13 | 8.65 | 达标 |

实测中浅色 `placeholder-text` 在 `base` / `window` / `alternate-base` 上达到 4.5，但在 `midlight`（弱选中底）上为 4.37，未达到普通文字最低要求。辅助说明、表头和状态文字也应达到对应阈值；落在 `midlight` 上时使用经核对达到目标的 `palette(text)` 或 `palette(link)`。不得以文字并非主要正文为由接受 3:1。

### 2.4 颜色禁令

不得出现：

1. 直接 hex 写前景/背景/边框：`background: #ffffff`、`color: #2f3440`、`border: 1px solid #c8cdd6`。深色下必然出白块或黑字黑底。
2. 关键字色：`color: gray`、`black`、`white`。全局 remap 只处理 hex，这些关键字在深色下原样保留。
3. `rgba(0,0,0,...)` 做分隔或阴影。需要分隔用 `palette(shadow)`。
4. 在页面里自带一套主题判断：`if theme.is_dark(): setStyleSheet("...")`。两套分支必然漂移。
5. 用 `setPalette()` 改页面/控件底色绕过主题（`cover_maker.py` 的色板预览属合理例外）。

唯一允许的硬编码色：窗口关闭按钮悬停 `#e81123` / 按下 `#c42b1c`（Windows 系统语义，两个主题一致），以及 toast 的四种提示底色（见 2.6）。

另有一种**非手写**的十六进制值：`theme(warning)` 由主题层按当前主题注入（见 2.2），它由 `theme.py` 生成、随主题重算、不受页面控制，不算违反本禁令。但页面代码里仍然不允许自己写 `#99600c` 这类值去模拟它——那样切主题不会变。

### 2.5 已知偏差（不要依靠这些写法）

这些是当前实现的真实行为，写代码时若依赖会踩坑：

1. QSS 里 `theme(danger_border)` 解析成 `palette(mid)`，不是 `theme.color("danger_border")` 的粉红边框。
   → 危险按钮边框用 `palette(mid)` 或 `palette(shadow)`，要靠 `theme.color("danger_border")` 就拿不到。
2. `_SURFACE_L2D` / `_TEXT_L2D` 只覆盖历史页面里出现过的有限色值。新写 `#fafbfc`、`#eef2f7` 这类"没登记"的浅色，深色下不会被映射，直接留白。**兼容层不是规范**：它能救旧页面，不能给新代码兜底。
   → 实例：`settings.py` 依赖检测骨架条曾用 `#e7ebf1` / `#d7dde6` / `#dfe4ea`，三个值都不在映射表，深色下整块留成白条；已改为 `palette(midlight)` / `palette(shadow)` / `palette(alternate-base)`。
   → 判断方法：写完 `grep -n "#[0-9a-fA-F]\{3,8\}"`，出现即先查 `_SURFACE_L2D` / `_TEXT_L2D` 是否登记；没登记就只能改角色引用。

### 2.6 固定配色组件

toast（`src/gui_qt/toast.py`）刻意不随主题：深色底 + 白字，四种语义 `info #2a5a8c` / `success #2e7d32` / `warning #b8860b` / `error #c62828`。不要把它改成 `palette(base)`，浅色下会与页面底混在一起失去提示性。

### 2.7 `palette(accent)` 与主按钮悬停

`accent_hover` 需要一个能和 `Highlight` 区分开的实色，但 QSS 的 `palette()` 只能引用 QPalette 角色。`QPalette` 全部角色中，本主题体系的 20 个已各有所属，**只剩 `Accent`**（Qt 6.6+ 新增）空闲，因此：

- `_PALETTE_SPEC["Accent"] = ("#275bc1", "#568fe5")`，与 `_COLORS` 合并为单一来源，`theme.color("accent_hover")` 与 `theme(accent_hover)` 现在取同一个值。
- `NEUTRAL_QSS` 的 `QPushButton[variant="primary"]:hover` 因此真正生效：浅色 `#356bd6 → #275bc1`（变深），深色 `#3f76c9 → #568fe5`（变亮）。
- `Accent` **只用于主按钮 hover**。不要把它当第二强调色，也不要写进页面样式；Qt 6.6+ 的原生控件（复选框、滑块等）会读这个角色，设成别的语义色会让原生绘制跑偏。
- 悬停态文字同样须满足 WCAG 对比度要求；没有因 hover 是瞬时状态而普遍降低阈值的例外。历史测量的深色主按钮 hover 白字为 3.25:1，只达到大字阈值，普通尺寸按钮文字未达 4.5:1。修改该链路时应在已授权范围内调整并复测；不得依赖“瞬时反馈”豁免，也不因本条自动改动全局主题。

**升级注意**：低于 Qt 6.6 的环境没有 `Accent` 角色，`palette(accent)` 会回退到默认色（浅灰）。`_audit_theme_tokens()` 在首次 `apply_theme()` 时会打出 warning 提示缺失，此时应升级 PySide6，而不是在页面里写死颜色兜底。

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
| 18px | 对话框 / 独立窗口标题 | 弹窗、二级窗口；页面顶端不再使用 |
| 22px | 局部大标题 | 卡片或详情内的强调标题，非页面顶端 |
| 27px | 统计数字 | `workbenchStatValue` 等数字展示 |

规则：

1. 只从表里取值。16 / 17 / 20 / 23 / 26 / 34 / 36 / 44px 是历史遗留越级值，**新代码一律不得新增**；改到某页时顺手归位。
2. 同一页面主标题只出现一次，层级靠「标题大一号 + 辅助文字小一号 + 留白」建立，不靠颜色深浅堆叠。
3. **页面顶端不再显示页面标题与说明文字。** 页面身份由顶栏（分组 / 页名）和侧栏导航提供；页面内容从第一块功能卡片或工具栏开始，顶部只保留必要的状态/日期与操作控件：状态/日期可左对齐，操作按钮右对齐；本页操作（如刷新）并入标签栏右上角（`QTabWidget.setCornerWidget`）或工具栏右侧，不单独占一整行。配置与资产备份恢复只在全局设置提供，业务页不放这类快捷入口；网络库的「网盘备份」业务页签保留（总约定 17）。
4. 不得在页面内新增「标题 + 副标题」头部，也不得恢复 `ToolPageHeader` 或等价组件；功能说明统一维护在 [software-feature-guide](../../software-feature-guide/SKILL.md)。
5. 数字（统计值、计数、时间码）可与界面字体不同族，用 `font-family: "Segoe UI", "Microsoft YaHei"` 让数字等宽易读（首页统计卡已如此）。
6. `QFont.setPointSize()` 与 QSS px 不等价（96 DPI 下 1pt ≈ 1.333px）。QSS 里用 px，`paintEvent` 自绘里用 pt，两者不要混算出"看起来差不多大"。

### 3.2 现状与收敛

历史静态盘点的 `font-size` 分布：11px 139 处、12px 61、14px 39、13px 26、15px 23、18px 20、16px 19、10px 13，其余为个位数。该计数不代表当前源码状态，调整字号前重新核对实际目标，不按旧计数批量修改。

不做全量重排（风险远大于收益）。约定：**新增代码严格按 3.1 取值；改动某个页面时，该页字号收敛到阶梯，不改其他页。**

### 3.3 字重与字体族

1. 字重只用 `bold`（或 `600`）。`700`、`900`、`lighter` 不要用。
2. 页面内不写 `font-family`，除非是等宽内容（路径、命令、日志、时间码）用 `Consolas, "Cascadia Mono", monospace`。中文界面字体只由全局 QSS 提供，页面重复声明会出现同一窗口两种中文字形。
3. 整个软件 UI 文案（按钮、标签、日志、toast、状态栏、tooltip、占位符、帮助浮层）不得使用 emoji。结构性图标（导航、卡片标题、按钮、下拉项）一律用 `gui_qt.navigation.line_icon()` 线形图标（见 4.4），不得用 emoji 充当；既有页面在涉及改动时逐步替换，不做批量返工。只读日志与状态栏由 `emoji_icons` 运行期统一删除 emoji；用户生成正文（弹幕、文章正文、文件名等数据）保留原样。

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
| 开关（`row_widgets.ToggleSwitch`） | 36×20，轨道 34×18，滑块 13；轨道取 `palette(mid)`→`palette(highlight)`，滑块 `palette(highlighted-text)`；仅键盘 Tab 聚焦画焦点圈 |
| 图标线宽 | 导航/工具图标 1.65，窗口控制图标 1.4 |
| 图标绘制 | 2 倍分辨率 + `setDevicePixelRatio(2)`，兼顾高 DPI |
| 图标统一实现 | `navigation.line_icon()` 自绘，禁止 emoji/图片充当，见 4.4 |

窗口外壳由 `window_chrome.py` 负责：无边框、DWM 圆角、四边/四角热区缩放、顶栏拖动与双击最大化。不要为单个页面重新实现标题栏或缩放逻辑。

### 4.4 线形图标（唯一图标画法）

结构性图标统一由 `src/gui_qt/navigation.py` 的 `line_icon(name, color, size=20)` 用 QPainter 绘制，不使用 emoji、彩色图片或外部图标字体。新图标在该文件的 `elif name == ...` 链里补分支；导航用图标同时补进 `tool_icon_name()` 映射。

绘制协议（新增图标必须一致）：

| 项 | 约定 |
| --- | --- |
| 画布 | 24×24 逻辑坐标系，`painter.scale(size / 24, size / 24)` |
| 描边 | `QPen(1.65, RoundCap, RoundJoin)` + `NoBrush`；窗口控制图标 1.4 |
| 元素 | 只用 `line()` / `rect()` / `drawEllipse()` / `QPainterPath`，不填充实色块 |
| 分辨率 | `size * 2` pixmap + `setDevicePixelRatio(2)`，按参数 `lru_cache` 缓存 |
| 命名 | 全小写英文单词或短横线（`folder`、`clipboard`、`win_close`） |

**已登记图标名（共 77 个，新增前先查表）**：语义接近就复用现有图标，不要为同一概念画第二个（例如「获取」用 `download`，不要再画 `fetch`）。

| 分组 | 可用名称 |
| --- | --- |
| 导航 / 内容 | `search` `compass` `list` `chart` `pulse` `globe` `folder` `document` `image` `video` `music` `message` `clipboard` `chip` `calendar` `clock` `settings` |
| 账号 / 权限 | `users` `key` `lock` `unlock` `question` |
| 操作 | `plus` `minus` `close` `check` `edit` `copy` `save` `trash` `download` `upload` `refresh` `swap` `expand` `pin` `broom` `scissors` `compress` `target` `rocket` `undo` `redo` `select_all` |
| 媒体 | `play` `pause` `stop` `skip_next` `skip_prev` `mic` `keyboard` `phone` |
| 业务对象 | `cart` `package` `ticket` `truck` `star` `heart` `smile` `palette` `dice` `tag` `link` `eye` `robot` `antenna` |
| 状态 / 主题 | `alert` `dot` `sun` `moon` `cloud` `arrow` `arrow_left` |
| 窗口控制 | `win_min` `win_max` `win_restore` `win_close` |

表里没有的图标才允许改 `navigation.py` 的 `elif name == ...` 链，且必须在同一次改动里把新名字补进本表；导航页用的图标还要补进 `tool_icon_name()` 映射。新增后核对：`grep -o 'name == "[^"]*"' src/gui_qt/navigation.py | wc -l` 的数量与本表总数一致。

颜色只传主题角色，不传 hex：

| 位置 | 颜色角色 |
| --- | --- |
| 导航项、卡片标题图标 | `palette(link)` |
| 普通 / 次级按钮图标 | `palette(button-text)` |
| 主按钮图标 | `palette(highlighted-text)` |
| 禁用态 | 不单独绘制，依赖 QIcon 自动灰化 |

用法：

- 卡片：`ToolSectionCard(title, description, icon="clipboard")`，或 `card.set_icon(name, color)`。
- 按钮：`btn.setIcon(line_icon(name, color, 16))` + `btn.setIconSize(QSize(16, 16))`，同时删除文案里的 emoji。
- 下拉：`combo.setItemIcon(index, line_icon(name, color, 16))`。
- 仅图标按钮仍必须有 tooltip（见第六节第 5 条）。

主题切换：图标会把颜色烘进像素，必须按第七节第 3 条重新生成。页面维护 `(widget, name, role)` 注册表，在 `on_theme_changed()` 里统一重设；参考实现 `src/gui_qt/pages/livestream_summary.py` 的 `_register_icon()` / `_refresh_icons()` / `_icon_color()`。

### 4.5 历史 emoji 的自动转换（运行时）

页面里既有的前导 emoji 由 `src/gui_qt/emoji_icons.py` 在运行期统一转换，不需要逐页改源码；`src/gui_qt/__init__.py` 导入包时自动安装。

| 控件 | 行为 |
| --- | --- |
| `QPushButton` / `QToolButton` | 前导 emoji 转 `setIcon`，文案同步去 emoji；主按钮（`variant=primary`）用 `highlighted-text`，其余 `button-text` |
| `QLabel` | 前导 emoji 转为文字左侧 14px 小图标（子 QLabel 绘制，不改文字颜色与字体）；`role` 为 caption/muted/subtitle 或 `objectName=navigationGroup` 时用 `placeholder-text` |
| `QCheckBox` / `QRadioButton` | 只去前导 emoji（控件本身不支持图标） |
| `QComboBox` / `QTabWidget` | 条目 / 页签 emoji 转对应图标（`link` 色）；动态 `setItemText` / `setTabText` 同样生效 |
| `QListWidget` / `QTableWidget` / `QTreeWidget` 条目 | 首列前导 emoji 转条目图标，句中 emoji 删除 |
| `QGroupBox` 标题 | 去掉 emoji（Qt 分组框没有图标位，不加图标） |
| `QAction` / `QMenu` | 菜单项前导 emoji 转菜单图标；菜单标题与句中 emoji 删除 |
| 窗口标题 | 去掉 emoji |
| `QPlainTextEdit` / `QTextEdit`（只读日志）/ `QStatusBar` | 删除 emoji；只读 `QTextEdit` 日志按级别使用 success/warning/danger/link 字色；`QTextBrowser` 用户正文除外 |
| `ToolSectionCard` | 标题 emoji 自动转 `icon=`，无需页面传参 |

约束与注意事项：

1. 前导 emoji 转图标；其余位置的 emoji（按钮、标签、页签、条目、菜单文案、只读日志、状态栏、tooltip、占位符、帮助浮层）直接删除并收拢空白；只读 `QPlainTextEdit`/`QTextEdit` 的日志写入、`QStatusBar.showMessage`、`QWidget.setToolTip`、`QLineEdit.setPlaceholderText` 已统一过滤。只读 `QTextEdit` 日志按提示级别使用 success/warning/danger/link 字色代替 emoji。可编辑输入区与 `QTextBrowser` 用户生成正文不处理。
2. 未登记的 emoji 保持原样。新增 emoji 必须在 `_EMOJI_ICONS` 里登记，且只能指向 `line_icon()` 已存在的图标名。
3. 显式调用 `setIcon()` 会关闭该控件的自动图标（导航栏等自定义图标不会被运行时转换覆盖）。
4. 颜色烘进图标，切主题后由 `theme.retheme_all()` 调用 `emoji_icons.refresh_all()` 统一重生成；新增主题切换入口必须走 `retheme_all()`。
5. 新增功能仍按 4.4 显式写图标（`icon=` / `setIcon`），不要把新代码依赖在这层兼容转换上。

### 4.6 文本右键菜单（统一接管）

Qt 内建的文本上下文菜单是英文（`&Copy` / `Select All`）且无图标，与中文主题不一致。`src/gui_qt/context_menu.py` 在应用级事件过滤器里统一接管（`main.py` 创建 QApplication 后 `install()`）：

| 控件 | 菜单内容 |
| --- | --- |
| `QLineEdit`（可编辑） | 撤销 / 重做 ｜ 剪切 / 复制 / 粘贴 / 删除 ｜ 全选 |
| `QLineEdit`（只读） | 复制 ｜ 全选 |
| `QLineEdit`（密码框） | 粘贴 ｜ 全选（不出现复制/剪切/删除） |
| `QTextEdit` / `QPlainTextEdit` | 按只读与否同上；可编辑区含撤销/重做与删除 |
| `QTextBrowser` | 复制（链接上额外「复制链接地址」）｜ 全选 |
| 可选中 `QLabel` | 复制 ｜ 全选 |

规则：

1. 文案中文、图标取 `line_icon()`、快捷键由 `QAction.setShortcut` 右对齐、禁用态随选区与剪贴板内容变化。
2. 只接管默认菜单策略的控件；页面 `setContextMenuPolicy(CustomContextMenu)` 或重写 `contextMenuEvent` 的自建菜单保持原样，新页面不要再自建文本菜单。
3. 菜单外观统一由 `theme.py` 的 `QMenu` QSS 负责：项圆角 4、内边距 `6px 16px`、分隔线 `palette(mid)`，全部取 palette 角色，不写死颜色。

## 五、布局结构与信息密度

本节管「东西摆在哪、占多大、放不下怎么办」。颜色仍归第二节，字号圆角归第四、五节。现状与规范不一致的页面，**只在改到该页时归位**，不批量重排（同 3.2 的收敛原则）。

### 5.1 页面骨架与四边留白

页面结构固定为：外壳顶栏 → 页面内容 → 外壳状态栏。页面自己是 `BasePage`，已通过 `pageSurface` 拿到 `window` 底色，**不再画外框、不再设背景**。

| 层 | 留白 | 说明 |
| --- | --- | --- |
| 页面外边距 | `16, 16, 16, 16` | 新页面唯一取值，四边一致 |
| 卡片之间 | 12（`setSpacing(12)`） | 竖向节奏不用 16 |
| 卡片内边距 | `12, 12, 12, 12` | 带标题行的卡片 `12, 10, 12, 12` |
| 大卡片内边距 | 上限 16 | 仅首页统计卡、详情主卡 |
| 同行控件间距 | 8 | 标签与控件之间 6 |
| 卡片内分组之间 | 12 | 用留白分隔，不加分隔线 |

规则：

1. 四边留白只在页面根布局设一次。子卡片再补 16 会变成 32，页面内容和外壳对不齐。
2. 有滚动区时，滚动区内边距设 `0, 0, 0, 0`，由外层 16 负责；滚动条贴右，不额外留白。
3. 首块内容距顶 16。页面顶端不放标题与说明（见 3.1 第 3 条），所以不存在「标题占位」。
4. 末块与状态栏之间保留 16，不留贴边。
5. 现存四种页面边距：`10,10,10,10`（`audio.py` / `editor.py` / `organize.py`）、`12,12,12,12`（`settings.py` / `subtitle.py` / `tickets_page.py`）、`16,12,16,12`（`netdisk.py` / `account_registry.py`）、`16,16,16,12`（`douyin_index.py` / `asset_image_search.py`）。新代码一律 16；老页面改到时收敛。

### 5.2 页签层级（一至四级）

层级由**位置**决定，不靠字号或颜色深浅区分。全项目最多四级。

| 级 | 位置 | 实现 | 形态 |
| --- | --- | --- | --- |
| 一级 | 左侧栏 | `main_window` 导航 | 导航项，不是页签 |
| 二级 | 页面顶部 | `pages/containers.TabbedPage` | 卡片式：`alternate-base` 底、上圆角 6、**1px `mid` 边框**、选中 `base` + 2px 下边线 |
| 三级 | 页内第一块卡片上方 | 页面内 `QTabWidget` / `AutoFitTabWidget` | 下划线式：无底色、无边框、`padding: 6px 12px`、选中 2px `highlight` 下划线 |
| 四级 | 卡片或区域内部 | 页面级 `objectName` 样式 | 分段式：容器 `alternate-base`、圆角 6、选中 `base` + 1px `shadow`、`padding: 4px 10px`、字号 12 |

规则：

1. **最多四级**。需要第五层说明信息架构有问题：改用筛选下拉、分段按钮或拆页，不再加一层页签。
2. 二级 2–6 个，三级 2–7 个，四级 2–5 个。超出数量改用「更多」或筛选。
3. 标题 2–6 字，不带 emoji；数量写成「视频 12」，不加括号之外的修饰。
4. 顺序按流程或使用频率，主视图第一；不按字母序、不按创建时间。
5. 本页操作（如刷新）放 `setCornerWidget` 或工具栏右侧，不混进页签行；配置与资产备份恢复入口统一在全局设置，网络库的「网盘备份」业务页签保留（总约定 17）。
6. 切换页签不重建整页；延迟构造沿用 `TabbedPage(lazy=True)`。
7. **三级、四级不得复用二级的卡片式外观**。同一屏出现两行长得一样的页签，用户分不清层级。现状三级/四级都用全局 `QTabBar` 样式（`theme.py:47-50`），改到该页时用页面级 `objectName` 样式收敛；**不动 `theme.py` 全局基线**。
8. **二级未选中页签的边框必须是 `palette(mid)`，不能用 `palette(shadow)`**。`alternate-base`（浅色 `#f0f3f8`）与页面底 `window`（`#f3f5f9`）只差 3 个色阶，用 `shadow` 做边框时整行几乎看不出卡片形状，只剩一条下划线（样张实测）。
9. **页签不响应滚轮**：滚轮只滚页面，不允许 `QTabBar` 随滚轮切页签。需要时由用户单项授权，控件上标 `wheelOk=true` 才放行。

### 5.3 边框宽度

| 场景 | 宽度 | 颜色 |
| --- | --- | --- |
| 卡片、分组框 | 1px | `palette(shadow)` |
| 输入框、下拉、表格、列表 | 1px | `palette(mid)` |
| 悬停 | 1px，只换色 | `palette(dark)` 或 `palette(highlight)` |
| 焦点 | 1px，只换色 | `palette(highlight)` |
| 页签选中指示 | 2px，仅下边线 | `palette(highlight)` |
| 分隔线、表头底边 | 1px | `palette(shadow)` |

规则：

1. 除页签选中指示外，不得出现 ≥2px 的边框；不许用 `border: 2px` 表达强调。
2. 悬停/焦点只换颜色不换宽度，否则控件会跳 1px。
3. 卡片已有 1px 边框时，内部控件不再套同色边框（双线）；表头只留底边。
4. 禁止 `border: none` + 伪阴影模拟卡片：本项目没有阴影体系，深色下会露出灰边。
5. 圆角与边框同时存在时边框由 Qt 画在内侧，不写 `outline` 补偿。

### 5.4 工具栏与按钮排布

一行一件事：`[输入 / 筛选] …… [次要操作] [主操作]`。

| 项 | 值 |
| --- | --- |
| 按钮高度 | 全局 `padding: 6px 12px`（约 30px）；同一行高度必须一致 |
| 仅图标按钮 | 30×26，必须 tooltip |
| 按钮间距 | 8；操作组之间 12 |
| 主操作 | 每行最多 1 个 `variant="primary"`，固定最右 |
| 危险操作 | `variant="danger"`，与主操作至少隔 12，或收进「更多」 |
| 文案 | 动词开头 2–6 字（抓取 / 开始 / 停止 / 导出 / 保存），不加标点 |
| 图标 | 16px，`line_icon` |

规则：

1. 左侧放输入与筛选，右侧放操作；不要把操作按钮夹在两个输入框之间。
2. 同一行按钮 ≤5 个（含主操作）。超过走 5.5。
3. 二次确认不靠按钮样式表达，统一用弹窗（见[交互与反馈](interaction.md)第 1.4 节）。
4. 禁用只改样式不改宽度，且保留 tooltip 说明原因。
5. 同一页面同类操作只用一种动词：「抓取」就不要在另一处写「获取」。
6. **带菜单的按钮保持中性外观**：`setMenu()` 的按钮展开/按下不得变成主操作蓝（`palette(highlight)` 底 + 白字），否则用户会误认成主操作。用 `menuButton` 属性单独压成 `midlight` 底 + `link` 字。
7. **菜单箭头只留一个**：Qt 会为 `setMenu()` 自动画 menu-indicator，不要再给按钮 `setIcon(chevron)`，否则出现双箭头（样张实测）。
8. **勾选框不要只给未选态上样式**：只写 `QCheckBox::indicator:unchecked` 时，选中态会落回原生绘制，同一列出现「白底灰框」与「原生勾号」两种观感。主程序统一由 `theme._CheckProxyStyle`（`QProxyStyle` 接管 `PE_IndicatorCheckBox` / `PE_IndicatorItemViewItemCheck`）自绘：未选 `base` 底 + 1px `mid` 边框 + 圆角 3，选中 `highlight` 实心 + `highlighted-text` 勾；QSS 只保留 `::indicator` 尺寸（16×16），**不再写 background/border**。若某处改用 delegate 自绘，绘制前必须 `opt.features &= ~QStyleOptionViewItem.HasCheckIndicator`，否则原生勾选标记会和自绘框叠成双层。
9. **数字框覆盖 subcontrol 必须补箭头图**：一旦写了 `QSpinBox::up-button/down-button`，原生箭头就不再绘制。必须同时给 `::up-arrow` / `::down-arrow` 的 `image`，右侧 `padding` 留 22px，按钮 `width: 20px`、贴右边缘上下各占一半（样张实测：不补 image 时箭头完全消失）。

### 5.5 超出可视范围时怎么摆

严格按顺序，不得跳级：

1. **收进「更多 ▾」**：低频或次要操作放 `QMenu`，用 `QPushButton` + `setMenu()`（先例：`tcdoc_bridge.py` 的账号按钮）。菜单项顺序与原按钮一致，不重排。
2. **换行**：整组操作换到第二行工具栏，主操作仍在最右。需要流式布局时把 `pages/asset.py` 的私有 `_FlowLayout` 提取到 `common`，不要复制第二份实现。
3. **缩短文案**：只去掉修饰词，不改变动作语义。

禁止：

- 出现横向滚动条；
- 按钮被压缩到文字截断，或悄悄只剩图标（要转仅图标就必须显式转 + tooltip）；
- 按钮被布局隐藏；
- 为塞下按钮而缩小字号（字号阶梯是硬的）。

判定：默认 1240 宽放得下就保持一行；缩到最小 880 宽时，主操作与「更多」必须可见；窗口变宽恢复原布局，不记忆折叠状态。

### 5.6 下拉框总模式

结构：`标签 + 下拉` 同行，标签在左、控件在右。

| 项 | 规则 |
| --- | --- |
| 宽度 | 同组等宽，`setMinimumWidth(100~160)`，不随选项文字跳动 |
| 长文本 | `setSizeAdjustPolicy(AdjustToMinimumContentsLengthWithIcon)` + `setMinimumContentsLength(8)`，超出省略并 tooltip 全文 |
| 选项数 | 2–12 普通下拉；>12 用可输入下拉（`setEditable(True)` + `QCompleter`）或配搜索框；<2 改只读文本 |
| 首项 | 筛选类固定「全部」，不用空项、不用「请选择」；输入类用 placeholder |
| 滚轮 | 不响应滚轮改值（滚轮只滚页面）：`setFocusPolicy(Qt.StrongFocus)`，必要时在 `wheelEvent` 里转发父级；**数字框、滑条、页签同样适用**，统一在应用级事件过滤器里拦（`gui_qt/wheel_guard.py`，`main.py` 创建 QApplication 后安装，滚轮转给最近滚动区），控件声明 `wheelOk=true` 才放行；`QScrollBar` 不在拦截名单 |
| 图标 | `setItemIcon(index, line_icon(name, color, 16))` |
| 禁用 | 保留文字，用禁用色；不换成「无」 |

规则：

1. 同一行两个以上下拉时，标签取组内最长宽度并固定，控件等宽对齐。
2. 选项文案与被筛选数据的显示值完全一致（含单位与大小写），不另造简称。
3. 不用下拉承载超过一屏（>15 项）且不可搜索的列表。
4. **弹层圆角交给系统原生，不要自己改窗口标志**：Win11 的 DWM 会给弹层圆角，`QComboBox` 默认弹层即可。**禁止在弹层 Show 事件里给它 `setWindowFlag` / `WA_TranslucentBackground` / 换样式表**——那会重建原生窗口，表现为首次点开一片空白、点不掉、卡一下，切到别的窗口再点才恢复（主程序实测）。确需自绘圆角时，只能在弹层创建前一次性设定，且要验证首帧与关闭；现成结论：不值得，用原生。

## 六、组件写法

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

## 七、主题切换铁律

主题有两条生效路径：`QPalette` + 全局 QSS（自动生效），以及内联样式（由 `theme` 的 remap 补丁处理）。下列七条是自绘与缓存内容的必守项。

1. **内联 QSS 只用角色**：`palette(...)` 或 `theme(...)`，不写 hex。remap 只兜底历史色值，不会为新色值发明映射。
2. **自绘控件每帧取色**：`paintEvent` 内 `pal = theme.current_palette()`，不要 `__init__` 里缓存 `QColor`。并在 `on_theme_changed()` 里 `self.update()`（首页环形图/趋势图即此模式）。
3. **预生成的 QIcon 必须重生成**：`line_icon(name, color)` 把颜色烘进像素，切主题后不会变。参考 `MainWindow._sync_shell_icons()`：主题变化时重新取 `palette(link)` / `palette(placeholder-text)` 再生成。
4. **富文本与表格不要直接刷色**：给 `QTextCharFormat` / item 打 role 属性，`on_theme_changed()` 里按 role 重新 setForeground（`asset.py` 的 `refresh_theme()` 模式）。这样不破坏选择、滚动位置与图片资源。
5. **页面实现 `on_theme_changed()`，不重建控件**：主窗口只通知当前页，其余页记入 `_theme_dirty`，切回时补通知。重建会丢输入、丢滚动位置、重跑加载逻辑。
6. **不要用 `setPalette()` 改界面底色**：与全局主题打架，且 Qt 会缓存 `palette()` 解析结果。
7. **独立窗口与浮层**：普通窗口要有不透明底（否则深色下露出桌面或形成黑洞）；`WA_TranslucentBackground` 只给浮层、拖拽预览、媒体覆盖层。带系统标题栏的窗口由 `theme._set_native_titlebar()` 对齐深色，走无边框外壳的则由 `window_chrome` 负责。

## 八、验证路径（四条都要过）

新增或修改任何视觉相关代码后，逐条走完：

1. **侧栏手动切换**：浅 → 深 → 浅，观察新控件是否有残留白块、黑字黑底、错位边框、图标消失。
2. **跟随系统**：全局设置里开启「跟随系统深浅色」，切换 Windows 主题，10 秒内应自动跟随。
3. **定时切换**：设置浅色/深色时间点，验证到点自动切换且手动切换会在下个检查点被拉回目标主题。
4. **重启恢复**：深色下关闭再启动，首屏（骨架页 + 首页）不得先亮后暗闪烁。

补充三条容易漏的场景：

- **懒加载页**：先在浅色下打开 A 页，切深色，再进 B 页（未加载过）。B 页必须直接是深色。
- **弹窗**：主题切换后新开的对话框、`QMessageBox`、右键菜单必须正确着色（依赖 `Show` 事件适配，不能只在构造时算一次）。
- **托盘/独立窗口**：直播录制二级窗口、弹幕浮层、拖拽预览窗口单独验证。

烟测须在隔离程序副本中以 `windows` 平台插件和 offscreen 各验证一次；用户当前运行实例不属于可接入的测试环境。过程截图存 `_smoking/`，只有最终报告证据截图存 `Progress/ui/<日期>/`。仅 offscreen 通过不算完成；隔离副本、数据保护与运行实例要求见统一入口。

## 九、提交前自检清单

- [ ] 无新增 hex 前景/背景/边框色（`grep` 过一遍改动文件）
- [ ] 无 `gray` / `black` / `white` 关键字色
- [ ] 新字号 ∈ 10/11/12/13/14/15/18/22/27
- [ ] 新圆角 ∈ 4/6/8/10/12
- [ ] 新间距 ∈ 2/4/6/8/10/12/16
- [ ] 背景层级只用 window / base / alternate-base 三层
- [ ] 子控件背景为 `transparent`，未叠出不透明矩形
- [ ] 自绘控件每次取 `current_palette()`，并实现了 `on_theme_changed()`
- [ ] 预生成图标在切主题后重新生成
- [ ] 结构性图标统一用 `line_icon` 线形图标，未用 emoji / 图片 / 图标字体充当
- [ ] 依赖 4.5 自动转换时，前导 emoji 已在 `_EMOJI_ICONS` 登记且指向已存在图标
- [ ] 图标颜色取自主题角色（导航/标题 link、按钮 button-text、主按钮 highlighted-text），未传 hex
- [ ] 页面内 `setStyleSheet` 选择器带 `objectName`
- [ ] 仅图标按钮有 tooltip
- [ ] 页面四边留白 16，子卡片没有重复加边距
- [ ] 页签层级不超过四级，三级/四级没有复用二级的卡片式外观
- [ ] 边框 1px（仅页签选中指示允许 2px 下边线），悬停/焦点只换色不换宽
- [ ] 单行按钮不超过 5 个，主操作在最右；放不下按「更多 → 换行 → 缩文案」顺序处理
- [ ] 同组下拉等宽且有最小宽度，下拉/数字框/滑条/页签都不响应滚轮改值
- [ ] 带菜单的按钮没有变主操作蓝，菜单箭头只有一个
- [ ] 勾选框选中/未选同一套自绘样式，没有「QSS 未选 + 原生选中」混搭
- [ ] 数字框上下箭头能看见且能点（覆盖 subcontrol 时已补 arrow image）
- [ ] 下拉弹层用系统原生圆角，没有在 Show/Resize 里改窗口标志或透明属性
- [ ] 二级页签边框用 `palette(mid)`，在页面底上能看出卡片形状
- [ ] 新图标已查 4.4 清单，未重复造图，且已回写本表
- [ ] 弱对比 `placeholder-text` 未承载必读正文
- [ ] 四条验证路径 + 懒加载页 + 弹窗 + 独立窗口都已实测

## 十、反例与改法

以下取自 `src/gui_qt/pages/tracker.py`，是项目里最典型的旧写法，其他页面存在同类问题。

| 反例 | 深色下后果 | 改法 |
| --- | --- | --- |
| `setStyleSheet("QFrame { background: #ffffff; border-radius: 8px; }")` | 依赖兼容层映射，未登记的近似白会留白块 | `setProperty("variant", "card")`，或 `background: palette(base); border: 1px solid palette(shadow); border-radius: 12px;` |
| `"background: transparent; border: 1px solid #c8cdd6; color: #2f3440;"` | 靠 remap 勉强可用，但边框/文字分属两张映射表，易错配 | `border: 1px solid palette(mid); color: palette(text);` |
| `"background: #2e7d32; color: white;"` | 底色不映射，深色下依旧亮绿；`white` 关键字不映射 | `setProperty("variant", "primary")`，或 `background: palette(highlight); color: palette(highlighted-text);` |
| `"color: gray;"` | 关键字不映射，深色底上灰字变模糊 | `color: palette(placeholder-text);` |
| `setStyleSheet("color: #888; font-size: 15px;")` | `#888888` 命中兼容层变浅灰，但 15px 与 11px 说明文字混用破坏层级 | `color: palette(placeholder-text); font-size: 11px;` |
| 裸 `QFrame { ... }` 页面级样式 | 命中该页面所有 QFrame，含懒加载子卡片与弹窗 | 加 `objectName` 限定 |

## 十一、待评估改进项（需用户明确同意后才动）

这些改动影响全站观感与既有截图，不在本次范围内，不要顺手改。

1. **字号越级值收敛**（16/17/20/23/26/34/36/44 共 27 处）。建议按页面逐页收敛，不做一次性全局替换。
2. **`tracker.py` / `music_tracker.py` / `douyin_danmaku.py` 内联样式下沉**（分别 61 / 50 / 65 处 `setStyleSheet`）。改造风险中等，需逐页授权。
3. **浅色 `placeholder-text` 在 `midlight` 上仍是 4.37**。要彻底达标需继续压深或给 `midlight` 提亮，会连带影响弱选中底观感，暂不动；改为「落在 midlight 上的文字不用 muted」。

### 已完成（2026-11-17）

以下三项曾列在这里，现已实施并验证：

| 项 | 改动 | 结果 |
| --- | --- | --- |
| 浅色辅助文字对比度 | `PlaceholderText` `#6e7f96` → `#5f7089` | base 4.09 → 5.04，window 3.74 → 4.62，alternate-base 4.53 |
| 深色主按钮白字 | `Highlight` 深色 `#447ccf` → `#3f76c9` | 4.18 → 4.52 |
| primary 按钮 hover 无差异 | `accent_hover` 改由新增的 `palette(accent)` 承载 | 浅色 `#356bd6 → #275bc1`，深色 `#3f76c9 → #568fe5` |
