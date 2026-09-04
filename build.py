"""一键打包脚本：PyInstaller 绿色版 + Inno Setup 安装器。

用法：
  python build.py                   # 完整流程：清理 → PyInstaller → 隐私扫描 → 生成/编译安装器
  python build.py --skip-build      # 跳过 PyInstaller（复用 dist/），只重新生成并编译安装器
  python build.py --no-installer    # 只出绿色版，不生成安装器
  python build.py --with-dycast     # 把 dycast-main（弹幕姬源码）一并打进安装包
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
    ("src/toolbox/a_bogus.js", "toolbox"),
    ("src/toolbox/abogus_cli.js", "toolbox"),
    ("src/toolbox/_unlock_keys.json", "toolbox"),
    ("src/skills", "skills"),  # 内置 AI Skill（设置页「下载 Skill」导出用）
]

# dist 中禁止出现的隐私/本机数据（文件名 / 目录名）
PRIVACY_FILES = {
    "cookies.json", "cookies.json.bak", "cache.json", "config.json",
    "accounts.json", "groups.json", "accounts.txt", "rename_log.log",
    "music_accounts.json", "music_groups.json", "collected.db",
}
PRIVACY_FILE_PATTERNS = [re.compile(r"^app_\d{8}_\d{6}.*\.log$")]
PRIVACY_DIRS = {"config", "logs", "download", "__pycache__"}

DYCAST_EXCLUDE = {"node_modules", "downloads", "sessions", ".git", ".github", "__pycache__"}

ISS_TEMPLATE = r"""; 由 build.py 自动生成 — Inno Setup 6 脚本（安装目录可选 / 写注册表 / 可卸载）
; 手动编译：ISCC.exe installer.iss
; 注意：本文件里 {xxx} 是 Inno 常量（如 {app}），{{ 才是字面量大括号
#define MyAppName "封茗囧菌数据汇总库 统一维护工具箱"
#define MyAppNameEn "{APP_NAME}"
#define MyAppVersion "{APP_VERSION}"
#define MyAppPublisher "{APP_PUBLISHER}"
#define MyAppExeName "{APP_NAME}.exe"

; 优先用简体中文语言包（Inno 6.3+ 自带），未安装时回退英文向导
#if FileExists(CompilerPath + "\Languages\ChineseSimplified.isl")
  #define MsgFile "compiler:Languages\ChineseSimplified.isl"
#else
  #define MsgFile "compiler:Default.isl"
#endif

[Setup]
AppId={{8F6A2C71-9D4E-4B57-A3C2-7E9F01B5D384}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
; 安装向导默认目录 = Program Files\FengMingToolbox，用户可在向导中自由修改
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=..\dist\installer
OutputBaseFilename={#MyAppNameEn}_Setup_{#MyAppVersion}
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
            print(f"PyInstaller {r.stdout.strip()} ✓")
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
        # 函数体内动态 import 的模块，静态分析收不到，必须显式声明
        "--hidden-import", "qrcode",                        # 扫码登录二维码
        "--hidden-import", "PySide6.QtWebEngineWidgets",    # 软件内嵌浏览器登录
        "--hidden-import", "PySide6.QtWebEngineCore",
    ]
    icon = ROOT / "icon.ico"
    if icon.exists():
        cmd += ["--icon", str(icon)]
    else:
        print("（未找到 icon.ico，使用默认图标；放一个 icon.ico 到项目根即可自定义）")
    for src, dst in ADD_DATA:
        cmd += ["--add-data", f"{src};{dst}"]
    cmd.append("app_qt.py")

    print(" ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if r.returncode != 0 or not (dist / f"{name}.exe").exists():
        sys.exit("PyInstaller 打包失败，请检查上方报错")
    print(f"绿色版完成：{dist}")


def privacy_scan(name: str):
    step("隐私扫描（确保不携带 Cookie/账号等本机数据）")
    dist = ROOT / "dist" / name
    hits = []
    for p in dist.rglob("*"):
        rel = p.relative_to(dist)
        if p.is_dir():
            if p.name in PRIVACY_DIRS:
                hits.append(f"[目录] {rel}")
            continue
        if p.name in PRIVACY_FILES or any(pat.match(p.name) for pat in PRIVACY_FILE_PATTERNS):
            hits.append(f"[文件] {rel}")
    if hits:
        print("发现隐私/本机数据，禁止分发：")
        for h in hits:
            print("  " + h)
        sys.exit("请检查 ADD_DATA / 打包配置后重试")
    print("扫描通过：dist 内无 Cookie / 配置 / 账号等隐私文件")


def copy_dycast(name: str):
    step("附带 dycast-main（弹幕姬）")
    src = ROOT / "dycast-main"
    dst = ROOT / "dist" / name / "dycast-main"
    if not src.is_dir():
        print("未找到 dycast-main，跳过")
        return

    def ignore(dir_path, names):
        return [n for n in names if n in DYCAST_EXCLUDE]

    shutil.copytree(src, dst, ignore=ignore)
    print(f"已复制 → {dst}（已排除 node_modules/downloads/sessions/.git）")


def find_iscc() -> str | None:
    exe = shutil.which("ISCC.exe")
    if exe:
        return exe
    candidates = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),  # winget 按用户安装
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
        r"C:\Program Files\Inno Setup 5\ISCC.exe",
    ]
    for p in candidates:
        if p and Path(p).exists():
            return p
    return None


def build_installer(name: str, ver: dict):
    step("生成 Inno Setup 安装器")
    iss_dir = ROOT / "build"
    iss_dir.mkdir(exist_ok=True)
    iss = iss_dir / "installer.iss"
    iss.write_text(
        ISS_TEMPLATE
        .replace("{APP_NAME}", name)
        .replace("{APP_VERSION}", ver["APP_VERSION"])
        .replace("{APP_PUBLISHER}", ver["APP_PUBLISHER"]),
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
    exe = out / f"{name}_Setup_{ver['APP_VERSION']}.exe"
    if r.returncode == 0 and exe.exists():
        print(f"安装器完成：{exe}")
    else:
        sys.exit("安装器编译失败，请查看 ISCC 输出")


def main():
    ap = argparse.ArgumentParser(description="封茗囧菌工具箱 打包脚本")
    ap.add_argument("--name", default=None, help="产物/exe 名称（默认取 version.py 的 APP_NAME）")
    ap.add_argument("--skip-build", action="store_true", help="跳过 PyInstaller，仅重新生成安装器")
    ap.add_argument("--no-installer", action="store_true", help="只打绿色版，不出安装器")
    ap.add_argument("--with-dycast", action="store_true", help="附带 dycast-main 弹幕姬源码")
    args = ap.parse_args()

    ver = read_version_info()
    name = args.name or ver["APP_NAME"]
    print(f"应用：{ver['APP_DISPLAY_NAME']}  v{ver['APP_VERSION']}  制作：{ver['APP_PUBLISHER']}")
    print(f"产物名：{name}")

    if not args.skip_build:
        if not check_pyinstaller():
            sys.exit(1)
        run_pyinstaller(name)

    if args.with_dycast:
        copy_dycast(name)

    privacy_scan(name)

    if args.no_installer:
        print("\n完成（--no-installer：未生成安装器）")
        return

    build_installer(name, ver)
    print("\n全部完成 ✓")
    print(f"  绿色版：dist\\{name}\\{name}.exe（无 CMD 窗口，双击即用）")
    print(f"  安装器：dist\\installer\\{name}_Setup_{ver['APP_VERSION']}.exe（可选目录/注册表/可卸载）")


if __name__ == "__main__":
    main()
