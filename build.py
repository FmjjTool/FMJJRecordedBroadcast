"""一键打包脚本：PyInstaller 绿色版 + Inno Setup 安装器。

用法：
  python build.py                   # 完整流程：清理 → PyInstaller → 隐私扫描 → 生成/编译安装器
  python build.py --skip-build      # 跳过 PyInstaller（复用 dist/），只重新生成并编译安装器
  python build.py --no-installer    # 只出绿色版，不生成安装器
  python build.py --name 名称        # 自定义产物目录/exe 名（默认 FengMingToolbox）

产物：
  dist/<NAME>/                              绿色版（双击 exe 直接用，无 CMD 窗口）
  dist/installer/<NAME>_Setup_v<版本>.exe   安装器（可选安装位置 / 写注册表 / 控制面板可卸载）

隐私保证：
  打包只收集代码模块 + 引擎资源（a_bogus.js 等），绝不携带 config/cookies.json、
  追踪账号等本机数据；打包完成后自动扫描 dist，发现隐私文件立即报错中止。
  安装包首次运行时由 init_all_configs() 重新生成默认配置。

依赖：
  pip install pyinstaller
  安装器（可选）：Inno Setup 6 — https://jrsoftware.org/isdl.php
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 打包后必须随包携带的引擎数据文件：(源路径, 包内相对目录)
ADD_DATA = [
    ("icon.ico", "."),  # Qt 窗口与任务栏共用，不只嵌入 exe
    ("src/toolbox/a_bogus.js", "toolbox"),
    ("src/toolbox/abogus_cli.js", "toolbox"),
    ("src/toolbox/_unlock_keys.json", "toolbox"),
    ("src/toolbox/bin", "toolbox/bin"),  # 内置 7-Zip（rar/7z/zip 解压与压缩）
    ("src/skills", "skills"),  # 内置 AI Skill（设置页「下载 Skill」导出用）
    ("src/gui_qt/assets", "gui_qt/assets"),  # 音乐标签页官方风格图标
    ("src/toolbox/webui/static", "toolbox/webui/static"),  # WebUI 独立前端
]

# dist 中禁止出现的隐私/本机数据（文件名 / 目录名）
PRIVACY_FILES = {
    "cookies.json", "cookies.json.bak", "cache.json", "config.json",
    "accounts.json", "groups.json", "accounts.txt", "rename_log.log",
    "music_accounts.json", "music_groups.json", "collected.db",
}
PRIVACY_FILE_PATTERNS = [re.compile(r"^app_\d{8}_\d{6}.*\.log$")]
PRIVACY_DIRS = {"config", "logs", "download", "__pycache__"}

# 本地总结引擎与 GGUF 始终由用户按需下载，标准版和 FULL 均不得携带。
OPTIONAL_LLM_BINARY = re.compile(
    r"^(?:lib)?llama(?:[-_.].*)?\.(?:exe|dll|so(?:\.\d+)*|dylib|lib|a)$",
    re.IGNORECASE,
)

ISS_TEMPLATE = r"""; 由 build.py 自动生成 — Inno Setup 6 脚本（安装目录可选 / 写注册表 / 可卸载）
; 手动编译：ISCC.exe installer.iss
; 注意：本文件里 {xxx} 是 Inno 常量（如 {app}），{{ 才是字面量大括号
#define MyAppName "封茗囧菌数据汇总库 统一维护工具箱"
#define MyAppNameEn "{APP_NAME}"
#define MyAppVersion "{APP_VERSION}"
#define MyAppPublisher "{APP_PUBLISHER}"
#define MyAppExeName "{APP_NAME}.exe"
; 版本形态：标准版留空 / FULL 版为 " FULL 版" 与 "_Full"
#define MyEditionTitle "{EDITION_TITLE}"
#define MyEditionFileSuffix "{EDITION_SUFFIX}"
#define MyEditionId "{EDITION_ID}"

; 优先用简体中文语言包（Inno 6.3+ 自带），未安装时回退英文向导
#if FileExists(CompilerPath + "\Languages\ChineseSimplified.isl")
  #define MsgFile "compiler:Languages\ChineseSimplified.isl"
#else
  #define MsgFile "compiler:Default.isl"
#endif

[Setup]
AppId={{8F6A2C71-9D4E-4B57-A3C2-7E9F01B5D384}
AppName={#MyAppName}{#MyEditionTitle}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}{#MyEditionTitle}
AppPublisher={#MyAppPublisher}
; 安装向导默认目录 = Program Files\FengMingToolbox，用户可在向导中自由修改
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
UninstallDisplayName={#MyAppName}{#MyEditionTitle}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=..\dist\installer
OutputBaseFilename={#MyAppNameEn}{#MyEditionFileSuffix}_Setup_{#MyAppVersion}
{SETUP_ICON}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64

[Languages]
Name: "chs"; MessagesFile: "{#MsgFile}"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "quicklaunchicon"; Description: "{cm:CreateQuickLaunchIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\{#MyAppNameEn}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; 运行期数据目录（日志/导出），提前授予普通用户可写权限
Name: "{app}\logs"; Permissions: users-modify
Name: "{app}\download"; Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: quicklaunchicon

[Registry]
; 自定义注册表项（记录安装位置/版本，供外部查询；卸载时自动清除）
Root: HKLM; Subkey: "Software\{#MyAppNameEn}"; ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKLM; Subkey: "Software\{#MyAppNameEn}"; ValueType: string; ValueName: "Version"; ValueData: "{#MyAppVersion}"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppNameEn}"; ValueType: string; ValueName: "Edition"; ValueData: "{#MyEditionId}"; Flags: uninsdeletevalue
Root: HKLM; Subkey: "Software\{#MyAppNameEn}"; ValueType: string; ValueName: "Publisher"; ValueData: "{#MyAppPublisher}"; Flags: uninsdeletevalue
; 控制面板「应用与功能」的卸载条目（DisplayName/UninstallString/InstallLocation/
; DisplayIcon 等）由 Inno 自动写入 HKLM\...\CurrentVersion\Uninstall\..._is1

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 默认保留用户数据（config\Cookie、下载记录、日志）；如需卸载时彻底清理，
; 取消下面注释：
; Type: filesandordirs; Name: "{app}\config"
; Type: filesandordirs; Name: "{app}\logs"
"""


def read_version_info() -> dict:
    ns = {}
    exec((ROOT / "src" / "version.py").read_text(encoding="utf-8"), ns)
    return {
        "APP_NAME": ns.get("APP_NAME", "FengMingToolbox"),
        "APP_DISPLAY_NAME": ns.get("APP_DISPLAY_NAME", "FengMingToolbox"),
        "APP_VERSION": ns.get("APP_VERSION", "0.0.0"),
        "APP_PUBLISHER": ns.get("APP_PUBLISHER", ""),
    }


def step(msg):
    print(f"\n=== {msg} ===")


def check_pyinstaller() -> bool:
    try:
        r = subprocess.run([sys.executable, "-m", "PyInstaller", "--version"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            print(f"PyInstaller {r.stdout.strip()} OK")
            return True
    except Exception:
        pass
    print("未检测到 PyInstaller，请先安装：pip install pyinstaller")
    return False


def run_pyinstaller(name: str):
    dist = ROOT / "dist" / name
    step(f"PyInstaller 打包 → dist/{name}")
    if dist.exists():
        shutil.rmtree(dist)
    build_dir = ROOT / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",
        "--windowed",             # 无 CMD 控制台（软件照常运行）
        "--name", name,
        "--paths", "src",
        "--exclude-module", "tkinter",
        # 环境里同时装有 PyQt5（pywebview 的 hook 会触发其收集），本项目只用 PySide6，
        # 显式排除以免 PyInstaller 报 "multiple Qt bindings" 中止
        "--exclude-module", "PyQt5",
        "--exclude-module", "PyQt5.sip",
        # 全源码零引用、纯传递依赖拖入的死重（安装包体积优化报告 2026-09-05）
        "--exclude-module", "matplotlib",
        "--exclude-module", "IPython",
        "--exclude-module", "jedi",
        "--exclude-module", "parso",
        # 选装的本地总结使用独立 llama.cpp 组件，不能误收集开发环境的 Python 绑定。
        "--exclude-module", "llama_cpp",
        # 函数体内动态 import 的模块，静态分析收不到，必须显式声明
        # （QtWebEngine 已移除：登录改走系统浏览器 + Playwright 嵌入）
        "--hidden-import", "qrcode",                        # 扫码登录二维码
    ]
    # 页面是懒加载的（main_window._page_class 用 importlib 按字符串导入），静态分析
    # 看不到 gui_qt.pages.*。注意 --collect-submodules 在 spec 执行阶段 sys.path 里
    # 还没有 src，收集结果为空（实测打包后切页报 ModuleNotFoundError: gui_qt.pages），
    # 所以这里直接按目录列出全部页面模块。
    pages_dir = ROOT / "src" / "gui_qt" / "pages"
    if pages_dir.is_dir():
        for f in sorted(pages_dir.glob("*.py")):
            if f.stem != "__init__":
                cmd += ["--hidden-import", f"gui_qt.pages.{f.stem}"]
    # 腾讯文档子模块按需导入；显式收集，保证打包后首次打开页签仍可加载。
    tcdoc_dir = ROOT / "src" / "toolbox" / "tcdoc"
    for f in sorted(tcdoc_dir.glob("*.py")):
        if f.stem != "__init__":
            cmd += ["--hidden-import", f"toolbox.tcdoc.{f.stem}"]
    # tracker 包同属函数体内懒加载（tracker 页里 from tracker.accounts 等动态 import），
    # 静态分析收不到，按包+子包目录显式声明（含 douyin_live 直播历史模块）
    tracker_dir = ROOT / "src" / "tracker"
    if tracker_dir.is_dir():
        for f in sorted(tracker_dir.glob("*.py")):
            if f.stem != "__init__":
                cmd += ["--hidden-import", f"tracker.{f.stem}"]
        collectors_dir = tracker_dir / "collectors"
        for f in sorted(collectors_dir.glob("*.py")):
            if f.stem != "__init__":
                cmd += ["--hidden-import", f"tracker.collectors.{f.stem}"]
    # 音乐账号追踪 + 定时任务：函数体内懒加载 / 无界面入口的模块，显式声明
    # （music_account.* 由 music_tracker 页动态导入；account_tracker_* 是计划任务入口）
    for mod in ("music_account.accounts", "music_account.collectors",
                "music_account.export_png", "toolbox.account_tracker_job",
                "toolbox.account_tracker_task", "toolbox.task_center"):
        cmd += ["--hidden-import", mod]
    icon = ROOT / "icon.ico"
    if icon.exists():
        cmd += ["--icon", str(icon)]
    else:
        print("（未找到 icon.ico，使用默认图标；放一个 icon.ico 到项目根即可自定义）")
    for src, dst in ADD_DATA:
        cmd += ["--add-data", f"{src};{dst}"]
    cmd.append("main.py")

    print(" ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0 or not (dist / f"{name}.exe").exists():
        sys.exit("PyInstaller 打包失败，请检查上方报错")
    print(f"绿色版完成：{dist}")


# ==================== 产物瘦身（安装包体积优化报告 2026-09-05） ====================
# PyInstaller 的 PySide6/playwright hook 全量拷贝运行时，以下白名单清理零功能损失：
# 1. WebEngine 调试/开发资源（F12 devtools，终端用户用不到，88MB）
# 2. 多语言文件（53 种语言 → 只留中文+英文兜底，约 92MB）
# 3. 源码零引用的 Qt DLL（3D/Charts/Pdf/Quick 风格包等，见 QT_DLL_KEEP 白名单）
PRUNE_FILES = [
    # WebEngine devtools/调试资源（*.debug.* 只服务于内嵌页 F12 调试）
    "PySide6/resources/qtwebengine_devtools_resources.debug.pak",
    "PySide6/resources/qtwebengine_resources.debug.pak",
    "PySide6/resources/qtwebengine_resources_100p.debug.pak",
    "PySide6/resources/qtwebengine_resources_200p.debug.pak",
    "PySide6/resources/v8_context_snapshot.debug.bin",
    "PySide6/resources/qtwebengine_devtools_resources.pak",
]

# Qt6*.dll 保留白名单：源码实际 import 的 5 个模块（Core/Gui/Widgets/Multimedia/
# MultimediaWidgets，2026-09-05 全源码统计）+ 各自的依赖链。WebEngine/Qml/Quick/
# WebChannel/Positioning/ShaderTools 随内嵌登录改走系统浏览器一并移除（~340MB）
QT_DLL_KEEP = {
    # 核心界面
    "Qt6Core", "Qt6Gui", "Qt6Widgets", "Qt6Network", "Qt6Svg", "Qt6OpenGL",
    "Qt6OpenGLWidgets", "Qt6Concurrent",
    # 视频预览（image_sorter）
    "Qt6Multimedia", "Qt6MultimediaWidgets",
    "Qt6SpatialAudio",
    # 其余非 Qt6* 前缀的 DLL（python310/vcruntime/avcodec 等）不受白名单管辖
}

# playwright 内置浏览器（改走系统 Edge 后整目录删除；下载兜底走用户目录）
PW_BROWSER_DIR = "playwright/driver/package/.local-browsers"

# 各语言目录的保留文件（zh-CN 主用 + en-US 兜底）
LOCALE_DIRS = [
    "PySide6/translations/qtwebengine_locales",
    "playwright/driver/package/.local-browsers/*/chrome-headless-shell-win64/locales",
]
LOCALE_KEEP = {"zh-CN.pak", "en-US.pak"}
QM_KEEP = {"qt_zh_CN.qm", "qtbase_zh_CN.qm"}


def _prune_report(removed: list, tag: str):
    if removed:
        mb = sum(s for _, s in removed) / 1048576
        print(f"  [{tag}] 删除 {len(removed)} 项，共 {mb:.1f} MB")


def prune(name: str):
    step("产物瘦身（调试资源 / 多语言 / 未用 Qt 模块）")
    internal = ROOT / "dist" / name / "_internal"
    if not internal.is_dir():
        return
    total_removed = []

    # 1. 指定文件（debug/devtools 资源）
    removed = []
    for rel in PRUNE_FILES:
        p = internal / rel
        if p.is_file():
            removed.append((rel, p.stat().st_size))
            p.unlink()
    _prune_report(removed, "调试资源")
    total_removed += removed

    # 2. Qt DLL 白名单之外的删除（Qt6*.dll）
    removed = []
    for p in (internal / "PySide6").glob("Qt6*.dll"):
        stem = p.stem  # Qt63DCore → 去掉 .dll
        if stem not in QT_DLL_KEEP:
            removed.append((p.name, p.stat().st_size))
            p.unlink()
    # 未用模块的 pyd 同步删除（源码 import 的 5 个模块 + QtMultimedia 的 shiboken
    # 内部依赖 QtNetwork——实测 import QtMultimedia 必拉起 QtNetwork.pyd，漏删即崩；
    # QtOpenGL.pyd 8.3MB 是大头）
    PYD_KEEP = {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtMultimedia",
                "QtMultimediaWidgets"}
    for p in (internal / "PySide6").glob("*.pyd"):
        if p.name[:-4] not in PYD_KEEP:
            removed.append((p.name, p.stat().st_size))
            p.unlink()
    _prune_report(removed, "未用Qt模块")
    total_removed += removed

    # 3. QML 插件树（Widgets 应用不加载 QML；WebEngine 需要的核心 QtQuick 已在 DLL 白名单）
    #    实测删 qml/ 前先验证 WebEngine 登录窗，见报告二级方案
    qml = internal / "PySide6" / "qml"
    if qml.is_dir():
        removed = [("qml/", sum(f.stat().st_size for f in qml.rglob("*") if f.is_file()))]
        shutil.rmtree(qml)
        _prune_report(removed, "QML插件树")
        total_removed += removed

    # 4. playwright 内置浏览器（走系统 Edge 方案后不再打包）
    removed = []
    lb = internal / PW_BROWSER_DIR
    if lb.is_dir():
        size = sum(f.stat().st_size for f in lb.rglob("*") if f.is_file())
        removed.append((PW_BROWSER_DIR, size))
        shutil.rmtree(lb)
        _prune_report(removed, "PW内置浏览器")
        total_removed += removed

    # 5. 多语言 → 只留中英
    removed = []
    for pattern in LOCALE_DIRS:
        for loc_dir in internal.glob(pattern):
            if not loc_dir.is_dir():
                continue
            for pak in loc_dir.iterdir():
                if pak.is_file() and pak.name not in LOCALE_KEEP:
                    removed.append((str(pak.relative_to(internal)), pak.stat().st_size))
                    pak.unlink()
    trans = internal / "PySide6" / "translations"
    if trans.is_dir():
        for qm in trans.iterdir():
            if qm.is_file() and qm.suffix == ".qm" and qm.name not in QM_KEEP:
                removed.append((qm.name, qm.stat().st_size))
                qm.unlink()
    _prune_report(removed, "多余语言")
    total_removed += removed

    if total_removed:
        mb = sum(s for _, s in total_removed) / 1048576
        print(f"  瘦身合计：{len(total_removed)} 项，{mb:.1f} MB")
    else:
        print("  无可清理项")


def optional_llm_bundle_hits(root: Path) -> list[str]:
    """只读检查误混入产物/供应商目录的选装本地总结组件，不删除或改写文件。"""
    hits = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        parts = tuple(part.casefold() for part in rel.parts)
        if path.is_dir():
            if (parts[-2:] == ("optional_components", "local_llm")
                    or path.name.casefold() == "llama_cpp"):
                hits.append(f"[可选推理目录] {rel}")
            continue
        if ".gguf" in (suffix.casefold() for suffix in path.suffixes):
            hits.append(f"[可选语言模型] {rel}")
        elif OPTIONAL_LLM_BINARY.fullmatch(path.name):
            hits.append(f"[可选推理程序] {rel}")
        elif path.name.casefold().startswith("llama_cpp_python-"):
            hits.append(f"[可选推理安装包] {rel}")
    return hits


def privacy_scan(name: str):
    step("隐私扫描（确保不携带 Cookie/账号等本机数据）")
    dist = ROOT / "dist" / name
    hits = optional_llm_bundle_hits(dist)
    for p in dist.rglob("*"):
        rel = p.relative_to(dist)
        rel_posix = rel.as_posix()
        if p.is_dir():
            if p.name in PRIVACY_DIRS:
                hits.append(f"[目录] {rel}")
            continue
        # FULL 版内置模型自带的 config.json 是模型参数，不是本机配置，允许存在
        if rel_posix.startswith("models/") and p.name == "config.json":
            continue
        if p.name in PRIVACY_FILES or any(pat.match(p.name) for pat in PRIVACY_FILE_PATTERNS):
            hits.append(f"[文件] {rel}")
    if hits:
        print("发现隐私/本机数据或选装本地推理组件，禁止分发：")
        for h in hits:
            print("  " + h)
        sys.exit("请检查 ADD_DATA / 打包配置后重试")
    print("扫描通过：dist 内无 Cookie / 配置 / 账号等隐私文件，且未携带选装本地推理组件或 GGUF")


def find_iscc() -> str | None:
    exe = shutil.which("ISCC.exe")
    if exe:
        return exe
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 7", "ISCC.exe"),  # 按用户安装
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
        r"C:\Program Files\Inno Setup 7\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
        r"C:\Program Files\Inno Setup 5\ISCC.exe",
    ]
    for p in candidates:
        if p and Path(p).exists():
            return p
    return None


def write_sha256sums(exes: list) -> Path:
    """生成客户端更新要用的校验文件（每个安装包一行，必须随 Release 上传）。"""
    import hashlib

    lines = []
    for exe in exes:
        h = hashlib.sha256()
        with exe.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        lines.append(f"{h.hexdigest()}  {exe.name}")
    sums = Path(exes[0]).parent / "SHA256SUMS.txt"
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sums


def installer_path(name: str, version: str, edition: str = "standard") -> Path:
    suffix = "_Full" if edition == "full" else ""
    return ROOT / "dist" / "installer" / f"{name}{suffix}_Setup_{version}.exe"


# ---- FULL 内置版：组件准备与装配 ----
# 供应商目录放在 build/ 之外，避免 run_pyinstaller 清理 build/ 时被删。
FULL_VENDOR = ROOT / "build_full_vendor"
WHISPER_MODELS = ("base",)


def _download_file(url: str, dest: Path, label: str):
    import requests

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        print(f"  已存在：{label}")
        return
    tmp = dest.with_name(dest.name + ".part")
    print(f"  下载：{label}")
    with requests.get(url, stream=True, timeout=(15, 180)) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_content(1024 * 256):
                if chunk:
                    f.write(chunk)
    tmp.replace(dest)


def prepare_full_vendor():
    """把 FULL 版需要的一切外部组件收集到 build_full_vendor/（只需跑一次）。"""
    step("准备 FULL 版内置组件")
    vendor = FULL_VENDOR
    (vendor / "bin").mkdir(parents=True, exist_ok=True)
    (vendor / "models").mkdir(parents=True, exist_ok=True)
    (vendor / "browsers").mkdir(parents=True, exist_ok=True)

    for exe in ("ffmpeg.exe", "ffprobe.exe"):
        dst = vendor / "bin" / exe
        if dst.exists():
            print(f"  已存在：bin/{exe}")
            continue
        src = shutil.which(exe)
        if not src:
            sys.exit(f"本机未找到 {exe}，请先安装 FFmpeg（或手动放入 {dst}）")
        shutil.copy2(src, dst)
        print(f"  复制：{exe} ← {src}")

    src = ROOT / "models" / "whisper-faster.exe"
    dst = vendor / "models" / "whisper-faster.exe"
    if not dst.exists():
        if not src.is_file():
            sys.exit(f"未找到本地转写程序：{src}")
        shutil.copy2(src, dst)
        print("  复制：whisper-faster.exe")

    sys.path.insert(0, str(ROOT / "src"))
    from toolbox import asr_engine, image_embedding

    for key in WHISPER_MODELS:
        folder = vendor / "models" / f"faster-whisper-{key}"
        if (folder / "model.bin").is_file():
            print(f"  已存在：faster-whisper-{key}")
            continue
        files = asr_engine._repo_file_list(key)
        if not files:
            sys.exit(f"模型 {key} 文件列表为空，请检查网络")
        repo = asr_engine.MODEL_REPO_TPL.format(key=key)
        for item in files:
            rel = item["Path"]
            url = asr_engine.MODELSCOPE_RESOLVE.format(repo=repo, path=rel)
            _download_file(url, folder / rel, f"faster-whisper-{key}/{rel}")

    clip_dir = vendor / "models" / "clip"
    if not any(clip_dir.glob("*.onnx")):
        last = None
        for url in image_embedding.MODEL_DOWNLOAD_URLS:
            try:
                _download_file(url, clip_dir / "vision_model_quantized.onnx", "CLIP 视觉编码器")
                last = None
                break
            except Exception as exc:  # noqa: BLE001 - 逐个源回退
                last = exc
                print(f"  源失败：{url}（{exc.__class__.__name__}）")
        if last is not None:
            sys.exit(f"CLIP 模型下载失败：{last}")

    browsers_root = (Path(os.environ.get("LOCALAPPDATA", ""))
                     / "ms-playwright")
    shells = sorted(browsers_root.glob("chromium_headless_shell-*"))
    target = vendor / "browsers" / shells[-1].name if shells else None
    if shells and not (target / "chrome-win" / "headless_shell.exe").is_file():
        shutil.copytree(shells[-1], target, dirs_exist_ok=True)
        print(f"  复制：{shells[-1].name}")
    elif target is not None:
        print(f"  已存在：{target.name}")
    else:
        print("  警告：未找到 chromium_headless_shell-*，FULL 包将不含采集引擎内核")
        print("        可先运行：python -m playwright install chromium-headless-shell")

    print(f"组件目录：{vendor}")


def stage_full_bundle(dist: Path):
    """把内置组件装配进 dist，并写入 FULL_EDITION 标记。"""
    step("装配 FULL 内置组件")
    optional_hits = optional_llm_bundle_hits(FULL_VENDOR)
    if optional_hits:
        print("本地总结 CPU/GPU 引擎与 GGUF 不随 FULL 包内置：")
        for hit in optional_hits:
            print("  " + hit)
        sys.exit("请将上述选装组件移出 FULL 供应商目录后重试；本次未复制内置组件")
    missing = []
    for rel in ("bin/ffmpeg.exe", "bin/ffprobe.exe", "models/whisper-faster.exe"):
        if not (FULL_VENDOR / rel).is_file():
            missing.append(rel)
    for key in WHISPER_MODELS:
        if not (FULL_VENDOR / "models" / f"faster-whisper-{key}" / "model.bin").is_file():
            missing.append(f"models/faster-whisper-{key}")
    if not any((FULL_VENDOR / "models" / "clip").glob("*.onnx")):
        missing.append("models/clip/*.onnx")
    if missing:
        sys.exit("FULL 内置组件不完整，请先运行：python build.py --prepare-full\n"
                 "缺少：" + "、".join(missing))

    for sub in ("bin", "models", "browsers"):
        src = FULL_VENDOR / sub
        if src.is_dir():
            shutil.copytree(src, dist / sub, dirs_exist_ok=True)
            size = sum(p.stat().st_size for p in src.rglob("*") if p.is_file())
            print(f"  {sub}/ → dist（{size / 1048576:.1f} MB）")
    (dist / "FULL_EDITION").write_text("full\n", encoding="utf-8")
    print("  写入标记：FULL_EDITION")


def build_installer(name: str, ver: dict, edition: str = "standard"):
    step("生成 Inno Setup 安装器")
    iss_dir = ROOT / "build"
    iss_dir.mkdir(exist_ok=True)
    iss = iss_dir / "installer.iss"
    # 安装器 exe 图标：项目根 icon.ico（iss 在 build/ 下编译，相对路径 ..\）
    setup_icon = ("SetupIconFile=..\\icon.ico" if (ROOT / "icon.ico").exists()
                  else "; 未找到项目根 icon.ico，安装器使用 Inno 默认图标")
    iss.write_text(
        ISS_TEMPLATE
        .replace("{APP_NAME}", name)
        .replace("{APP_VERSION}", ver["APP_VERSION"])
        .replace("{APP_PUBLISHER}", ver["APP_PUBLISHER"])
        .replace("{EDITION_TITLE}", " FULL 版" if edition == "full" else "")
        .replace("{EDITION_SUFFIX}", "_Full" if edition == "full" else "")
        .replace("{EDITION_ID}", edition)
        .replace("{SETUP_ICON}", setup_icon),
        encoding="utf-8-sig",  # BOM：保证 ISCC 正确识别 UTF-8 中文
    )
    print(f"安装脚本已生成：{iss}")

    iscc = find_iscc()
    if not iscc:
        print("未找到 Inno Setup（ISCC.exe），无法自动编译安装器。")
        print("  1) 安装 Inno Setup 6：https://jrsoftware.org/isdl.php")
        print("  2) 重跑 python build.py --skip-build")
        return

    out = ROOT / "dist" / "installer"
    r = subprocess.run([iscc, str(iss)], cwd=iss_dir)
    exe = installer_path(name, ver["APP_VERSION"], edition)
    if r.returncode == 0 and exe.exists():
        print(f"安装器完成：{exe}（{exe.stat().st_size / 1048576:.1f} MB）")
    else:
        sys.exit("安装器编译失败，请查看 ISCC 输出")


def main():
    ap = argparse.ArgumentParser(description="封茗囧菌工具箱 打包脚本")
    ap.add_argument("--name", default=None, help="产物/exe 名称（默认取 version.py 的 APP_NAME）")
    ap.add_argument("--skip-build", action="store_true", help="跳过 PyInstaller，仅重新生成安装器")
    ap.add_argument("--no-installer", action="store_true", help="只打绿色版，不出安装器")
    ap.add_argument("--edition", choices=("standard", "full"), default="standard",
                    help="standard=标准版（默认）；full=FULL 内置版（需先 --prepare-full）")
    ap.add_argument("--prepare-full", action="store_true",
                    help="收集 FULL 版内置组件到 build_full_vendor/ 后退出")
    args = ap.parse_args()

    if args.prepare_full:
        prepare_full_vendor()
        return

    ver = read_version_info()
    name = args.name or ver["APP_NAME"]
    print(f"应用：{ver['APP_DISPLAY_NAME']}  v{ver['APP_VERSION']}  制作：{ver['APP_PUBLISHER']}")
    print(f"产物名：{name}  形态：{args.edition}")

    if not args.skip_build:
        if not check_pyinstaller():
            sys.exit(1)
        run_pyinstaller(name)
        prune(name)

    if args.edition == "full":
        stage_full_bundle(ROOT / "dist" / name)

    privacy_scan(name)

    if args.no_installer:
        print("\n完成（--no-installer：未生成安装器）")
        return

    build_installer(name, ver, args.edition)
    version = ver["APP_VERSION"]
    existing = [p for p in (installer_path(name, version, "standard"),
                            installer_path(name, version, "full")) if p.exists()]
    if existing:
        sums = write_sha256sums(existing)
        print(f"\n校验文件：{sums}（{len(existing)} 行）")
        if args.edition == "full" and len(existing) < 2:
            print("  警告：本机没有同版本标准版安装器，校验文件只有 FULL 一行；"
                  "发布前请先构建标准版")
        print("  上传 Release 时请把 SHA256SUMS.txt 与安装包一起附上，"
              "客户端更新会强校验（缺文件则只允许手动下载）。")

    print("\n全部完成")
    print(f"  绿色版：dist\\{name}\\{name}.exe（无 CMD 窗口，双击即用）")
    print(f"  安装器：dist\\installer\\{installer_path(name, version, args.edition).name}"
          "（可选目录/注册表/可卸载）")


if __name__ == "__main__":
    main()
